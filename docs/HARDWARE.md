# Hardware & installation

## Parts
- Raspberry Pi 4 (tested) or Pi 3B+/Zero 2 (should work; lower `pwm_bits` if it struggles). Pi 5 needs the
  RP1 build of rpi-rgb-led-matrix (untested here).
- HUB75 RGB LED panel(s): 128x64 (one 128x64, or two 64x64 chained), 64x32, 64x64, 128x32 presets exist.
- Adafruit RGB Matrix HAT/Bonnet (with the PWM jumper solder mod recommended) or direct wiring.
- 5 V supply sized for the panel (a 128x64 can draw 4 A+ at full white).

## Install
```bash
curl -fsSL https://raw.githubusercontent.com/kas21/nhl-scoreboard/main/scripts/install.sh | bash
sudo /opt/scoreboard/scripts/pi_tuning.sh && sudo reboot
```
(From a checkout: `sudo ./scripts/install.sh` uses that checkout in place.)
`install.sh`: apt deps → venv → `pip install -e .` → `rgbmatrix` (prebuilt wheel for this Python, else source
build) → `scoreboard.service` (root, `--output hardware`, restart on failure) → starts it.
`pi_tuning.sh`: blacklists `snd_bcm2835` (conflicts with the matrix PWM — the driver refuses to start
otherwise) and adds `isolcpus=3` (dedicated core for the refresh thread; removes residual flicker).

## Updates
The dashboard checks GitHub daily (`web.update_check_hours`) and shows **Update available** with a one-click
*Update & restart* (git fast-forward → reinstall if dependencies changed → restart). Requires the install to be a
git checkout, which `install.sh` guarantees. API: `GET/POST /api/system/update`, `POST /api/system/update/check`.

## Display settings (Settings → Display, or the wizard)
| Setting | Notes |
|---|---|
| width/height/chain/parallel | total pixels and how panels are wired |
| gpio_mapping | `adafruit-hat-pwm` (modded HAT), `adafruit-hat`, `regular` |
| rgb_sequence | fix swapped colours; Kevin's panel needs `RGB` |
| pixel_mapper | `Rotate:180`, `Mirror:H` … |
| slowdown_gpio | 1 (Pi 3), 2 (Pi 4, default), 3–4 if flicker/ghosting |
| pwm_bits / pwm_lsb_nanoseconds / pwm_dither_bits | colour depth vs refresh; 7/130/1 is a good default |
| limit_refresh | cap Hz (60) for steadier brightness |
| fps | render loop rate (30) |
Driver options only apply at start — use the wizard's *Apply* (restart) button.

## Service management
```bash
sudo systemctl status|restart|stop scoreboard
journalctl -u scoreboard -f
sudo sed -i 's|--output hardware|--output hardware --demo|' /etc/systemd/system/scoreboard.service && sudo systemctl daemon-reload && sudo systemctl restart scoreboard   # demo mode (revert the same way)
```
Config lives at `/etc/scoreboard/config.json` (root-only; edit through the web UI). Backups `config.json.1..5`.
