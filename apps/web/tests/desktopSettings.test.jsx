// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DesktopSettings } from "../src/features/settings/DesktopSettings.jsx";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

afterEach(() => {
  cleanup();
  invoke.mockReset();
  delete window.__TAURI_INTERNALS__;
});

describe("DesktopSettings", () => {
  it("saves a local OpenAI-compatible endpoint and restarts once", async () => {
    window.__TAURI_INTERNALS__ = {};
    invoke.mockImplementation((command) => {
      if (command === "get_provider_config") return Promise.resolve({
        provider: "openai",
        base_url: "https://api.openai.com/v1",
        model: "gpt-4.1-mini",
        api_format: "responses",
        web_search_provider: "",
        web_search_base_url: ""
      });
      return Promise.resolve({ mode: "desktop" });
    });
    const refreshed = vi.fn();
    const user = userEvent.setup();
    render(<DesktopSettings open onClose={vi.fn()} systemInfo={{}} secrets={{ configured: false, providers: [] }} onRuntimeRestarted={refreshed} />);

    await waitFor(() => expect(screen.getByLabelText(/^请求地址/).value).toBe("https://api.openai.com/v1"));
    await user.clear(screen.getByLabelText(/^请求地址/));
    await user.type(screen.getByLabelText(/^请求地址/), "http://127.0.0.1:8317/v1");
    await user.clear(screen.getByLabelText(/^模型/));
    await user.type(screen.getByLabelText(/^模型/), "grok-4.5");
    await user.selectOptions(screen.getByLabelText(/^API 格式/), "chat");
    await user.type(screen.getByLabelText(/^API Key/), "local-test-key");
    await user.click(screen.getByRole("button", { name: /保存并重启服务/ }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_provider_config", {
      provider: "openai",
      baseUrl: "http://127.0.0.1:8317/v1",
      model: "grok-4.5",
      apiFormat: "chat",
      secret: "local-test-key",
      webSearchProvider: "",
      webSearchBaseUrl: "",
      webSearchSecret: null
    }));
    expect(invoke.mock.calls.filter(([command]) => command === "restart_backend")).toHaveLength(0);
    expect(refreshed).toHaveBeenCalledOnce();
  });
});
