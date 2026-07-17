# EAI Desktop 0.4 Handoff

## Start Here

The active workspace is `D:\Test\GUAN\EAI`. The former `EAI-Desktop` repository is a read-only source archive and is not a build dependency. This repository has an empty personal seed and is compatible with the installed identifier `com.eai.desktop`.

## Runtime State

- Main Agent is v2.1. New turns use the conditional `decide -> capability -> observe` loop, steer, attempts, replayable SSE, Evidence Guard, approvals and OperationBatch.
- Plain conversation uses zero capabilities and zero sources. Research context starts small and expands through registered capabilities.
- Research Store owns canonical Works, Atlas placements, claims, evidence spans, graph relations and personal research state in `research.db`.
- Agent task/event/checkpoint/approval state remains physically separate in `runtime.db`.
- Canvas exposes argument and Campaign views. Campaign uses the pinned AI Scientist v2 derivative for evidence preparation, progressive experiment branches, writing, three-role review, revision and release validation.
- Legacy AgentRun and ChangeSet data remain readable. Legacy LabRun is hash-migrated to an archived manual Campaign. Legacy mutation APIs return `410 Gone`.

## Structural Changes In 0.4

- `apps/web/src/App.jsx` is only the application entry; workspace and extracted Atlas/Inspector features live in dedicated modules.
- Obsolete v1 execution hooks were removed. Historical `AgentRunTrace` remains display-only.
- `services/api/app/main.py` is a minimal ASGI entrypoint; compatibility code is under `app/legacy`.
- Agent v2 fixed-specialist and Lab-write branches were removed.
- Architecture checks prevent old workspace paths, hot-path regressions, and version drift.
- Desktop AppData can be overridden only in explicit test mode, enabling isolated install smoke without touching real user data.

## Validation Status

Completed in the new workspace:

- clean `npm ci` and repository-local Python bootstrap
- UTF-8/architecture checks
- frontend Vitest and production build
- FastAPI suite, including Agent v2, Research Store, Campaign, synthetic legacy fixtures, transaction rollback and `410` compatibility
- isolated Playwright coverage at desktop, medium and narrow widths
- Campaign Docker fixture and freshly built PyInstaller sidecar smoke
- Rust fmt, Clippy with warnings denied, and three desktop data-path tests
- NSIS 0.4.0 build plus isolated install/start/close/uninstall smoke
- real `%APPDATA%\com.eai.desktop` SHA-256 tree unchanged before and after installed-app smoke

Installer metadata is recorded in `docs/VALIDATION.md`.

## Known Constraints

- The sidecar bootstrap may use Python 3.14 when the registered local Python 3.13 path is stale; PyInstaller output must therefore be tested rather than assumed compatible.
- Full Campaign execution requires Docker. Without Docker, ideation, plans and command previews remain available but execution is disabled.
- The NSIS package is unsigned and has no automatic updater.
- AI Scientist v2 is pinned at `96bd51617cfdbb494a9fc283af00fe090edfae48`; preserve its LICENSE and EAI derivation notice.

## Next Priorities

1. Keep shrinking workspace orchestration and the compatibility application module by domain, without changing API behavior.
2. Expand retrieval quality evaluation across all Atlas collections and real bilingual research queries.
3. Improve Campaign runtime installation diagnostics and reproducibility reporting.
4. Add signed release and update infrastructure only after data migration and rollback procedures are production-tested.
