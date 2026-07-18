# EAI Desktop 0.5 Development Handoff

## Start Here

The active workspace is `D:\Test\GUAN\EAI`. The former `EAI-Desktop` repository is a read-only source archive and is not a build dependency. This repository has an empty personal seed and is compatible with the installed identifier `com.eai.desktop`.

## Runtime State

- Main Agent is v2.1. New turns use the conditional `decide -> capability -> observe` loop, steer, attempts, replayable SSE, Evidence Guard, approvals and OperationBatch.
- Plain conversation uses zero capabilities and zero sources. Research context starts small and expands through registered capabilities.
- Research Store owns canonical Works, Atlas placements, claims, evidence spans, graph relations and personal research state in `research.db`.
- Agent task/event/checkpoint/approval state remains physically separate in `runtime.db`.
- Canvas exposes argument and Campaign views. Campaign uses the pinned AI Scientist v2 derivative for evidence preparation, progressive experiment branches, writing, three-role review, revision and release validation.
- Legacy AgentRun and ChangeSet data remain readable. Legacy LabRun is hash-migrated to an archived manual Campaign. Legacy mutation APIs return `410 Gone`.
- Schema 4 is active. SQLite is canonical; JSON is an outbox-driven compatibility projection with pending/failed replay, not part of a cross-medium transaction.
- The only official downgrade bridge is 0.4.1, which opens newer schema read-only and rejects durable writes with structured `409 schema_newer_than_app`.

## Structural Changes Toward 0.5

- `apps/web/src/App.jsx` is only the application entry; workspace and extracted Atlas/Inspector features live in dedicated modules.
- Obsolete v1 execution hooks were removed. Historical `AgentRunTrace` remains display-only.
- `services/api/app/main.py` is a minimal ASGI entrypoint; compatibility code is under `app/legacy`.
- Agent v2 fixed-specialist and Lab-write branches were removed.
- Architecture checks prevent old workspace paths, hot-path regressions, and version drift.
- Desktop AppData can be overridden only in explicit test mode, enabling isolated install smoke without touching real user data.
- Workspace server state now uses React Query keys; thread detail remains local because SSE patches it incrementally. Atlas switching, knowledge polling and runtime refresh no longer use request counters or ad hoc timers.
- Workspace, Draft, System, Atlas/Object Memory and Agent v2/source/document APIs use explicit router/service boundaries. Agent API orchestration and its shared thread lock are lifespan-owned. Architecture checks validate dependency direction and registered routers rather than treating file size as the primary metric.
- Legacy Agent event replay buffers and cancellation markers are also AppServices-owned and cleared with the runtime; the AST gate rejects new mutable runtime containers in `application.py` outside a fixed static-configuration allowlist.
- SourcePolicy, fail-closed AnswerDraft/Evidence Guard, revisioned drafts, PDF locator normalization, page render budgets, cancellation isolation and non-blocking SSE are implemented and covered offline.

## Validation Status

Completed in the new workspace:

- clean `npm ci` and repository-local Python bootstrap
- UTF-8/architecture checks
- frontend Vitest and production build
- FastAPI suite, including Agent v2, Research Store, Campaign, synthetic legacy fixtures, transaction rollback and `410` compatibility
- isolated Playwright coverage at desktop, medium and narrow widths
- previously green Campaign Docker fixture and freshly built PyInstaller sidecar smoke; the latest Docker rerun is blocked as noted below
- Rust fmt, Clippy with warnings denied, and three desktop data-path tests
- historical NSIS 0.4.0 isolated install/start/close/uninstall smoke
- real `%APPDATA%\com.eai.desktop` SHA-256 tree unchanged before and after installed-app smoke

The historical installer metadata and current 0.5 release gaps are recorded in `docs/VALIDATION.md`. Do not describe 0.5 as released until those gaps close.

## Known Constraints

- The sidecar bootstrap may use Python 3.14 when the registered local Python 3.13 path is stale; PyInstaller output must therefore be tested rather than assumed compatible.
- Full Campaign execution requires Docker. Without Docker, ideation, plans and command previews remain available but execution is disabled.
- The NSIS package is unsigned and has no automatic updater.
- AI Scientist v2 is pinned at `96bd51617cfdbb494a9fc283af00fe090edfae48`; preserve its LICENSE and EAI derivation notice.
- Campaign Docker smoke is currently blocked by an anonymous Docker Hub token EOF while pulling `python:3.11-slim`. This is an external failure, not a Docker-unavailable skip, and no host fallback occurred.
- A Web E2E run once timed out waiting for an Agent resumed stream and passed on immediate isolated rerun. Treat recurrence as an SSE/terminal-state race, not as an ignorable failure.

## Next Priorities

1. Extract legacy AgentRun/ChangeSet compatibility and Task Pack/Result routers without changing frozen OpenAPI/SSE/409/410 contracts.
2. Remove dead compatibility implementations only after their callers use services and the full gates are green.
3. Run the schema 3 -> 4 backup/restore report and the 0.4.1 schema 4 read-only downgrade/reopen exercise.
4. Complete sidecar, Rust, PyInstaller and NSIS release gates, then run the restricted 20-query real Provider citation audit.
