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
    await user.type(screen.getByLabelText(/^请求地址/), "http://127.0.0.1:8000/v1");
    await user.clear(screen.getByLabelText(/^模型/));
    await user.type(screen.getByLabelText(/^模型/), "grok-4.5");
    await user.click(screen.getByLabelText(/^API 格式/));
    await user.click(screen.getByRole("option", { name: "Chat Completions" }));
    await user.type(screen.getByLabelText(/^API Key/), "local-test-key");
    await user.click(screen.getByRole("button", { name: /保存并重启服务/ }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_provider_config", {
      provider: "openai",
      baseUrl: "http://127.0.0.1:8000/v1",
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

  it("switches to native Anthropic Messages with an isolated credential", async () => {
    window.__TAURI_INTERNALS__ = {};
    invoke.mockImplementation((command) => command === "get_provider_config" ? Promise.resolve({
      provider: "openai", base_url: "https://api.openai.com/v1", model: "gpt-4.1-mini",
      api_format: "responses", web_search_provider: "", web_search_base_url: ""
    }) : Promise.resolve({ mode: "desktop" }));
    const user = userEvent.setup();
    render(<DesktopSettings open onClose={vi.fn()} systemInfo={{}} secrets={{ configured: false, providers: [] }} onRuntimeRestarted={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText(/^Provider/)).toBeTruthy());
    await user.click(screen.getByLabelText(/^Provider/));
    await user.click(screen.getByRole("option", { name: "Anthropic" }));
    expect(screen.getByLabelText(/^请求地址/).value).toBe("https://api.anthropic.com");
    expect(screen.getByLabelText(/^模型/).value).toBe("claude-opus-4-7");
    expect(screen.getByLabelText(/^API 格式/).getAttribute("data-value")).toBe("anthropic");
    await user.type(screen.getByLabelText(/^API Key/), "anthropic-test-key");
    await user.click(screen.getByRole("button", { name: /保存并重启服务/ }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_provider_config", expect.objectContaining({
      provider: "anthropic", baseUrl: "https://api.anthropic.com", model: "claude-opus-4-7",
      apiFormat: "anthropic", secret: "anthropic-test-key"
    })));
  });

  it("allows a keyless Ollama loopback preset", async () => {
    window.__TAURI_INTERNALS__ = {};
    invoke.mockImplementation((command) => command === "get_provider_config" ? Promise.resolve({
      provider: "openai", base_url: "https://api.openai.com/v1", model: "gpt-4.1-mini",
      api_format: "responses", web_search_provider: "", web_search_base_url: ""
    }) : Promise.resolve({ mode: "desktop" }));
    const user = userEvent.setup();
    render(<DesktopSettings open onClose={vi.fn()} systemInfo={{}} secrets={{ configured: false, providers: [] }} onRuntimeRestarted={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText(/^Provider/)).toBeTruthy());
    await user.click(screen.getByLabelText(/^Provider/));
    await user.click(screen.getByRole("option", { name: "Ollama（本机）" }));
    expect(screen.getByLabelText(/^请求地址/).value).toBe("http://127.0.0.1:11434/v1");
    expect(screen.getByLabelText(/^API Key/).getAttribute("placeholder")).toBe("本机服务无需密钥时留空");
    await user.click(screen.getByRole("button", { name: /保存并重启服务/ }));

    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_provider_config", expect.objectContaining({
      provider: "ollama", secret: null, apiFormat: "chat"
    })));
  });
});
