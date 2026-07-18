# EAI Desktop

EAI Desktop 是一个本地优先的个人科研工作台。它把日常问答、证据调查、论文与 Atlas、研究 Canvas，以及实验 Campaign 放在同一个可审计工作流中。

当前正式版本：`1.0.0`。支持 Windows 10/11 x64。

## 下载

从 [GitHub Releases](https://github.com/zGuanZhe/EAI/releases/latest) 下载 `EAI-Desktop_1.0.0_x64-setup.exe`。

安装包当前未进行代码签名，Windows 可能显示 SmartScreen 提示。应用数据保存在 `%APPDATA%\com.eai.desktop`，模型密钥保存在 Windows Credential Manager。

## EAI 能做什么

- **询问**：回答日常问题。信息型问题会在来源策略允许的范围内并行检索线程资料、Atlas、本地文档、学术源和已配置网页源；闲聊、改写、翻译和创作不会进行无意义搜索。
- **研究任务**：在后台执行界定问题、检索、阅读、比较、引用核验和报告生成。任务运行时，Composer 仍可继续普通询问。
- **研究 Campaign**：把已有证据和可检验假设升级为实验、写作、审稿与发布闭环。实验执行必须经过审批，并且只在 Docker 沙箱中运行。
- **系统操作**：Agent 可调用已注册的 EAI 领域能力。读取自动执行；持久修改先形成 OperationBatch 预览，由用户确认后提交，并支持冲突安全撤销。

EAI 不向 Agent 暴露 SQL、数据库文件、密钥、任意宿主路径、DOM 或宿主命令。模型固有知识不算证据；只有实际转化为 `SourceRecord` / `EvidenceChunk` 的内容才能被引用。

完整产品说明见 [docs/INTRODUCTION.md](docs/INTRODUCTION.md)。

## 模型与搜索配置

桌面设置支持 OpenAI-compatible Provider，包括本机 HTTP loopback 中转和 HTTPS 远程端点。模型通道本身不等于网页搜索：通用网页检索需要单独配置 SearXNG、Brave 或 Tavily。

1. 从左侧导航打开“设置”。
2. 在“模型 Provider”中选择 OpenAI-compatible，填写 Base URL、模型名称、API 格式和 Key。本机中转可使用 `http://127.0.0.1:8000/v1` 这类 loopback 地址；非本机地址必须使用 HTTPS。普通 `/chat/completions` 中转选择 Chat Completions；实现 `/responses` 的端点选择 Responses API。OpenRouter 预设使用 Chat Completions。
3. 保存后，桌面端会重启 sidecar 并重新获取运行时状态，不需要刷新页面。
4. 保存后发送一个简单询问验证模型连接。`401/403` 通常表示 Key 或上游权限错误，`404` 通常表示 Base URL/API 格式不匹配，模型不存在错误应检查模型名称。
5. 如需通用网页搜索，在“网页搜索”中选择 SearXNG、Brave 或 Tavily，然后点击“检查连接”。SearXNG 填写实例根地址（EAI 会请求其 `/search` JSON 接口），本地实例可用 `http://127.0.0.1:8080` 且不需要 Key；远程实例必须 HTTPS。Brave 与 Tavily 使用官方默认 API 地址，只需填写各自控制台签发的 API Key。连接失败会显示原因，不会由模型模拟成功。

- Provider Base URL、模型和 API 格式属于非秘密配置。
- API Key 只进入 Windows Credential Manager。
- 未配置网页连接器时，系统会明确显示降级状态，不会假装已经联网搜索。
- `external_only` 等 SourcePolicy 在规划、执行、Prompt 装配和 Evidence Guard 四层约束。

Composer 的来源控件用于选择当前轮 SourcePolicy，默认是“全部可用来源”。发送前的执行摘要会显示本轮实际允许的上下文、来源与修改边界。Run Center 的“Agent 能力”视图列出当前可读取、可建议、需确认写入、需批准执行和不可用的注册能力及连接器状态。

### 远程数据边界

- 配置远程模型时，模型端点会收到完成本轮任务所需的消息、允许的上下文和已经筛选的证据；不会收到 Credential Manager 中的密钥、SQL、数据库文件或任意宿主路径。
- 学术与网页搜索连接器会收到检索词；网页读取目标站点会收到正常的 HTTPS 请求。第三方服务的保留和隐私政策由对应服务提供方控制。
- 选择 `none` 可禁止证据检索；选择 `local_only` 或 `atlas_only` 可阻止外部搜索。`external_only` 仍允许最小线程上下文帮助理解请求，但禁止本地材料进入可引用证据 Prompt。
- SourcePolicy 控制证据来源和搜索，不会阻止对已配置远程模型的推理请求。敏感内容必须完全留在设备内时，应使用本机 loopback 模型 Provider，而不是仅切换 SourcePolicy。

## 技术结构

```text
apps/web                    React 19 + Vite 7 产品界面
apps/desktop/src-tauri      Tauri 2 Windows 桌面壳
services/api                FastAPI sidecar 与领域服务
resources/atlas-cache       只读 Atlas 资源
third_party/ai-scientist-v2 固定版本的 AI Scientist v2 派生组件
scripts                     启动、验证和发布脚本
tests                       合成迁移与跨版本 fixture
docs                        产品、架构、迁移和验证文档
```

SQLite 是权威状态。JSON 仅是通过 `projection_journal` 幂等重放的兼容投影；JSON 投影失败不会撤销已经提交的 SQLite 事务，也不会被描述成跨介质原子事务。

## 本地开发

需要 Node.js、Python、Rust 与 Windows WebView2。完整 Campaign 执行还需要 Docker Desktop。

```powershell
git clone https://github.com/zGuanZhe/EAI.git
cd EAI
npm ci
npm run bootstrap
npm run dev
```

日常验证：

```powershell
npm run verify
```

构建并验证 Windows 安装包：

```powershell
npm run desktop:build
npm run desktop:smoke
```

安装包生成于 `apps/desktop/src-tauri/target/release/bundle/nsis/`。自动化测试只使用 `runtime/` 或操作系统临时目录，不读取真实用户数据。

## 安全与限制

- 应用仅绑定随机 loopback 端口，并用每次启动生成的 `X-EAI-Session` 令牌保护本地 API。
- Provider Key 不进入日志、事件、前端存储或 Git。
- 网页读取拒绝私网地址、凭据 URL、本地文件、危险重定向和超限正文。
- Docker 不可用时只生成执行预览，绝不回退到宿主执行。
- Evidence Guard 核验的是引用定位和证据充分性，不承诺证明事实本身绝对真实。
- v1.0 不提供向实验版 0.x 的官方降级；升级前应备份 `%APPDATA%\com.eai.desktop`。

### 备份与恢复

退出 EAI Desktop 并确认 sidecar 已结束后，复制整个 `%APPDATA%\com.eai.desktop` 目录即可形成一致的离线备份。恢复时保持应用关闭，并恢复同一主版本生成的完整目录；不要用旧程序打开并写入较新的 schema。数据库迁移自身还会创建 SQLite 在线备份，但它不能替代用户维护的 AppData 备份。

### 校验安装包

Release 页面同时公布安装包 SHA-256。下载后可在 PowerShell 中运行：

```powershell
Get-FileHash -Algorithm SHA256 '.\EAI-Desktop_1.0.0_x64-setup.exe'
```

输出必须与 Release Notes 完全一致。安装包未签名；只应从 `github.com/zGuanZhe/EAI` 的正式 Release 下载，不应忽略来自其他来源的 SmartScreen 或下载警告。

## 文档

- [产品介绍](docs/INTRODUCTION.md)
- [产品定义](docs/PRODUCT.md)
- [架构](docs/ARCHITECTURE.md)
- [迁移与数据安全](docs/MIGRATION.md)
- [验证记录](docs/VALIDATION.md)
- [开发交接](docs/HANDOFF.md)
- [打包说明](docs/DESKTOP_PACKAGING.md)

## 许可证

EAI Desktop 源代码采用 [MIT License](LICENSE)。`third_party/ai-scientist-v2` 保留其上游许可证与派生说明，分发时必须一并保留。
