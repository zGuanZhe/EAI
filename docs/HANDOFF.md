# EAI Desktop 1.0.1 Development Handoff

## Start Here

This repository is the complete active workspace and has no dependency on former experimental checkouts. It has an empty personal seed and remains compatible with the installed identifier `com.eai.desktop`.

## Runtime State

- Agent v3 is the product entry. AskTurn and ResearchTask are independent lanes over the existing audited runtime graph.
- Informational asks search every allowed available source; greetings and text transformations do not search. Generic web search is independent from the OpenAI-compatible model channel and supports SearXNG, Brave and Tavily.
- ResearchTask persists ContextManifest and checkpoint history, supports targeted steer/pause/resume/cancel, keeps the Composer available, and promotes to Campaign only through an explicit command.
- Runtime schema 2 adds `context_manifests`, `research_checkpoints`, and `ui_commands`. A higher research or runtime schema forces the whole application into structured read-only mode.
- The model channel supports native Anthropic Messages and editable OpenAI-compatible presets for OpenAI, OpenRouter, Gemini, DeepSeek, Qwen, xAI, Groq, SiliconFlow, Kimi, Ollama and LM Studio. Loopback endpoints may be keyless; remote endpoints remain HTTPS-and-key only.

- Agent v3 is the user-facing entry over the audited v2 runtime engine. New turns use the conditional `decide -> capability -> observe` loop, steer, attempts, replayable SSE, Evidence Guard, approvals and OperationBatch.
- Plain conversation uses zero capabilities and zero sources. Research context starts small and expands through registered capabilities.
- Research Store owns canonical Works, Atlas placements, claims, evidence spans, graph relations and personal research state in `research.db`.
- Agent task/event/checkpoint/approval state remains physically separate in `runtime.db`.
- Canvas exposes argument and Campaign views. Campaign uses the pinned AI Scientist v2 derivative for evidence preparation, progressive experiment branches, writing, three-role review, revision and release validation.
- Legacy AgentRun and ChangeSet data remain readable. Legacy LabRun is hash-migrated to an archived manual Campaign. Legacy mutation APIs return `410 Gone`.
- Schema 4 is active. SQLite is canonical; JSON is an outbox-driven compatibility projection with pending/failed replay, not part of a cross-medium transaction.
- v1.0 is the first supported public desktop release. Downgrade to experimental 0.x builds is unsupported; users should retain an AppData backup before changing major versions. Higher research/runtime schemas still force structured read-only mode.

## Structural Changes Through 1.0.1

- `apps/web/src/App.jsx` is only the application entry; workspace and extracted Atlas/Inspector features live in dedicated modules.
- Obsolete v1 execution hooks were removed. Historical `AgentRunTrace` remains display-only.
- `services/api/app/main.py` is a minimal ASGI entrypoint; compatibility code is under `app/legacy`.
- Agent v2 fixed-specialist and Lab-write branches were removed.
- Architecture checks prevent old workspace paths, hot-path regressions, and version drift.
- Desktop AppData can be overridden only in explicit test mode, enabling isolated install smoke without touching real user data.
- Desktop settings persist the selected Provider, Base URL, model and API format as non-secret AppData configuration while API keys remain in Windows Credential Manager. Only the selected Provider is injected into the sidecar; remote endpoints require HTTPS and loopback endpoints may use HTTP.
- A fresh empty workspace now opens on a welcome Composer with example questions and model readiness. The first send creates one research thread under the selected project or Unfiled Work and then launches the selected AskTurn/ResearchTask; explicit project/thread dialogs remain available but are never startup gates.
- Versioned local-only onboarding provides four non-modal, skippable and replayable steps across model readiness, first input, lane/source controls and workspace navigation. The shared portal Listbox replaces every native web select while preserving values, form semantics and accessible names.
- Workspace server state now uses React Query keys; thread detail remains local because SSE patches it incrementally. Atlas switching, knowledge polling and runtime refresh no longer use request counters or ad hoc timers.
- Workspace, Draft, System, Atlas/Object Memory and Agent v2/source/document APIs use explicit router/service boundaries. Agent API orchestration and its shared thread lock are lifespan-owned. Architecture checks validate dependency direction and registered routers rather than treating file size as the primary metric.
- Change review, thread content, Task Pack and legacy read/replay APIs now use explicit router/service boundaries. Legacy Agent v1 mutations terminate at the frozen `410 Gone` router; their unreachable execution, Provider loop and Lab/Task Pack construction code has been removed.
- Frozen `ApplicationConfig` and `ApplicationAssembly` provide isolated application factories. `application.py` contains configuration, lifespan composition, router registration and compatibility adapters only; it has no route decorators or domain implementation.
- `AppServices` owns Workspace, Atlas, Provider, Change Review, Thread Content, Task Pack, Agent Domain, Agent Operations, Runtime, Campaign and legacy read instances. Its data-root/schema signatures rebuild dependent services without mutable path globals.
- ChangeSet and OperationBatch canonical writes share `ResearchStore.apply_record_batch()`: all expected values are preflighted, canonical rows and projection journal entries commit in one SQLite transaction, and JSON replay happens afterward. A runtime receipt failure after the research commit is reconciled from the canonical `operation_batch_id` marker instead of applying the batch twice.
- Legacy Agent event replay buffers and cancellation markers were removed with the dead v1 executor; the AST gate rejects new mutable runtime containers in `application.py` outside a fixed static-configuration allowlist.
- SourcePolicy, fail-closed AnswerDraft/Evidence Guard, revisioned drafts, PDF locator normalization, page render budgets, cancellation isolation and non-blocking SSE are implemented and covered offline.

## Validation Status

Completed in the new workspace:

- clean `npm ci` and repository-local Python bootstrap
- UTF-8/architecture checks
- 40-test frontend Vitest suite and production build, including first-send failure/retry guards, onboarding state, shared Listbox interaction, Provider settings and provider-neutral Task Pack availability
- 108-test FastAPI suite, including Agent v3 dual-lane routing, Research Store, Campaign, native Anthropic mapping, Provider precedence and fail-closed selection, synthetic legacy fixtures, canonical batch rollback, projection failure, runtime receipt reconciliation and `410` compatibility
- isolated Playwright coverage at desktop, medium and narrow widths
- Campaign Docker fixture, with execution isolated in Docker and no host fallback
- freshly built PyInstaller sidecar smoke on a random port
- Rust fmt, Clippy with warnings denied, and seven desktop data-path/provider-config tests
- 1.0.1 PyInstaller, Tauri release and NSIS isolated install/start/close/uninstall smoke
- real `%APPDATA%\com.eai.desktop` SHA-256 tree unchanged before and after installed-app smoke

The source and installed-app gates for v1.0.1 are recorded in `docs/VALIDATION.md`. The final installer passed isolated install/start/normal-close/uninstall smoke and was installed to `D:\Test\EAI Desktop` with all 147 real AppData files unchanged. Real Provider acceptance is environment-dependent and is not represented by offline fixtures.

## Known Constraints

- The sidecar bootstrap may use Python 3.14 when the registered local Python 3.13 path is stale; PyInstaller output must therefore be tested rather than assumed compatible.
- Full Campaign execution requires Docker. Without Docker, ideation, plans and command previews remain available but execution is disabled.
- The NSIS package is unsigned and has no automatic updater.
- AI Scientist v2 is pinned at `96bd51617cfdbb494a9fc283af00fe090edfae48`; preserve its LICENSE and EAI derivation notice.
- Agent v3 routes explicit workspace actions before retrieval. The attached-paper “保存为长期资料” regression now reaches OperationBatch approval without invoking academic or web connectors; negated actions remain read-only.

## Next Priorities

1. Add code signing and an automatic updater for future Windows releases.
2. Expand real-provider acceptance across OpenAI-compatible endpoints and supported web connectors without storing credentials or raw responses.
3. Preserve runtime/research read-only compatibility before any future schema increase.
