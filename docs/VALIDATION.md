# EAI Desktop 1.0 Validation

## Per-Checkpoint Gates

1. `npm run check:mojibake`
2. `npm run check:architecture`
3. `npm run web:test`
4. `npm run web:build`
5. `npm run web:e2e`
6. `npm run service:test`
7. `git diff --check`

Run Campaign Docker smoke when Campaign/Docker changes, sidecar gates when its dependencies or packaging change, and Rust fmt/Clippy/test when Tauri changes. Full PyInstaller, Rust release and NSIS smoke run for runtime migration, desktop lifecycle changes and final releases. Docker failure never permits host execution.

## Current Results

- Repository bootstrap: passed with clean npm dependencies and repository-local Python environment.
- Frontend Vitest: 26 tests passed, including axe, Agent v3 dual-lane controls, Atlas query race coverage, dismissible first-run creation and desktop Provider/web-search configuration.
- Frontend production build: passed.
- Service unittest: 102 tests passed, including Agent v3 AskTurn/ResearchTask isolation, 240+ bilingual retrieval-routing cases, ContextManifest, web URL hardening, SourcePolicy, Evidence Guard, SSE replay, runtime schema 2 recovery, Research Store schema 4/outbox, PDF limits, Campaign, legacy read/410 boundaries, drafts, OperationBatch and ChangeSet undo.
- Canonical transaction fault coverage passed: multi-record failure rolls back every canonical row and journal entry; expected-payload conflict writes nothing; JSON projection failure preserves the SQLite commit and replayable journal; a runtime receipt failure after commit reconciles without applying OperationBatch twice.
- Application composition coverage passed: an immutable `ApplicationConfig` creates an isolated application/data root and `application.py` has zero route decorators. The legacy contract remains 107 paths/116 operations with path-method hash `e6395d9c1486b2a37d4ed6c04c84a38ac61656a3a59e202cb540dda23730144e`; the complete v1 contract is 122 paths/132 operations with version-neutral hash `1c4e552e0ba33a094e722c8c75bca9f1503a97f027600b0c487cf91e773e1dba`.
- Playwright: passed with the isolated empty seed at `1440x900`, `1180x820`, and `760x900`; narrow main width was 696px.
- Campaign Docker fixture: passed in Docker with the expected fixture metric; no host fallback ran.
- Sidecar: rebuilt from the new workspace with Python 3.14.4; random-port authentication and Tauri CORS smoke passed.
- Rust: fmt passed, Clippy passed with `-D warnings`, and 5 tests passed, including loopback/HTTPS Provider URL validation and backward-compatible web-search settings.
- The 1.0.0 PyInstaller sidecar, Tauri release and NSIS package built successfully. Isolated install/start/normal-close/uninstall smoke passed with real AppData unchanged. Installer size is 75,909,325 bytes and SHA-256 is `7B88CDD85518872FFF9197190E59E91BFFF9953A13D239D9C8B0B6FFED80D61E`.

## Installed-App Acceptance

The smoke script silently installs NSIS to a temporary directory, launches with test-only AppData, waits for the sidecar readiness file, closes the application, verifies no new sidecar process remains, silently uninstalls, and compares a SHA-256 manifest of real `%APPDATA%\com.eai.desktop` before and after.

## v1.0 Release Contract

- Freeze the legacy 107-path/116-operation path-method hash separately from the complete v1 OpenAPI contract. Freeze Agent v3 ResearchTask SSE order, monotonic seq, reconnect and terminal deduplication independently.
- Validate runtime schema 1 -> 2 paired backups, failure restoration and whole-application read-only startup for higher research or runtime schema.
- Real-provider acceptance must use isolated data and must never persist a key, raw Provider body or host path in logs or fixtures.
- Information routing must search for every informational sample and avoid search for greetings and text transformations. Unknown SourcePolicy and forged cross-task citations must produce zero network or zero accepted citation respectively.

- Record schema 3 -> 4 online backup, migration report and projection backlog report.
- The synthetic schema-ceiling round trip is green: a newer schema opens read-only and every attempted write has zero side effects. Downgrade to experimental 0.x packages is not supported by v1.0.
- Sidecar build/test, Rust fmt/Clippy/test, PyInstaller, Rust release and isolated NSIS install/start/close/uninstall must be green for the tagged source.
- Restricted real Provider audits target zero forged citations, openable locators and correct source-policy enforcement; offline release gates do not claim a real-provider sample count.
- Scan runtime code/scripts for absolute workspace paths and secret-like values; remove temporary development switches.
