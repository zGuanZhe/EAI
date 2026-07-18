# EAI Desktop 0.5 Validation

## Per-Checkpoint Gates

1. `npm run check:mojibake`
2. `npm run check:architecture`
3. `npm run web:test`
4. `npm run web:build`
5. `npm run web:e2e`
6. `npm run service:test`
7. `git diff --check`

Run Campaign Docker smoke when Campaign/Docker changes, sidecar gates when its dependencies or packaging change, and Rust fmt/Clippy/test when Tauri changes. Full PyInstaller, Rust release and NSIS smoke run only for the 0.4.1 bridge, database migration or desktop lifecycle changes, and final 0.5.0 release. Docker failure never permits host execution.

## Current Results

- Repository bootstrap: passed with clean npm dependencies and repository-local Python environment.
- Frontend Vitest: 23 tests passed, including axe and Atlas query race coverage.
- Frontend production build: passed.
- Service unittest: 79 tests passed, including Agent v2.1, SourcePolicy, Evidence Guard, SSE replay, Research Store schema 4/outbox, PDF limits, Campaign, legacy read/410 boundaries, drafts, OperationBatch and ChangeSet undo.
- Playwright: passed with the isolated empty seed at `1440x900`, `1180x820`, and `760x900`; narrow main width was 696px.
- Campaign Docker fixture: last attempt failed while Docker Hub returned an anonymous-token EOF pulling `python:3.11-slim`; no host fallback ran. A green rerun is required before release.
- Sidecar: rebuilt from the new workspace with Python 3.14.4; random-port authentication and Tauri CORS smoke passed.
- Rust: fmt passed, Clippy passed with `-D warnings`, and 3 tests passed.
- Historical 0.4.0 NSIS smoke passed. The 0.4.1 safety bridge and final 0.5.0 installer/upgrade/downgrade/uninstall exercises are still required.

## Installed-App Acceptance

The smoke script silently installs NSIS to a temporary directory, launches with test-only AppData, waits for the sidecar readiness file, closes the application, verifies no new sidecar process remains, silently uninstalls, and compares a SHA-256 manifest of real `%APPDATA%\com.eai.desktop` before and after.

## 0.5 Release Requirements

- Record schema 3 -> 4 online backup, migration report and projection backlog report.
- Verify 0.4.1 opens schema 4 read-only, every write has zero side effects, and reopening with 0.5 preserves drafts, projection journal, Campaign and revisions.
- Run sidecar build/test, Rust fmt/Clippy/test, PyInstaller, Rust release and NSIS install/upgrade/downgrade/uninstall with isolated AppData.
- Run 20 restricted real Provider queries: zero forged citations, every locator opens, and source-policy correctness is at least 95%.
- Scan runtime code/scripts for absolute workspace paths and secret-like values; remove temporary development switches.

## Historical 0.4.0 Record

Recorded from the historical successful build; it is not the 0.5 release artifact:

```text
Installer: apps/desktop/src-tauri/target/release/bundle/nsis/EAI Desktop_0.4.0_x64-setup.exe
Bytes: 75,785,557 (72.27 MiB)
SHA-256: 62A302C854BF2F0667583C47E7BBD61E82503FCA9D6C2A3D0901A76859896739
Install smoke: passed
Real AppData unchanged: passed
```
