# Projet : Voiture RC contrôlée par ordinateur avec retour vidéo

## Objectif

Transformer une voiture radiocommandée pour la contrôler depuis un ordinateur via WiFi, avec retour vidéo en temps réel depuis une caméra montée sur le toit.

---

## Architecture globale

```
┌─────────────────────────────────┐         WiFi           ┌─────────────────────────────────┐
│         ORDINATEUR              │◄──────────────────────►│          Pi Zero W              │
│                                 │                        │                                 │
│  Clavier:                       │   Commandes UDP:       │  GPIO 12 ──► Servo direction    │
│  - Flèches ↑↓ : vitesse         │   - direction          │  GPIO 13 ──► Servo caméra       │
│  - Flèches ←→ : direction       │   - vitesse            │  GPIO 18 ──► ESC moteur         │
│  - ZQSD : orientation caméra    │   - camera             │                                 │
│                                 │ ─────────────────────► │  Serveur Python réception       │
│  Interface Python + OpenCV      │                        │                                 │
│                                 │ ◄───────────────────── │  Pi Camera V2 (CSI)             │
│  Affichage flux vidéo           │   Stream H264 TCP      │  sur servo panoramique          │
└─────────────────────────────────┘                        └─────────────────────────────────┘
```

---

## Matériel

### Sur la voiture (Pi Zero W)

| Composant | Rôle | Connexion |
|-----------|------|-----------|
| Pi Zero W | Cerveau embarqué | — |
| Pi Camera V2 | Caméra avec encodage GPU | Port CSI |
| Servo direction | Tourner les roues | GPIO 12 (PWM0) |
| Servo caméra | Panoramique 180° gauche/droite | GPIO 13 (PWM1) |
| ESC 320A brushed | Contrôle moteur, compatible 2S-3S LiPo | GPIO 18 |
| Batterie LiPo | Alimentation | — |

### Côté ordinateur

- Clavier standard
- Python + OpenCV pour l'interface

---

## Câblage détaillé Pi Zero W

```
Pi Zero W                         Composants
──────────────────────────────────────────────────────────
GPIO 12 (PWM0) ──────────────────► Servo direction (signal)
GPIO 13 (PWM1) ──────────────────► Servo caméra (signal)
GPIO 18        ──────────────────► ESC (signal)

Port CSI       ──────────────────► Pi Camera V2 (nappe)

GND            ──────────────────► GND commun (tous composants)
```

**Important** : Les servos et l'ESC sont alimentés par l'alimentation externe (BEC de l'ESC ou batterie), pas par le Pi. Seul le fil signal va au GPIO.

---

## Contrôles clavier

| Touche | Action | Valeur envoyée |
|--------|--------|----------------|
| Flèche ↑ | Accélérer | vitesse +0.1 |
| Flèche ↓ | Freiner / Reculer | vitesse -0.1 |
| Flèche ← | Tourner à gauche | direction -0.1 |
| Flèche → | Tourner à droite | direction +0.1 |
| Z | Caméra vers l'avant | camera = 0 |
| S | Caméra vers l'arrière | camera = 1 ou -1 |
| Q | Caméra vers la gauche | camera = -0.5 |
| D | Caméra vers la droite | camera = 0.5 |
| Espace | Arrêt d'urgence | vitesse = 0 |

---

## Protocole de communication

### Commandes (Ordinateur → Pi) : UDP port 5000

Format JSON :
```json
{"type": "direction", "value": 0.5}
{"type": "vitesse", "value": 0.8}
{"type": "camera", "value": -0.5}
```

Valeurs de -1 à 1 :
- **direction** : -1 = gauche max, 0 = centre, 1 = droite max
- **vitesse** : -1 = recul max, 0 = arrêt, 1 = avant max
- **camera** : -1 = arrière gauche, 0 = avant, 1 = arrière droite

### Vidéo (Pi → Ordinateur) : TCP port 5001

Stream H264 via libcamera-vid avec encodage matériel GPU.

---

## Logiciels à développer

### 1. Serveur Pi Zero W (`server.py`)

**Responsabilités :**
- Démarrer le stream vidéo Pi Camera via libcamera-vid
- Écouter les commandes UDP sur port 5000
- Piloter les 3 sorties PWM (direction, caméra, ESC)

**Dépendances :**
- gpiozero
- pigpio (pour PWM matériel stable)

**Configuration préalable :**
```bash
sudo apt install pigpio python3-pigpio
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
```

### 2. Client ordinateur (`controller.py`)

**Responsabilités :**
- Capturer les entrées clavier
- Envoyer les commandes UDP au Pi
- Recevoir et afficher le flux vidéo H264

**Dépendances :**
- opencv-python
- pygame (pour les entrées clavier) ou OpenCV seul

---

## Configuration PWM servos

Les servos standard utilisent :
- Fréquence : 50 Hz
- Pulse min : 0.5 ms (position -1)
- Pulse max : 2.5 ms (position +1)
- Pulse centre : 1.5 ms (position 0)

Avec gpiozero :
```python
from gpiozero import Servo
from gpiozero.pins.pigpio import PiGPIOFactory

factory = PiGPIOFactory()
servo = Servo(12, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000, pin_factory=factory)
servo.value = 0  # Centre
```

---

## Montage caméra

La Pi Camera V2 est fixée sur un servo monté sur le toit de la voiture. Le servo permet une rotation de 180° (pseudo-360° : 90° à gauche, 90° à droite depuis la position avant).

**Positions :**
- **Avant** : servo.value = 0
- **Arrière** : servo.value = 1 (ou -1 selon montage)
- **Gauche** : servo.value = -0.5
- **Droite** : servo.value = 0.5

---

## Commandes de démarrage

### Sur le Pi Zero W

```bash
# Lancer le serveur
python3 server.py
```

Le serveur démarre automatiquement le stream vidéo :
```bash
libcamera-vid -t 0 --inline --width 640 --height 480 --framerate 30 --codec h264 --listen -o tcp://0.0.0.0:5001
```

### Sur l'ordinateur

```bash
python3 controller.py
```

---

## Résumé des fichiers à créer

| Fichier | Emplacement | Rôle |
|---------|-------------|------|
| `server.py` | Pi Zero W | Réception commandes + pilotage PWM + stream vidéo |
| `controller.py` | Ordinateur | Interface clavier + envoi commandes + affichage vidéo |

---

## Points d'attention pour le développement

1. **PWM matériel** : utiliser pigpio via PiGPIOFactory, pas le PWM logiciel par défaut
2. **Latence vidéo** : libcamera-vid avec encodage H264 matériel est crucial sur Pi Zero W
3. **UDP pour les commandes** : plus rapide que TCP, acceptable de perdre quelques paquets
4. **Alimentation servos** : ne jamais alimenter depuis le Pi, utiliser le BEC de l'ESC
5. **Zone morte** : implémenter une zone morte (~0.1) pour éviter les micro-mouvements parasites

---

## Exemples de code de référence

### Serveur Pi Zero W (structure de base)

```python
import socket
import json
import subprocess
from gpiozero import Servo
from gpiozero.pins.pigpio import PiGPIOFactory

# Configuration PWM matériel
factory = PiGPIOFactory()

servo_direction = Servo(12, min_pulse_width=0.5/1000, 
                        max_pulse_width=2.5/1000, pin_factory=factory)
servo_camera = Servo(13, min_pulse_width=0.5/1000, 
                     max_pulse_width=2.5/1000, pin_factory=factory)
# ESC sur GPIO 18 - configuration similaire

def start_video_stream():
    subprocess.Popen([
        'libcamera-vid', '-t', '0', '--inline',
        '--width', '640', '--height', '480', '--framerate', '30',
        '--codec', 'h264', '--listen', '-o', 'tcp://0.0.0.0:5001'
    ])

def handle_command(cmd):
    if cmd['type'] == 'direction':
        servo_direction.value = cmd['value']
    elif cmd['type'] == 'camera':
        servo_camera.value = cmd['value']
    elif cmd['type'] == 'vitesse':
        # Gérer ESC
        pass

def main():
    start_video_stream()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 5000))
    
    while True:
        data, addr = sock.recvfrom(1024)
        cmd = json.loads(data.decode())
        handle_command(cmd)

if __name__ == '__main__':
    main()
```

### Client ordinateur (structure de base)

```python
import cv2
import socket
import json

PI_ADDRESS = ('192.168.1.XX', 5000)  # À adapter
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# État
vitesse = 0
direction = 0

# Positions caméra prédéfinies
CAMERA_POSITIONS = {
    'front': 0,
    'rear': 1,
    'left': -0.5,
    'right': 0.5
}

def send_command(cmd_type, value):
    cmd = json.dumps({'type': cmd_type, 'value': value})
    sock.sendto(cmd.encode(), PI_ADDRESS)

# Flux vidéo
cap = cv2.VideoCapture('tcp://192.168.1.XX:5001')

while True:
    ret, frame = cap.read()
    if ret:
        cv2.imshow('RC Car', frame)
    
    key = cv2.waitKey(1) & 0xFF
    
    if key == ord('q'):
        break
    # Flèches pour direction/vitesse
    elif key == 82:  # Haut
        vitesse = min(1, vitesse + 0.1)
        send_command('vitesse', vitesse)
    elif key == 84:  # Bas
        vitesse = max(-1, vitesse - 0.1)
        send_command('vitesse', vitesse)
    elif key == 81:  # Gauche
        direction = max(-1, direction - 0.1)
        send_command('direction', direction)
    elif key == 83:  # Droite
        direction = min(1, direction + 0.1)
        send_command('direction', direction)
    # ZQSD pour caméra
    elif key == ord('z'):
        send_command('camera', CAMERA_POSITIONS['front'])
    elif key == ord('s'):
        send_command('camera', CAMERA_POSITIONS['rear'])
    elif key == ord('a'):
        send_command('camera', CAMERA_POSITIONS['left'])
    elif key == ord('d'):
        send_command('camera', CAMERA_POSITIONS['right'])
    elif key == ord(' '):
        vitesse = 0
        send_command('vitesse', 0)

cap.release()
cv2.destroyAllWindows()
```

---

## Historique des décisions

1. **Réutilisation de l'ESC existant** : le Pi remplace le récepteur radio, pas le contrôleur moteur
2. **Pi Camera V2 plutôt qu'USB** : encodage matériel GPU indispensable sur Pi Zero W mono-cœur
3. **Un seul servo caméra 180°** plutôt que deux caméras : simplifie le matériel et évite la surcharge CPU
4. **Contrôle clavier ZQSD** plutôt que Nunchuk Wii : évite la complexité d'un adaptateur USB I2C
5. **UDP pour les commandes** : priorité à la réactivité sur la fiabilité
