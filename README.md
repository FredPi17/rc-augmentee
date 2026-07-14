# RC Car Controller

Voiture RC pilotee en WiFi depuis un PC avec retour video temps reel.

Le recepteur radio d'origine est remplace par un Raspberry Pi Zero W qui recoit les commandes au clavier et renvoie le flux video d'une Pi Camera embarquee.

## Architecture

```
PC (controller.py)                         Pi Zero W (server.py)
+---------------------------+              +---------------------------+
|  Application PyQt6        |   UDP 5000   |  Servo direction (GPIO 12)|
|  - Flux video             | -----------> |  Servo camera  (GPIO 13) |
|  - Jauges / boussole      |              |  ESC moteur    (GPIO 18) |
|  - Indicateurs status     |   TCP 5001   |  LED avant     (GPIO 24) |
|  - Logs                   | <----------- |  LED arriere   (GPIO 25) |
|  - Jauges batteries       |   H264 video |  LED recul     (GPIO 23) |
|                           |   UDP 5002   |  MCP3008 ADC   (SPI)     |
+---------------------------+              |  Pi Camera V1  (CSI)     |
                                           +---------------------------+
```

## Demarrage rapide

### Pi Zero W

```bash
# Prerequis (une seule fois)
sudo apt install pigpio python3-pigpio
sudo systemctl enable pigpiod
sudo systemctl start pigpiod
pip install -r requirements-pi.txt

# Lancer le serveur
python3 server.py
```

### PC

```bash
# Prerequis
pip install -r requirements-pc.txt
# ffmpeg doit etre installe :
#   macOS  : brew install ffmpeg
#   Linux  : sudo apt install ffmpeg

# Lancer le controller
python3 controller.py
```

Dans l'application, entrer l'adresse du Pi (`raspberrypi.local` ou son IP) et cliquer **Connecter**.

### Mode simulateur (sans materiel)

```bash
# Terminal 1
python3 simulator.py

# Terminal 2
python3 controller.py
# Cocher "Mode simulateur" puis Connecter
```

## Controles

| Touche | Action |
|--------|--------|
| `↑` `↓` | Accelerer / freiner / reculer |
| `←` `→` | Tourner |
| `Z` | Camera avant |
| `Q` | Camera gauche |
| `S` | Camera arriere |
| `D` | Camera droite |
| `L` | Phares on/off |
| `Espace` | Arret d'urgence |

## Cablage

```
Pi Zero W                    Composants
-----------------------------------------------------
GPIO 12 (PWM0) ────────────> Servo direction (signal)
GPIO 13 (PWM1) ────────────> Servo camera (signal)
GPIO 18        ────────────> ESC (signal)
GPIO 24 ── 220 Ohm ── LED ─> GND   (blanc avant)
GPIO 25 ── 220 Ohm ── LED ─> GND   (rouge arriere, PWM)
GPIO 23 ── 220 Ohm ── LED ─> GND   (blanc recul)
Port CSI ──────────────────> Pi Camera V1 (nappe Zero)

SPI (MCP3008 - monitoring batterie moteur) :
GPIO 11 (SCLK) ────────────> MCP3008 CLK
GPIO 10 (MOSI) ────────────> MCP3008 DIN
GPIO 9  (MISO) ────────────> MCP3008 DOUT
GPIO 8  (CE0)  ────────────> MCP3008 CS
Batterie moteur + ──[R1 10k]──┬──[R2 4.7k]── GND
                              └──────────────> MCP3008 CH0 (pont diviseur)
```

Les servos et l'ESC sont alimentes par le BEC de l'ESC, pas par le Pi.
Le feu de recul est passe de GPIO8 a GPIO23 (GPIO8 = CE0 SPI du MCP3008).
Activer le SPI sur le Pi : `sudo raspi-config` ou `dtparam=spi=on`.

## Monitoring batteries

Deux batteries suivies :

| Batterie | Mesure | Affichage |
|----------|--------|-----------|
| Moteur (NiMH) | MCP3008 CH0 + pont diviseur | Jauge % + tension (courbe NiMH, approximatif sous charge) |
| Pi (power bank 5V) | `vcgencmd get_throttled` | Alim OK / sous-tension (pas de %, sortie 5V regulee) |

Le serveur envoie la telemetrie au controller en UDP 5002 (~1 Hz).

## Eclairage

| Feu | Comportement |
|-----|-------------|
| Position avant (blanc) | Manuel : touche `L` |
| Position arriere (rouge) | Manuel : touche `L` (20% PWM) |
| Freinage (rouge) | Automatique : 100% au freinage |
| Recul (blanc) | Automatique : allume en marche arriere |

## Calibration

Dans `server.py` :

```python
DIRECTION_INVERT = True    # Inverse le sens du servo direction
DIRECTION_TRIM = -0.18     # Offset de centrage des roues
```

## Structure

```
server.py             Serveur Pi Zero W
controller.py         Application desktop PyQt6
simulator.py          Simulateur 3D (test sans materiel)
requirements-pi.txt   Dependances Pi
requirements-pc.txt   Dependances PC
projet-rc-car.md      Cahier des charges
```

## Dependances

**Pi Zero W** : `gpiozero`, `pigpio`, `spidev` + service `pigpiod` + SPI activé

**PC** : `PyQt6`, `opencv-python`, `numpy`, `ffmpeg`
