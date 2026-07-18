import { useState } from "react";
import { Check, KeyRound, Trash2 } from "lucide-react";
import { resetRuntimeInfo } from "../../api.js";
import { Button, Drawer, FieldRow, InlineNotice, StatusDot } from "../../components/ui/index.jsx";
import "./settings.css";

async function invoke(command, payload) {
  const module = await import("@tauri-apps/api/core");
  return module.invoke(command, payload);
}

export function DesktopSettings({ open, onClose, systemInfo, secrets, onRuntimeRestarted }) {
  const [provider, setProvider] = useState("openrouter");
  const [secret, setSecret] = useState("");
  const [status, setStatus] = useState("");

  const restartService = async () => {
    setStatus("正在重启本地服务");
    await invoke("restart_backend");
    resetRuntimeInfo();
    await onRuntimeRestarted?.();
    setStatus("模型通道已更新");
  };
  const save = async () => {
    if (!window.__TAURI_INTERNALS__) return setStatus("浏览器开发模式不保存系统密钥");
    if (!secret.trim()) return setStatus("请输入密钥");
    setStatus("正在写入 Windows Credential Manager");
    try { await invoke("set_provider_secret", { provider, secret: secret.trim() }); setSecret(""); await restartService(); }
    catch (error) { setStatus(String(error)); }
  };
  const clear = async () => {
    if (!window.__TAURI_INTERNALS__) return setStatus("浏览器开发模式没有系统密钥");
    setStatus("正在清除密钥");
    try { await invoke("clear_provider_secret", { provider }); await restartService(); }
    catch (error) { setStatus(String(error)); }
  };

  return (
    <Drawer open={open} onClose={onClose} tone="violet" eyebrow="桌面应用" title="设置" className="settings-drawer" overlay>
      <section className="settings-group">
        <header><KeyRound size={16} /><strong>模型通道</strong><StatusDot status={secrets?.configured ? "ready" : "idle"} label={secrets?.configured ? "已配置" : "未配置"} /></header>
        <FieldRow label="Provider" hint="OpenAI-compatible">
          <select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="openrouter">OpenRouter</option><option value="openai">OpenAI</option></select>
        </FieldRow>
        <FieldRow label="API Key" hint="仅写入系统凭据">
          <input type="password" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder="输入私密密钥" />
        </FieldRow>
        <div className="settings-actions"><Button variant="primary" onClick={save}><Check size={14} />保存并重启服务</Button><Button variant="quiet" onClick={clear}><Trash2 size={14} />清除</Button></div>
        {status && <InlineNotice tone={status.includes("失败") ? "error" : "info"}>{status}</InlineNotice>}
      </section>
      <section className="settings-group runtime-boundaries">
        <header><strong>当前数据边界</strong></header>
        <FieldRow label="运行模式"><code>{systemInfo?.runtime_mode || "browser"}</code></FieldRow>
        <FieldRow label="个人数据"><code>{systemInfo?.personal_dir || "正在读取"}</code></FieldRow>
        <FieldRow label="Atlas"><code>{systemInfo?.atlas_cache_dir || "正在读取"}</code></FieldRow>
      </section>
    </Drawer>
  );
}
