// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ONBOARDING_STORAGE_KEY,
  OnboardingTour,
  readOnboardingStatus,
  writeOnboardingStatus
} from "../src/features/onboarding/OnboardingTour.jsx";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 80, y: 80, left: 80, top: 80, right: 480, bottom: 160,
    width: 400, height: 80, toJSON: () => ({})
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function TourFixture({ step = 0, workspaceReady = false, onStep = vi.fn(), onSkip = vi.fn(), onComplete = vi.fn() }) {
  return (
    <>
      <button data-onboarding="setup">设置</button>
      <div data-onboarding="composer">输入框</div>
      <div data-onboarding="mode">工作方式</div>
      <aside data-onboarding="navigation">导航</aside>
      <OnboardingTour active step={step} workspaceReady={workspaceReady} onStep={onStep} onSkip={onSkip} onComplete={onComplete} />
    </>
  );
}

describe("OnboardingTour", () => {
  it("persists versioned completion and tolerates malformed storage", () => {
    writeOnboardingStatus("completed");
    expect(readOnboardingStatus()).toEqual({ version: 1, status: "completed" });
    localStorage.setItem(ONBOARDING_STORAGE_KEY, "not-json");
    expect(readOnboardingStatus()).toBeNull();
  });

  it("shows a non-modal anchored step and can be skipped with Escape", async () => {
    const user = userEvent.setup();
    const onSkip = vi.fn();
    render(<TourFixture onSkip={onSkip} />);
    const dialog = await screen.findByRole("dialog", { name: "先确认准备状态" });
    expect(dialog.getAttribute("aria-modal")).toBe("false");
    expect(document.querySelector(".onboarding-highlight")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(onSkip).toHaveBeenCalledOnce();
  });

  it("waits for the first question before continuing to workspace steps", async () => {
    render(<TourFixture step={1} workspaceReady={false} />);
    await screen.findByRole("dialog", { name: "从一个问题开始" });
    expect(screen.getByText("发送第一个问题后，指南会自动继续。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /下一步/ })).toBeNull();
  });

  it("completes the final workspace step", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    render(<TourFixture step={3} workspaceReady onComplete={onComplete} />);
    await waitFor(() => expect(screen.getByRole("dialog", { name: "研究工作都在左侧" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "完成" }));
    expect(onComplete).toHaveBeenCalledOnce();
  });
});
