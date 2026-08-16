# Architecture

## Goals

The application is being built in layers so each patch leaves the repo in a usable state:

1. product shell and project hygiene
2. authentication and session persistence
3. Steam client runtime and boost orchestration
4. account management, slot scheduling, and operational hardening

## Chosen Stack

- `Python` for the application and auth/runtime orchestration
- `pywebview` for a frameless desktop host
- custom `HTML/CSS/JS` for the branded shell
- `SteamCommunityKit` for Steam web authentication/session flows only
- `steam-user` and `steam-session` for modern Steam client authorization
- `ValvePython/steam` for the current live Steam client played-state runtime

## Boundaries

### Desktop shell

The desktop layer owns:

- custom frameless window chrome
- navigation, panels, page layout, and styling
- local config editing and runtime visibility
- desktop actions for opening local runtime assets and exporting diagnostics

The shell is hosted in `pywebview` so the product can use a fully custom front-end surface without depending on the broken local Qt native bindings in the current Python 3.8 environment. It should remain independent from the live Steam runtime so it stays testable and can be iterated without touching protocol logic.
Because the UI itself is web-based, the shell can still be moved to a different desktop host such as `Go + Wails` later without throwing away the front-end layer.

### Authentication gateway

The web auth layer wraps `SteamCommunityKit` and normalizes:

- credential login
- Steam Guard email/app code flows
- refresh-token login
- QR login challenge creation and approval polling
- session bundle export for persistence

This prevents the rest of the application from directly depending on `SteamCommunityKit` response shapes.

Steam client authorization is a separate boundary. The Python app launches a bundled Node bridge that uses `steam-user`
and `steam-session` to obtain a SteamClient refresh token through the modern Steam auth path. That client token is stored
as `client_refresh_token` inside the saved account bundle while the SteamCommunityKit web token remains available for
web-session validation.

### Runtime boundary

The boost runtime now sits behind internal contracts so the desktop shell stays independent from the protocol implementation. The current live engine uses `ValvePython/steam` with the saved SteamClient refresh token, plus cached login-key reuse for reconnects, for:

- account session startup
- concurrent game-slot play state
- pause, resume, and conflict handling behavior
- reconnect, retry, and live lane reconfiguration logic
- persistent runtime event logging

## Local State

App state is stored under `%LOCALAPPDATA%\\SteamHourBooster`:

- `config.json` for safe non-secret UI/config state
- `sessions/` for exported community bundles and runtime cache data
- `logs/` for operational logs

Secrets should not be written into the main config payload.

## Release Packaging

GitHub release tags trigger the Windows packaging workflow:

- PyInstaller builds the portable desktop app folder.
- The workflow bundles `node.exe` and `node_modules` so Steam client authorization works without a user-installed Node.js runtime.
- Inno Setup wraps the portable app into a per-user Windows installer.
- The generated uninstaller removes installed app files but intentionally leaves `%LOCALAPPDATA%\\SteamHourBooster` account/session data in place.

## Current Deliverables

- repo/package scaffold
- custom branded shell
- config and session stores
- auth gateway wrappers
- credential, refresh-token, and QR onboarding bridge methods
- saved community session bundle persistence per account
- runtime-facing account profile editing and slot-list validation
- startup runtime readiness classification and per-account lane state
- modern Steam client authorization through the bundled Node bridge
- desktop runtime controls backed by a live Steam client transport
- reconnect-aware lane telemetry surfaced back into the desktop shell
- cached login-key reuse, conflict policy handling, and live slot/persona updates for active lanes
- persistent runtime event logging surfaced in the shell and written to disk
- in-app file/folder actions and runtime snapshot export for operator workflows
- tag-driven GitHub release workflow that publishes a Windows installer
- tests for config, auth mapping, session storage, and desktop bridge workflows
