# EAI Desktop 0.4

EAI Desktop 是本地优先的个人科研工作台。Main Agent v2.1 负责对话、证据调查和可确认操作；Research Store 统一论文身份、全文证据、Atlas 图和个人研究状态；Canvas Campaign 将问题推进到实验、写作、审稿和发布。

## Workspace

```text
apps/web                    React + Vite
apps/desktop/src-tauri      Tauri v2 Windows shell
services/api                FastAPI sidecar
resources/atlas-cache       read-only curated Atlas resources
third_party/ai-scientist-v2 pinned AI Scientist v2 derivative
migration/personal-snapshot empty first-run seed
scripts                     bootstrap, verification and packaging
tests                       synthetic cross-version fixtures
docs                        product, architecture and handoff
```

## Start

```powershell
npm ci
npm run bootstrap
npm run dev
```

`bootstrap` creates the repository-local Python environment and resolves the local Rust toolchain. Development data is written below the ignored `runtime/` directory; it does not read the former workspace.

## Verify And Build

```powershell
npm run verify
npm run desktop:build
npm run desktop:smoke
```

The NSIS installer is generated under `apps/desktop/src-tauri/target/release/bundle/nsis`. It is unsigned and intended for local Windows validation.

## Data Safety

- `resources/atlas-cache` and the vendored upstream source are immutable application resources.
- Installed data remains under `%APPDATA%\com.eai.desktop`; an upgrade never replaces an existing personal directory.
- `migration/personal-snapshot` contains only `.gitkeep`. No personal thread, backup, LabRun, runtime database or key belongs in Git.
- Provider keys stay in Windows Credential Manager and must not appear in JSON, logs, events or frontend storage.
- `EAI_DESKTOP_APP_DATA_DIR` is accepted only together with `EAI_DESKTOP_TEST_MODE=1` for isolated installed-app smoke tests.

Read [AGENTS.md](AGENTS.md) before changing the repository. Current architecture and validation state are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/HANDOFF.md](docs/HANDOFF.md), and [docs/VALIDATION.md](docs/VALIDATION.md).
