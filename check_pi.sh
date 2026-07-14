#!/usr/bin/env bash
#
# check_pi.sh — Diagnostic de la RC car sur le Raspberry Pi Zero W
# À exécuter SUR le Pi :  bash check_pi.sh
#
# Vérifie tout ce qu'il faut pour que server.py fonctionne :
# OS/caméra, démon pigpio, dépendances Python, GPIO, ports réseau.

set -uo pipefail

# ─── Couleurs & compteurs ─────────────────────────────────────────────────────
GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; BLUE=$'\e[34m'; BOLD=$'\e[1m'; RST=$'\e[0m'
PASS=0; FAIL=0; WARN=0

ok()   { echo "  ${GREEN}✓${RST} $1"; PASS=$((PASS+1)); }
ko()   { echo "  ${RED}✗${RST} $1"; FAIL=$((FAIL+1)); }
warn() { echo "  ${YELLOW}!${RST} $1"; WARN=$((WARN+1)); }
head() { echo; echo "${BOLD}${BLUE}$1${RST}"; }

# GPIO utilisés par server.py
GPIOS="12 13 18 24 25 8"
UDP_PORT=5000
VIDEO_PORT=5001

echo "${BOLD}=== Diagnostic RC car — $(hostname) — $(date '+%Y-%m-%d %H:%M') ===${RST}"

# ─── 1. Système ───────────────────────────────────────────────────────────────
head "1. Système"
if [ -f /etc/os-release ]; then
  . /etc/os-release
  ok "OS : ${PRETTY_NAME:-inconnu}"
  case "${VERSION_CODENAME:-}" in
    bookworm|trixie) ok "Version récente (${VERSION_CODENAME}) — rpicam disponible" ;;
    *) warn "Version ${VERSION_CODENAME:-?} : vérifier la présence de rpicam-vid (ancien = libcamera-vid)" ;;
  esac
else
  warn "/etc/os-release introuvable"
fi
command -v python3 >/dev/null && ok "python3 : $(python3 --version 2>&1)" || ko "python3 introuvable"

# ─── 2. Démon pigpio (critique) ───────────────────────────────────────────────
head "2. Démon pigpio (PWM matériel — critique)"
if command -v pigpiod >/dev/null; then
  ok "pigpiod installé"
else
  ko "pigpiod absent → sudo apt install pigpio python3-pigpio"
fi
if systemctl is-active --quiet pigpiod 2>/dev/null; then
  ok "service pigpiod actif"
else
  ko "pigpiod NON actif → sudo systemctl enable --now pigpiod"
fi
if systemctl is-enabled --quiet pigpiod 2>/dev/null; then
  ok "pigpiod activé au démarrage"
else
  warn "pigpiod pas activé au boot → sudo systemctl enable pigpiod"
fi
# Test définitif : se connecter comme le fait gpiozero (gère IPv4 et IPv6)
if python3 -c "import pigpio,sys; sys.exit(0 if pigpio.pi().connected else 1)" 2>/dev/null; then
  ok "démon pigpio joignable (pigpio.pi().connected)"
else
  warn "démon pigpio non joignable — vérifier 'systemctl status pigpiod'"
fi

# ─── 3. Dépendances Python ────────────────────────────────────────────────────
head "3. Dépendances Python"
for mod in gpiozero pigpio; do
  if python3 -c "import $mod" 2>/dev/null; then
    ok "module python '$mod' importable"
  else
    ko "module '$mod' manquant → pip install $mod"
  fi
done
# Test concret de la PiGPIOFactory (ce que server.py utilise réellement)
if python3 -c "from gpiozero.pins.pigpio import PiGPIOFactory; PiGPIOFactory()" 2>/dev/null; then
  ok "PiGPIOFactory s'initialise (gpiozero ↔ pigpiod OK)"
else
  ko "PiGPIOFactory échoue → pigpiod arrêté ou gpiozero mal configuré"
fi

# ─── 4. Caméra ────────────────────────────────────────────────────────────────
head "4. Caméra"
if command -v rpicam-vid >/dev/null; then
  ok "rpicam-vid présent"
  CAM_BIN=rpicam-hello
elif command -v libcamera-vid >/dev/null; then
  warn "libcamera-vid présent mais server.py appelle rpicam-vid → adapter ou installer rpicam-apps"
  CAM_BIN=libcamera-hello
else
  ko "ni rpicam-vid ni libcamera-vid → sudo apt install rpicam-apps"
  CAM_BIN=""
fi
if [ -n "$CAM_BIN" ] && command -v "$CAM_BIN" >/dev/null; then
  # rpicam-hello écrit la liste sur stderr → capturer 2>&1.
  # Une ligne "  0 : ov5647 [...]" = caméra ; "No cameras available!" = rien.
  CAM_LIST=$("$CAM_BIN" --list-cameras 2>&1)
  if echo "$CAM_LIST" | grep -qE '^[[:space:]]*[0-9]+[[:space:]]*:[[:space:]]'; then
    SENSOR=$(echo "$CAM_LIST" | grep -oiE 'imx[0-9]+|ov[0-9]+' | head -1)
    ok "caméra détectée : ${SENSOR:-?}"
  else
    ko "aucune caméra détectée → vérifier la nappe CSI et l'activation"
  fi
fi

# ─── 5. GPIO ──────────────────────────────────────────────────────────────────
head "5. Accès GPIO"
if [ -d /sys/class/gpio ] || [ -e /dev/gpiochip0 ]; then
  ok "interface GPIO présente (/dev/gpiochip0)"
else
  ko "pas d'interface GPIO — pas sur un Pi ?"
fi
if id -nG "$USER" 2>/dev/null | grep -qw gpio; then
  ok "utilisateur '$USER' dans le groupe gpio"
else
  warn "utilisateur '$USER' pas dans le groupe gpio → sudo usermod -aG gpio $USER (puis relogin)"
fi
echo "  ${BLUE}i${RST} GPIO attendus par server.py : $GPIOS (dir=12 cam=13 esc=18 leds=24/25/8)"

# ─── 6. Réseau & ports ────────────────────────────────────────────────────────
head "6. Réseau & ports"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "${IP:-}" ] && ok "IP locale : $IP" || warn "pas d'IP trouvée (WiFi connecté ?)"
# Vérifie que les ports du serveur sont libres (ou déjà pris par server.py)
port_status() {
  local proto=$1 port=$2 label=$3
  if command -v ss >/dev/null; then
    local flag; [ "$proto" = udp ] && flag=-lun || flag=-ltn
    if ss $flag 2>/dev/null | grep -q ":$port\b"; then
      warn "$label ($proto $port) déjà utilisé — server.py déjà lancé ?"
    else
      ok "$label ($proto $port) libre"
    fi
  else
    warn "commande 'ss' absente — impossible de vérifier le port $port"
  fi
}
port_status udp "$UDP_PORT"  "Commandes"
port_status tcp "$VIDEO_PORT" "Vidéo"

# ─── Résumé ───────────────────────────────────────────────────────────────────
head "Résumé"
echo "  ${GREEN}$PASS OK${RST}   ${YELLOW}$WARN avertissement(s)${RST}   ${RED}$FAIL échec(s)${RST}"
if [ "$FAIL" -eq 0 ]; then
  echo "  ${GREEN}${BOLD}→ Prêt : lance  python3 server.py${RST}"
  exit 0
else
  echo "  ${RED}${BOLD}→ Corrige les ✗ ci-dessus avant de lancer server.py${RST}"
  exit 1
fi
