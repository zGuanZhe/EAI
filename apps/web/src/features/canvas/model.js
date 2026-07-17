export const NODE_LABELS = {
  question: "问题",
  hypothesis: "假设",
  conclusion: "结论",
  material: "材料",
  evidence: "证据",
  decision: "判断",
  finding: "发现",
  campaign_ref: "Campaign",
  task: "任务"
};

export const CAMPAIGN_STAGE_LABELS = {
  evidence_preparation: "证据准备",
  initial_implementation: "初始实现",
  baseline_tuning: "基线调优",
  creative_research: "创新研究",
  ablation: "消融",
  writeup: "写作",
  review: "审稿",
  release: "发布",
  legacy_import: "历史归档"
};

export function canonicalNodeType(type) {
  if (type === "material") return "evidence";
  if (type === "conclusion") return "decision";
  return type;
}

export const EDGE_LABELS = {
  supports: "支持",
  challenges: "质疑",
  leads_to: "推出",
  requires: "需要"
};

export const TASK_STATUS_LABELS = { todo: "待办", doing: "进行中", done: "已完成" };

export const CANVAS_COLUMNS = [
  { type: "question", label: "问题", x: 54 },
  { type: "hypothesis", label: "假设", x: 292 },
  { type: "conclusion", label: "结论", x: 530 },
  { type: "material", label: "材料", x: 768 },
  { type: "task", label: "任务", x: 1006 }
];

const FLOW_TYPE_ORDER = { question: 0, hypothesis: 1, material: 2, evidence: 2, conclusion: 3, decision: 3, finding: 4, campaign_ref: 4, task: 5 };

export function buildCampaignTree(branches = [], stageId, bestBranchId = null) {
  const stageBranches = branches.filter((branch) => branch.stage_id === stageId && branch.status !== "discarded");
  const map = new Map(stageBranches.map((branch) => [branch.id, branch]));
  const children = new Map(stageBranches.map((branch) => [branch.id, []]));
  for (const branch of stageBranches) {
    if (branch.parent_id && children.has(branch.parent_id)) children.get(branch.parent_id).push(branch);
  }
  const bestPath = new Set();
  let cursor = map.get(bestBranchId);
  while (cursor) {
    bestPath.add(cursor.id);
    cursor = map.get(cursor.parent_id);
  }
  const roots = stageBranches.filter((branch) => !branch.parent_id || !map.has(branch.parent_id));
  const items = [];
  let lane = 0;
  function visit(branch, depth, currentLane) {
    items.push({ branch, depth, lane: currentLane, onBestPath: bestPath.has(branch.id) });
    const branchChildren = children.get(branch.id) || [];
    branchChildren.forEach((child, index) => visit(child, depth + 1, index === 0 ? currentLane : ++lane));
  }
  roots.forEach((root, index) => visit(root, 0, index === 0 ? lane : ++lane));
  return { items, maxDepth: Math.max(0, ...items.map((item) => item.depth)), bestPathIds: [...bestPath] };
}

export function normalizeEdgeLabel(label) {
  return EDGE_LABELS[label] ? label : "supports";
}

function normalizeNode(node, index = 0) {
  const column = CANVAS_COLUMNS.find((item) => item.type === node.type) || CANVAS_COLUMNS[0];
  return {
    ...node,
    status: node.status || (node.type === "task" ? "todo" : null),
    priority: node.priority ?? (node.type === "task" ? 1 : null),
    x: Number.isFinite(node.x) && node.x > 0 ? node.x : column.x,
    y: Number.isFinite(node.y) && node.y > 0 ? node.y : 110 + index * 118
  };
}

export function layoutCanvas(nodes = []) {
  const counts = {};
  return nodes.map((node, index) => {
    const normalized = normalizeNode(node, index);
    const column = CANVAS_COLUMNS.find((item) => item.type === normalized.type) || CANVAS_COLUMNS[0];
    const order = counts[normalized.type] || 0;
    counts[normalized.type] = order + 1;
    return { ...normalized, x: column.x, y: 116 + order * 126 };
  });
}

function flowSort(nodeMap) {
  return (leftId, rightId) => {
    const left = nodeMap.get(leftId) || {};
    const right = nodeMap.get(rightId) || {};
    return (FLOW_TYPE_ORDER[left.type] ?? 99) - (FLOW_TYPE_ORDER[right.type] ?? 99)
      || (Number(left.y) || 0) - (Number(right.y) || 0)
      || String(left.title || "").localeCompare(String(right.title || ""), "zh-CN");
  };
}

export function buildArgumentFlow(nodes = [], edges = []) {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) {
    if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) continue;
    outgoing.get(edge.source).push(edge);
    incoming.get(edge.target).push(edge);
  }
  const compare = flowSort(nodeMap);
  for (const list of outgoing.values()) list.sort((a, b) => compare(a.target, b.target));

  const visited = new Set();
  const active = new Set();
  const items = [];
  const cycleEdges = [];
  const crossLinks = [];
  const orphanIds = [];

  function visit(nodeId, depth, parentEdge = null, orphan = false) {
    if (active.has(nodeId)) {
      if (parentEdge) cycleEdges.push(parentEdge.id);
      return;
    }
    if (visited.has(nodeId)) {
      if (parentEdge) crossLinks.push(parentEdge.id);
      return;
    }
    const node = nodeMap.get(nodeId);
    if (!node) return;
    visited.add(nodeId);
    active.add(nodeId);
    items.push({ node, depth, parentEdge, orphan, incomingCount: incoming.get(nodeId).length, outgoingCount: outgoing.get(nodeId).length });
    for (const edge of outgoing.get(nodeId)) visit(edge.target, Math.min(depth + 1, 3), edge, orphan);
    active.delete(nodeId);
  }

  const questionRoots = nodes.filter((node) => node.type === "question").map((node) => node.id).sort(compare);
  const structuralRoots = nodes.filter((node) => !incoming.get(node.id).length && node.type !== "question").map((node) => node.id).sort(compare);
  for (const rootId of questionRoots) visit(rootId, 0);
  for (const rootId of structuralRoots) {
    if (visited.has(rootId)) continue;
    orphanIds.push(rootId);
    visit(rootId, 0, null, true);
  }
  for (const nodeId of nodes.map((node) => node.id).sort(compare)) {
    if (visited.has(nodeId)) continue;
    orphanIds.push(nodeId);
    visit(nodeId, 0, null, true);
  }
  return { items, cycleEdges, crossLinks, orphanIds };
}
