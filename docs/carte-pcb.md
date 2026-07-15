# Carte d'interface RC — Raspberry Pi Zero W

Carte 2 couches (~80×60 mm) qui regroupe toute la connectique de la voiture.
Le Pi s'enfiche via un header 2×20 ; il ne reste qu'à souder les connecteurs
(tous **traversants**) et à brancher les composants.

> Ce document est le design de référence. Le routage et les fichiers Gerber se
> produisent dans **EasyEDA** (recette en bas), pas générés automatiquement ici.

## 1. Format & mécanique

- PCB 2 couches, **~80×60 mm**.
- Le Pi Zero W (avec son header **mâle 2×20** soudé) s'enfiche sur un **header femelle 2×20** (J1) en bord de carte.
- 4 trous de fixation **M2.5** aux coins (les 2 trous du Pi Zero W sont à 58×23 mm ; prévoir aussi 2 trous côté carte).
- Composants uniquement **through-hole** → soudure de broches seulement.

## 2. Netlist complète (source de vérité)

Numérotation broches Pi = numéro **physique** du header 40 broches.

| Signal carte | Broche(s) Pi | Détail |
|--------------|--------------|--------|
| ESC — signal | GPIO18 (pin 12) | header 3p |
| Servo direction — signal | GPIO12 (pin 32) | header 3p |
| Servo caméra — signal | GPIO13 (pin 33) | header 3p |
| Rail **VSERVO** (+V des 3 headers) | — | fourni par le **BEC de l'ESC**, PAS le 5 V du Pi |
| LED avant | GPIO24 (pin 18) | → R 220 Ω → LED → GND |
| LED arrière (PWM) | GPIO25 (pin 22) | → R 220 Ω → LED → GND |
| LED recul | GPIO23 (pin 16) | → R 220 Ω → LED → GND |
| Batterie moteur + | — | → R1 10k — nœud — R2 4.7k → GND |
| Pont diviseur (nœud) | — | → MCP3008 CH0 (pin 1) |
| MCP3008 CLK | GPIO11 / SCLK (pin 23) | |
| MCP3008 DOUT | GPIO9 / MISO (pin 21) | |
| MCP3008 DIN | GPIO10 / MOSI (pin 19) | |
| MCP3008 CS | GPIO8 / CE0 (pin 24) | |
| MCP3008 VDD + VREF | 3V3 (pin 1 / 17) | + condo 100 nF VDD↔GND |
| MCP3008 AGND + DGND | GND | |
| OLED VCC | 3V3 | header 4p |
| OLED GND | GND | |
| OLED SDA | GPIO2 (pin 3) | |
| OLED SCL | GPIO3 (pin 5) | |
| **GND commun** | pins 6/9/14/20/25/… | ESC/BEC, Pi, batterie, LED, MCP3008 |

### Règles d'alimentation (critiques)

- **VSERVO (5–6 V)** = sortie **BEC de l'ESC** (broche +V de son header 3p). Elle alimente les 3 servos/ESC. **Ne jamais relier VSERVO au 5 V du Pi** (le Pi est sur son power bank).
- **GND commun** obligatoire entre tous les blocs — c'est le seul lien de masse partagé.
- Le SPI matériel doit être activé côté Pi (`dtparam=spi=on`) pour le MCP3008.

## 3. Nomenclature (BOM)

| Réf | Composant | Empreinte | Qté |
|-----|-----------|-----------|-----|
| J1 | Header **femelle** 2×20, 2,54 mm | THT | 1 |
| J_ESC / J_DIR / J_CAM | Header **mâle** 3 broches, 2,54 mm | THT | 3 |
| J_LEDav / J_LEDar / J_LEDrec / J_BATT | Bornier à vis 2 broches, pas 5,08 mm | THT | 4 |
| J_OLED | Header femelle 4 broches, 2,54 mm | THT | 1 |
| U1 | MCP3008-I/P + support **DIP-16** | DIP-16 | 1 |
| R1 | 10 kΩ 1/4 W | axial | 1 |
| R2 | 4,7 kΩ 1/4 W | axial | 1 |
| R3–R5 | 220 Ω 1/4 W | axial | 3 |
| C1 | 100 nF céramique | THT 5,08 mm | 1 |
| — | PCB 2 couches ~80×60 mm | — | 1 |

## 4. Recette EasyEDA → JLCPCB (routage + Gerbers)

> Compte EasyEDA gratuit requis (à créer toi-même). Étapes pensées débutant.

1. **Nouveau projet** : EasyEDA (Std Edition) → *File ▸ New ▸ Project* puis *New ▸ Schematic*.
2. **Placer les composants** depuis la librairie (icône *Library*), en cherchant :
   - « header female 2.54 1x40 » (couper à 2×20) ou « 2x20 female » pour J1
   - « pin header 1x3 » ×3, « screw terminal 5.08 1x2 » (KF301) ×4, « 1x4 female » pour J_OLED
   - « MCP3008 » (ou DIP-16 générique + assigner MCP3008)
   - résistances axiales (R0.25W / AXIAL-0.4) et « CAP 100nF » THT
3. **Câbler** (outil *Wire*, touche `W`) en suivant la netlist §2. Astuce : pour GND/3V3, poser des **Net Labels** (touche `P` → *Netlabel*) plutôt que tout relier au fil — nomme `GND`, `3V3`, `VSERVO`, `ADC0`.
   - Les 3 broches +V des headers servo → même label `VSERVO`.
   - Toutes les masses → `GND`.
4. **Vérifier** : *Design ▸ Check DRC* (schéma). Corriger les fils non connectés.
5. **Passer au PCB** : *Design ▸ Convert to PCB*.
6. **Contour de carte** : sur la couche *BoardOutline*, dessiner un rectangle ~80×60 mm. Placer J1 en bord ; répartir les autres connecteurs (voir schéma visuel). Ajouter 4 trous M2.5 (*Place ▸ Hole*).
7. **Router** : *Route ▸ Auto Router* (une carte aussi simple passe bien en auto), ou à la main (pistes signal 0,3 mm, GND/VSERVO 0,6 mm). Idéalement un **plan de masse** GND sur la couche du bas (*Place ▸ Copper Area* = GND).
8. **DRC PCB** : *Design ▸ Check DRC* → 0 erreur.
9. **Export Gerbers** : *Fabrication ▸ PCB Fabrication File (Gerber)* → *Generate*. Tu peux commander JLCPCB directement (*Order at JLCPCB*) ou télécharger le .zip Gerber pour un autre fab.
10. **Commande** : 2 couches, FR-4, 1,6 mm, finition HASL — largement suffisant.

## 5. Assemblage

- Ordre de soudure : composants **bas d'abord** (résistances, condo, support DIP), puis headers/borniers.
- **Ne pas** souder le MCP3008 directement : l'enficher dans son support DIP-16.
- Vérifier au multimètre : continuité **GND** partout, et **pas** de court entre 3V3/VSERVO/GND avant de brancher le Pi.
- Rappel : VSERVO alimenté par le BEC de l'ESC, jamais par le Pi.

## 6. Vérif rapide avant mise sous tension

- [ ] Pas de court 3V3 ↔ GND, VSERVO ↔ GND (multimètre)
- [ ] MCP3008 bien orienté dans le support (encoche)
- [ ] SPI activé sur le Pi (`/dev/spidev0.0` présent)
- [ ] Sens des LED (anode côté résistance/GPIO)
