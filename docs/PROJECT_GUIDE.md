# Project Guide

## Product Loop

```text
research question -> Agent and Atlas evidence -> argument Canvas
-> Campaign experiments -> manuscript and review -> confirmed research state
```

Home and Thread own the Composer. Atlas, paper reading, Canvas, Campaign and Run Center are focused work surfaces; they return selected objects to Main Agent as explicit attachments.

## Frontend

- React 19, Vite 7, TanStack Query, lucide-react and layered CSS.
- `workspace/WorkspaceApp.jsx` assembles navigation, shared state and cross-surface actions.
- `features/thread` owns Agent v3 AskTurn/ResearchTask interaction, activity, citations and approval entry points.
- `features/atlas` owns graph layout, deterministic relation routing and paper selection.
- `features/canvas` owns argument projection and Campaign views.
- `features/inspector` owns source, detail, approval and changeset drawers.
- `features/tools` owns Run Center and knowledge synchronization.

Do not reintroduce Agent v1 write hooks or a second Campaign chat. Do not render a global Composer outside Home/Thread.

## Backend

- `factory.py` builds FastAPI and middleware; `main.py` exposes the ASGI app.
- `application.py` currently supplies compatibility assembly while domain modules are extracted.
- `agent_v3` owns AskTurn/ResearchTask lanes, ContextManifest and web connectors; the audited `agent_v2` runtime owns task state, capability dispatch, providers, evidence policy and sandbox previews.
- `research` owns identity, evidence, indexing, graph retrieval and enrichment.
- `campaign` owns ideas, branches, metrics, checkpoints, manuscripts, reviews and legacy Lab migration.
- `legacy` owns historical AgentRun/ChangeSet models and read-only support.

No model-generated payload writes files directly. All persistent mutations are server-resolved and transaction-checked.

## Desktop

Tauri starts a hidden PyInstaller FastAPI sidecar on a random loopback port. A per-launch token protects every local API request. Tauri owns sidecar cleanup and Credential Manager access; the WebView receives only runtime URL, token and non-secret paths through IPC.

## Commands

Use the root npm scripts. `npm run verify` is the source-level gate; `npm run desktop:build` repeats required gates and creates NSIS; `npm run desktop:smoke` validates the installed application with isolated AppData.
