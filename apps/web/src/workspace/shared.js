export const FALLBACK_COLORS = [
  "#64748b", "#b45309", "#0d9669", "#7c3aed", "#1d4ed8",
  "#dc2626", "#0891b2", "#7f1d1d", "#0369a1", "#4d7c0f"
];

export function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function tokenEstimate(text = "") {
  return Math.max(120, Math.ceil(text.length / 3));
}

export function cardKey(card) {
  return `${card.type}:${JSON.stringify(card.source_ref || {})}`;
}

export function objectMemoryKey(type, id) {
  return type && id ? `${type}:${id}` : "";
}

export function objectRefFromDetail(detail, activeAtlas) {
  if (!detail) return null;
  if (detail.type === "paper") {
    return { atlasId: activeAtlas, type: "paper", id: detail.value.id, title: detail.value.title };
  }
  if (detail.type === "relation") {
    return {
      atlasId: activeAtlas,
      type: "relation",
      id: detail.value.id,
      title: `${detail.source?.title || detail.value.source} -> ${detail.target?.title || detail.value.target}`
    };
  }
  return null;
}

export function routeLabel(route, index) {
  return route?.cn || route?.name || route?.id || `Route ${index + 1}`;
}

export function routeShort(route, index) {
  return route?.short || route?.raw_fields?.routeShort || routeLabel(route, index).split(/[ /]/)[0];
}

export function getPaperRouteName(paper) {
  return paper.raw_fields?.routeCN || paper.route_name || paper.route_id || "未归类";
}

export function getPaperRouteId(paper) {
  return paper.route_id || paper.raw_fields?.routeName || paper.raw_fields?.routeCN || "unrouted";
}

export function getPaperLevel(paper) {
  return paper.raw_fields?.level || paper.tier || paper.include_type || "论文";
}

export function detailKind(detail) {
  if (!detail) return "canvas";
  return detail.type === "canvas_node" ? "canvas" : detail.type || "canvas";
}

export function recommendedTemplateIds(detail) {
  const kind = detailKind(detail);
  if (kind === "paper") return ["atlas_gap", "method_evolution", "candidate_audit"];
  if (kind === "relation" || kind === "path") return ["relation_explain", "candidate_audit", "atlas_gap"];
  return ["atlas_gap", "candidate_audit", "method_evolution"];
}
