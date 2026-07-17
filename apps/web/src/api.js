const browserApiRoot = import.meta.env.VITE_EAI_API_ROOT || "/api/vnext";

let runtimePromise;

async function loadRuntime() {
  if (!window.__TAURI_INTERNALS__) {
    return { api_root: browserApiRoot, session_token: "", mode: "browser" };
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("get_runtime_info");
}

export function getRuntimeInfo() {
  if (!runtimePromise) runtimePromise = loadRuntime();
  return runtimePromise;
}

export function resetRuntimeInfo() {
  runtimePromise = undefined;
}

export async function apiFetch(path, options = {}) {
  const runtime = await getRuntimeInfo();
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) headers.set("Content-Type", "application/json");
  if (runtime.session_token) headers.set("X-EAI-Session", runtime.session_token);
  return fetch(`${runtime.api_root}${path}`, { ...options, headers });
}

export async function api(path, options = {}) {
  const response = await apiFetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const payload = JSON.parse(text);
      detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message || payload.message || text;
    } catch {
      // Keep the server text when the response is not JSON.
    }
    const error = new Error(detail || response.statusText);
    error.status = response.status;
    error.payload = text;
    throw error;
  }
  return response.json();
}
