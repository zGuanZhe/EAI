// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceCreateDialog } from "../src/features/workspace/WorkspaceCreateDialog.jsx";

const atlases = [
  { id: "G", title_cn: "基础模型" },
  { id: "H", title_cn: "多模态" }
];

afterEach(() => cleanup());

describe("WorkspaceCreateDialog", () => {
  it("requires a concrete research question and preserves input after an API error", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockRejectedValue(new Error("服务暂时不可用"));
    render(
      <WorkspaceCreateDialog
        kind="thread"
        atlases={atlases}
        defaultAtlasId="G"
        projectTitle="检索综述"
        required
        onCreate={onCreate}
      />
    );

    await user.click(screen.getByRole("button", { name: "创建研究问题" }));
    expect(screen.getByRole("alert").textContent).toContain("请填写问题标题和研究问题");

    await user.type(screen.getByLabelText("问题标题"), "视觉检索证据瓶颈");
    await user.type(screen.getByLabelText("研究问题"), "哪些证据能区分索引缺失与模型能力不足？");
    await user.click(screen.getByLabelText("起始 Atlas"));
    await user.click(screen.getByRole("option", { name: "H · 多模态" }));
    await user.click(screen.getByRole("button", { name: "创建研究问题" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("服务暂时不可用"));
    expect(screen.getByLabelText("问题标题").value).toBe("视觉检索证据瓶颈");
    expect(screen.getByLabelText("研究问题").value).toBe("哪些证据能区分索引缺失与模型能力不足？");
    expect(onCreate).toHaveBeenCalledWith({
      title: "视觉检索证据瓶颈",
      goal: "哪些证据能区分索引缺失与模型能力不足？",
      atlasId: "H"
    });
  });

  it("supports Escape dismissal and has no serious accessibility violations", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <WorkspaceCreateDialog
        kind="project"
        atlases={atlases}
        defaultAtlasId="G"
        required
        onCancel={onCancel}
        onCreate={vi.fn()}
      />
    );

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "成果名称" })));
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "关闭创建窗口" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消" })).toBeTruthy();

    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact))).toEqual([]);
  });
});
