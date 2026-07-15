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
| `M` | Mode manoeuvre on/off |
| `Espace` | Arret d'urgence |

### Mode manoeuvre (`M`)

Pour le placement precis (brancher une remorque, petites manoeuvres). Une fois active :

- La vitesse est **momentanee** : le moteur ne tourne qu'a l'appui sur `↑`/`↓` et s'arrete au relachement (pas d'accumulation).
- Elle est **plafonnee au minimum** (avant `0.15`, arriere `0.20` — l'arriere plus fort pour declencher le recul de l'ESC), reglable dans `controller.py`.
- Un indicateur « Manoeuvre » s'allume dans le cockpit.

### Affichage batteries

Le cockpit affiche en continu (telemetrie UDP 5002 depuis le Pi) :

- **Batterie moteur** : jauge % + tension (code couleur, alerte sous 20 %).
- **Alim Pi** : indicateur OK / sous-tension.

Voir la section [Monitoring batteries](#monitoring-batteries) pour le detail materiel.

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

I2C (ecran OLED de statut) :
3V3            ────────────> OLED VCC
GND            ────────────> OLED GND
GPIO 2  (SDA)  ────────────> OLED SDA
GPIO 3  (SCL)  ────────────> OLED SCL
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

## Ecran de statut OLED

Un petit ecran OLED I2C (SSD1306/SH1106 128x64) affiche l'etat du Pi au boot,
via `display.py` (service systemd independant de server.py) :

```
RC-CAR
IP 192.168.1.95
Wifi MonReseau -52dBm
srv:OK  cli:OK
pgp:OK cam:OK spi:OK
bat 7.4V 65%
```

- **IP + WiFi** : pour se connecter depuis le controller.
- **srv / cli** : server.py demarre (via `/tmp/rc_status.json`) + client connecte.
- **pgp / cam / spi** : sante systeme (pigpiod, camera, SPI).
- **bat** : tension / % batterie moteur (relayes par server.py).

Installation sur le Pi :

```bash
sudo raspi-config          # activer l'I2C (ou 'dtparam=i2c_arm=on')
pip install -r requirements-pi.txt

sudo cp rc-display.service /etc/systemd/system/
# adapter User= et le chemin ExecStart= si besoin
sudo systemctl enable --now rc-display
```

`display.py` est autonome : il affiche l'IP des le boot, meme si server.py n'est
pas (encore) lance — dans ce cas `srv:X`. Driver/taille reglables en tete du script
(`OLED_DRIVER`, `OLED_WIDTH/HEIGHT`).

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
display.py            Ecran de statut OLED (Pi, service systemd)
rc-display.service    Unite systemd de l'ecran de statut
check_pi.sh           Diagnostic a lancer sur le Pi
requirements-pi.txt   Dependances Pi
requirements-pc.txt   Dependances PC
projet-rc-car.md      Cahier des charges
```

## Dependances

**Pi Zero W** : `gpiozero`, `pigpio`, `spidev` + service `pigpiod` + SPI activé

**PC** : `PyQt6`, `opencv-python`, `numpy`, `ffmpeg`
