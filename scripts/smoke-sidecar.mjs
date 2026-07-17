import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { execFileSync, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const root = resolve(import.meta.dirname, "..");
const runtime = join(root, "runtime", "sidecar-smoke");
const token = randomUUID().replaceAll("-", "");
const readyFile = join(runtime, `ready-${token}.json`);
await mkdir(join(runtime, "personal"), { recursive: true });
await mkdir(join(runtime, "logs"), { recursive: true });

const child = spawn(join(root, "apps", "desktop", "src-tauri", "binaries", "eai-service-x86_64-pc-windows-msvc.exe"), [
  "--port", "0", "--session-token", token,
  "--atlas-cache-dir", join(root, "resources", "atlas-cache"),
  "--personal-dir", join(runtime, "personal"),
  "--log-dir", join(runtime, "logs"), "--ready-file", readyFile, "--root", root
], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });

let stderr = "";
child.stderr.setEncoding("utf8");
child.stderr.on("data", (chunk) => { stderr += chunk; });

try {
  const ready = await new Promise((resolveReady, reject) => {
    const timeout = setTimeout(() => reject(new Error("Sidecar readiness handshake timed out")), 45_000);
    let buffer = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const line = buffer.split(/\r?\n/, 1)[0];
      if (!line) return;
      try {
        const payload = JSON.parse(line);
        if (payload.event === "ready") {
          clearTimeout(timeout);
          resolveReady(payload);
        }
      } catch {}
    });
    child.once("exit", (code) => reject(new Error(`Sidecar exited early (${code}): ${stderr}`)));
    child.once("error", (error) => reject(new Error(`Sidecar failed to start: ${error.message}`)));
    const poll = setInterval(async () => {
      try {
        const { readFile } = await import("node:fs/promises");
        const payload = JSON.parse(await readFile(readyFile, "utf8"));
        if (payload.event === "ready") {
          clearInterval(poll);
          clearTimeout(timeout);
          resolveReady(payload);
        }
      } catch {}
    }, 100);
  });
  const url = `http://127.0.0.1:${ready.port}/api/vnext/health`;
  const unauthorized = await fetch(url);
  if (unauthorized.status !== 401) throw new Error(`Expected 401 without token, received ${unauthorized.status}`);
  const authorized = await fetch(url, { headers: { "X-EAI-Session": token } });
  if (!authorized.ok || !(await authorized.json()).ok) throw new Error("Authenticated health request failed");
  const preflight = await fetch(url, {
    method: "OPTIONS",
    headers: {
      Origin: "http://tauri.localhost",
      "Access-Control-Request-Method": "GET",
      "Access-Control-Request-Headers": "x-eai-session"
    }
  });
  if (!preflight.ok || preflight.headers.get("access-control-allow-origin") !== "http://tauri.localhost") {
    throw new Error("Tauri WebView CORS preflight failed");
  }
  console.log(`Sidecar smoke passed on random port ${ready.port}.`);
} finally {
  try { execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); }
  catch { child.kill(); }
}
