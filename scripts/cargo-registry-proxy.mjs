import http from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";

const port = Number(process.argv[2] || 47839);
const cacheDir = join(import.meta.dirname, "..", "runtime", "cargo-registry-cache");
await mkdir(cacheDir, { recursive: true });

async function fetchWithRetry(url) {
  let lastError;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { "User-Agent": "eai-desktop-cargo-proxy/1" },
        signal: AbortSignal.timeout(30_000)
      });
      if (response.ok) {
        const body = Buffer.from(await response.arrayBuffer());
        const expected = Number(response.headers.get("content-length") || body.length);
        if (body.length !== expected) throw new Error(`Partial response: ${body.length}/${expected}`);
        return { response, body };
      }
      lastError = new Error(`Upstream returned ${response.status}`);
    } catch (error) { lastError = error; }
    await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1)));
  }
  throw lastError;
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.url === "/config.json") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ dl: `http://127.0.0.1:${port}/crates`, api: "https://crates.io" }));
      return;
    }
    const crate = request.url.match(/^\/crates\/([^/]+)\/([^/]+)\/download$/);
    const target = crate
      ? `https://static.crates.io/crates/${crate[1]}/${crate[1]}-${crate[2]}.crate`
      : `https://index.crates.io${request.url}`;
    const cachePath = join(cacheDir, createHash("sha256").update(target).digest("hex"));
    try {
      const cached = await readFile(cachePath);
      response.end(cached);
      return;
    } catch {}
    const { response: upstream, body } = await fetchWithRetry(target);
    response.statusCode = upstream.status;
    for (const name of ["content-type", "content-length", "etag", "cache-control", "last-modified"]) {
      const value = upstream.headers.get(name);
      if (value) response.setHeader(name, value);
    }
    await writeFile(cachePath, body);
    response.end(body);
  } catch (error) {
    response.statusCode = 502;
    response.end(String(error));
  }
});

server.listen(port, "127.0.0.1", () => console.log(`Cargo registry proxy listening on ${port}`));
