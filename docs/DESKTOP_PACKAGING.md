# Windows 打包

首版使用 Tauri v2 NSIS current-user 安装包和 PyInstaller 单文件 FastAPI sidecar。

构建顺序固定为：乱码检查、前端测试、前端构建、sidecar 构建、Rust/Tauri 构建。构建机需要 Node.js、Python 3.13（优先；当前已验证 3.14 兼容回退）、Rust MSVC 和 WebView2。

首版不启用代码签名和自动更新。正式分发前必须补充签名证书、升级回滚和安装包来源校验。
