// @vitest-environment jsdom

import React, { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { Select } from "../src/components/ui/index.jsx";

const OPTIONS = [
  { value: "atlas", label: "Atlas" },
  { value: "local", label: "本地资料" },
  { value: "external", label: "外部来源" },
  { value: "disabled", label: "不可用", disabled: true }
];

function SelectFixture() {
  const [value, setValue] = useState("atlas");
  return <Select value={value} options={OPTIONS} onChange={setValue} ariaLabel="来源范围" />;
}

afterEach(() => cleanup());

describe("Select", () => {
  it("supports pointer selection in a portal and restores trigger focus", async () => {
    const user = userEvent.setup();
    render(<SelectFixture />);
    const trigger = screen.getByRole("combobox", { name: "来源范围" });
    await user.click(trigger);
    expect(screen.getByRole("listbox", { name: "来源范围" }).parentElement).toBe(document.body);
    await user.click(screen.getByRole("option", { name: "外部来源" }));
    expect(trigger.textContent).toContain("外部来源");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("supports arrows, typeahead, Escape and disabled options", async () => {
    const user = userEvent.setup();
    render(<SelectFixture />);
    const trigger = screen.getByRole("combobox", { name: "来源范围" });
    trigger.focus();
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{Enter}");
    expect(trigger.textContent).toContain("外部来源");
    await user.keyboard("a");
    expect(screen.getByRole("listbox")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.click(trigger);
    expect(screen.getByRole("option", { name: "不可用" }).disabled).toBe(true);
  });

  it("has no serious accessibility violations", async () => {
    const user = userEvent.setup();
    render(<SelectFixture />);
    await user.click(screen.getByRole("combobox"));
    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact))).toEqual([]);
  });
});
