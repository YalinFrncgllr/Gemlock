# Gemlock

A small Windows tool for switching between Riot accounts with one click.

- Pick a game (Valorant, League of Legends, or just the Riot Client) and an account; Gemlock types the login into the Riot Client, launches the game, and closes itself.
- Shows Valorant ranks (via the HenrikDev API) and League Solo/Duo ranks (read from the local League client after you log in).
- Optionally opens Blitz together with League.
- Updates itself from this repository.

## Security
- Passwords are encrypted with Windows DPAPI and stored only on your PC (`%APPDATA%\riot_hesaplar.json`). Only your Windows user can decrypt them.
- Passwords are never sent anywhere except typed into the Riot Client login screen.
- Only public Riot IDs are sent to the HenrikDev API.

## Install
1. Install Python 3 from python.org (tick "Add python.exe to PATH").
2. Double-click `Gemlock Setup.pyw` (or `gemlock.pyw`). It installs itself to `%LOCALAPPDATA%\Gemlock` and puts a Gemlock shortcut on the desktop.

## Updates
Gemlock checks `version.json` in this repository on startup. To publish an update, replace `gemlock.pyw`, bump `version` and `sha256` in `version.json`, and push to `main`.

Gemlock is not affiliated with or endorsed by Riot Games.
