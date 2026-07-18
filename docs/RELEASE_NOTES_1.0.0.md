# EAI Desktop 1.0.0

EAI Desktop 1.0.0 是本地优先科研工作台的首个正式公开版本，并替代本仓库此前的实验性 Atlas 版本。

## 主要能力

- Agent v3 将日常询问（AskTurn）与后台研究任务（ResearchTask）分成两个独立通道。
- 信息型询问会在全部允许且可用的本地、Atlas、学术和网页来源上执行有界检索。
- 研究任务支持检查点进度、定向追加要求、暂停、恢复和取消，普通 Composer 始终可用。
- ContextManifest 记录每轮实际可见的上下文、revision、信任层与内容 hash。
- Evidence Guard 在引用定位或证据不足时 fail closed。
- 注册能力提供 EAI 读取、确认式持久写入、受限 UI 导航和批准后的 Docker 执行，不暴露 SQL、密钥或宿主控制权。
- Research Store schema 4 以 SQLite 为权威状态，并通过可重放 projection journal 最终一致地维护 JSON 兼容投影。
- 桌面设置支持 OpenAI-compatible 模型端点，以及独立的 SearXNG、Brave 和 Tavily 网页搜索连接器。

## 安装

从本 Release 下载 `EAI-Desktop_1.0.0_x64-setup.exe`，在 Windows 10/11 x64 上运行。安装包当前未签名，Windows 可能显示 SmartScreen 提示。

应用数据位于 `%APPDATA%\com.eai.desktop`，Provider Key 位于 Windows Credential Manager。变更主版本前请备份完整 AppData；不支持降级到实验性 0.x 包。

安装包大小：`75,909,325` 字节。

SHA-256：`7B88CDD85518872FFF9197190E59E91BFFF9953A13D239D9C8B0B6FFED80D61E`

校验命令：

```powershell
Get-FileHash -Algorithm SHA256 '.\EAI-Desktop_1.0.0_x64-setup.exe'
```

## 验证

- 26 项前端测试
- 102 项服务测试
- Playwright 桌面、中等和窄屏 E2E
- 架构、UTF-8 和 diff 门禁
- Campaign Docker smoke，且无宿主回退
- PyInstaller sidecar 随机端口与鉴权 smoke
- Rust fmt、Clippy warnings denied 和 5 项测试
- Tauri release 与隔离 NSIS 安装、启动、正常关闭、卸载 smoke

## 已知限制

- 仅支持 Windows x64。
- 安装包未代码签名，暂无自动更新。
- 完整 Campaign 执行需要 Docker Desktop。
- 通用网页搜索需要单独配置 SearXNG、Brave 或 Tavily。
- Evidence Guard 核验引用完整性和证据充分性，不等同于证明所有引用结论绝对真实。

## 许可证

EAI Desktop 使用 MIT License。内置的 AI Scientist v2 派生组件保留上游许可证和 EAI 派生说明。
