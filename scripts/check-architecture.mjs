import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const root = resolve(import.meta.dirname, "..");
const failures = [];
const read = (path) => readFileSync(resolve(root, path), "utf8");
const lines = (path) => read(path).split(/\r?\n/).length;

for (const [path, maximum] of [
  ["apps/web/src/App.jsx", 50],
  ["services/api/app/main.py", 50],
]) {
  const count = lines(path);
  if (count > maximum) failures.push(`${path}: ${count} lines exceeds ${maximum}`);
}

const bundledPython = resolve(root, "services/api/.venv/Scripts/python.exe");
const python = existsSync(bundledPython) ? bundledPython : "python";
const pythonArchitecture = spawnSync(python, [resolve(root, "scripts/check-python-architecture.py")], {
  cwd: root,
  encoding: "utf8",
});
if (pythonArchitecture.status !== 0) {
  failures.push((pythonArchitecture.stderr || pythonArchitecture.stdout || "Python architecture check failed").trim());
}

for (const removed of [
  "apps/web/src/features/thread/useAgentRun.js",
  "apps/web/src/features/thread/useThreadChat.js",
]) {
  if (existsSync(resolve(root, removed))) failures.push(`${removed}: obsolete v1 execution hook still exists`);
}

const runtime = read("services/api/app/agent_v2/runtime.py");
for (const forbidden of ["def _specialists(", "capability_id == \"lab.create\"", "capability_id == \"lab.update\""]) {
  if (runtime.includes(forbidden)) failures.push(`agent_v2/runtime.py still contains ${forbidden}`);
}

const application = read("services/api/app/application.py");
if (application.includes("from .agent.")) failures.push("legacy imports must use app.legacy");
if (/^@(app|router)\.(get|post|put|patch|delete)/m.test(read("services/api/app/main.py"))) {
  failures.push("main.py must not contain HTTP routes");
}

function sourceFiles(directory) {
  const absolute = resolve(root, directory);
  return readdirSync(absolute, { withFileTypes: true }).flatMap((entry) => {
    const relative = `${directory}/${entry.name}`;
    return entry.isDirectory() ? sourceFiles(relative) : /\.(js|jsx|py|ps1|rs)$/.test(entry.name) ? [relative] : [];
  });
}

for (const path of sourceFiles("apps").concat(sourceFiles("services"), sourceFiles("scripts"))) {
  const content = read(path);
  if (/D:\\Test\\GUAN\\EAI-Desktop/i.test(content)) failures.push(`${path}: contains old workspace path`);
}

for (const directory of ["services/api/app/agent_v2", "services/api/app/research", "services/api/app/campaign"]) {
  for (const path of sourceFiles(directory)) {
    if (/from\s+app\.legacy|from\s+\.\.legacy|import\s+app\.legacy/.test(read(path))) {
      failures.push(`${path}: hot-path module depends on legacy compatibility code`);
    }
  }
}

const packageVersion = JSON.parse(read("package.json")).version;
const webVersion = JSON.parse(read("apps/web/package.json")).version;
const tauriVersion = JSON.parse(read("apps/desktop/src-tauri/tauri.conf.json")).version;
const cargoVersion = read("apps/desktop/src-tauri/Cargo.toml").match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (new Set([packageVersion, webVersion, tauriVersion, cargoVersion]).size !== 1 || packageVersion !== "0.4.1") {
  failures.push(`version mismatch: package=${packageVersion}, web=${webVersion}, tauri=${tauriVersion}, cargo=${cargoVersion}`);
}

const snapshotEntries = readdirSync(resolve(root, "migration/personal-snapshot"));
if (snapshotEntries.length !== 1 || snapshotEntries[0] !== ".gitkeep") {
  failures.push("migration/personal-snapshot must contain only .gitkeep");
}

if (failures.length) {
  process.stderr.write(`${failures.join("\n")}\n`);
  process.exit(1);
}
process.stdout.write("Architecture boundaries passed.\n");
