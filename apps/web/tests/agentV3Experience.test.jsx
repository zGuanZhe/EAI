// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "../src/Composer.jsx";
import { ResearchTaskShelf } from "../src/features/thread/ResearchTaskShelf.jsx";

afterEach(() => cleanup());

describe("Agent v3 interaction lanes", () => {
  it("switches between ask and research independently from source policy", async () => {
    const user = userEvent.setup();
    const changeLane = vi.fn();
    const changePolicy = vi.fn();
    render(
      <Composer
        value="compare current evidence"
        setValue={vi.fn()}
        onSubmit={(event) => event.preventDefault()}
        toolOpen={false}
        setToolOpen={vi.fn()}
        onOpenContext={vi.fn()}
        commands={[]}
        onRunCommand={vi.fn()}
        interactionMode="ask"
        onInteractionModeChange={changeLane}
        sourcePolicy="local_and_external"
        onSourcePolicyChange={changePolicy}
      />
    );

    await user.click(screen.getByRole("button", { name: "研究任务" }));
    await user.click(screen.getByRole("combobox", { name: "来源范围" }));
    await user.click(screen.getByRole("option", { name: "仅外部" }));
    expect(changeLane).toHaveBeenCalledWith("research");
    expect(changePolicy).toHaveBeenCalledWith("external_only");
  });

  it("keeps research controls targeted to one task", async () => {
    const user = userEvent.setup();
    const onSteer = vi.fn();
    const onPause = vi.fn();
    const task = { id: "research-1", objective: "Compare three methods", phase: "planning", status: "running" };
    render(<ResearchTaskShelf tasks={[task]} onSteer={onSteer} onPause={onPause} />);

    await user.type(screen.getByRole("textbox", { name: "向研究任务追加要求" }), "prioritize original tables");
    await user.click(screen.getByTitle("追加要求"));
    await user.click(screen.getByTitle("暂停研究任务"));
    expect(onSteer).toHaveBeenCalledWith("research-1", "prioritize original tables");
    expect(onPause).toHaveBeenCalledWith("research-1");
  });
});
