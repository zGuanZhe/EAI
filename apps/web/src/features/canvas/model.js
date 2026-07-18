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

export function getCampaignNextStep(snapshot, pendingApproval = null) {
  const campaign = snapshot?.campaign;
  if (!campaign) return null;
  const currentStage = campaign.stages?.find((item) => item.id === campaign.current_stage_id) || null;
  const stageLabel = currentStage?.title || CAMPAIGN_STAGE_LABELS[currentStage?.kind] || "尚未开始";
  const branches = (snapshot.branches || []).filter((item) => !currentStage || item.stage_id === currentStage.id);
  const approvalBranchId = pendingApproval?.payload?.campaign_id === campaign.id
    ? pendingApproval.payload.branch_id
    : null;
  const approvalBranch = branches.find((item) => item.id === approvalBranchId);

  if (campaign.status === "archived") {
    return { stageLabel, status: "archived", passiveLabel: "只读归档", title: "历史 Campaign 仅供查阅", description: "归档记录不会重新执行或写回当前研究状态。" };
  }
  if (campaign.status === "ready") {
    return { stageLabel, action: "start", label: "启动 Campaign", title: "生成首轮独立实验分支", description: "启动后先固定证据边界，再进入初始实现阶段。" };
  }
  if (["paused", "interrupted"].includes(campaign.status)) {
    return { stageLabel, action: "resume", label: "继续 Campaign", title: "从当前检查点继续", description: "已完成分支和 Journal 检查点会被复用。" };
  }
  if (campaign.status === "cancelled") {
    return { stageLabel, status: "cancelled", passiveLabel: "已停止", title: "Campaign 已停止", description: "晚到运行结果会被隔离，不再写入阶段、消息或产物。" };
  }
  if (campaign.status === "completed") {
    return { stageLabel, action: "open_artifacts", label: "查看发布产物", title: "Campaign 已完成", description: "检查论文、披露、引用和可复现发布包。" };
  }
  if (approvalBranch) {
    return { stageLabel, action: "inspect_branch", branchId: approvalBranch.id, label: "检查执行授权", title: "分支等待授权", description: "核对镜像、资源、挂载、网络和超时后再决定是否执行。" };
  }
  if (currentStage?.kind === "writeup") {
    if (!(snapshot.manuscripts || []).length) {
      return { stageLabel, action: "generate_manuscript", label: "生成论文候选稿", title: "实验结论已进入写作", description: "候选稿只使用 Campaign 指标、产物和可解析证据。" };
    }
    return { stageLabel, action: "open_manuscript", label: "检查论文", title: "论文候选稿已生成", description: "检查引用、披露和警告，再进入审稿。" };
  }
  if (currentStage?.kind === "review") {
    const manuscript = (snapshot.manuscripts || []).find((item) => item.id === campaign.current_manuscript_id) || (snapshot.manuscripts || []).at(-1);
    return manuscript
      ? { stageLabel, action: "start_review", manuscriptId: manuscript.id, label: "开始三角色审稿", title: "论文等待独立审查", description: "方法、证据和表达分别审查后生成 meta-review。" }
      : { stageLabel, action: "open_manuscript", label: "返回论文", title: "缺少可审稿候选稿", description: "先生成并检查论文候选稿。" };
  }
  if (currentStage?.kind === "release") {
    return { stageLabel, action: "open_manuscript", label: "检查并导出", title: "准备发布包", description: "确认引用、披露和警告后导出正式包或带警告草稿包。" };
  }

  const successful = branches.find((item) => item.id === currentStage?.best_branch_id)
    || branches.find((item) => ["succeeded", "promoted"].includes(item.status));
  if (successful) {
    return { stageLabel, action: "inspect_branch", branchId: successful.id, label: "检查并推进", title: "已有成功分支", description: "核对指标与失败边界，再决定晋升或进入下一阶段。" };
  }
  const proposed = branches.find((item) => item.status === "proposed");
  if (proposed) {
    return { stageLabel, action: "inspect_branch", branchId: proposed.id, label: "检查实验计划", title: "选择一个分支开始", description: "先检查计划，再准备隔离执行会话。" };
  }
  if (branches.some((item) => item.status === "running")) {
    return { stageLabel, status: "running", passiveLabel: "运行中", title: "实验分支运行中", description: "完成后将在此显示指标、产物和推荐依据。" };
  }
  return { stageLabel, status: campaign.status, passiveLabel: "等待更新", title: "等待当前阶段更新", description: "运行状态变化后会给出可执行的下一步。" };
}

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
