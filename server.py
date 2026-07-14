"""
Serveur Pi Zero W — Voiture RC
Reçoit les commandes UDP, pilote les servos/ESC, lance le stream vidéo.
"""

import socket
import json
import subprocess
import threading
import time
import signal
import sys
from gpiozero import Servo, PWMLED, LED
from gpiozero.pins.pigpio import PiGPIOFactory

# ─── Configuration ──────────────────────────────────────────────────────────────

UDP_PORT = 5000
VIDEO_PORT = 5001
HEARTBEAT_TIMEOUT = 0.5  # secondes sans commande → arrêt moteur
DEAD_ZONE = 0.1          # ignore les valeurs entre -0.1 et 0.1
COMMAND_RATE_LOG = 5      # log toutes les N secondes
REVERSE_DELAY = 0.15      # délai en secondes pour la séquence frein→neutre→recul

# Calibration direction
DIRECTION_INVERT = True   # True = inverse le sens du servo direction
DIRECTION_TRIM = -0.18     # offset de centrage — compense le décalage gauche au repos

VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
VIDEO_FPS = 30

GPIO_DIRECTION = 12  # PWM0
GPIO_CAMERA = 13     # PWM1
GPIO_ESC = 18

# LEDs éclairage
GPIO_LED_FRONT = 24       # Feux de position avant (blanc)
GPIO_LED_REAR = 25        # Feux de position arrière + freinage (rouge) — PWM
GPIO_LED_REVERSE = 8      # Feux de recul (blanc)

REAR_LIGHT_DIM = 0.2      # Intensité feux de position arrière (20%)
REAR_LIGHT_BRAKE = 1.0    # Intensité freinage (100%)

# Plages PWM servos (en secondes)
SERVO_MIN_PULSE = 0.5 / 1000   # 0.5 ms
SERVO_MAX_PULSE = 2.5 / 1000   # 2.5 ms

# ESC : même plage PWM qu'un servo standard
ESC_MIN_PULSE = 1.0 / 1000  # 1.0 ms = recul max
ESC_MAX_PULSE = 2.0 / 1000  # 2.0 ms = avant max
ESC_NEUTRAL_PULSE = 1.5 / 1000  # 1.5 ms = neutre

# Détection d'arrêt : si la voiture est déjà immobile, on saute le freinage de la
# séquence de recul (source d'un à-coup inutile — il n'y a rien à freiner à l'arrêt).
STANDSTILL_EPS = 0.05    # sortie avant en-dessous = considérée nulle (arrêt)
STANDSTILL_TIME = 0.3    # durée d'immobilité (s) avant d'autoriser un recul sans freinage


# ─── État global ────────────────────────────────────────────────────────────────

state = {
    'direction': 0.0,
    'vitesse': 0.0,
    'camera': 0.0,
    'phares': False,         # feux de position on/off (toggle depuis controller)
}
last_command_time = time.monotonic()
lock = threading.Lock()
running = True

# Machine à états pour la marche arrière ESC
# États : 'forward', 'braking', 'neutral_wait', 'reverse'
esc_state = 'forward'
esc_last_transition = 0.0
esc_zero_since = None  # instant où la sortie avant est retombée à ~0 (None = en mouvement)


# ─── Initialisation matériel ────────────────────────────────────────────────────

factory = PiGPIOFactory()

servo_direction = Servo(
    GPIO_DIRECTION,
    min_pulse_width=SERVO_MIN_PULSE,
    max_pulse_width=SERVO_MAX_PULSE,
    pin_factory=factory,
)
servo_camera = Servo(
    GPIO_CAMERA,
    min_pulse_width=SERVO_MIN_PULSE,
    max_pulse_width=SERVO_MAX_PULSE,
    pin_factory=factory,
)
esc_motor = Servo(
    GPIO_ESC,
    min_pulse_width=ESC_MIN_PULSE,
    max_pulse_width=ESC_MAX_PULSE,
    pin_factory=factory,
)

# LEDs
led_front = LED(GPIO_LED_FRONT, pin_factory=factory)         # on/off simple
led_rear = PWMLED(GPIO_LED_REAR, pin_factory=factory)        # PWM pour dim/bright
led_reverse = LED(GPIO_LED_REVERSE, pin_factory=factory)     # on/off simple


# ─── Fonctions utilitaires ──────────────────────────────────────────────────────

def apply_dead_zone(value):
    """Retourne 0 si la valeur est dans la zone morte."""
    if abs(value) < DEAD_ZONE:
        return 0.0
    return value


def clamp(value, min_val=-1.0, max_val=1.0):
    """Limite une valeur entre min et max."""
    return max(min_val, min(max_val, value))


def _esc_state_machine(speed, now):
    """
    Machine à états double-tap pour la marche arrière ESC. Retourne la valeur
    ESC *continue* à appliquer et met à jour esc_state.

    Séquence pour reculer depuis la marche avant :
    1. forward → braking  : envoie signal négatif (frein)
    2. braking → neutral  : retour au neutre (0)
    3. neutral → reverse  : envoie signal négatif (recul)

    Si la voiture est DÉJÀ à l'arrêt (sortie avant ~nulle depuis STANDSTILL_TIME),
    l'étape 1 est sautée : pas de freinage → pas d'à-coup, on passe directement
    par le neutre avant le recul.
    """
    global esc_state, esc_last_transition, esc_zero_since

    if speed >= 0:
        esc_state = 'forward'
        # Suivi de l'immobilité : depuis quand la sortie avant est-elle ~nulle ?
        if speed <= STANDSTILL_EPS:
            if esc_zero_since is None:
                esc_zero_since = now
        else:
            esc_zero_since = None
        return speed

    # Le pilote veut reculer (speed < 0)
    if esc_state == 'forward':
        stopped = esc_zero_since is not None and (now - esc_zero_since) >= STANDSTILL_TIME
        if stopped:
            # Déjà à l'arrêt → on saute le freinage (pas d'à-coup).
            esc_state = 'neutral_wait'
            esc_last_transition = now
            print("[ESC] Déjà à l'arrêt → recul sans freinage")
            return 0.0
        esc_state = 'braking'
        esc_last_transition = now
        print(f"[ESC] Freinage ({speed:+.2f})")
        return speed
    if esc_state == 'braking':
        if now - esc_last_transition >= REVERSE_DELAY:
            esc_state = 'neutral_wait'
            esc_last_transition = now
            print("[ESC] Neutre (transition)")
            return 0.0
        return speed  # maintient le frein
    if esc_state == 'neutral_wait':
        if now - esc_last_transition >= REVERSE_DELAY:
            esc_state = 'reverse'
            esc_last_transition = now
            print("[ESC] Marche arrière activée")
        return 0.0  # maintient le neutre
    if esc_state == 'reverse':
        return speed
    return 0.0


def apply_esc_value(target_speed):
    """
    Applique la vitesse à l'ESC : zone morte sur l'intention du pilote, puis
    machine à états avant/frein/neutre/recul.

    Le mode manœuvre (vitesse lente plafonnée + commande momentanée) est géré
    côté controller : il envoie simplement une petite vitesse fixe. Le serveur
    ne fait aucune distinction — il applique la valeur reçue.
    """
    now = time.monotonic()
    speed = apply_dead_zone(clamp(target_speed))
    esc_motor.value = _esc_state_machine(speed, now)


# Cache des sorties précédentes pour éviter les écritures GPIO inutiles
_prev_outputs = {
    'direction': None,
    'esc': None,
    'camera': None,
    'front': None,
    'rear': None,
    'reverse': None,
}


def _set_if_changed(key, value, setter):
    """N'écrit sur le GPIO que si la valeur a changé."""
    if _prev_outputs[key] != value:
        setter(value)
        _prev_outputs[key] = value


def apply_state():
    """Applique l'état courant aux sorties PWM — avec cache pour limiter les I/O."""
    with lock:
        d = state['direction']
        v = state['vitesse']
        c = state['camera']
        phares = state['phares']

    # Direction : inversion + trim + dead zone
    dir_value = apply_dead_zone(clamp(d))
    if DIRECTION_INVERT:
        dir_value = -dir_value
    dir_value = clamp(dir_value + DIRECTION_TRIM)

    _set_if_changed('direction', round(dir_value, 3), lambda val: setattr(servo_direction, 'value', val))

    apply_esc_value(v)

    _set_if_changed('camera', round(clamp(c), 3), lambda val: setattr(servo_camera, 'value', val))

    # ── Éclairage automatique ──
    speed = apply_dead_zone(clamp(v))
    is_braking = (speed < 0 and esc_state == 'forward') or esc_state == 'braking'
    is_reversing = esc_state == 'reverse' or esc_state == 'neutral_wait'

    # Feux de position avant
    front_on = phares
    _set_if_changed('front', front_on, lambda val: led_front.on() if val else led_front.off())

    # Feux arrière rouges : freinage > position > éteint
    if is_braking:
        rear_val = REAR_LIGHT_BRAKE
    elif phares:
        rear_val = REAR_LIGHT_DIM
    else:
        rear_val = 0
    _set_if_changed('rear', rear_val, lambda val: setattr(led_rear, 'value', val))

    # Feux de recul blancs
    _set_if_changed('reverse', is_reversing, lambda val: led_reverse.on() if val else led_reverse.off())


def emergency_stop():
    """Coupe le moteur, recentre la direction, éteint les feux."""
    global esc_state
    with lock:
        state['vitesse'] = 0.0
        state['direction'] = 0.0
    esc_motor.value = 0
    servo_direction.value = 0
    esc_state = 'forward'
    led_reverse.off()
    led_rear.value = 0
    print("[FAILSAFE] Arrêt d'urgence — pas de heartbeat")


# ─── Stream vidéo ───────────────────────────────────────────────────────────────

video_process = None


def start_video_stream():
    """Lance rpicam-vid en stream TCP H264."""
    global video_process
    cmd = [
        'rpicam-vid',
        '-t', '0',
        '--inline',
        '--width', str(VIDEO_WIDTH),
        '--height', str(VIDEO_HEIGHT),
        '--framerate', str(VIDEO_FPS),
        '--codec', 'h264',
        '--profile', 'baseline',
        '--level', '4',
        '--bitrate', '2000000',
        '--listen',
        '-o', f'tcp://0.0.0.0:{VIDEO_PORT}',
    ]
    video_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[VIDEO] Stream H264 démarré sur tcp://0.0.0.0:{VIDEO_PORT}")


def stop_video_stream():
    """Arrête le processus vidéo."""
    global video_process
    if video_process:
        video_process.terminate()
        video_process.wait(timeout=5)
        video_process = None
        print("[VIDEO] Stream arrêté")


# ─── Réception des commandes UDP ────────────────────────────────────────────────

def udp_listener():
    """Écoute les commandes UDP et met à jour l'état."""
    global last_command_time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', UDP_PORT))
    sock.settimeout(0.1)

    print(f"[UDP] Écoute sur port {UDP_PORT}")

    while running:
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            cmd = json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        cmd_type = cmd.get('type')
        value = cmd.get('value')

        if cmd_type is None or value is None:
            continue

        try:
            value = float(value)
        except (TypeError, ValueError):
            continue

        with lock:
            if cmd_type in ('direction', 'vitesse', 'camera'):
                state[cmd_type] = value
                last_command_time = time.monotonic()
            elif cmd_type == 'phares':
                new_val = bool(value)
                if state['phares'] != new_val:
                    state['phares'] = new_val
                    print(f"[LIGHTS] Phares {'ON' if new_val else 'OFF'}")


    sock.close()
    print("[UDP] Listener arrêté")


# ─── Boucle d'application des sorties ─────────────────────────────────────────

APPLY_RATE_HZ = 50   # fréquence d'application des sorties (50 Hz = toutes les 20 ms)

def output_loop():
    """
    Boucle dédiée qui applique l'état aux servos/LEDs à fréquence fixe.
    Découplée de la réception UDP pour éviter les pics de latence.
    """
    interval = 1.0 / APPLY_RATE_HZ

    while running:
        start = time.monotonic()

        # Heartbeat / failsafe
        elapsed = start - last_command_time
        if elapsed > HEARTBEAT_TIMEOUT:
            with lock:
                if state['vitesse'] != 0.0:
                    emergency_stop()
        else:
            apply_state()

        dt = time.monotonic() - start
        sleep_time = interval - dt
        if sleep_time > 0:
            time.sleep(sleep_time)


# ─── Arrêt propre ──────────────────────────────────────────────────────────────

def shutdown(signum=None, frame=None):
    """Arrêt propre de tous les composants."""
    global running
    print("\n[SHUTDOWN] Arrêt en cours...")
    running = False

    # Tout à zéro
    esc_motor.value = 0
    servo_direction.value = 0
    servo_camera.value = 0
    led_front.off()
    led_rear.off()
    led_reverse.off()

    stop_video_stream()

    # Fermeture gpiozero
    servo_direction.close()
    servo_camera.close()
    esc_motor.close()
    led_front.close()
    led_rear.close()
    led_reverse.close()

    print("[SHUTDOWN] Terminé")
    sys.exit(0)


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("=" * 50)
    print("  Serveur voiture RC — Pi Zero W")
    print("=" * 50)

    # Initialisation ESC : position neutre
    esc_motor.value = 0
    servo_direction.value = 0
    servo_camera.value = 0
    print("[INIT] Servos et ESC au neutre")

    # Pause pour armer l'ESC (certains ESC demandent un signal neutre au boot)
    print("[INIT] Armement ESC (2s)...")
    time.sleep(2)

    start_video_stream()

    # Lancement des threads
    thread_udp = threading.Thread(target=udp_listener, daemon=True)
    thread_output = threading.Thread(target=output_loop, daemon=True)
    thread_udp.start()
    thread_output.start()

    print("[READY] Serveur prêt, en attente de commandes")

    # Boucle principale (garde le process vivant)
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == '__main__':
    main()
