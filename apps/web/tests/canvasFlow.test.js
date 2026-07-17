import { describe, expect, it } from "vitest";
import { buildArgumentFlow, buildCampaignTree, canonicalNodeType } from "../src/features/canvas/model.js";

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
