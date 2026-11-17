# Mouse Battery Overlay

Shows your wireless mouse battery level as a small overlay in the corner of the screen while you play a game.

- Detects the mouse automatically (no model list). Supported:
  - **Razer** wireless mice, over the USB dongle or cable. Verified on a DeathAdder V4 Pro.
  - **Logitech** mice via HID++ (Lightspeed/Unifying receiver or direct). Beta: written from the public protocol, not yet tested on hardware.
  - **Bluetooth** mice that Windows reports a battery level for. Beta: the Windows lookup is verified, not yet on a Bluetooth mouse.
  - Not supported yet: SteelSeries, Corsair, Glorious, ASUS ROG and other vendors on proprietary 2.4 GHz dongles.
- Shows only when a game is in the foreground (borderless/windowed fullscreen). True exclusive fullscreen hides any overlay; switch the game to borderless.
- Tray icon opens Settings: corner, opacity, show/hide hotkey (default Ctrl+Alt+B).
- Settings live in `%APPDATA%\MouseBatteryOverlay\config.json`. To add a game the auto-detect misses, add its folder to `game_dirs` or its exe name to `game_exes` there.

## Install

Download `MouseBatteryOverlay-Setup-x.y.z.exe` from Releases and run it. No admin rights needed. Windows SmartScreen may warn because the installer is not code-signed: More info, then Run anyway.

## Run from source

```
pip install -r requirements.txt
python app.py
```

## Build

Tag a version and push; GitHub Actions builds the installer and attaches it to a Release:

```
git tag v0.1.0 && git push origin v0.1.0
```
