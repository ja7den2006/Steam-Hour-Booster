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

This first patch intentionally establishes the product backbone before the live hour-boost runtime lands:

- branded desktop shell and page structure
- durable config store
- auth gateway abstractions wired to `SteamCommunityKit`
- test coverage for config and auth-wrapper behavior

The actual Steam CM boosting loop and multi-slot runtime will land in later patches on top of this foundation.

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
- On Python `3.8`, the desktop stack pins to a compatible PySide6 line.
- `SteamCommunityKit` is used only for authentication/session workflows in this app.
- The desktop shell is delivered as a custom HTML/CSS/JS surface inside a local `pywebview` host so it remains compatible with the current Python `3.8` environment.

## Repository Intent

This repository is being built as a professional desktop application, not a throwaway script bundle. Each patch is expected to be locally verified and pushed as a clean GitHub backup increment.
