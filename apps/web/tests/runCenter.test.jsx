// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunCenter } from "../src/features/tools/RunCenter.jsx";

afterEach(() => cleanup());

describe("RunCenter actions", () => {
  it("opens pending changes and keeps status-only maintenance rows non-interactive", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const onRefreshKnowledge = vi.fn();
    const { container } = render(
      <RunCenter
        thread={{
          tool_runs: [],
          changesets: [{ id: "change-1", summary: "更新研究目标", status: "pending", risk: "normal", operations: [{}] }]
        }}
        knowledgeStatus={{ coverage: {}, counts: {}, active_jobs: [], cache: {}, embedding: { ready: true } }}
        onAction={onAction}
        onRefreshKnowledge={onRefreshKnowledge}
      />
    );

    await user.click(screen.getByRole("button", { name: /更新研究目标/ }));
    expect(onAction).toHaveBeenCalledWith("changeset", "change-1");

    await user.click(screen.getByRole("button", { name: /质量与 Bundle 健康/ }));
    expect(onRefreshKnowledge).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: /模型通道/ }));
    expect(onAction).toHaveBeenCalledWith("settings");

    const backupRow = [...container.querySelectorAll(".maintenance-list .ui-activity-row")]
      .find((node) => node.textContent.includes("个人数据备份状态"));
    expect(backupRow?.tagName).toBe("DIV");
  });
});
