// @vitest-environment jsdom

import React, { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaperReadingOverlay } from "../src/features/paper/PaperReadingOverlay.jsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const detail = {
  atlas: { id: "I" },
  route: { title_cn: "证据学习" },
  routeColor: "#2563eb",
  value: { id: "paper-1", title: "Evidence Boundaries", year: 2025, venue: "TestConf", summary: "A bounded summary." },
  memory: {},
  relations: [],
  knowledge: { work: null, claims: [], evidence: [] }
};

function ReaderHarness({ onClosed }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>打开论文</button>
      {open && (
        <PaperReadingOverlay
          detail={detail}
          onClose={() => {
            setOpen(false);
            onClosed();
          }}
        />
      )}
    </>
  );
}

describe("PaperReadingOverlay", () => {
  it("uses a focus-contained portal and an in-app unsaved changes confirmation", async () => {
    const user = userEvent.setup();
    const onClosed = vi.fn();
    const browserConfirm = vi.spyOn(window, "confirm");
    const { container } = render(<ReaderHarness onClosed={onClosed} />);
    const trigger = screen.getByRole("button", { name: "打开论文" });

    await user.click(trigger);
    const reader = await screen.findByRole("dialog", { name: "Evidence Boundaries" });
    expect(container.querySelector(".paper-reader")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "收起" })));

    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(reader.contains(document.activeElement)).toBe(true);

    await user.click(screen.getByRole("button", { name: "个人判断 尚未整理" }));
    await user.type(screen.getByRole("textbox"), "需要进一步核验。" );
    await user.keyboard("{Escape}");
    const confirmation = await screen.findByRole("alertdialog", { name: "笔记尚未保存" });
    expect(browserConfirm).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "继续编辑" })));
    expect(reader.inert).toBe(true);

    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(confirmation.isConnected).toBe(false);
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("button", { name: "放弃修改并关闭" }));
    expect(onClosed).toHaveBeenCalledOnce();
    await waitFor(() => expect(document.activeElement).toBe(trigger));

  });

  it("has no serious accessibility violations", async () => {
    const user = userEvent.setup();
    render(<ReaderHarness onClosed={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "打开论文" }));
    await screen.findByRole("dialog", { name: "Evidence Boundaries" });
    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact))).toEqual([]);
  });
});
