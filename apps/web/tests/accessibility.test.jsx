// @vitest-environment jsdom

import React from "react";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "../src/Composer.jsx";


describe("read-only workspace controls", () => {
  it("disables message mutations and has no serious axe violations", async () => {
    const { container } = render(
      <Composer
        value="unsent research note"
        setValue={vi.fn()}
        onSubmit={vi.fn()}
        toolOpen={false}
        setToolOpen={vi.fn()}
        onOpenContext={vi.fn()}
        commands={[]}
        onRunCommand={vi.fn()}
        readOnly
      />
    );

    expect(screen.getByRole("textbox").disabled).toBe(true);
    expect(screen.getByRole("combobox", { name: "本轮工作方式" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "发送" }).disabled).toBe(true);

    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact))).toEqual([]);
  });
});
