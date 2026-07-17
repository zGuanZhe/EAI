import { readdir, readFile, stat } from "node:fs/promises";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const blocked = ["鍒", "鏂", "涓", "瀵", "閺", "閸", "娑", "鐎", "濞", "缁", "灏", "�"];
const blockedFragments = ["鈥?", "銆?", "锛?", "妫€", "绾跨▼", "鐮旂┒", "璁烘枃"];
const roots = [
  "apps/web/src",
  "apps/web/tests",
  "services/api/app",
  "services/api/tests",
  "scripts",
  "docs",
  "README.md",
  "AGENTS.md"
].map((path) => join(projectRoot, path));
const allowedExtensions = new Set([".js", ".jsx", ".mjs", ".css", ".py", ".ps1", ".rs", ".md", ".json"]);
const ignoredDirectories = new Set([
  ".git", ".venv", "node_modules", "dist", "target", "build", "runtime",
  "artifacts", "resources", "__pycache__", ".pytest_cache", "test-results"
]);

async function walk(path, files = [], extensions = allowedExtensions) {
  const info = await stat(path);
  if (info.isFile()) {
    if (extensions.has(extname(path))) files.push(path);
    return files;
  }
  for (const entry of await readdir(path, { withFileTypes: true })) {
    if (ignoredDirectories.has(entry.name)) continue;
    await walk(join(path, entry.name), files, extensions);
  }
  return files;
}

let failed = false;
const files = new Set();
for (const root of roots) for (const file of await walk(root)) files.add(file);
for (const file of await walk(projectRoot, [], new Set([".md"]))) files.add(file);
for (const file of files) {
    const text = await readFile(file, "utf8");
    for (const token of [...blocked, ...blockedFragments]) {
      if (text.includes(token)) {
        console.error(`Mojibake fragment "${token}" found in ${file.slice(projectRoot.length + 1)}`);
        failed = true;
      }
    }
}

if (failed) process.exit(1);
console.log("UTF-8 source check passed.");
