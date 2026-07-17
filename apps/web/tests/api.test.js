import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiFetch, getRuntimeInfo, resetRuntimeInfo } from "../src/api.js";

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeInfo();
});

describe("desktop API client", () => {
  it("uses the browser API root outside Tauri", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/health");
    expect(fetchMock).toHaveBeenCalledWith("/api/vnext/health", expect.any(Object));
    expect((await getRuntimeInfo()).mode).toBe("browser");
  });

  it("surfaces structured API errors", async () => {
    vi.stubGlobal("window", {});
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "冲突" }), { status: 409 })));
    await expect(api("/conflict")).rejects.toMatchObject({ message: "冲突", status: 409 });
  });
});
