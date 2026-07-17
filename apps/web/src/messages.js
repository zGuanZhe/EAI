export function resultItems(card, key) {
  const items = card?.parsed_json?.[key];
  return Array.isArray(items) ? items : [];
}

export function resultItemTitle(item) {
  if (typeof item === "string") return item;
  return item?.title || item?.summary || item?.task || item?.content || "未命名";
}

export function messageTitle(message, results = [], runs = []) {
  if (message.kind === "assistant_reply") return "主对话智能体";
  if (message.kind === "result") {
    return results.find((item) => item.id === message.linked_result_id)?.title || "Codex 返回";
  }
  if (message.kind === "task_pack") return "Task Pack";
  if (message.kind === "tool_run") {
    const run = runs.find((item) => item.id === message.linked_tool_run_id);
    return run?.summary || "工具运行";
  }
  if (message.kind === "state") return "状态";
  return message.role === "user" ? "你" : "消息";
}

export function messageMeta(message) {
  const parts = [];
  if (message.created_at) {
    parts.push(new Date(message.created_at).toLocaleString("zh-CN", { hour12: false }));
  }
  if (message.status && message.status !== "done") {
    parts.push(message.status === "preview" ? "待确认" : message.status === "pending" ? "运行中" : message.status);
  }
  return parts.join(" · ");
}

export function syntheticMessages(thread) {
  const messages = [...(thread.messages || [])];
  const seenResults = new Set(messages.map((item) => item.linked_result_id).filter(Boolean));
  const seenRuns = new Set(messages.map((item) => item.linked_tool_run_id).filter(Boolean));

  for (const result of thread.result_cards || []) {
    if (!seenResults.has(result.id)) {
      messages.push({
        id: `legacy-result-${result.id}`,
        role: "assistant",
        kind: "result",
        content: `已确认写入 Codex 返回：${result.title}`,
        created_at: result.created_at,
        status: "done",
        linked_result_id: result.id,
        legacy: true
      });
    }
  }

  for (const run of thread.tool_runs || []) {
    if (!seenRuns.has(run.id)) {
      messages.push({
        id: `legacy-run-${run.id}`,
        role: "tool",
        kind: run.tool === "export_task_pack" || run.tool === "research_template_copy" ? "task_pack" : "tool_run",
        content: run.summary || run.input_summary || run.tool,
        created_at: run.created_at,
        status: run.status || "done",
        linked_tool_run_id: run.id,
        legacy: true
      });
    }
  }

  return messages.sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
}
