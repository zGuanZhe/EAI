# Clean Repository Manifest

## Tracked

- `apps/web`: React product UI and UI tests.
- `apps/desktop/src-tauri`: Tauri source, configuration and Rust lockfile.
- `services/api`: FastAPI source, requirements and service tests.
- `resources/atlas-cache`: read-only curated Atlas resources.
- `third_party/ai-scientist-v2`: pinned upstream derivative, license and EAI notice.
- `migration/personal-snapshot/.gitkeep`: empty first-run migration seed only.
- `scripts`, `tests`, `docs`: verification, synthetic fixtures and documentation.

## Never Tracked

- Dependency folders: `node_modules`, `.venv`, Cargo registry/cache.
- Build outputs: Web `dist`, Tauri `target`, PyInstaller `build`, sidecar binaries and installers.
- Runtime data: databases, JSON projections, logs, screenshots, test results and Provider responses.
- Personal data: threads, projects, object memory, documents, Campaigns, backups and source snapshots.
- Secrets: `.env`, API keys, credentials, certificates and private keys.

## Release Assets

GitHub Release assets are built from the tagged source and uploaded separately. They are not committed to Git. Each release records the installer SHA-256 and the gates used to produce it.

## Verification

Before a release, run `git status --ignored`, the secret/absolute-path scan, `git diff --check`, all source gates, `desktop:build`, and `desktop:smoke`. Installed smoke must use both `EAI_DESKTOP_TEST_MODE=1` and an absolute temporary `EAI_DESKTOP_APP_DATA_DIR`.
