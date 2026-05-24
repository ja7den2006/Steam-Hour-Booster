# Steam Hour Booster

`Steam Hour Booster` is a desktop-first Steam hour boosting project built for real account workflows, staged patch-by-patch with local development first and GitHub used as backup history.

This repository starts with the production foundation:

- a custom frameless desktop shell hosted in `pywebview`
- a professional project layout with packaging and CI
- config and session storage models
- a SteamCommunityKit-backed authentication boundary for:
  - credential login
  - Steam Guard email codes
  - Steam Guard mobile codes
  - refresh-token reuse
  - QR login session approval

## Current Scope

The current repository state now covers the shell, auth boundary, and the first live Steam client runtime slice:

- branded desktop shell and page structure
- durable config and session storage
- auth gateway abstractions wired to `SteamCommunityKit`
- account onboarding through:
  - username and password login
  - Steam Guard email or app code follow-up
  - refresh-token login
  - QR challenge start and approval polling
- saved session bundle persistence per account
- account list and removal workflow
- runtime-facing account profile editing:
  - persona state
  - custom status
  - slot list up to 32 app IDs
  - operator notes
  - session bundle metadata visibility
- startup runtime readiness classification from saved session bundles
- runtime lane controls through the desktop shell:
  - refresh readiness
  - start a single ready lane
  - stop a running lane
  - start or stop all lanes
- a runtime controller with activity logging, ready/boosting/error counts, and per-account lane status cards
- a live Steam CM transport using `ValvePython/steam` and refresh-token client logon
- test coverage for config, auth mapping, session storage, and desktop bridge behavior

The current live path already sends real Steam client played-state lanes. The remaining work is the hardening layer around reconnect behavior, slot updates while active, and richer operator-facing runtime diagnostics.

## Local Development

```bash
pip install -e .[dev]
python -m steam_hour_booster
```

Run tests:

```bash
pytest -q
```

## Project Notes

- Python `3.8+` is supported.
- `SteamCommunityKit` is used only for authentication/session workflows in this app.
- `ValvePython/steam` is used for the Steam client protocol runtime that sends the played-game slot set.
- The desktop shell is delivered as a custom HTML/CSS/JS surface inside a local `pywebview` host so it remains compatible with the current Python `3.8` environment.
- The shell remains portable because the UI surface is web-based, so moving only the host to a Go/Wails shell later is still possible without throwing away the front-end work.
- Account session bundles are stored under local app data instead of being embedded in the main config payload.

## Repository Intent

This repository is being built as a professional desktop application, not a throwaway script bundle. Each patch is expected to be locally verified and pushed as a clean GitHub backup increment.
