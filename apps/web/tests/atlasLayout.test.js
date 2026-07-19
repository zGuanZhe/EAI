import { describe, expect, it } from "vitest";
import {
  computeAtlasPositions,
  computeHorizontalRevealDelta,
  nextAtlasZoom,
  sortPublicationYearsDescending
} from "../src/features/atlas/atlasLayout.js";

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

  it("orders mixed year formats from newest to oldest and keeps unknown years last", () => {
    expect(sortPublicationYearsDescending([2019, "unknown", "----", "published 2023", "2020-2022", "2026", 2019])).toEqual([
      "2026",
      "published 2023",
      "2020-2022",
      "2019",
      "unknown",
      "----"
    ]);
  });

  it("places numeric and string representations of the same year in one row", () => {
    const layout = computeAtlasPositions({
      papers: [
        { id: "number", title: "Number", route_id: "r1", year: 2026 },
        { id: "string", title: "String", route_id: "r1", year: "2026" }
      ],
      routes: [{ id: "r1" }],
      years: ["2026"],
      getRouteId: (paper) => paper.route_id
    });
    expect(layout.yearRows).toHaveLength(1);
    expect([...layout.positions.keys()]).toEqual(["number", "string"]);
  });

  it("changes Atlas zoom in fixed steps and clamps its range", () => {
    expect(nextAtlasZoom(1, -120)).toBe(1.1);
    expect(nextAtlasZoom(1, 120)).toBe(0.9);
    expect(nextAtlasZoom(1.4, -120)).toBe(1.4);
    expect(nextAtlasZoom(0.75, 120)).toBe(0.75);
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
