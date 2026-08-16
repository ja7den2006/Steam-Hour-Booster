<img width="1451" height="1079" alt="image" src="https://github.com/user-attachments/assets/2fe60ff0-4839-4826-982a-128a1c5f83ff" />


# Steam Hour Booster

Steam Hour Booster is a desktop app for running Steam played-state hour boosting across one or more Steam accounts. It uses a custom `pywebview` shell, SteamCommunityKit for web authentication, and a Node-backed Steam client bridge for modern Steam client authorization.

The app is built as a local-first desktop tool. Account sessions, runtime cache, and logs stay on the local machine under the user's app data directory.

> This project is not affiliated with Valve or Steam. Use it only with accounts you own. Steam account automation may carry account risk, so review Steam's current rules before using it.

## Features

- Custom frameless desktop UI with overview, account management, activity logs, and per-account editing.
- Username/password login with Steam Guard email or app-code follow-up.
- QR-code login through SteamCommunityKit.
- Separate Steam client authorization through `steam-user` / `steam-session`.
- Multiple saved Steam accounts with separate settings per account.
- Per-account boost toggle and appear-online/invisible behavior.
- Up to 32 configured app slots per account.
- Popular game presets plus custom App ID entries.
- Owned-game validation when a Steam Web API key is available.
- Start/stop controls for one account or all ready accounts.
- Conflict handling for other sessions playing games: pause, force kick, or yield.
- Auto-reply settings with custom message, cooldown, and timeout.
- Runtime event log and in-app diagnostics.

## Requirements

- Windows 10/11.
- Microsoft Edge WebView2 Runtime for `pywebview`.
- A Steam account you own.

For source development, install Python 3.8 or newer and Node.js with npm.

## Quick Start

Download the latest Windows installer from the GitHub Releases page and run `SteamHourBooster-Setup-<version>.exe`.

The installer adds Start Menu shortcuts, an optional desktop shortcut, and a standard Windows uninstall entry. It bundles the desktop app and Node runtime used for Steam client authorization.

## Source Development

Clone the repository, then run the desktop app from the project root.

```powershell
powershell -ExecutionPolicy Bypass -File .\run_dev.ps1
```

The launcher installs Python dependencies and Node bridge dependencies before starting the app.

If dependencies are already installed:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_dev.ps1 -SkipInstall
```

Batch launcher:

```bat
run_dev.cmd
run_dev.cmd -SkipInstall
```

Manual setup:

```powershell
python -m pip install -e ".[dev]"
npm install
python -m steam_hour_booster
```

## Basic Use

1. Open the app.
2. Add a Steam account with username/password or QR login.
3. If the account shows `Needs Auth`, open the account editor and run `Finish booster sign-in`.
4. Add game App IDs through presets or custom entries.
5. Save the account profile.
6. Start the account from Overview.

The console panel shows authentication and runtime activity such as account start, stop, reconnect, and conflict state.

## Local Data

Runtime data is stored outside the repository:

```text
%LOCALAPPDATA%\SteamHourBooster\
```

Important paths:

- `config.json`: app preferences and non-secret account profile settings.
- `sessions\`: SteamCommunityKit session bundles, Steam client auth metadata, and local runtime cache.
- `logs\runtime.log`: runtime event log.

Do not commit local runtime data, `.env` files, credentials, screenshots containing account secrets, or the contents of `%LOCALAPPDATA%\SteamHourBooster`.

Optional local icon assets can be placed at `tmp\steam_icon.ico` and `tmp\steam_icon.png` for development builds. They are intentionally ignored so branded third-party assets are not redistributed from this repository.

## Verification

Run tests:

```powershell
python -m pytest
```

Run a Node bridge smoke check:

```powershell
npm run smoke:steam-bridge
```

Compile Python sources:

```powershell
python -m compileall src
```

## Release Process

Installer releases are produced by GitHub Actions. Push a version tag to run the Windows packaging workflow:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The workflow runs the test suite, verifies the Node Steam bridge, builds a PyInstaller desktop app folder, wraps it with Inno Setup, uploads the installer artifact, and publishes a prerelease on GitHub.

## Architecture

The app is split into clear boundaries:

- `steam_hour_booster.web`: HTML/CSS/JS desktop shell and pywebview bridge.
- `steam_hour_booster.auth.community`: SteamCommunityKit web auth and session bundle handling.
- `steam_hour_booster.auth.client`: Node Steam client bridge wrapper for modern client refresh-token authorization.
- `steam_hour_booster.runtime`: live played-state runtime, reconnect handling, conflict policy, and telemetry.
- `steam_hour_booster.session_store`: local bundle/cache storage under app data.

The Steam client auth bridge depends on:

- `steam-session`: modern Steam auth/session flow.
- `steam-user`: Steam client login, refresh-token handling, `gamesPlayed`, conflict state, and chat events.

## Current Status

This is an alpha desktop application with a working local runtime path and a GitHub Actions workflow that produces a Windows installer on release tags.

Recommended next release hardening:

- Signed Windows build artifact.
- Clearer release notes and screenshots.

## Known Dependency Note

`steam-user` currently pulls a transitive `steam-appticket` dependency that npm audit flags through an older nested `protobufjs`. The app needs the current `steam-user` 5.x refresh-token path; npm's suggested forced audit fix downgrades `steam-user` and is not compatible with the runtime.

## License

MIT. See [LICENSE](LICENSE).
