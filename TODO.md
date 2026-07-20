# TODO — Voiture RC

Suivi des prochaines étapes. Coche au fur et à mesure.

## Tests & remise en route

- [ ] **Retester toutes les fonctions** après remontage
  - [ ] Rebrancher les lumières (feux avant / arrière / recul)
  - [ ] Vérifier direction, caméra et marche avant/arrière

## Matériel & mise en service Pi

- [ ] Vérifier le câblage complet (voir [docs/cablage-fritzing.html](docs/cablage-fritzing.html) ou [README](README.md) § Câblage)
- [ ] Vérifier le feu de recul bien sur **GPIO23**
- [ ] Écran OLED I²C : activer l'I²C, `pip install luma.oled`, installer et activer `rc-display.service`

## Calibration & réglages

- [ ] Affiner `MANEUVER_SPEED_FWD` / `MANEUVER_SPEED_REV` et `STANDSTILL_TIME` au ressenti
- [ ] Confirmer la calibration direction (`DIRECTION_INVERT`, `DIRECTION_TRIM`)

## Prototypage & intégration

- [ ] Réaliser une **carte prototype** (fils soudés) regroupant toute l'électronique
- [ ] Concevoir un **vrai PCB optimisé** (routage propre) à envoyer en fabrication
- [ ] **Prototype d'impression 3D** pour loger l'ensemble dans la voiture

## Idées / améliorations possibles

- [ ] OLED : basculer sur `SH1106` / 128×32 si l'écran diffère (constantes en tête de `display.py`)
- [ ] Crawl vraiment fluide à très basse vitesse → piste matérielle (ESC de crawler ou brushless sensored)
- [ ] Afficher davantage d'infos à l'écran (ex. état client connecté détaillé, IP du PC)

## Fait ✅

- [x] Mode manœuvre (touche M) : momentané, plafonné, avant/arrière dissociés
- [x] Recul sans à-coup depuis l'arrêt
- [x] Écran de statut OLED (display.py + service systemd) + indicateur alim Pi
- [x] Script de diagnostic `check_pi.sh`
- [x] Schémas de câblage (Fritzing, physique, pinmap) dans `docs/`

## Abandonné ❌

- Monitoring de la batterie moteur (MCP3008 + pont diviseur) — retiré du code et des schémas.
  `docs/carte-pcb.md` en garde encore la trace (conception PCB non mise à jour).
