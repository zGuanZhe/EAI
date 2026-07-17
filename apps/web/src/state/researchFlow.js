export const FLOW_STEPS = [
  { id: "question", label: "问题", surface: "thread" },
  { id: "evidence", label: "证据", surface: "atlas" },
  { id: "canvas", label: "编排", surface: "canvas" },
  { id: "taskPack", label: "Task Pack", rail: "templates" },
  { id: "return", label: "回写", rail: "context" }
];

export function canvasReadiness(thread) {
  const nodes = thread?.canvas?.nodes || [];
  const hasQuestion = nodes.some((node) => node.type === "question");
  const hasMaterial = nodes.some((node) => ["material", "evidence"].includes(node.type));
  const hasArgument = nodes.some((node) => ["hypothesis", "conclusion", "decision", "finding"].includes(node.type));
  const hasTask = nodes.some((node) => node.type === "task");
  return {
    hasQuestion,
    hasMaterial,
    hasArgument,
    hasTask,
    ready: hasQuestion && hasMaterial && hasArgument,
    score: [hasQuestion, hasMaterial, hasArgument, hasTask].filter(Boolean).length
  };
}

export function deriveResearchFlow({ thread, selectedCards = [], taskPackPreview, resultPreview }) {
  const messages = thread?.messages || [];
  const contextCards = thread?.context_cards || [];
  const readiness = canvasReadiness(thread);
  const hasQuestion = Boolean(thread?.goal) || messages.some((message) => message.role === "user");
  const hasEvidence = contextCards.length > 0 || selectedCards.length > 0;
  const hasTaskPack = Boolean(taskPackPreview) || (thread?.tool_runs || []).some((run) => run.mode === "copy" || run.mode === "api");
  const hasReturn = Boolean(resultPreview) || (thread?.result_cards || []).length > 0;
  const completed = {
    question: hasQuestion,
    evidence: hasEvidence,
    canvas: readiness.ready,
    taskPack: hasTaskPack,
    return: hasReturn
  };
  const active = FLOW_STEPS.find((step) => !completed[step.id])?.id || "return";
  return { active, completed, readiness };
}
