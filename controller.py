"""
Client PC — Voiture RC
Interface desktop PyQt6 : cockpit avec vidéo, jauges, boussole, logs.
Architecture multi-thread : input / UDP sender / vidéo ffmpeg.
"""

import socket
import json
import subprocess
import threading
import time
import sys
import math
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QGridLayout,
    QTextEdit, QSplitter, QFrame, QStackedWidget, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QPen, QKeyEvent

# ─── Configuration par défaut ────────────────────────────────────────────────────

DEFAULT_PI_HOST = 'rc-car.local'
DEFAULT_UDP_PORT = 5000
DEFAULT_VIDEO_PORT = 5001
TELEMETRY_PORT = 5002        # Pi → PC : télémétrie batteries (JSON)
BATTERY_LOW_PCT = 20         # seuil d'alerte batterie moteur basse

SEND_RATE_HZ = 30
SPEED_STEP = 0.05
DIRECTION_STEP = 0.1

# Mode manœuvre (touche M) : vitesse momentanée et plafonnée au minimum.
# Le moteur ne tourne qu'à l'appui sur ↑/↓, à cette vitesse fixe, et s'arrête au
# relâchement (aucune accumulation). Doivent rester > DEAD_ZONE serveur (0.1) sinon
# le serveur les ignore. À AJUSTER selon le ressenti.
# Avant et arrière sont dissociés : l'ESC a besoin d'un signal plus fort pour
# déclencher la séquence de recul, alors qu'un niveau plus doux suffit à l'avant.
MANEUVER_SPEED_FWD = 0.15   # marche avant : le mini qui fait tout juste avancer
MANEUVER_SPEED_REV = 0.20   # marche arrière : + élevé pour déclencher le recul de l'ESC

VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480


# ─── Signaux inter-threads ──────────────────────────────────────────────────────

class Signals(QObject):
    frame_ready = pyqtSignal(np.ndarray)
    log_message = pyqtSignal(str)
    video_status = pyqtSignal(bool)
    telemetry_ready = pyqtSignal(dict)


signals = Signals()


# ─── État global ────────────────────────────────────────────────────────────────

state = {
    'direction': 0.0,
    'vitesse': 0.0,
    'camera': 0.0,
    'phares': False,
    'slow_mode': False,
}
lock = threading.Lock()
running = False
connected = False
pressed_keys = set()
pressed_lock = threading.Lock()


def clamp(value, min_val=-1.0, max_val=1.0):
    return max(min_val, min(max_val, value))


def log(msg):
    """Log via signal (thread-safe)."""
    signals.log_message.emit(msg)


# ─── Résolution réseau ──────────────────────────────────────────────────────────

def resolve_host(host):
    """Résout le hostname (mDNS ou IP)."""
    try:
        ip = socket.gethostbyname(host)
        return ip
    except socket.gaierror:
        return None


# ─── Thread envoi UDP ───────────────────────────────────────────────────────────

class UDPSender(threading.Thread):
    def __init__(self, pi_addr, udp_port):
        super().__init__(daemon=True)
        self.pi_addr = pi_addr
        self.udp_port = udp_port

    def _send(self, sock, addr, cmd_type, value):
        """Envoie un paquet UDP de commande."""
        msg = json.dumps({'type': cmd_type, 'value': round(value, 2) if isinstance(value, float) else value})
        try:
            sock.sendto(msg.encode('utf-8'), addr)
        except OSError:
            pass

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr = (self.pi_addr, self.udp_port)
        interval = 1.0 / SEND_RATE_HZ
        prev_phares = None  # envoie phares uniquement au changement

        log(f"[UDP] Envoi vers {self.pi_addr}:{self.udp_port} à {SEND_RATE_HZ} Hz")

        while running:
            start = time.monotonic()

            update_state_from_keys()

            with lock:
                d, v, c = state['direction'], state['vitesse'], state['camera']
                phares = state['phares']

            # Commandes mouvements : envoyées chaque tick (heartbeat)
            self._send(sock, addr, 'direction', d)
            self._send(sock, addr, 'vitesse', v)
            self._send(sock, addr, 'camera', c)

            # Phares : envoyé uniquement quand ça change (pas de spam)
            if phares != prev_phares:
                self._send(sock, addr, 'phares', int(phares))
                prev_phares = phares

            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Arrêt propre : envoie zéro
        for cmd_type in ('vitesse', 'direction'):
            self._send(sock, addr, cmd_type, 0.0)
        sock.close()
        log("[UDP] Sender arrêté")


# ─── Gestion clavier ───────────────────────────────────────────────────────────

CAMERA_POSITIONS = {
    Qt.Key.Key_Z: 0.0,
    Qt.Key.Key_S: 1.0,
    Qt.Key.Key_Q: -0.5,
    Qt.Key.Key_D: 0.5,
}


def update_state_from_keys():
    """Met à jour l'état en fonction des touches enfoncées."""
    with pressed_lock:
        keys = set(pressed_keys)

    with lock:
        slow_mode = state['slow_mode']

        # Direction
        if Qt.Key.Key_Left in keys and Qt.Key.Key_Right not in keys:
            state['direction'] = clamp(state['direction'] - DIRECTION_STEP)
        elif Qt.Key.Key_Right in keys and Qt.Key.Key_Left not in keys:
            state['direction'] = clamp(state['direction'] + DIRECTION_STEP)
        else:
            state['direction'] = 0.0

        # Vitesse
        if slow_mode:
            # Mode manœuvre : momentané, plafonné au minimum. Le moteur ne tourne
            # qu'à l'appui, à vitesse mini fixe, et s'arrête au relâchement.
            if Qt.Key.Key_Up in keys and Qt.Key.Key_Down not in keys:
                state['vitesse'] = MANEUVER_SPEED_FWD
            elif Qt.Key.Key_Down in keys and Qt.Key.Key_Up not in keys:
                state['vitesse'] = -MANEUVER_SPEED_REV
            else:
                state['vitesse'] = 0.0
        else:
            # Conduite normale : accumulation par crans (maintien au relâchement).
            if Qt.Key.Key_Up in keys and Qt.Key.Key_Down not in keys:
                state['vitesse'] = clamp(state['vitesse'] + SPEED_STEP)
            elif Qt.Key.Key_Down in keys and Qt.Key.Key_Up not in keys:
                state['vitesse'] = clamp(state['vitesse'] - SPEED_STEP)

        # Espace = arrêt
        if Qt.Key.Key_Space in keys:
            state['vitesse'] = 0.0

        # Caméra
        for key_code, pos in CAMERA_POSITIONS.items():
            if key_code in keys:
                state['camera'] = pos
                break


# ─── Thread vidéo ───────────────────────────────────────────────────────────────

def recv_exact(sock, size):
    """Lit exactement `size` octets depuis un socket."""
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class VideoReceiverRaw(threading.Thread):
    """Réception vidéo en mode raw RGB (simulateur)."""

    def __init__(self, pi_addr, video_port):
        super().__init__(daemon=True)
        self.pi_addr = pi_addr
        self.video_port = video_port

    def run(self):
        frame_size = VIDEO_WIDTH * VIDEO_HEIGHT * 3

        while running:
            log(f"[VIDEO] Connexion raw à {self.pi_addr}:{self.video_port}...")
            signals.video_status.emit(False)

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self.pi_addr, self.video_port))
                sock.settimeout(2.0)
            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                log(f"[VIDEO] Connexion échouée : {e}")
                if running:
                    time.sleep(2)
                continue

            log("[VIDEO] Flux raw connecté")
            signals.video_status.emit(True)

            while running:
                try:
                    raw = recv_exact(sock, frame_size)
                except (socket.timeout, OSError):
                    log("[VIDEO] Timeout lecture")
                    break

                if raw is None:
                    log("[VIDEO] Flux interrompu, reconnexion dans 2s...")
                    signals.video_status.emit(False)
                    break

                frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (VIDEO_HEIGHT, VIDEO_WIDTH, 3)
                )
                signals.frame_ready.emit(frame)

            try:
                sock.close()
            except Exception:
                pass

            if running:
                time.sleep(2)

        log("[VIDEO] Receiver arrêté")


class VideoReceiverH264(threading.Thread):
    """Réception vidéo en mode H264 via ffmpeg (vrai Pi)."""

    def __init__(self, pi_addr, video_port):
        super().__init__(daemon=True)
        self.pi_addr = pi_addr
        self.video_port = video_port

    def run(self):
        video_url = f'tcp://{self.pi_addr}:{self.video_port}'
        frame_size = VIDEO_WIDTH * VIDEO_HEIGHT * 3

        while running:
            log(f"[VIDEO] Connexion H264 à {video_url}...")
            signals.video_status.emit(False)

            cmd = [
                'ffmpeg',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-probesize', '500000',
                '-analyzeduration', '500000',
                '-f', 'h264',
                '-i', video_url,
                '-f', 'rawvideo',
                '-pix_fmt', 'rgb24',
                '-an', '-sn',
                '-v', 'warning',
                'pipe:1',
            ]

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=frame_size,
                )
            except FileNotFoundError:
                log("[ERREUR] ffmpeg non trouvé !")
                log("  macOS : brew install ffmpeg")
                log("  Linux : sudo apt install ffmpeg")
                return

            log("[VIDEO] Flux H264 connecté")
            signals.video_status.emit(True)

            while running:
                raw = process.stdout.read(frame_size)
                if len(raw) != frame_size:
                    log("[VIDEO] Flux interrompu, reconnexion dans 2s...")
                    signals.video_status.emit(False)
                    break

                frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (VIDEO_HEIGHT, VIDEO_WIDTH, 3)
                )
                signals.frame_ready.emit(frame)

            process.terminate()
            process.wait()

            if running:
                time.sleep(2)

        log("[VIDEO] Receiver arrêté")


# ═══════════════════════════════════════════════════════════════════════════════
#  WIDGETS CUSTOM
# ═══════════════════════════════════════════════════════════════════════════════

class SpeedGauge(QWidget):
    """Jauge de vitesse verticale : -1 (recul) à +1 (avant)."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setMinimumSize(80, 200)

    def set_value(self, v):
        self.value = clamp(v)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin = 10
        bar_w = 40
        bar_x = (w - bar_w) // 2
        bar_h = h - 2 * margin
        center_y = margin + bar_h // 2

        # Fond
        p.fillRect(self.rect(), QColor(30, 30, 30))

        # Barre de fond
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.setBrush(QColor(50, 50, 50))
        p.drawRoundedRect(bar_x, margin, bar_w, bar_h, 5, 5)

        # Ligne centre (zéro)
        p.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
        p.drawLine(bar_x, center_y, bar_x + bar_w, center_y)

        # Barre de valeur
        fill_h = int(abs(self.value) * (bar_h // 2))
        if self.value >= 0:
            color = QColor(0, 200, 80)
            fill_y = center_y - fill_h
        else:
            color = QColor(220, 60, 60)
            fill_y = center_y

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(bar_x + 2, fill_y, bar_w - 4, fill_h, 3, 3)

        # Texte
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Courier', 12, QFont.Weight.Bold))
        p.drawText(0, 0, w, margin, Qt.AlignmentFlag.AlignCenter, "VIT")
        p.setFont(QFont('Courier', 14, QFont.Weight.Bold))
        p.setPen(color if self.value != 0 else QColor(150, 150, 150))
        p.drawText(0, h - margin - 5, w, margin + 5,
                   Qt.AlignmentFlag.AlignCenter, f"{self.value:+.2f}")

        p.end()


class DirectionCompass(QWidget):
    """Boussole de direction : barre horizontale avec indicateur."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setMinimumSize(200, 60)
        self.setMaximumHeight(80)

    def set_value(self, v):
        self.value = clamp(v)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin = 20
        bar_y = h // 2
        bar_left = margin
        bar_right = w - margin
        bar_w = bar_right - bar_left
        center_x = w // 2

        # Fond
        p.fillRect(self.rect(), QColor(30, 30, 30))

        # Barre de fond
        p.setPen(QPen(QColor(80, 80, 80), 3))
        p.drawLine(bar_left, bar_y, bar_right, bar_y)

        # Marqueurs
        p.setPen(QPen(QColor(100, 100, 100), 1))
        for i in range(-10, 11, 2):
            x = center_x + int(i / 10 * (bar_w // 2))
            tick_h = 8 if i % 5 == 0 else 4
            p.drawLine(x, bar_y - tick_h, x, bar_y + tick_h)

        # Centre
        p.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DashLine))
        p.drawLine(center_x, bar_y - 15, center_x, bar_y + 15)

        # Indicateur
        indicator_x = center_x + int(self.value * (bar_w // 2))
        color = QColor(0, 180, 255)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        points = [
            (indicator_x, bar_y - 12),
            (indicator_x - 6, bar_y - 22),
            (indicator_x + 6, bar_y - 22),
        ]
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPolygonF
        polygon = QPolygonF([QPointF(x, y) for x, y in points])
        p.drawPolygon(polygon)

        # Labels
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Courier', 9))
        p.drawText(bar_left - 5, bar_y + 20, "G")
        p.drawText(bar_right - 3, bar_y + 20, "D")

        # Valeur
        p.setFont(QFont('Courier', 11, QFont.Weight.Bold))
        p.setPen(color if self.value != 0 else QColor(150, 150, 150))
        p.drawText(0, h - 18, w, 18, Qt.AlignmentFlag.AlignCenter,
                   f"DIR {self.value:+.2f}")

        p.end()


class CameraIndicator(QWidget):
    """Indicateur de position caméra : vue du dessus avec angle."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setMinimumSize(100, 100)
        self.setMaximumSize(120, 120)

    def set_value(self, v):
        self.value = clamp(v)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 2 - 10

        # Fond
        p.fillRect(self.rect(), QColor(30, 30, 30))

        # Cercle extérieur
        p.setPen(QPen(QColor(70, 70, 70), 2))
        p.setBrush(QColor(40, 40, 40))
        p.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        # Labels
        p.setPen(QColor(100, 100, 100))
        p.setFont(QFont('Courier', 8))
        p.drawText(cx - 4, cy - radius + 12, "AV")
        p.drawText(cx - 4, cy + radius - 4, "AR")
        p.drawText(cx - radius + 2, cy + 4, "G")
        p.drawText(cx + radius - 10, cy + 4, "D")

        # Angle caméra : 0=avant (haut), 1=arrière droite, -1=arrière gauche
        # Mapping : value → angle en radians (0=haut, positif=horaire)
        angle_rad = self.value * math.pi  # -π à π
        end_x = cx + int(math.sin(angle_rad) * (radius - 5))
        end_y = cy - int(math.cos(angle_rad) * (radius - 5))

        # Cône de vision
        cone_angle = 0.4
        cone_r = radius - 5
        lx = cx + int(math.sin(angle_rad - cone_angle) * cone_r)
        ly = cy - int(math.cos(angle_rad - cone_angle) * cone_r)
        rx = cx + int(math.sin(angle_rad + cone_angle) * cone_r)
        ry = cy - int(math.cos(angle_rad + cone_angle) * cone_r)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 150, 255, 40))
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPolygonF
        cone = QPolygonF([QPointF(cx, cy), QPointF(lx, ly), QPointF(rx, ry)])
        p.drawPolygon(cone)

        # Ligne de direction caméra
        p.setPen(QPen(QColor(0, 180, 255), 2))
        p.drawLine(cx, cy, end_x, end_y)

        # Point central (voiture)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 200, 0))
        p.drawEllipse(cx - 4, cy - 4, 8, 8)

        # Label
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Courier', 9, QFont.Weight.Bold))
        p.drawText(0, 0, w, 12, Qt.AlignmentFlag.AlignCenter, "CAM")

        p.end()


class StatusIndicator(QWidget):
    """Pastille de statut : vert = connecté, rouge = déconnecté."""

    def __init__(self, label_text):
        super().__init__()
        self.label_text = label_text
        self.is_active = False
        self.setFixedSize(120, 30)

    def set_active(self, active):
        self.is_active = active
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(0, 200, 80) if self.is_active else QColor(180, 50, 50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(5, 8, 14, 14)

        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont('Courier', 10))
        p.drawText(25, 0, 90, 30, Qt.AlignmentFlag.AlignVCenter, self.label_text)

        p.end()


class BatteryIndicator(QWidget):
    """Jauge batterie : barre remplie au %, couleur selon le niveau, + tension."""

    def __init__(self, label_text):
        super().__init__()
        self.label_text = label_text
        self.pct = None
        self.voltage = None
        self.setFixedSize(170, 32)

    def set_value(self, pct, voltage):
        self.pct = pct
        self.voltage = voltage
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Libellé
        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont('Courier', 9))
        p.drawText(0, 0, 56, 32, Qt.AlignmentFlag.AlignVCenter, self.label_text)

        # Cadre de la barre
        bx, by, bw, bh = 58, 7, 108, 18
        p.setPen(QPen(QColor(120, 120, 120), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bx, by, bw, bh)

        if self.pct is None:
            p.setPen(QColor(120, 120, 120))
            p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, "N/A")
            p.end()
            return

        pct = max(0, min(100, int(self.pct)))
        if pct > 50:
            col = QColor(0, 200, 80)
        elif pct >= BATTERY_LOW_PCT:
            col = QColor(230, 160, 0)
        else:
            col = QColor(210, 50, 50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRect(bx + 1, by + 1, int((bw - 2) * pct / 100), bh - 2)

        # Texte % + tension par-dessus
        txt = f"{pct}%"
        if self.voltage is not None:
            txt += f"  {self.voltage:.1f}V"
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Courier', 9, QFont.Weight.Bold))
        p.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class TelemetryReceiver(threading.Thread):
    """Reçoit la télémétrie batterie du Pi (UDP) et la transmet à l'UI via signal."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.port))
        except OSError as e:
            log(f"[TELEM] Bind UDP {self.port} impossible : {e}")
            return
        sock.settimeout(0.5)
        log(f"[TELEM] Écoute télémétrie sur UDP {self.port}")

        while running:
            try:
                data, _ = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            signals.telemetry_ready.emit(payload)

        sock.close()
        log("[TELEM] Récepteur télémétrie arrêté")


# ═══════════════════════════════════════════════════════════════════════════════
#  ÉCRAN DE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class ConfigScreen(QWidget):
    connect_requested = pyqtSignal(str, int, int, bool)  # ip, udp_port, video_port, simulator_mode

    def __init__(self):
        super().__init__()
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Titre
        title = QLabel("🏎️  RC Car Controller")
        title.setFont(QFont('Arial', 24, QFont.Weight.Bold))
        title.setStyleSheet("color: #00b4ff; margin-bottom: 20px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Groupe connexion
        group = QGroupBox("Configuration connexion")
        group.setStyleSheet("""
            QGroupBox {
                color: #ccc;
                border: 1px solid #555;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 20px;
                font-size: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px;
            }
        """)
        group.setMaximumWidth(450)
        grid = QGridLayout(group)
        grid.setSpacing(12)

        input_style = """
            QLineEdit {
                background: #2a2a2a;
                color: #fff;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
                font-family: Courier;
            }
            QLineEdit:focus {
                border-color: #00b4ff;
            }
        """

        # Host
        grid.addWidget(QLabel("Adresse du Pi"), 0, 0)
        self.host_input = QLineEdit(DEFAULT_PI_HOST)
        self.host_input.setStyleSheet(input_style)
        self.host_input.setPlaceholderText("ex: 192.168.1.42 ou raspberrypi.local")
        grid.addWidget(self.host_input, 0, 1)

        # UDP port
        grid.addWidget(QLabel("Port commandes (UDP)"), 1, 0)
        self.udp_input = QLineEdit(str(DEFAULT_UDP_PORT))
        self.udp_input.setStyleSheet(input_style)
        grid.addWidget(self.udp_input, 1, 1)

        # Video port
        grid.addWidget(QLabel("Port vidéo (TCP)"), 2, 0)
        self.video_input = QLineEdit(str(DEFAULT_VIDEO_PORT))
        self.video_input.setStyleSheet(input_style)
        grid.addWidget(self.video_input, 2, 1)

        # Mode simulateur
        self.simulator_check = QCheckBox("Mode simulateur (localhost, sans ffmpeg)")
        self.simulator_check.setStyleSheet("""
            QCheckBox {
                color: #ffa500;
                font-size: 13px;
                padding: 5px;
            }
            QCheckBox::indicator {
                width: 18px; height: 18px;
            }
        """)
        self.simulator_check.toggled.connect(self.on_simulator_toggle)
        grid.addWidget(self.simulator_check, 3, 0, 1, 2)

        layout.addWidget(group)

        # Status
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #ff6b6b; font-size: 13px; margin: 10px;")
        layout.addWidget(self.status_label)

        # Bouton connexion
        self.connect_btn = QPushButton("🔌  Connecter")
        self.connect_btn.setFixedSize(200, 50)
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background: #00b4ff;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #0090cc;
            }
            QPushButton:pressed {
                background: #006699;
            }
            QPushButton:disabled {
                background: #555;
                color: #888;
            }
        """)
        self.connect_btn.clicked.connect(self.on_connect)
        layout.addWidget(self.connect_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Contrôles
        help_text = QLabel(
            "↑↓ Vitesse  |  ←→ Direction  |  ZQSD Caméra  |  L Phares  |  M Manœuvre  |  ESPACE Stop"
        )
        help_text.setStyleSheet("color: #666; margin-top: 20px; font-size: 11px;")
        help_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(help_text)

    def on_simulator_toggle(self, checked):
        """Pré-remplit localhost quand le mode simulateur est coché."""
        if checked:
            self.host_input.setText('localhost')
        else:
            self.host_input.setText(DEFAULT_PI_HOST)

    def on_connect(self):
        host = self.host_input.text().strip()
        if not host:
            self.status_label.setText("⚠ Adresse du Pi requise")
            return

        try:
            udp_port = int(self.udp_input.text().strip())
        except ValueError:
            self.status_label.setText("⚠ Port UDP invalide")
            return

        try:
            video_port = int(self.video_input.text().strip())
        except ValueError:
            self.status_label.setText("⚠ Port vidéo invalide")
            return

        simulator = self.simulator_check.isChecked()

        self.connect_btn.setEnabled(False)
        self.status_label.setStyleSheet("color: #ffa500; font-size: 13px; margin: 10px;")
        self.status_label.setText(f"Résolution de {host}...")

        # Résolution en thread pour ne pas bloquer l'UI
        def do_resolve():
            ip = resolve_host(host)
            if ip:
                self.connect_requested.emit(ip, udp_port, video_port, simulator)
            else:
                self.status_label.setStyleSheet(
                    "color: #ff6b6b; font-size: 13px; margin: 10px;"
                )
                self.status_label.setText(
                    f"❌ Impossible de résoudre {host}\n"
                    "Vérifiez que le Pi est allumé et sur le même réseau."
                )
                self.connect_btn.setEnabled(True)

        threading.Thread(target=do_resolve, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════════
#  ÉCRAN COCKPIT (PILOTAGE)
# ═══════════════════════════════════════════════════════════════════════════════

class CockpitScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # ── Colonne gauche : jauge vitesse ──
        left_col = QVBoxLayout()
        self.speed_gauge = SpeedGauge()
        left_col.addWidget(self.speed_gauge)
        main_layout.addLayout(left_col)

        # ── Colonne centre : vidéo + direction ──
        center_col = QVBoxLayout()

        # Vidéo
        self.video_label = QLabel()
        self.video_label.setMinimumSize(VIDEO_WIDTH, VIDEO_HEIGHT)
        self.video_label.setStyleSheet("background: #111; border: 1px solid #333;")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setText("En attente du flux vidéo...")
        self.video_label.setStyleSheet(
            "background: #111; border: 1px solid #333; color: #555; font-size: 14px;"
        )
        center_col.addWidget(self.video_label, stretch=1)

        # Boussole direction
        self.direction_compass = DirectionCompass()
        center_col.addWidget(self.direction_compass)

        main_layout.addLayout(center_col, stretch=1)

        # ── Colonne droite : caméra, status, logs, bouton stop ──
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        # Indicateurs status
        status_group = QGroupBox("Statut")
        status_group.setStyleSheet("""
            QGroupBox {
                color: #aaa; border: 1px solid #444;
                border-radius: 6px; margin-top: 8px; padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }
        """)
        status_layout = QVBoxLayout(status_group)
        self.udp_status = StatusIndicator("UDP")
        self.video_status = StatusIndicator("Vidéo")
        self.lights_status = StatusIndicator("Phares")
        self.slow_status = StatusIndicator("Manœuvre")
        status_layout.addWidget(self.udp_status)
        status_layout.addWidget(self.video_status)
        status_layout.addWidget(self.lights_status)
        status_layout.addWidget(self.slow_status)
        right_col.addWidget(status_group)

        # Batteries
        batt_group = QGroupBox("Batteries")
        batt_group.setStyleSheet(status_group.styleSheet())
        batt_layout = QVBoxLayout(batt_group)
        self.motor_battery = BatteryIndicator("Moteur")
        self.pi_power_status = StatusIndicator("Alim Pi")
        batt_layout.addWidget(self.motor_battery)
        batt_layout.addWidget(self.pi_power_status)
        right_col.addWidget(batt_group)

        # État interne alerte batterie basse (pour ne logguer qu'au franchissement)
        self._batt_low_alerted = False

        # Indicateur caméra
        cam_group = QGroupBox("Caméra")
        cam_group.setStyleSheet(status_group.styleSheet())
        cam_layout = QVBoxLayout(cam_group)
        self.camera_indicator = CameraIndicator()
        cam_layout.addWidget(self.camera_indicator, alignment=Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(cam_group)

        # Logs
        log_group = QGroupBox("Logs")
        log_group.setStyleSheet(status_group.styleSheet())
        log_layout = QVBoxLayout(log_group)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: #1a1a1a; color: #0f0; border: none;
                font-family: Courier; font-size: 11px;
            }
        """)
        log_layout.addWidget(self.log_area)
        right_col.addWidget(log_group, stretch=1)

        # Bouton arrêt d'urgence
        self.stop_btn = QPushButton("⛔  ARRÊT D'URGENCE")
        self.stop_btn.setFixedHeight(50)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: #cc0000;
                color: #fff;
                border: 2px solid #ff3333;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background: #ff0000; }
            QPushButton:pressed { background: #990000; }
        """)
        self.stop_btn.clicked.connect(self.emergency_stop)
        right_col.addWidget(self.stop_btn)

        # Bouton déconnecter
        self.disconnect_btn = QPushButton("Déconnecter")
        self.disconnect_btn.setStyleSheet("""
            QPushButton {
                background: #444; color: #ccc; border: 1px solid #666;
                border-radius: 4px; padding: 8px; font-size: 12px;
            }
            QPushButton:hover { background: #555; }
        """)
        right_col.addWidget(self.disconnect_btn)

        main_layout.addLayout(right_col)

    def emergency_stop(self):
        with lock:
            state['vitesse'] = 0.0
        log("[STOP] Arrêt d'urgence déclenché")

    def update_display(self):
        with lock:
            d, v, c = state['direction'], state['vitesse'], state['camera']
            phares = state['phares']
            slow_mode = state['slow_mode']
        self.speed_gauge.set_value(v)
        self.direction_compass.set_value(d)
        self.camera_indicator.set_value(c)
        self.lights_status.set_active(phares)
        self.slow_status.set_active(slow_mode)

    def update_telemetry(self, data):
        """Met à jour l'affichage batteries depuis un paquet de télémétrie du Pi."""
        pct = data.get('motor_pct')
        volt = data.get('motor_v')
        self.motor_battery.set_value(pct, volt)
        # Alim Pi : vert = OK, rouge = sous-tension signalée
        self.pi_power_status.set_active(not data.get('pi_undervolt', False))

        # Alerte batterie moteur basse — logguée seulement au franchissement du seuil
        if pct is not None and pct < BATTERY_LOW_PCT:
            if not self._batt_low_alerted:
                log(f"[BATT] ⚠ Batterie moteur basse : {pct}%")
                self._batt_low_alerted = True
        elif pct is not None and pct >= BATTERY_LOW_PCT:
            self._batt_low_alerted = False

    def display_frame(self, frame):
        h, w, ch = frame.shape
        qimg = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        scaled = pixmap.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.video_label.setPixmap(scaled)

    def append_log(self, msg):
        self.log_area.append(msg)
        self.log_area.verticalScrollBar().setValue(
            self.log_area.verticalScrollBar().maximum()
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  FENÊTRE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RC Car Controller")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)

        # Style global
        self.setStyleSheet("""
            QMainWindow { background: #1e1e1e; }
            QWidget { color: #ddd; }
            QLabel { color: #bbb; font-size: 13px; }
            QGroupBox { font-size: 12px; }
        """)

        # Stack des écrans
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Écran config
        self.config_screen = ConfigScreen()
        self.config_screen.connect_requested.connect(self.start_connection)
        self.stack.addWidget(self.config_screen)

        # Écran cockpit
        self.cockpit = CockpitScreen()
        self.cockpit.disconnect_btn.clicked.connect(self.disconnect)
        self.stack.addWidget(self.cockpit)

        # Signaux
        signals.frame_ready.connect(self.cockpit.display_frame)
        signals.log_message.connect(self.cockpit.append_log)
        signals.video_status.connect(self.cockpit.video_status.set_active)
        signals.telemetry_ready.connect(self.cockpit.update_telemetry)

        # Timer refresh jauges (60 fps)
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.cockpit.update_display)
        self.display_timer.setInterval(16)

        self.udp_thread = None
        self.video_thread = None
        self.telemetry_thread = None

    def start_connection(self, ip, udp_port, video_port, simulator_mode=False):
        global running, connected
        running = True
        connected = True

        mode_label = "SIMULATEUR" if simulator_mode else "Pi"
        log(f"[NET] Connecté à {ip} ({mode_label})")
        log(f"[NET] UDP:{udp_port} / Vidéo:{video_port}")

        # Switch vers cockpit
        self.stack.setCurrentWidget(self.cockpit)
        self.cockpit.udp_status.set_active(True)

        # Lancer les threads
        self.udp_thread = UDPSender(ip, udp_port)
        self.udp_thread.start()

        # Vidéo : raw TCP pour simulateur, H264/ffmpeg pour le vrai Pi
        if simulator_mode:
            self.video_thread = VideoReceiverRaw(ip, video_port)
        else:
            self.video_thread = VideoReceiverH264(ip, video_port)
        self.video_thread.start()

        # Télémétrie batteries (le vrai Pi l'envoie ; le simulateur non → reste "N/A")
        self.telemetry_thread = TelemetryReceiver(TELEMETRY_PORT)
        self.telemetry_thread.start()

        self.display_timer.start()
        self.pi_addr = ip
        self.udp_port = udp_port

    def disconnect(self):
        global running, connected
        log("[SHUTDOWN] Déconnexion...")
        running = False
        connected = False

        self.display_timer.stop()
        self.cockpit.udp_status.set_active(False)
        self.cockpit.video_status.set_active(False)
        self.cockpit.pi_power_status.set_active(False)
        self.cockpit.motor_battery.set_value(None, None)

        # Reset state
        with lock:
            state['vitesse'] = 0.0
            state['direction'] = 0.0
            state['camera'] = 0.0

        # Retour config
        self.config_screen.connect_btn.setEnabled(True)
        self.config_screen.status_label.setText("")
        self.stack.setCurrentWidget(self.config_screen)

        # Reset vidéo
        self.cockpit.video_label.setPixmap(QPixmap())
        self.cockpit.video_label.setText("En attente du flux vidéo...")

    def closeEvent(self, event):
        global running
        running = False
        time.sleep(0.1)
        event.accept()


class KeyInterceptor(QObject):
    """
    Event filter global qui intercepte TOUTES les touches
    avant qu'elles n'arrivent aux widgets enfants (logs, boutons, etc.).
    """

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress and not event.isAutoRepeat():
            key = Qt.Key(event.key())
            with pressed_lock:
                pressed_keys.add(key)

            # Toggle phares sur appui de L
            if key == Qt.Key.Key_L:
                with lock:
                    state['phares'] = not state['phares']
                    status = "ON" if state['phares'] else "OFF"
                log(f"[LIGHTS] Phares {status}")
                return True

            # Toggle mode manœuvre sur appui de M
            if key == Qt.Key.Key_M:
                with lock:
                    state['slow_mode'] = not state['slow_mode']
                    status = "ON" if state['slow_mode'] else "OFF"
                log(f"[ESC] Mode manœuvre {status}")
                return True

            # Les touches de contrôle ne sont PAS propagées aux widgets
            if key in CONTROL_KEYS:
                return True  # consommé

        elif event.type() == event.Type.KeyRelease and not event.isAutoRepeat():
            with pressed_lock:
                pressed_keys.discard(Qt.Key(event.key()))
            if Qt.Key(event.key()) in CONTROL_KEYS:
                return True

        return False


# Touches que l'intercepteur doit bloquer (pas de propagation aux widgets)
CONTROL_KEYS = {
    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
    Qt.Key.Key_Z, Qt.Key.Key_Q, Qt.Key.Key_S, Qt.Key.Key_D,
    Qt.Key.Key_Space, Qt.Key.Key_L, Qt.Key.Key_M,
}


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)

    # Thème sombre natif
    app.setStyle('Fusion')

    # Intercepteur clavier global — capte les touches AVANT les widgets
    interceptor = KeyInterceptor()
    app.installEventFilter(interceptor)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
