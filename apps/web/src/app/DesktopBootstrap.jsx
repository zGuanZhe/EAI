import { useCallback, useEffect, useState } from "react";
import { FolderOpen, RefreshCcw } from "lucide-react";
import { api, getRuntimeInfo, resetRuntimeInfo } from "../api.js";
import "./desktop.css";

async function invokeDesktop(command) {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke(command);
}

export function DesktopBootstrap({ children }) {
  const [state, setState] = useState({ status: "loading", message: "正在启动本地研究服务" });

  const connect = useCallback(async () => {
    setState({ status: "loading", message: "正在启动本地研究服务" });
    try {
      await getRuntimeInfo();
      await api("/health");
      setState({ status: "ready", message: "" });
    } catch (error) {
      setState({ status: "error", message: error.message || "本地研究服务启动失败" });
    }
  }, []);

  useEffect(() => { connect(); }, [connect]);

  const restart = async () => {
    try {
      if (window.__TAURI_INTERNALS__) await invokeDesktop("restart_backend");
      resetRuntimeInfo();
      await connect();
    } catch (error) {
      setState({ status: "error", message: error.message || "服务重启失败" });
    }
  };

  if (state.status === "ready") return children;
  return (
    <main className="desktop-bootstrap">
      <div className="desktop-bootstrap-mark" aria-hidden="true"><span /><span /><span /></div>
      <h1>EAI Desktop</h1>
      <p>{state.message}</p>
      {state.status === "error" && (
        <div className="desktop-bootstrap-actions">
          <button type="button" onClick={restart}><RefreshCcw size={16} />重启服务</button>
          {window.__TAURI_INTERNALS__ && (
            <button type="button" onClick={() => invokeDesktop("open_logs")}><FolderOpen size={16} />打开日志</button>
          )}
        </div>
      )}
    </main>
  );
}
