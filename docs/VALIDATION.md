# EAI Desktop 0.4 Validation

## Required Gate Order

1. `npm run check:mojibake`
2. `npm run check:architecture`
3. `npm run web:test`
4. `npm run web:build`
5. `npm run web:e2e`
6. `npm run service:test`
7. `npm run campaign:docker-test`
8. `npm run sidecar:test`
9. `npm run rust:fmt`
10. `npm run rust:clippy`
11. `npm run rust:test`
12. `npm run desktop:build`
13. `npm run desktop:smoke`

Web and service gates must pass before packaging. Run `git diff --check`, absolute-path and secret scans before creating the baseline commit.

## Current Results

- Repository bootstrap: passed with clean npm dependencies and repository-local Python environment.
- Frontend Vitest: 13 tests passed.
- Frontend production build: passed.
- Service unittest: 67 tests passed, including Agent v2.1, Research Store, Campaign, legacy read/410 boundaries, LabRun migration, ChangeSet undo and multi-file rollback.
- Playwright: passed with the isolated empty seed at `1440x900`, `1180x820`, and `760x900`; narrow main width was 696px.
- Campaign Docker fixture: passed with an isolated metric result and no host fallback.
- Sidecar: rebuilt from the new workspace with Python 3.14.4; random-port authentication and Tauri CORS smoke passed.
- Rust: fmt passed, Clippy passed with `-D warnings`, and 3 tests passed.
- NSIS and installed-app smoke: passed; normal close left zero new sidecar processes, uninstall completed, and real AppData was unchanged.

## Installed-App Acceptance

The smoke script silently installs NSIS to a temporary directory, launches with test-only AppData, waits for the sidecar readiness file, closes the application, verifies no new sidecar process remains, silently uninstalls, and compares a SHA-256 manifest of real `%APPDATA%\com.eai.desktop` before and after.

## Release Record

Populate after the final successful build:

```text
Installer: apps/desktop/src-tauri/target/release/bundle/nsis/EAI Desktop_0.4.0_x64-setup.exe
Bytes: 75,785,557 (72.27 MiB)
SHA-256: 62A302C854BF2F0667583C47E7BBD61E82503FCA9D6C2A3D0901A76859896739
Install smoke: passed
Real AppData unchanged: passed
```
