// @vitest-environment jsdom

import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAtlasServerState } from "../src/features/workspace/useWorkspaceServerState.js";

const pending = new Map();

vi.mock("../src/api.js", () => ({
  api: vi.fn((path) => new Promise((resolve) => pending.set(path, resolve)))
}));

function Harness({ atlasId }) {
  const { bundleQuery } = useAtlasServerState(atlasId);
  return <output>{bundleQuery.data?.atlas_id || "loading"}</output>;
}

function renderHarness(atlasId) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Harness atlasId={atlasId} />
    </QueryClientProvider>
  );
  return {
    ...view,
    rerenderAtlas(nextAtlasId) {
      view.rerender(
        <QueryClientProvider client={queryClient}>
          <Harness atlasId={nextAtlasId} />
        </QueryClientProvider>
      );
    }
  };
}

afterEach(() => {
  pending.clear();
  cleanup();
});

describe("workspace Atlas server state", () => {
  it("does not let a late response from the previous Atlas replace the active Atlas", async () => {
    const view = renderHarness("A");
    view.rerenderAtlas("B");

    pending.get("/atlases/B/bundle")({ atlas_id: "B" });
    expect(await screen.findByText("B")).toBeTruthy();

    pending.get("/atlases/A/bundle")({ atlas_id: "A" });
    expect(screen.getByText("B")).toBeTruthy();
  });
});
