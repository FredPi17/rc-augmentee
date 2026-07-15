# TODO — Voiture RC

Suivi des prochaines étapes. Coche au fur et à mesure.

## Matériel & mise en service Pi

- [ ] **Activer le SPI matériel** sur le Pi (`sudo raspi-config` → Interface → SPI, ou `dtparam=spi=on`) + reboot
- [ ] Recopier le `server.py` durci sur le Pi et **vérifier que la direction remarche** (plus de warning SPISoftwareFallback)
- [ ] Câbler le **MCP3008** + pont diviseur (R1 10 kΩ / R2 4,7 kΩ) sur la batterie moteur (voir [README](README.md) § Câblage)
- [ ] Vérifier le feu de recul bien déplacé **GPIO8 → GPIO23**
- [ ] Écran OLED I²C : activer l'I²C, `pip install luma.oled`, installer et activer `rc-display.service`

## Calibration & réglages

- [ ] Régler `NIMH_CELLS` et `NIMH_CELL_CURVE` dans `server.py` selon le pack réel
- [ ] Ajuster `DIVIDER_R1` / `DIVIDER_R2` selon les résistances réellement utilisées
- [ ] Valider la lecture batterie (tension + %) au repos **et sous charge** (sag moteur)
- [ ] Affiner `MANEUVER_SPEED_FWD` / `MANEUVER_SPEED_REV` et `STANDSTILL_TIME` au ressenti
- [ ] Confirmer la calibration direction (`DIRECTION_INVERT`, `DIRECTION_TRIM`)

## Idées / améliorations possibles

- [ ] OLED : basculer sur `SH1106` / 128×32 si l'écran diffère (constantes en tête de `display.py`)
- [ ] Crawl vraiment fluide à très basse vitesse → piste matérielle (ESC de crawler ou brushless sensored)
- [ ] Afficher davantage d'infos à l'écran (ex. état client connecté détaillé, IP du PC)

## Fait ✅

- [x] Mode manœuvre (touche M) : momentané, plafonné, avant/arrière dissociés
- [x] Recul sans à-coup depuis l'arrêt
- [x] Monitoring batteries (MCP3008 + télémétrie UDP + jauge controller)
- [x] Écran de statut OLED (display.py + service systemd)
- [x] Garde SPI matériel (pas de fallback logiciel)
- [x] Script de diagnostic `check_pi.sh`
