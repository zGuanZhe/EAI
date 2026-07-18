import { describe, expect, it } from "vitest";
import { buildArgumentFlow, buildCampaignTree, canonicalNodeType, getCampaignNextStep } from "../src/features/canvas/model.js";

describe("buildArgumentFlow", () => {
  it("orders a vertical argument chain and keeps branch depth", () => {
    const nodes = [
      { id: "q", type: "question", title: "Q" },
      { id: "h", type: "hypothesis", title: "H" },
      { id: "m", type: "material", title: "M" },
      { id: "c", type: "conclusion", title: "C" },
      { id: "t", type: "task", title: "T" }
    ];
    const edges = [
      { id: "e1", source: "q", target: "h" },
      { id: "e2", source: "h", target: "m" },
      { id: "e3", source: "h", target: "c" },
      { id: "e4", source: "c", target: "t" }
    ];
    const flow = buildArgumentFlow(nodes, edges);
    expect(flow.items.map((item) => item.node.id)).toEqual(["q", "h", "m", "c", "t"]);
    expect(flow.items.map((item) => item.depth)).toEqual([0, 1, 2, 2, 3]);
  });

  it("reports cycles and disconnected nodes without repeating them", () => {
    const nodes = [{ id: "q", type: "question" }, { id: "h", type: "hypothesis" }, { id: "x", type: "task" }];
    const edges = [{ id: "a", source: "q", target: "h" }, { id: "b", source: "h", target: "q" }];
    const flow = buildArgumentFlow(nodes, edges);
    expect(flow.items).toHaveLength(3);
    expect(flow.cycleEdges).toEqual(["b"]);
    expect(flow.orphanIds).toEqual(["x"]);
  });
});

describe("buildCampaignTree", () => {
  it("keeps draft, improve and debug lineage in stable lanes", () => {
    const branches = [
      { id: "draft-a", stage_id: "stage-1", parent_id: null, status: "succeeded" },
      { id: "draft-b", stage_id: "stage-1", parent_id: null, status: "failed" },
      { id: "improve-a", stage_id: "stage-1", parent_id: "draft-a", status: "succeeded" },
      { id: "debug-b", stage_id: "stage-1", parent_id: "draft-b", status: "proposed" }
    ];
    const tree = buildCampaignTree(branches, "stage-1", "improve-a");
    expect(tree.items.map((item) => item.branch.id)).toEqual(["draft-a", "improve-a", "draft-b", "debug-b"]);
    expect(tree.items.find((item) => item.branch.id === "improve-a").depth).toBe(1);
    expect(tree.bestPathIds.sort()).toEqual(["draft-a", "improve-a"]);
  });

  it("filters discarded and other-stage branches", () => {
    const tree = buildCampaignTree([
      { id: "visible", stage_id: "stage-1", status: "proposed" },
      { id: "discarded", stage_id: "stage-1", status: "discarded" },
      { id: "other", stage_id: "stage-2", status: "proposed" }
    ], "stage-1");
    expect(tree.items.map((item) => item.branch.id)).toEqual(["visible"]);
  });
});

describe("canonicalNodeType", () => {
  it("keeps legacy Canvas aliases readable", () => {
    expect(canonicalNodeType("material")).toBe("evidence");
    expect(canonicalNodeType("conclusion")).toBe("decision");
    expect(canonicalNodeType("finding")).toBe("finding");
  });
});

describe("getCampaignNextStep", () => {
  const campaign = {
    id: "campaign-1",
    status: "running",
    current_stage_id: "stage-2",
    stages: [{ id: "stage-2", kind: "initial_implementation", title: "初始实现" }]
  };

  it("prioritizes an approval over other branch work", () => {
    const step = getCampaignNextStep(
      { campaign, branches: [{ id: "branch-1", stage_id: "stage-2", status: "proposed" }] },
      { payload: { campaign_id: "campaign-1", branch_id: "branch-1" } }
    );
    expect(step.action).toBe("inspect_branch");
    expect(step.branchId).toBe("branch-1");
    expect(step.label).toBe("检查执行授权");
  });

  it("moves successful experiments toward explicit review and stage advance", () => {
    const step = getCampaignNextStep({
      campaign,
      branches: [{ id: "branch-1", stage_id: "stage-2", status: "succeeded" }]
    });
    expect(step.action).toBe("inspect_branch");
    expect(step.label).toBe("检查并推进");
  });

  it("routes writeup and review stages to their real workflow actions", () => {
    const writeup = getCampaignNextStep({
      campaign: { ...campaign, current_stage_id: "stage-6", stages: [{ id: "stage-6", kind: "writeup", title: "写作" }] },
      branches: [],
      manuscripts: []
    });
    expect(writeup.action).toBe("generate_manuscript");

    const review = getCampaignNextStep({
      campaign: { ...campaign, current_stage_id: "stage-7", current_manuscript_id: "manuscript-1", stages: [{ id: "stage-7", kind: "review", title: "审稿" }] },
      branches: [],
      manuscripts: [{ id: "manuscript-1" }]
    });
    expect(review.action).toBe("start_review");
    expect(review.manuscriptId).toBe("manuscript-1");
  });
});
