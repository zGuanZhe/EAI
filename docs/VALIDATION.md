# EAI Desktop 1.0.1 Validation

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
- Frontend Vitest: 40 tests passed across 13 files, including axe, Agent v3 dual-lane controls, Atlas query race coverage, first-send creation/retry guards, versioned onboarding, portal Listbox keyboard behavior, desktop Provider/web-search configuration and provider-neutral Task Pack availability.
- Frontend production build: passed.
- Service unittest: 108 tests passed, including Agent v3 AskTurn/ResearchTask isolation, 240+ bilingual retrieval-routing cases, native Anthropic text/stream/tool mapping with prompt caching, Provider precedence, fail-closed explicit selection and audit identity, ContextManifest, web URL hardening, SourcePolicy, Evidence Guard, SSE replay, runtime schema 2 recovery, Research Store schema 4/outbox, PDF limits, Campaign, legacy read/410 boundaries, drafts, OperationBatch and ChangeSet undo.
- Canonical transaction fault coverage passed: multi-record failure rolls back every canonical row and journal entry; expected-payload conflict writes nothing; JSON projection failure preserves the SQLite commit and replayable journal; a runtime receipt failure after commit reconciles without applying OperationBatch twice.
- Application composition coverage passed: an immutable `ApplicationConfig` creates an isolated application/data root and `application.py` has zero route decorators. The legacy contract remains 107 paths/116 operations with path-method hash `e6395d9c1486b2a37d4ed6c04c84a38ac61656a3a59e202cb540dda23730144e`; the complete v1 contract is 122 paths/132 operations with version-neutral hash `1c4e552e0ba33a094e722c8c75bca9f1503a97f027600b0c487cf91e773e1dba`.
- Playwright: passed with isolated data at `760`, `900`, `1119`, `1120`, `1180`, and `1440px` widths, plus a 200% effective-zoom first-run viewport and reduced-motion context. The welcome Composer, explicit creation dialogs, settings, all portal Listboxes, Atlas focus/zoom/pan, drawers and narrow layouts remained usable without horizontal overflow; narrow main width was 696px.
- Campaign Docker fixture: passed in Docker with the expected fixture metric; no host fallback ran.
- Sidecar: rebuilt from the new workspace with Python 3.14.4; random-port authentication and Tauri CORS smoke passed.
- Rust: fmt passed, Clippy passed with `-D warnings`, and 7 tests passed, including the Provider catalog, native Anthropic format boundary, loopback keyless boundary, loopback/HTTPS URL validation and backward-compatible web-search settings.
- The 1.0.1 PyInstaller sidecar, Tauri release and NSIS package built successfully. Isolated install/start/normal-close/uninstall smoke passed with real AppData unchanged. The release installer is 77,584,376 bytes with SHA-256 `479824B3FE6A40F75D375AE78C9B4B1181A0B4C8501B6A0D145E774F5828FD9D`.
- The release installer was applied in place to `D:\Test\EAI Desktop`; a path/length/SHA-256 manifest of all 147 files below real `%APPDATA%\com.eai.desktop` was identical before and after installation.

## Installed-App Acceptance

The smoke script silently installs NSIS to a temporary directory, launches with test-only AppData, waits for the sidecar readiness file, closes the application, verifies no new sidecar process remains, silently uninstalls, and compares a SHA-256 manifest of real `%APPDATA%\com.eai.desktop` before and after.

## v1.0.1 Release Contract

- Freeze the legacy 107-path/116-operation path-method hash separately from the complete v1 OpenAPI contract. Freeze Agent v3 ResearchTask SSE order, monotonic seq, reconnect and terminal deduplication independently.
- Validate runtime schema 1 -> 2 paired backups, failure restoration and whole-application read-only startup for higher research or runtime schema.
- Real-provider acceptance must use isolated data and must never persist a key, raw Provider body or host path in logs or fixtures.
- Information routing must search for every informational sample and avoid search for greetings and text transformations. Unknown SourcePolicy and forged cross-task citations must produce zero network or zero accepted citation respectively.

- Record schema 3 -> 4 online backup, migration report and projection backlog report.
- The synthetic schema-ceiling round trip is green: a newer schema opens read-only and every attempted write has zero side effects. Downgrade to experimental 0.x packages is not supported by v1.0.
- Sidecar build/test, Rust fmt/Clippy/test, PyInstaller, Rust release and isolated NSIS install/start/close/uninstall must be green for the tagged source.
- Restricted real Provider audits target zero forged citations, openable locators and correct source-policy enforcement; offline release gates do not claim a real-provider sample count.
- Scan runtime code/scripts for absolute workspace paths and secret-like values; remove temporary development switches.
