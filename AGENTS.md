# EAI Workspace Instructions

This repository is the only active EAI workspace. Do not edit or depend on any former EAI Desktop checkout.

## Read First

1. Read `docs/HANDOFF.md`, `docs/ARCHITECTURE.md`, and `docs/PRODUCT.md`.
2. Inspect the working tree before editing. Preserve user changes and generated data.
3. Run commands from the repository root unless a script says otherwise.

## Boundaries

- `apps/web` owns product UI. Root `App.jsx` only mounts the workspace; feature behavior belongs under `features`, `layout`, or `workspace`.
- `services/api/app/main.py` is ASGI assembly only. Domain code must not move back into it.
- Agent v2 may call only registered capabilities. It never receives SQL, secrets, raw provider bodies, arbitrary host paths, or direct durable-write access.
- Durable Agent and Campaign changes use `OperationBatch`, revision checks, transaction snapshots, approval, receipts, and conflict-safe undo.
- `services/api/app/legacy` is read-only compatibility. Agent v2, Research Store, and Campaign modules must not depend on it.
- Legacy AgentRun, ChangeSet, Thread, and LabRun records remain readable. Legacy chat/Agent v1 mutations return `410 Gone`; safe ChangeSet undo remains supported.
- Official Atlas resources, upstream source, old SQLite, user source snapshots, and unconfirmed personal research state are never modified in place.
- Docker absence produces previews only. Never fall back to host command execution.

## Local Data

- Do not read or write real `%APPDATA%\com.eai.desktop` during automated tests.
- Web/service tests use ignored temporary data below `runtime/` or OS temp directories.
- Installed-app tests must set both `EAI_DESKTOP_TEST_MODE=1` and an absolute `EAI_DESKTOP_APP_DATA_DIR`.
- Never add personal snapshots, databases, keys, logs, screenshots, binaries, installers, dependency folders, or build output to Git.

## Standard Gates

```powershell
npm run check:mojibake
npm run check:architecture
npm run web:test
npm run web:build
npm run web:e2e
npm run service:test
npm run campaign:docker-test
npm run sidecar:test
npm run rust:fmt
npm run rust:clippy
npm run rust:test
npm run desktop:build
npm run desktop:smoke
```

Web and service gates must pass before `desktop:build`. A Docker-unavailable Campaign smoke may report a documented skip, but it must never execute on the host.

## Commit Rules

- Keep commits scoped and do not commit generated artifacts.
- Run `git diff --check` and the relevant gates before committing.
- Search runtime code and scripts for absolute workspace paths and secret-like values.
- Preserve `com.eai.desktop` and user-data compatibility unless an explicit migration is approved.
- Current product version is `1.0.1`.
