# Gemlock

A small Windows tool for switching between Riot accounts with one click.

- Pick a game (Valorant, League of Legends, or just the Riot Client) and an account; Gemlock types the login into the Riot Client, launches the game, and closes itself.
- Shows Valorant ranks (via the HenrikDev API) and League Solo/Duo ranks (read from the local League client after you log in).
- Optionally opens Blitz together with League.
- Updates itself from this repository.

## Privacy and security
- Like Obsidian, everything stays local: account labels, usernames, passwords, Riot IDs, ranks and settings live only on your PC in `%LOCALAPPDATA%\Gemlock\data`, encrypted with Windows DPAPI. Only your Windows user on your PC can decrypt them.
- This repository contains only the program. Anyone who downloads it starts with an empty list.
- Gemlock never uploads your data. Its only network requests are: Riot IDs (public) to the HenrikDev API for Valorant ranks, and reading `version.json` / `gemlock.pyw` from this repository for updates.
- Credentials are typed only while the Riot Client window has focus; if focus changes, typing stops.

## Install
1. Install Python 3 from python.org (tick "Add python.exe to PATH").
2. Double-click `Gemlock Setup.pyw` (or `gemlock.pyw`). It installs itself to `%LOCALAPPDATA%\Gemlock` and puts a Gemlock shortcut on the desktop.

## Updates
Gemlock checks `version.json` in this repository on startup. To publish an update, replace `gemlock.pyw`, bump `version` and `sha256` in `version.json`, and push to `main`.

Gemlock is not affiliated with or endorsed by Riot Games.
