# EAI Desktop 1.0.1

EAI Desktop 1.0.1 扩大了可连接的模型范围，并完成首次体验与 Atlas 交互优化。公共 API、SSE、Research Store schema 4、Runtime schema 2 和用户数据目录均未改变。

## 模型支持

- 新增 Anthropic 原生 Messages、流式回答和 capability tool-use，使用官方 Python SDK，并为可复用 Prompt 启用 ephemeral cache control。
- 设置页新增 OpenAI/自定义兼容、OpenRouter、Anthropic、Google Gemini、DeepSeek、阿里云百炼/Qwen、xAI、Groq、SiliconFlow、Moonshot/Kimi、Ollama 和 LM Studio 预设。
- 所有预设的请求地址和模型 ID 仍可编辑，可继续使用其他 OpenAI-compatible 服务及本地中转。
- Ollama、LM Studio 和其他严格 loopback 地址允许无 API Key；远程地址仍必须使用 HTTPS 和独立 Key。
- Task Pack 与 Agent 统一使用当前模型 Provider，不再把非 OpenAI 配置误判为不可用。

## 首次体验与界面

- 全新工作区直接显示欢迎 Composer，不再强制先创建项目；首次发送会安全地创建一个研究问题。
- 新增可跳过、可重看的四步非阻塞教学箭头。
- 原生下拉框迁移为统一的 portal Listbox，补齐键盘、焦点恢复、前缀搜索和对话框防裁切。
- 提高 Composer、设置、创建对话框、侧栏、菜单和通知等关键文字的可读性。

## Atlas

- 论文年份统一从上到下按 2026 至更早年份排列。
- 选中论文后突出关系线并淡化无关卡片，便于阅读连接关系。
- 支持 `Ctrl + 滚轮` 缩放；缩放后拖动平移、点击空白取消选择继续有效。

## 安装

从本 Release 下载 `EAI-Desktop_1.0.1_x64-setup.exe`。安装包支持 Windows 10/11 x64，当前未代码签名，Windows 可能显示 SmartScreen 提示。

```text
SHA-256  479824B3FE6A40F75D375AE78C9B4B1181A0B4C8501B6A0D145E774F5828FD9D
大小      77,584,376 bytes
```

升级会沿用 `%APPDATA%\com.eai.desktop` 中的现有数据和非秘密配置；模型密钥仍保存在 Windows Credential Manager。v1.0.x 不支持降级到实验性 0.x 包。
