#!/usr/bin/env python3
"""
Écran de statut OLED (I²C) — Voiture RC / Pi Zero W.

Script autonome lancé au boot (service systemd rc-display.service), INDÉPENDANT
de server.py : il affiche l'IP dès le démarrage et surveille l'état du serveur.

Affiche sur un OLED SSD1306/SH1106 128x64 :
  - adresse IP + WiFi (SSID + signal)
  - statut server.py (via la fraîcheur de /tmp/rc_status.json) + client connecté
  - santé système (pigpiod, caméra, SPI)
  - tension/% batterie moteur (relayés par server.py)

La logique de collecte et de formatage est séparée du rendu OLED pour être
testable sans matériel.
"""

import json
import os
import subprocess
import time

# ─── Configuration écran ────────────────────────────────────────────────────────
OLED_DRIVER = 'ssd1306'     # 'ssd1306' ou 'sh1106' selon ta puce
OLED_WIDTH = 128
OLED_HEIGHT = 64
OLED_I2C_ADDR = 0x3C        # adresse I²C typique (0x3C ; parfois 0x3D)
OLED_I2C_PORT = 1           # bus I²C du Pi (i2c-1)

STATUS_FILE = '/tmp/rc_status.json'   # écrit par server.py
STATUS_MAX_AGE = 3.0        # s : au-delà, server.py est considéré arrêté
REFRESH_INTERVAL = 2.0      # s : cadence de rafraîchissement de l'écran
CAMERA_CHECK_INTERVAL = 30.0  # s : la détection caméra est lente → peu fréquente


# ─── Collecte d'infos système ───────────────────────────────────────────────────

def _run(cmd, timeout=2.0):
    """Exécute une commande, renvoie stdout (str) ou '' en cas d'échec."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def get_ip():
    """Première IP non-locale du Pi, ou None."""
    ips = _run(['hostname', '-I']).split()
    return ips[0] if ips else None


def get_wifi():
    """(SSID, signal_dBm) — chacun None si indisponible."""
    ssid = _run(['iwgetid', '-r']).strip() or None
    signal = None
    try:
        with open('/proc/net/wireless') as f:
            lines = f.readlines()
        if len(lines) >= 3:
            # colonne 4 = niveau (dBm)
            signal = int(float(lines[2].split()[3]))
    except (OSError, IndexError, ValueError):
        pass
    return ssid, signal


def service_active(name):
    """True si le service systemd `name` est actif."""
    return _run(['systemctl', 'is-active', name]).strip() == 'active'


def spi_available():
    return os.path.exists('/dev/spidev0.0')


def camera_present():
    """True si une caméra est détectée (test lent → à mettre en cache)."""
    txt = _run(['rpicam-hello', '--list-cameras'], timeout=5.0)
    # une ligne « 0 : ov5647 [...] » = caméra
    return any(l.strip()[:1].isdigit() and ':' in l for l in txt.splitlines())


def read_status(now=None):
    """Lit STATUS_FILE. Retourne (server_up, data) ; server_up=False si absent/périmé."""
    if now is None:
        now = time.time()
    try:
        if now - os.path.getmtime(STATUS_FILE) > STATUS_MAX_AGE:
            return False, {}
        with open(STATUS_FILE) as f:
            return True, json.load(f)
    except (OSError, ValueError):
        return False, {}


# ─── Formatage (pur — testable sans matériel) ───────────────────────────────────

def yn(v):
    """Symbole compact oui/non pour la police par défaut (ASCII)."""
    return 'OK' if v else 'X'


def build_lines(info):
    """Construit les lignes de texte à afficher (fonction pure)."""
    ip = info.get('ip') or '---'
    ssid = info.get('ssid') or '---'
    sig = info.get('signal')
    wifi = ssid if sig is None else f"{ssid} {sig}dBm"

    lines = [
        "RC-CAR",
        f"IP {ip}",
        f"Wifi {wifi}"[:21],
        f"srv:{yn(info.get('server_up'))}  cli:{yn(info.get('client'))}",
        f"pgp:{yn(info.get('pigpio'))} cam:{yn(info.get('camera'))} spi:{yn(info.get('spi'))}",
    ]
    mv, mp = info.get('motor_v'), info.get('motor_pct')
    lines.append(f"bat {mv:.1f}V {mp}%" if mv is not None and mp is not None else "bat ---")
    return lines


def gather(cam_status):
    """Rassemble toutes les infos en un dict (passé à build_lines)."""
    ssid, signal = get_wifi()
    server_up, data = read_status()
    return {
        'ip': get_ip(),
        'ssid': ssid,
        'signal': signal,
        'server_up': server_up,
        'client': data.get('client'),
        'pigpio': service_active('pigpiod'),
        'spi': spi_available(),
        'camera': cam_status,
        'motor_v': data.get('motor_v'),
        'motor_pct': data.get('motor_pct'),
    }


# ─── Rendu OLED (matériel) ──────────────────────────────────────────────────────

try:
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import ssd1306, sh1106
    HAVE_OLED = True
except ImportError:
    HAVE_OLED = False


def make_device():
    serial = i2c(port=OLED_I2C_PORT, address=OLED_I2C_ADDR)
    driver = {'ssd1306': ssd1306, 'sh1106': sh1106}[OLED_DRIVER]
    return driver(serial, width=OLED_WIDTH, height=OLED_HEIGHT)


def render(device, lines):
    ys = [0, 11, 21, 31, 42, 53]
    with canvas(device) as draw:
        for y, text in zip(ys, lines):
            draw.text((0, y), text, fill="white")


def main():
    if not HAVE_OLED:
        print("luma.oled absent — installer 'luma.oled' (pip install luma.oled)")
        return
    try:
        device = make_device()
    except Exception as e:  # noqa: BLE001
        print(f"[DISPLAY] Init OLED échouée : {e}")
        return

    print("[DISPLAY] Écran de statut démarré")
    cam_status = camera_present()
    last_cam_check = time.monotonic()

    while True:
        now = time.monotonic()
        if now - last_cam_check > CAMERA_CHECK_INTERVAL:
            cam_status = camera_present()
            last_cam_check = now
        try:
            render(device, build_lines(gather(cam_status)))
        except Exception as e:  # noqa: BLE001 — l'écran ne doit jamais crasher le loop
            print(f"[DISPLAY] Erreur de rendu : {e}")
        time.sleep(REFRESH_INTERVAL)


if __name__ == '__main__':
    main()
