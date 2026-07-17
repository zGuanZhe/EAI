import { describe, expect, it } from "vitest";
import { computeAtlasPositions, computeHorizontalRevealDelta } from "../src/features/atlas/atlasLayout.js";

describe("Atlas layout", () => {
  it("returns deterministic node positions", () => {
    const papers = [
      { id: "p1", route_id: "r1", year: 2024 },
      { id: "p2", route_id: "r1", year: 2023 }
    ];
    const input = {
      papers,
      routes: [{ id: "r1" }],
      years: [2024, 2023],
      getRouteId: (paper) => paper.route_id
    };
    const first = computeAtlasPositions(input);
    const second = computeAtlasPositions(input);
    expect([...first.positions.entries()]).toEqual([...second.positions.entries()]);
  });

  it.each([
    ["keeps an already visible paper still", { containerLeft: 0, containerRight: 800, nodeLeft: 220, nodeRight: 420, leadingInset: 90, trailingInset: 24 }, 0],
    ["reveals a paper clipped on the right", { containerLeft: 0, containerRight: 800, nodeLeft: 640, nodeRight: 840, leadingInset: 90, trailingInset: 24 }, 64],
    ["reveals a paper inside the year gutter", { containerLeft: 0, containerRight: 800, nodeLeft: 54, nodeRight: 252, leadingInset: 90, trailingInset: 24 }, -36],
    ["uses an overlay inspector as the visible edge", { containerLeft: 0, containerRight: 1000, nodeLeft: 620, nodeRight: 820, leadingInset: 90, trailingInset: 24, occluderLeft: 760 }, 84],
    ["uses the resized container in push mode", { containerLeft: 0, containerRight: 620, nodeLeft: 390, nodeRight: 590, leadingInset: 90, trailingInset: 24, occluderLeft: 620 }, 0]
  ])("%s", (_label, input, expected) => {
    expect(computeHorizontalRevealDelta(input)).toBe(expected);
  });
});
