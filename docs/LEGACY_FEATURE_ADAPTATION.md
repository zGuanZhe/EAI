# 旧版功能吸收清单

旧版不是地基，但里面有几块值得继续吸收。原则：只吸收工作流精华，不复制旧后台视觉。

## 已放入参考目录的旧文件

- `legacy-reference/web/desktop.html`
- `legacy-reference/web/ai_studio.html`
- `legacy-reference/web/ai_scientist_workbench.html`
- `legacy-reference/web/intake.html`
- `legacy-reference/web/assets/eai-ais-workbench-v2.*`
- `legacy-reference/web/assets/eai-ai-studio-v2.*`
- `legacy-reference/web/assets/eai-atlas-detail-v2.*`
- `legacy-reference/web/assets/eai-atlas-drawer-v2.*`
- `legacy-reference/web/assets/eai-atlas-overview-v2.*`
- `legacy-reference/web/assets/eai-atlas-data-loader.js`
- `legacy-reference/web/assets/eai-paper-relation-unifier.js`
- `legacy-reference/web/assets/eai-desktop-shell.js`

## 旧 Atlas 精华

应吸收：

- 路线 × 年份的论文组织语义。
- 论文、关系、路径的选择逻辑。
- 论文卡中的判断、分类理由、关系邻域、个人标记。
- 关系高亮和路径串联。
- Atlas 整体趋势说明。

不要吸收：

- 重表格背景。
- 大块路线颜色铺底。
- 后台统计 pills 常驻。
- 多个独立 HTML 页的跳转结构。

vNext 适配位置：

- `AtlasSurface`
- `RightRail Atlas 总览`
- `PaperReadingPage`
- `ObjectMemory`

## 论文更新精华

应吸收：

- 生成更新 Task Pack。
- 给 Codex 明确查重清单、路线说明、年份分布和输出格式。
- 解析候选论文和候选关系。
- 人工审查候选，再应用。

vNext 设计：

- 候选不写旧 bundle。
- 候选进入 `data/personal/atlas_updates/{atlas_id}.json`。
- 中央 Atlas 中以虚框论文卡显示。
- 逐个“应用 / 暂缓 / 驳回 / 加入 Context”。
- 已应用只是个人叠加层，不是假装正式库。

下一步缺口：

- 候选冲突检测还需要更强：标题、DOI、arXiv、作者年份相似度。
- 候选关系应能画虚线关系。
- 候选应用后应自动生成对象记忆快照。
- 更新运行记录要能从 Tools 回看和再次复制。

## 研究实验室精华

应吸收：

- BFTS 阶段树：初始实现、基线复现、创新实验、消融实验、结果分析。
- 失败节点修复提示词。
- 调试上下文复制。
- stage progress、artifact manifest、日志片段导入。
- 实验发现和下一步任务回写。

vNext 设计：

- 从 Context Canvas 的任务/假设节点创建实验运行。
- `data/personal/lab_runs/{run_id}.json` 保存 LabRun。
- 第一版不执行本地命令，只记录计划、命令建议、日志片段、产物 manifest 和结论。
- 实验页从右侧展开，占中间 + 右栏，不遮左栏。
- 返回经 preview/confirm 后写入 LabRun、线程消息和 Canvas。

下一步缺口：

- 手动添加日志/产物的体验还要加强。
- 阶段树需要更清楚的进度摘要和失败分支视觉。
- “复制完整实验上下文”应比单个失败阶段调试更醒目。
- Tools 页应能按失败、待确认、已完成筛选 LabRun。

## 论文详情卡精华

应吸收：

- 论文核心创新。
- 核心技术。
- 实验/证据。
- 局限。
- 可复用启发。
- 待查问题。
- 个人判断、成熟度、标签、星标。
- 与当前 Atlas 路线的关系和边界。

vNext 设计：

- 右侧 Detail 是摘要卡。
- 点击“展开阅读页”后变成论文对象工作页。
- 阅读页内直接对话；API 可用时调用服务端，无 key 时复制给 Codex。
- 结构化阅读字段写入 object memory。

下一步缺口：

- 阅读页对话应该能引用当前 Canvas 和 Context Cards。
- 结构化字段需要可编辑、可撤销、可重新生成。
- 论文页应显示关系邻域和相关候选更新。

## AI Studio / 工具库精华

应吸收：

- 模板化任务包。
- API key 状态检查。
- 复制优先、API 辅助。
- 运行记录。

不要吸收：

- 多页面后台式表单。
- 在主界面暴露完整配置。
- 把工具入口做成主导航。
