# Clean Package Manifest

## 包含

### 可运行代码

- `app/frontend`
- `app/service`

### 只读研究数据

- `data/atlas-cache/*.bundle.json`
- `data/atlas-cache/atlases*.json`
- `data/atlas-cache/papers*.json`
- `data/atlas-cache/search.index.json`
- `data/atlas-cache/stats.json`

### 个人数据空壳

- `data/personal/threads/.gitkeep`
- `data/personal/projects/.gitkeep`
- `data/personal/objects/.gitkeep`
- `data/personal/atlas_updates/.gitkeep`
- `data/personal/lab_runs/.gitkeep`
- `data/personal/backups/.gitkeep`

### 旧版参考

只复制了有迁移价值的旧前端文件。旧 SQLite、旧 backend、旧 admin、旧打包产物没有复制。

### 文档

- `README.md`
- `docs/HISTORICAL_MEMORY.md`
- `docs/PROJECT_GUIDE.md`
- `docs/ENGINEERING_EXPERIENCE.md`
- `docs/LEGACY_FEATURE_ADAPTATION.md`
- `docs/FRESH_AGENT_PROMPT.md`
- `docs/OLD_VNEXT_README.md`
- `docs/LEGACY_FUNCTIONS_AND_TECH_DOC.md`

## 排除

- `node_modules`
- `dist`
- `__pycache__`
- `.venv`
- `.playwright-browsers`
- `.codex-analysis`
- 调试截图
- `eai.db`
- `backend/eai.db`
- `backend/.env`
- 个人线程、对象记忆、运行记录 JSON

## 已做适配

- `app/service/app/main.py` 中 Atlas 数据路径从旧 `web/data` 改为本包 `data/atlas-cache`。
- `.gitignore` 改为匹配本包目录。

## 仍未清理的债务

- 前端 `main.jsx` 仍很大。
- 服务端 `main.py` 仍很大。
- 某些历史函数可能还留有覆盖式定义，应在下一轮拆模块时顺手删除。
- UI 样式仍需进一步 token 化。
