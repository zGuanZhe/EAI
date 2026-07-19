import { useEffect, useState } from "react";
import { Check, KeyRound, RefreshCcw, Trash2 } from "lucide-react";
import { api, resetRuntimeInfo } from "../../api.js";
import { Button, Drawer, FieldRow, InlineNotice, Select, StatusDot } from "../../components/ui/index.jsx";
import "./settings.css";

async function invoke(command, payload) {
  const module = await import("@tauri-apps/api/core");
  return module.invoke(command, payload);
}

export const MODEL_PROVIDER_PRESETS = {
  openai: { label: "OpenAI / 自定义兼容", baseUrl: "https://api.openai.com/v1", model: "gpt-4.1-mini", apiFormat: "responses" },
  openrouter: { label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", model: "openrouter/auto", apiFormat: "chat" },
  anthropic: { label: "Anthropic", baseUrl: "https://api.anthropic.com", model: "claude-opus-4-7", apiFormat: "anthropic" },
  gemini: { label: "Google Gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.5-flash", apiFormat: "chat" },
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat", apiFormat: "chat" },
  dashscope: { label: "阿里云百炼 / Qwen", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", apiFormat: "chat" },
  xai: { label: "xAI", baseUrl: "https://api.x.ai/v1", model: "grok-4", apiFormat: "chat" },
  groq: { label: "Groq", baseUrl: "https://api.groq.com/openai/v1", model: "openai/gpt-oss-20b", apiFormat: "chat" },
  siliconflow: { label: "SiliconFlow", baseUrl: "https://api.siliconflow.cn/v1", model: "Qwen/Qwen3-8B", apiFormat: "chat" },
  moonshot: { label: "Moonshot / Kimi", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-auto", apiFormat: "chat" },
  ollama: { label: "Ollama（本机）", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen3:8b", apiFormat: "chat" },
  lmstudio: { label: "LM Studio（本机）", baseUrl: "http://127.0.0.1:1234/v1", model: "local-model", apiFormat: "chat" }
};

const MODEL_PROVIDER_OPTIONS = Object.entries(MODEL_PROVIDER_PRESETS).map(([value, preset]) => ({ value, label: preset.label }));

function isLoopbackUrl(value) {
  try {
    const host = new URL(value).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return host === "localhost" || host === "::1" || host.startsWith("127.");
  } catch {
    return false;
  }
}

export function DesktopSettings({ open, onClose, systemInfo, secrets, onRuntimeRestarted }) {
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [model, setModel] = useState("gpt-4.1-mini");
  const [apiFormat, setApiFormat] = useState("responses");
  const [secret, setSecret] = useState("");
  const [webSearchProvider, setWebSearchProvider] = useState("");
  const [webSearchBaseUrl, setWebSearchBaseUrl] = useState("");
  const [webSearchSecret, setWebSearchSecret] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!open || !window.__TAURI_INTERNALS__) return;
    invoke("get_provider_config").then((config) => {
      setProvider(config.provider);
      setBaseUrl(config.base_url);
      setModel(config.model);
      setApiFormat(config.api_format);
      setWebSearchProvider(config.web_search_provider || "");
      setWebSearchBaseUrl(config.web_search_base_url || "");
    }).catch((error) => setStatus(`读取模型配置失败：${error}`));
  }, [open]);

  const selectProvider = (nextProvider) => {
    const preset = MODEL_PROVIDER_PRESETS[nextProvider];
    if (!preset) return;
    setProvider(nextProvider);
    setBaseUrl(preset.baseUrl);
    setModel(preset.model);
    setApiFormat(preset.apiFormat);
    setSecret("");
  };

  const refreshAfterRestart = async () => {
    resetRuntimeInfo();
    await onRuntimeRestarted?.();
  };

  const save = async () => {
    if (!window.__TAURI_INTERNALS__) return setStatus("浏览器开发模式不保存模型配置");
    if (!baseUrl.trim() || !model.trim()) return setStatus("请填写请求地址和模型");
    setStatus("正在保存配置并重启本地服务");
    try {
      await invoke("save_provider_config", {
        provider,
        baseUrl: baseUrl.trim(),
        model: model.trim(),
        apiFormat,
        secret: secret.trim() || null,
        webSearchProvider,
        webSearchBaseUrl: webSearchBaseUrl.trim(),
        webSearchSecret: webSearchSecret.trim() || null
      });
      setSecret("");
      setWebSearchSecret("");
      await refreshAfterRestart();
      setStatus("模型通道已更新");
    }
    catch (error) { setStatus(String(error)); }
  };
  const clear = async () => {
    if (!window.__TAURI_INTERNALS__) return setStatus("浏览器开发模式没有系统密钥");
    setStatus("正在清除密钥");
    try {
      await invoke("clear_provider_secret", { provider });
      await refreshAfterRestart();
      setStatus("密钥已清除");
    }
    catch (error) { setStatus(String(error)); }
  };

  const checkWebSearch = async () => {
    setStatus("正在检查网页搜索连接");
    try {
      const result = await api("/search-connectors/web/check", {
        method: "POST",
        body: JSON.stringify({})
      });
      setStatus(result.health === "healthy" ? "网页搜索连接正常" : (result.reason || "网页搜索连接不可用"));
    } catch (error) {
      setStatus(`网页搜索连接失败：${error.message || error}`);
    }
  };

  const selectedStatus = secrets?.providers?.find((item) => item.provider === provider);
  const providerPreset = MODEL_PROVIDER_PRESETS[provider] || MODEL_PROVIDER_PRESETS.openai;
  const loopbackProvider = isLoopbackUrl(baseUrl);
  const apiFormatOptions = apiFormat === "anthropic"
    ? [{ value: "anthropic", label: "Anthropic Messages" }]
    : [{ value: "chat", label: "Chat Completions" }, { value: "responses", label: "Responses API" }];

  return (
    <Drawer open={open} onClose={onClose} tone="violet" eyebrow="桌面应用" title="设置" className="settings-drawer" overlay>
      <section className="settings-group">
        <header><KeyRound size={16} /><strong>模型通道</strong><StatusDot status={secrets?.configured ? "ready" : "idle"} label={secrets?.configured ? "已配置" : "未配置"} /></header>
        <FieldRow label="Provider" hint={providerPreset.label}>
          <Select value={provider} options={MODEL_PROVIDER_OPTIONS} onChange={selectProvider} ariaLabel="Provider" tone="violet" />
        </FieldRow>
        <FieldRow label="请求地址" hint="本地 HTTP 仅限 loopback">
          <input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8000/v1" autoComplete="url" />
        </FieldRow>
        <FieldRow label="模型" hint={selectedStatus?.model ? `当前：${selectedStatus.model}` : "可填写 Provider 当前支持的任意模型 ID"}>
          <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="例如：grok-4.5" autoComplete="off" />
        </FieldRow>
        <FieldRow label="API 格式" hint="按中转能力选择">
          <Select value={apiFormat} options={apiFormatOptions} onChange={setApiFormat} ariaLabel="API 格式" tone="violet" />
        </FieldRow>
        <FieldRow label="API Key" hint={loopbackProvider ? "本机 loopback 可留空" : "留空则保留已有密钥"}>
          <input type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder={loopbackProvider ? "本机服务无需密钥时留空" : (selectedStatus ? "已安全保存" : "输入私密密钥")} />
        </FieldRow>
        <div className="settings-actions"><Button variant="primary" onClick={save}><Check size={14} />保存并重启服务</Button><Button variant="quiet" onClick={clear}><Trash2 size={14} />清除</Button></div>
        {status && <InlineNotice tone={status.includes("失败") ? "error" : "info"}>{status}</InlineNotice>}
      </section>
      <section className="settings-group">
        <header><KeyRound size={16} /><strong>通用网页搜索</strong><StatusDot status={webSearchProvider ? "ready" : "idle"} label={webSearchProvider ? "已选择" : "未启用"} /></header>
        <FieldRow label="搜索后端" hint="信息型询问会使用可用来源">
          <Select value={webSearchProvider} options={[
            { value: "", label: "不启用通用网页" },
            { value: "searxng", label: "SearXNG" },
            { value: "brave", label: "Brave Search" },
            { value: "tavily", label: "Tavily" }
          ]} onChange={(value) => {
            setWebSearchProvider(value);
            if (value === "searxng") setWebSearchBaseUrl("http://127.0.0.1:8080");
            else setWebSearchBaseUrl("");
          }} ariaLabel="搜索后端" tone="violet" />
        </FieldRow>
        {webSearchProvider === "searxng" && <FieldRow label="SearXNG 地址" hint="本地 HTTP 仅限 loopback"><input type="url" value={webSearchBaseUrl} onChange={(event) => setWebSearchBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8080" /></FieldRow>}
        {["brave", "tavily"].includes(webSearchProvider) && <FieldRow label="搜索 API Key" hint="留空则保留已保存密钥"><input type="password" autoComplete="new-password" value={webSearchSecret} onChange={(event) => setWebSearchSecret(event.target.value)} placeholder="安全保存在 Windows 凭据管理器" /></FieldRow>}
        <div className="settings-actions">
          <Button variant="quiet" onClick={checkWebSearch} disabled={!webSearchProvider}>
            <RefreshCcw size={14} />检查当前连接
          </Button>
        </div>
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
