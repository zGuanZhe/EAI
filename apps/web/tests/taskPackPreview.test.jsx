// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskPackPreview } from "../src/features/taskPack/TaskPackPreview.jsx";

afterEach(cleanup);

const preview = {
  title: "Research Task Pack",
  included_cards: 1,
  token_estimate: 120,
  markdown: "# Research Task Pack",
};

describe("TaskPackPreview", () => {
  it("enables API sending for any configured model provider", () => {
    render(<TaskPackPreview preview={preview} secrets={{
      configured: true,
      providers: [{ provider: "anthropic", configured: true }],
    }} onRun={vi.fn()} />);
    expect(screen.getByRole("button", { name: /API/ }).disabled).toBe(false);
  });

  it("keeps API sending disabled without a configured provider", () => {
    render(<TaskPackPreview preview={preview} secrets={{ configured: false, providers: [] }} />);
    expect(screen.getByRole("button", { name: /API/ }).disabled).toBe(true);
  });
});
