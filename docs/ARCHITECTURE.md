# 桌面架构

## 进程边界

EAI Desktop 由 Tauri 主进程、React WebView 和 FastAPI sidecar 组成。Tauri 只负责窗口、sidecar 生命周期、应用路径和系统密钥；研究领域逻辑仍由 FastAPI 提供。

Sidecar 绑定随机的 `127.0.0.1` 端口，并通过标准输出发送 readiness 握手。Tauri 将动态 API 地址和临时会话令牌通过只读 IPC 交给 WebView。所有 `/api/vnext` 请求必须携带会话令牌。

## 数据边界

Atlas 资源只读。领域权威库位于 `%APPDATA%/EAI Desktop/data/research/research.db`；`runtime.db` 仅保存 Agent task、event、checkpoint、approval 和临时 artifact。个人线程、项目、对象记忆、候选和实验运行以 SQLite 为权威状态。每次权威写入在同一个 SQLite transaction 中追加 `projection_journal` pending 记录并提交；JSON 兼容投影由 projector 在提交后原子 replace，并在独立 SQLite transaction 中标记 applied。SQLite 与 JSON 之间不存在跨介质原子事务：JSON 失败不撤销权威提交，而是保留 pending/failed 状态供启动、健康检查或维护动作幂等重放。旧 revision 不得覆盖新 revision，删除使用 tombstone。首次运行会备份个人 JSON 并幂等导入，后续升级不会覆盖用户数据。

Research Store 分为 `curated / derived / personal` 三层。字段解析顺序固定为 `personal > curated > derived`，冲突来源保留而不互相覆盖。Atlas bundle 按文件 SHA-256 幂等导入为策展图；686 个 Work、907 个 Placement、113 条 Route 和 1556 条 Relation 保持独立语义，跨 Atlas Placement 不复制 Work。DOI、arXiv 和 OpenAlex 精确标识自动归并，模糊标题只生成身份候选。

项目、线程、Canvas、Lab 和记忆同时投影为研究状态实体与关系。Canvas 是 Question、Hypothesis、Decision、Task、Experiment、Finding 和 Artifact 的可视化视图，不再是 Agent 无法查询的孤立坐标 JSON。

## 代码依赖方向

```text
desktop -> process/path/credential management
web -> API client -> feature queries -> workspace UI state
api routers -> services -> repositories -> core storage
agent-v3 lanes -> agent-v2 compatibility graph -> capability registry -> repositories
```

模型没有直接写工具。旧回合继续读取 AgentRun v1 与 ChangeSet；新回合写入 Agent Runtime v2 task/event，并通过 OperationBatch 完成确认、冲突检查、事务写入和安全撤销。

冻结的 `ApplicationConfig` 描述数据路径、runtime 路径、服务版本和密钥候选；`ApplicationAssembly` 按配置创建独立 FastAPI 应用，并由 lifespan 管理服务生命周期。`AppServices` 按数据根和 schema 相关签名持有 Workspace、Atlas、Provider、Change Review、Thread Content、Task Pack、Agent Domain、Agent Operations、Runtime、Campaign 与 legacy read 实例，签名变化时重建依赖实例。

`application.py` 只承担默认配置、lifespan 组装、router 注册和兼容适配导出，不包含路由装饰器、Provider 协议或领域写入。Workspace、Draft、System、Atlas/Object Memory、Thread Content、Change Review、Task Pack、Research、Campaign、Agent v2 和 Legacy Read 均通过独立 router/service 边界注册。架构门禁用 AST 检查 router/service/repository 依赖方向、legacy 反向依赖、导入副作用和领域可变全局状态；文件行数只用于辅助报警。

ChangeSet 与 OperationBatch 共用 Research Store 的 canonical batch executor。执行器先校验所有 expected payload，再在一个 SQLite transaction 中写入全部权威记录和对应 `projection_journal`；任一预检或写入失败时 SQLite 零写入。提交后 projector 独立重放 JSON，投影失败只保留 pending/failed journal，不撤销权威提交。若 OperationBatch 在 research commit 后、runtime receipt 前中断，重试通过线程中的 canonical `operation_batch_id` marker 对账，禁止重复应用；`research.db`、`runtime.db` 与 JSON 之间不宣称原子事务。

## Agent v3

Agent v3 提供两个独立 lane。AskTurn 对信息型问题执行一次有界并行检索，对闲聊、改写、翻译和创作保持零检索；ResearchTask 使用 `scope -> search_map -> gather -> read -> compare -> synthesize -> guard -> report` checkpoint，并支持定向 steer、pause、resume、cancel 与显式 Campaign promotion。同一线程最多一个活跃 AskTurn 和一个活跃 ResearchTask，研究 lane 不占用询问 lane。

`ContextManifest` 按显式附件、焦点对象、线程资料、项目/对象记忆、Atlas 和外部来源的优先级记录 revision、source layer、trust 与 content hash。线程目标、最近消息和非敏感页面状态只是理解上下文，`evidence_eligible=false`；只有注册 capability 生成的 `SourceRecord/EvidenceChunk` 才能引用。SourcePolicy 在 routing、capability filter、Prompt broker 和 Evidence Guard 四层执行，未知值由 Pydantic 返回 `422` 且零联网。

通用网页能力由独立 `WebSearchProvider` 提供 SearXNG、Brave 和 Tavily adapter。`web.read` 只接受无凭据 HTTPS 公网 URL，逐跳重新验证 DNS/IP 与重定向，限制内容类型、超时和解压后 2 MB 正文；连接器状态查询不联网，显式检查动作才执行健康探测。密钥只从桌面凭据管理器注入 sidecar，不进入状态、日志或 API 响应。

能力注册表是权限真相。读取自动执行；`UICommand` 只允许前端白名单页面映射并保存 applied/dismissed receipt；项目、线程、Context、Canvas、对象记忆和候选论文等持久操作走 OperationBatch；Campaign 执行和阶段跃迁保留独立审批。模型永远不能获得 SQL、密钥、原始 Provider body、任意路径、DOM 或宿主命令。

Runtime schema 2 保存 ContextManifest、Research checkpoint 和 UICommand。启动迁移前分别在线备份 `research.db` 与 `runtime.db`；runtime 初始化失败会恢复 runtime 备份。任一数据库 schema 高于应用支持版本时，Research Store、Runtime、Workspace、Campaign 与 Agent 写边界整体只读，读取与安全预览继续可用。

## Agent Runtime v2.1

Runtime v2 使用 LangGraph 和应用自有 SQLite checkpointer。图节点固定为：

```text
intake -> route -> context_seed -> decide
-> capability_execute -> observe -> decide
-> synthesize -> evidence_guard -> policy
-> approval interrupt -> execute -> verify -> finalize
```

- 普通交流走 `conversation` 快速路径，不检索、不创建来源、ToolCall 或审计噪声。
- 研究任务由 `ServiceDecision` 选择服务和来源边界，再由模型根据清洗后的 `Observation` 逐步选择已注册能力；没有固定 Atlas + 外部调查脚本。
- 初始 Context 只包含线程目标、摘要、最近 8 条消息、本轮附件、当前页面对象和 Campaign 摘要。Canvas、长期资料、记忆、论文档案和 Campaign 详情按能力调用读取。
- 每次能力调用保存 `ToolCall` 与清洗后的 `Observation`。能力注册表绑定精确 schema、服务范围、权限、预算、超时和结果清洗器；未实现能力不暴露给模型。
- 自动 / 仅本地限制为 4 轮、8 次能力和 120 秒；深度研究限制为 8 轮、20 次能力和 300 秒。只读幂等能力最多并行 3 个，写入、Campaign 与沙箱调用串行。
- task、attempt、单调递增 event、来源关联、artifact、审批、OperationBatch、文档索引和长期记忆保存在 `%APPDATA%/EAI Desktop/data/runtime/runtime.db`。
- task 与来源采用多对多关联。同一 Atlas 论文可被多个任务复用，不覆盖旧任务审计历史。
- 运行中追加要求写为 steer 事件，在下一个安全 checkpoint 合并进原请求并重新规划。审批等待不占用线程运行槽，新回合可继续创建。
- retry 复用原用户消息和 Assistant 消息，只创建新的 `AgentAttempt`；旧 ToolCall 与 Observation 保留在 attempt 审计中。
- 服务重启将未完成 task 标记为 `interrupted`；LangGraph checkpoint 与事件 `seq` 支持恢复和重放，不重复已完成能力。
- v2 Provider 支持 Chat Completions 与 Responses tool calls。文本流使用可取消的隔离 I/O，HTTP body、URL、密钥和宿主路径不会进入事件或消息。
- 取消是单调终态。前端在用户点击后立即进入 cancelled 状态；任何晚到 Provider、工具或线程结果都必须通过 task generation/status 检查隔离，不能再写消息、来源、成功事件或业务数据。网络线程物理退出耗时只记录诊断，不作为跨 DNS、TLS 和打包环境的正确性承诺。
- `research_campaign` 已接入固定版本 AI Scientist v2 派生源码。`research.db` 保存 Campaign、选中 Idea、阶段和已晋升分支；`runtime.db` 保存临时分支、Journal checkpoint、指标、事件和审批。
- Campaign 使用 `evidence -> initial implementation -> baseline tuning -> creative research -> ablation -> writeup -> review -> release`。实验阶段保留 `draft / improve / debug` Journal 父子关系；写作、三角色审稿、两轮修订和发布校验已接入。
- 安装版由 Tauri 将 vendored AI Scientist 资源目录显式传给 sidecar。Campaign Runtime 首次使用时在隐藏后台构建固定 hash 的 CPU/CUDA Docker 镜像，模型密钥不进入容器。
- 分支会话以只读 seed、资源预算和断网策略运行；每步输出 JSONL 事件并保存上游 Journal 兼容 checkpoint。分支代码与结果形成 Git commit 和内容 hash。
- 上游依赖只进入独立 Linux Docker 镜像。sidecar 不导入上游重型依赖，也不允许上游宿主执行、任意进程清理或绕过 EAI 审批写盘。

## 来源与证据边界

统一 `SourceRecord` 区分系统记录、用户知识、Atlas 策展摘要、元数据、摘要、全文片段和网页内容。模型固有知识不算来源。

`SourcePolicy` 的精确边界如下：`none` 只允许最小线程理解上下文；`atlas_only` 仅允许 Atlas 证据；`local_only` 允许 Atlas、个人资料、本地文档与受限附件；`external_only` 允许外部检索但禁止任何本地证据进入 evidence prompt；`local_and_external` 同时允许两类来源。最小线程上下文只含线程目标、最近消息、当前页面和非敏感系统状态，不能转化为引用证据。缺省值为 `local_and_external`；旧 `local_only + requested_outputs=["atlas_only"]` 映射为 `atlas_only`；未知值返回列出允许值的 `422`，绝不静默联网。

研究回答先生成隐藏 `AnswerDraft`。`Evidence Guard` 只为真实存在且定位一致的来源生成 `[S1]`；方法、实验、结果、比较与局限要求摘要或全文的相应证据，Atlas 策展摘要只能支撑路线定位和策展判断。无依据论断会被删除、降级为推断或标记待查证。

本轮附件和长期资料先解析为显式来源：Atlas 论文引用精确卡片；其他材料标记为用户知识。随后才进行 FTS5、Atlas 和外部学术检索。外部连接器当前覆盖 OpenAlex、arXiv、Crossref，并支持可选 Semantic Scholar。开放 PDF 只允许可信 HTTPS 学术域名，且重定向后再次校验域名。

全文以 SHA-256 存入 content-addressed blob 目录。PyMuPDF 保留页码、文本块和定位信息，pypdf 作为降级。quote 使用版本化规范化：Unicode NFKC、casefold、Unicode 空白折叠、soft-hyphen 删除、仅对换行断词去连字符、标点标准化和受限 OCR 字符间空白处理；同时保存规范化 hash、页码和 RLE 字符映射。只有唯一的精确规范化匹配能成为已核验 locator，歧义匹配降级为 limited，不使用宽松模糊命中。检索按精确标识、FTS5、多语言向量、Atlas 一跳图和个人研究状态组合。量化 `multilingual-e5-small` ONNX profile 按需下载并逐文件校验固定 SHA-256，向量以 float16 保存；依赖或模型不可用时自动回退 FTS5 + 图检索。

可重新下载的全文和派生产物受 10GB LRU 限制，用户导入文件永不被缓存回收。PDF 页图只接受数据库 document ID，DPI 为 72-180，总像素不超过 16MP，并发为 2，单页有时间预算，512MB LRU 以 content hash/page/DPI 为键。Schema 升级前使用 SQLite online backup；索引 manifest 记录 schema、Atlas 快照和 embedding profile，便于完整性检查和重建。

## 权限与执行

- 读取、默认学术检索、导航和临时附件自动执行。
- Context、项目、线程、对象记忆、候选、Canvas 与 Lab 写入需要普通确认。
- 每条 Docker 命令逐次确认，并显示命令、网络、挂载、资源和超时；Docker 不可用时只保留预览，绝不回退到宿主机执行。
- 正式 Atlas、旧 bundle、旧 SQLite、密钥内容和任意宿主路径始终不可写或不可读。
- OperationBatch 应用前校验 thread revision 与真实 before 值；全部 canonical 目标和 projection journal 在同一 Research Store SQLite transaction 中提交。撤销前校验目标仍等于实际 after 值，冲突时 SQLite 零写入；JSON 投影始终在提交后独立重放。

## Canvas 与实验结果

Canvas 文件继续保存兼容的 `nodes`、`edges`、`x` 和 `y`。前端通过纯函数遍历关系图，派生竖向论证主链；旧 `material/conclusion` 分别投影为 `evidence/decision`。Campaign 分支不复制进线程 JSON，只有用户确认晋升的 finding、task 和 campaign reference 写回 Canvas。

实验结果预览返回稳定的 `apply_items`。确认接口可接收 `selected_item_ids`，只把所选阶段、产物、发现和任务写入兼容结果记录与 Canvas；省略该字段时保持旧客户端的全量确认行为。新 LabRun 不再创建；历史 LabRun 只读并可幂等迁移为 archived manual Campaign。
