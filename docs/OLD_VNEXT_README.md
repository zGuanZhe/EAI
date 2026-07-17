# EAI vNext

vNext is the parallel rebuild of EAI-Desktop as a personal research OS.

The old app remains available as a source of data and workflow recipes. New personal work writes to `data/personal`, not to the legacy SQLite database.

## Run

Start the thin service:

```powershell
cd D:\Test\GUAN\EAI-Desktop\vnext\service
C:\Users\观\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Start the frontend:

```powershell
cd D:\Test\GUAN\EAI-Desktop\vnext\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## First-Phase Workflow

1. Create or open a research thread.
2. Open an Atlas subgraph.
3. Click a paper or relation to add it as a Context Card.
4. Shift-click papers to draft a path, then add that path as context.
5. Use Context Canvas to inspect the reasoning board.
6. Export selected cards as a Markdown task pack for Codex.
7. Paste Codex output back into Thread to create a result card.

## Data Boundaries

- Public research data: existing `web/data/*.bundle.json` cache.
- Personal truth: `data/personal/threads/*.json`.
- Backups: `data/personal/backups/*.json`.
- Secrets: user config, for example `~/.eai-desktop/secrets.json`.

The frontend must not persist API keys in browser storage.
