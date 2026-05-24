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

## Boundaries

### Desktop shell

The desktop layer owns:

- custom frameless window chrome
- navigation, panels, page layout, and styling
- local config editing and runtime visibility

The shell is hosted in `pywebview` so the product can use a fully custom front-end surface without depending on the broken local Qt native bindings in the current Python 3.8 environment. It should remain independent from the live Steam runtime so it stays testable and can be iterated without touching protocol logic.

### Authentication gateway

The auth layer wraps `SteamCommunityKit` and normalizes:

- credential login
- Steam Guard email/app code flows
- refresh-token login
- QR login challenge creation and approval polling
- session bundle export for persistence

This prevents the rest of the application from directly depending on `SteamCommunityKit` response shapes.

### Runtime boundary

The boost runtime is represented by internal contracts from the start, even before the live implementation exists. Later patches will plug a Steam client protocol engine into that boundary for:

- account session startup
- concurrent game-slot play state
- pause/resume behavior
- reconnect and retry logic

## Local State

App state is stored under `%LOCALAPPDATA%\\SteamHourBooster`:

- `config.json` for safe non-secret UI/config state
- `sessions/` for exported community bundles or later runtime caches
- `logs/` for operational logs

Secrets should not be written into the main config payload.

## First Patch Deliverables

- repo/package scaffold
- custom branded shell
- config models and store
- auth gateway wrappers
- tests for config and auth mapping
