# 给下一轮 Codex 的启动提示

把下面整段复制给新的 Codex 任务即可。

```text
你正在一个 EAI-Desktop vNext clean-start 包中工作。请先阅读：

1. README.md
2. docs/HISTORICAL_MEMORY.md
3. docs/PROJECT_GUIDE.md
4. docs/ENGINEERING_EXPERIENCE.md
5. docs/LEGACY_FEATURE_ADAPTATION.md

项目目标：
把 EAI-Desktop vNext 升级为 Codex-like 个人研究工作台。它不是论文阅读器，也不是旧后台；它是研究思维脚手架。核心对象是项目、研究线程、Atlas、Context Canvas、对象记忆、Task Pack、论文更新候选和研究实验运行。

风格要求：
Codex Mac 风格，清爽、圆润、低边界、无渐变、无纸质感、无后台表单感。彩色只用于细线、状态点、路线和节点类型区分。界面默认中文，保留 Codex / Atlas / Context Canvas / Task Pack 等产品名。

数据边界：
只读 data/atlas-cache。个人数据只写 data/personal。不要写旧 bundle、旧 SQLite、旧后台。不要把 API key、完整大包、原始 API 响应写入前端、线程、对象记忆、运行记录或实验运行。

当前首要任务：
先做结构化重构，不急着扩功能。

建议顺序：
1. 把 app/frontend/src/main.jsx 拆成 Sidebar、RightRail、AtlasSurface、CanvasSurface、PaperReadingPage、LabRunPage、ToolsSurface、workspace state hook/reducer。
2. 把 app/service/app/main.py 拆成 core、schemas、services、routers。
3. 修复所有中文乱码和文案散落问题。
4. 保持 npm run build 和 python -m unittest discover -s app/service/tests -v 通过。
5. 再开始升级论文阅读页、论文更新虚框候选、研究实验室。

旧功能吸收重点：
- 旧 Atlas：路线 × 年份、关系/路径选择、论文详情卡信息。
- 论文更新：更新包、查重、候选卡、人工审查。
- 研究实验室：BFTS 阶段树、失败节点修复提示、artifact/log manifest。
- AI Studio：模板化 Task Pack、复制优先、API 辅助、运行记录。

不要做：
- 不要把首页做成仪表盘。
- 不要把 Tools 做成旧后台入口合集。
- 不要把 Atlas 做成 PDF 阅读器。
- 不要让 API agent 自动写回。
- 不要为了演示改重数据模型。

每次完成后请验证：
- npm run build
- python -m unittest discover -s app/service/tests -v
- 手动检查 Atlas 滚动、右栏切换、论文阅读页展开、实验运行页展开、普通消息输入和 / 命令。
```
