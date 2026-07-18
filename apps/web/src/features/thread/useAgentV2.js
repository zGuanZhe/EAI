import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiFetch } from "../../api.js";

const RESTORABLE_STATUSES = new Set(["pending", "running", "waiting_approval", "interrupted", "paused"]);
const RUNNING_STATUSES = new Set(["pending", "running"]);

function parseSseBlock(block) {
  if (!block || block.startsWith(":")) return null;
  let event = "message";
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  try {
    const envelope = JSON.parse(data.join("\n"));
    return { event, envelope, payload: envelope.payload || envelope, seq: Number(envelope.seq || 0) };
  } catch {
    return null;
  }
}

function replaceById(items = [], value) {
  const id = value?.id;
  if (!id) return items;
  const index = items.findIndex((item) => item.id === id);
  if (index < 0) return [...items, value];
  return items.map((item, itemIndex) => itemIndex === index ? { ...item, ...value } : item);
}

function updateAssistant(thread, messageId, updater) {
  if (!thread || !messageId) return thread;
  return {
    ...thread,
    messages: (thread.messages || []).map((message) => message.id === messageId ? updater(message) : message)
  };
}

export function useAgentV2({ thread, surface, setThread, refreshThreads, setSurface, setToolsPage, setRail, setRailOpen, setStatus }) {
  const [tasksById, setTasksById] = useState({});
  const [chatError, setChatError] = useState("");
  const controllersRef = useRef(new Map());
  const cancelledTaskIdsRef = useRef(new Set());
  const threadRef = useRef(thread);
  threadRef.current = thread;

  const patchCurrentThread = useCallback((threadId, updater) => {
    if (threadRef.current?.id !== threadId) return;
    setThread((current) => current?.id === threadId ? updater(current) : current);
  }, [setThread]);

  const trackTask = useCallback((task) => {
    if (!task?.id) return;
    setTasksById((current) => ({ ...current, [task.id]: { ...(current[task.id] || {}), ...task } }));
  }, []);

  const untrackTask = useCallback((taskId) => {
    setTasksById((current) => {
      if (!current[taskId]) return current;
      const next = { ...current };
      delete next[taskId];
      return next;
    });
  }, []);

  const enterThreadMode = useCallback(() => {
    setToolsPage(false);
    setSurface("thread");
    setRailOpen(false);
  }, [setRailOpen, setSurface, setToolsPage]);

  const executeUiCommand = useCallback((command) => {
    const targetType = String(command?.target?.type || "");
    const surfaceByType = {
      thread: "thread",
      project: "thread",
      paper: "atlas",
      work: "atlas",
      document: "atlas",
      "canvas-node": "canvas",
      campaign: "canvas"
    };
    const nextSurface = surfaceByType[targetType];
    const resolution = nextSurface && command?.action === "open_object" ? "applied" : "dismissed";
    if (resolution === "applied") {
      setToolsPage(false);
      setSurface(nextSurface);
      if (nextSurface === "atlas") {
        setRail?.("atlas");
        setRailOpen(true);
      } else {
        setRailOpen(false);
      }
    }
    if (command?.id) {
      api(`/agent/ui-commands/${command.id}/resolve?status=${resolution}`, {
        method: "POST",
        body: JSON.stringify({})
      }).catch(() => {});
    }
  }, [setRail, setRailOpen, setSurface, setToolsPage]);

  const applyEvent = useCallback((threadId, assistantMessageId, event, payload, seq) => {
    patchCurrentThread(threadId, (current) => updateAssistant(current, assistantMessageId, (message) => {
      const refs = { ...(message.refs || {}) };
      const v2 = { ...(refs.agent_v2 || {}), last_seq: Math.max(Number(refs.agent_v2?.last_seq || 0), seq || 0) };
      const v3 = { ...(refs.agent_v3 || {}), last_seq: Math.max(Number(refs.agent_v3?.last_seq || 0), seq || 0) };
      if (event === "service_selected") {
        v2.service = payload.service;
        v2.service_label = payload.label;
        v2.depth = payload.depth;
        v2.source_policy = payload.source_policy;
        refs.service_decision = payload;
      } else if (event === "objective_confirmed") {
        v2.objective = payload.objective;
        v2.status = payload.requires_clarification ? "需要确认任务范围" : "已理解本轮目标";
      } else if (event === "status") {
        v2.status = payload.label || "正在工作";
        v2.phase = payload.phase;
        v2.tone = payload.tone;
        v2.activity = [...(v2.activity || []).slice(-11), payload];
        v3.phase = payload.phase;
        v3.status = payload.label;
      } else if (event === "context_ready") {
        v3.context_manifest_id = payload.manifest_id;
        v3.context_item_count = payload.item_count;
      } else if (event === "connector_unavailable") {
        v3.unavailable_scopes = payload.scopes || [];
        v3.notice = payload.message;
      } else if (event === "paused" || event === "resumed") {
        v3.status = event;
      } else if (event === "specialist_started") {
        v2.status = payload.label;
        v2.specialists = [...(v2.specialists || []), payload];
      } else if (event === "source_found") {
        v2.sources = replaceById(v2.sources, payload);
      } else if (event === "capability_started") {
        v2.status = payload.label ? `正在${payload.label}` : "正在调用研究能力";
        v2.capabilities = replaceById(v2.capabilities, { ...payload, status: "running" });
      } else if (event === "capability_completed") {
        v2.status = payload.summary || "已取得新的观察结果";
        v2.capabilities = replaceById(v2.capabilities, payload);
      } else if (event === "steer_applied") {
        v2.status = "已应用追加要求，正在重新规划";
        v2.activity = [...(v2.activity || []).slice(-11), { label: "已应用追加要求", phase: "steer" }];
      } else if (event === "evidence_gap") {
        v2.evidence_gaps = [...(v2.evidence_gaps || []), payload];
      } else if (event === "answer_ready") {
        v2.answer_validated = Boolean(payload.validated);
        v2.guard_status = payload.guard_status;
        v2.citation_integrity = payload.citation_integrity;
        v2.evidence_sufficiency = payload.evidence_sufficiency;
      } else if (event === "artifact_ready") {
        v2.artifacts = replaceById(v2.artifacts, payload);
        refs.agent_v2_artifacts = replaceById(refs.agent_v2_artifacts, payload);
      } else if (event === "approval_required") {
        v2.approvals = replaceById(v2.approvals, payload);
        refs.agent_v2_approvals = replaceById(refs.agent_v2_approvals, payload);
        v2.status = payload.kind === "sandbox_command" ? "等待确认沙箱命令" : "等待确认工作区修改";
      } else if (event === "answer_delta") {
        const started = Boolean(v2.answer_started);
        v2.answer_started = true;
        refs.agent_v2 = v2;
        refs.agent_v3 = v3;
        return { ...message, status: "streaming", content: started ? `${message.content || ""}${payload.text || ""}` : (payload.text || ""), refs };
      } else if (event === "operation_applied") {
        v2.operation = payload;
        v2.status = "已应用并验证操作";
      } else if (event === "error") {
        refs.error = payload.message || "Agent Runtime v2 运行失败";
        v2.status = refs.error;
      }
      refs.agent_v2 = v2;
      refs.agent_v3 = v3;
      return { ...message, status: event === "error" ? "failed" : "streaming", refs };
    }));
  }, [patchCurrentThread]);

  const consumeEvents = useCallback(async (threadId, taskId, assistantMessageId, afterSeq = 0, interactionLane = "legacy") => {
    if (controllersRef.current.has(taskId)) {
      return { taskId, lastSeq: afterSeq, alreadyConnected: true };
    }
    const controller = new AbortController();
    controllersRef.current.set(taskId, controller);
    const eventPath = interactionLane === "research"
      ? `/research-tasks/${taskId}/events`
      : `/agent-v2/tasks/${taskId}/events`;
    const response = await apiFetch(`${eventPath}?after_seq=${afterSeq}`, { signal: controller.signal });
    if (!response.ok || !response.body) throw new Error(await response.text() || "无法读取 Agent v2 事件");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let lastSeq = afterSeq;
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const parsed = parseSseBlock(block.trim());
          if (!parsed) continue;
          lastSeq = Math.max(lastSeq, parsed.seq);
          if (parsed.event === "done") {
            const finalThread = parsed.payload.thread;
            if (finalThread) patchCurrentThread(threadId, () => finalThread);
            const completedTask = parsed.payload.task;
            if (completedTask?.interaction_lane === "research") trackTask(completedTask);
            else untrackTask(taskId);
            setStatus(parsed.payload.status === "cancelled" ? "已停止本轮任务" : "Main Agent 已完成本轮任务");
          } else {
            if (parsed.event === "ui_command") executeUiCommand(parsed.payload);
            if (parsed.event === "approval_required") {
              trackTask({ id: taskId, thread_id: threadId, status: "waiting_approval" });
            } else if (parsed.event === "paused") {
              trackTask({ id: taskId, thread_id: threadId, status: "paused", phase: "paused" });
            } else if (parsed.event === "resumed") {
              trackTask({ id: taskId, thread_id: threadId, status: "pending" });
            } else if (["objective_confirmed", "status", "capability_started", "capability_completed", "steer_applied"].includes(parsed.event)) {
              trackTask({ id: taskId, thread_id: threadId, status: "running" });
            } else if (parsed.event === "error") {
              trackTask({ id: taskId, thread_id: threadId, status: "failed", error: parsed.payload.message || "" });
            }
            applyEvent(threadId, assistantMessageId, parsed.event, parsed.payload, parsed.seq);
          }
        }
        if (done) break;
      }
    } finally {
      controllersRef.current.delete(taskId);
    }
    await refreshThreads?.();
    return { taskId, lastSeq };
  }, [applyEvent, executeUiCommand, patchCurrentThread, refreshThreads, setStatus, trackTask, untrackTask]);

  const sendThreadChat = useCallback(async (text, options = {}) => {
    if (!thread?.id || !text?.trim()) return null;
    const threadId = thread.id;
    setChatError("");
    enterThreadMode();
    try {
      const interactionMode = options.interactionMode || "ask";
      const endpoint = interactionMode === "research"
        ? `/threads/${threadId}/research-tasks`
        : `/threads/${threadId}/agent/asks`;
      const requestPayload = interactionMode === "research" ? {
        objective: text.trim(),
        surface: options.surface || surface,
        source_policy: options.sourcePolicy || "local_and_external",
        attachments: options.turnAttachments || [],
        deliverable: options.deliverable || "research_note",
        depth: options.depth || "standard"
      } : {
        message: text.trim(),
        surface: options.surface || surface,
        source_policy: options.sourcePolicy || "local_and_external",
        attachments: options.turnAttachments || []
      };
      const started = await api(endpoint, {
        method: "POST",
        body: JSON.stringify(requestPayload)
      });
      options.onStarted?.(started);
      patchCurrentThread(threadId, () => started.thread);
      trackTask(started.task);
      setStatus(interactionMode === "research" ? "研究任务已在后台启动" : "Main Agent 正在检索并回答");
      consumeEvents(threadId, started.task.id, started.assistant_message_id, 0, started.task.interaction_lane).catch((error) => {
        if (error.name === "AbortError") return;
        setChatError(error.message || "Agent Runtime v2 事件流中断");
        setStatus(`Main Agent 事件流中断：${error.message || "未知错误"}`);
      });
      return started;
    } catch (error) {
      if (error.name !== "AbortError") {
        setChatError(error.message || "Agent Runtime v2 运行失败");
        setStatus(`Main Agent 运行失败：${error.message || "未知错误"}`);
      }
      return null;
    }
  }, [consumeEvents, enterThreadMode, patchCurrentThread, setStatus, surface, thread?.id, trackTask]);

  const stopThreadChat = useCallback(async () => {
    const active = Object.values(tasksById)
      .filter((task) => task.thread_id === thread?.id && task.interaction_lane !== "research" && RUNNING_STATUSES.has(task.status))
      .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))[0];
    if (!active?.id) return;
    cancelledTaskIdsRef.current.add(active.id);
    controllersRef.current.get(active.id)?.abort();
    untrackTask(active.id);
    patchCurrentThread(thread.id, (current) => ({
      ...current,
      messages: (current.messages || []).map((message) => [message.refs?.agent_v2_task_id, message.refs?.agent_v3_task_id].includes(active.id) ? {
        ...message,
        content: "已停止本轮任务。",
        status: "done",
        refs: { ...(message.refs || {}), cancelled: true }
      } : message)
    }));
    setStatus("已停止本轮任务");
    try {
      await api(`/agent-v2/tasks/${active.id}/cancel`, { method: "POST", body: JSON.stringify({}) });
      const latest = await api(`/threads/${thread.id}`);
      patchCurrentThread(thread.id, () => latest);
    } catch (error) {
      cancelledTaskIdsRef.current.delete(active.id);
      setChatError(error.message || "停止任务失败");
      try {
        const [{ task }, latest] = await Promise.all([
          api(`/agent-v2/tasks/${active.id}`),
          api(`/threads/${thread.id}`)
        ]);
        patchCurrentThread(thread.id, () => latest);
        if (RUNNING_STATUSES.has(task.status)) trackTask(task);
      } catch {
        // The visible error remains authoritative when reconciliation also fails.
      }
    }
  }, [patchCurrentThread, setStatus, tasksById, thread?.id, trackTask, untrackTask]);

  const steerThreadChat = useCallback(async (text) => {
    const active = Object.values(tasksById)
      .filter((task) => task.thread_id === thread?.id && task.interaction_lane !== "research" && RUNNING_STATUSES.has(task.status))
      .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))[0];
    if (!active?.id || !text?.trim()) return null;
    try {
      const response = await api(`/agent-v2/tasks/${active.id}/steer`, {
        method: "POST",
        body: JSON.stringify({ message: text.trim() })
      });
      if (response.thread) patchCurrentThread(thread.id, () => response.thread);
      setStatus("已追加要求，Main Agent 将在下一步重新规划");
      return response;
    } catch (error) {
      setChatError(error.message || "追加要求失败");
      return null;
    }
  }, [patchCurrentThread, setStatus, tasksById, thread?.id]);

  const retryThreadChat = useCallback(async (assistantMessageId) => {
    const message = (thread?.messages || []).find((item) => item.id === assistantMessageId);
    const taskId = message?.refs?.agent_v3_task_id || message?.refs?.agent_v2_task_id;
    if (!taskId) {
      setChatError("这条消息不是 Agent Runtime v2 任务，无法继续");
      return null;
    }
    patchCurrentThread(thread.id, (current) => updateAssistant(current, assistantMessageId, (draft) => ({
      ...draft,
      content: "正在重新理解本轮目标...",
      status: "pending",
      refs: {
        ...(draft.refs || {}),
        citations: [],
        source_ids: [],
        artifact_ids: [],
        approval_ids: [],
        agent_v2: { status: "正在重新理解本轮目标", last_seq: Number(draft.refs?.agent_v2?.last_seq || 0) }
      }
    })));
    const response = await api(`/agent-v2/tasks/${taskId}/resume`, { method: "POST", body: JSON.stringify({}) });
    trackTask(response.task);
    return consumeEvents(
      thread.id,
      taskId,
      assistantMessageId,
      Number(message.refs?.agent_v3?.last_seq || message.refs?.agent_v2?.last_seq || 0),
      message.refs?.interaction_lane || "legacy"
    );
  }, [consumeEvents, thread, trackTask]);

  const resolveApproval = useCallback(async (approvalId, decision, options = {}) => {
    const response = await api(`/agent-v2/approvals/${approvalId}/resolve`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        selected_operation_ids: options.selectedOperationIds || null,
        edited_arguments: options.editedArguments || {}
      })
    });
    patchCurrentThread(thread.id, (current) => ({
      ...current,
      messages: (current.messages || []).map((message) => ({
        ...message,
        refs: {
          ...(message.refs || {}),
          agent_v2_approvals: (message.refs?.agent_v2_approvals || []).map((approval) => approval.id === approvalId ? response.approval : approval)
        }
      }))
    }));
    if (response.task && RUNNING_STATUSES.has(response.task.status)) trackTask(response.task);
    setStatus(decision === "approve" ? "已批准，Main Agent 正在执行" : "已拒绝本轮操作");
    return response;
  }, [patchCurrentThread, setStatus, thread?.id, trackTask]);

  const undoOperationBatch = useCallback(async (batchId) => {
    try {
      const response = await api(`/agent-v2/operation-batches/${batchId}/undo`, { method: "POST", body: JSON.stringify({}) });
      if (response.thread) patchCurrentThread(response.thread.id, () => response.thread);
      setStatus("已撤销本轮工作区修改");
      return response;
    } catch (error) {
      setChatError(error.message || "撤销工作区修改失败");
      setStatus(`无法撤销：${error.message || "目标已发生变化"}`);
      return null;
    }
  }, [patchCurrentThread, setStatus]);

  useEffect(() => {
    const pendingMessages = [...(thread?.messages || [])].filter((message) =>
      message.kind === "assistant_reply" && ["pending", "streaming"].includes(message.status)
      && (message.refs?.agent_v2_task_id || message.refs?.agent_v3_task_id)
    );
    let cancelled = false;
    for (const pending of pendingMessages) {
      const taskId = pending.refs?.agent_v3_task_id || pending.refs?.agent_v2_task_id;
      if (controllersRef.current.has(taskId) || cancelledTaskIdsRef.current.has(taskId)) continue;
      api(`/agent-v2/tasks/${taskId}`).then(({ task }) => {
        if (cancelled || !RESTORABLE_STATUSES.has(task.status)) return;
        trackTask(task);
        consumeEvents(
          thread.id,
          task.id,
          pending.id,
          Number(pending.refs?.agent_v3?.last_seq || pending.refs?.agent_v2?.last_seq || 0),
          task.interaction_lane
        ).catch(() => {});
      }).catch(() => {});
    }
    return () => { cancelled = true; };
  }, [consumeEvents, tasksById, thread, trackTask]);

  useEffect(() => {
    if (!thread?.id) return;
    let active = true;
    api(`/threads/${thread.id}/research-tasks`).then((tasks) => {
      if (!active) return;
      for (const task of tasks || []) trackTask(task);
    }).catch(() => {});
    return () => { active = false; };
  }, [thread?.id, trackTask]);

  const controlResearchTask = useCallback(async (taskId, action, payload = {}) => {
    const optimisticStatus = { pause: "paused", resume: "pending", cancel: "cancelled" }[action];
    if (optimisticStatus) trackTask({ id: taskId, status: optimisticStatus, ...(action === "pause" ? { phase: "paused" } : {}) });
    if (action === "cancel") controllersRef.current.get(taskId)?.abort();
    try {
      const response = await api(`/research-tasks/${taskId}/${action}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      if (response?.task) trackTask(response.task);
      if (action === "resume" && response?.task && !controllersRef.current.has(taskId)) {
        const assistant = (threadRef.current?.messages || []).find((message) => message.refs?.agent_v3_task_id === taskId);
        if (assistant) consumeEvents(
          response.task.thread_id,
          taskId,
          assistant.id,
          Number(assistant.refs?.agent_v3?.last_seq || 0),
          "research"
        ).catch(() => {});
      }
      return response;
    } catch (error) {
      try {
        const latest = await api(`/research-tasks/${taskId}`);
        if (latest?.task) trackTask(latest.task);
      } catch {
        // Keep the original action error when reconciliation also fails.
      }
      throw error;
    }
  }, [consumeEvents, trackTask]);

  const steerResearchTask = useCallback((taskId, message) => controlResearchTask(taskId, "steer", { message }), [controlResearchTask]);
  const pauseResearchTask = useCallback((taskId) => controlResearchTask(taskId, "pause"), [controlResearchTask]);
  const resumeResearchTask = useCallback((taskId) => controlResearchTask(taskId, "resume"), [controlResearchTask]);
  const cancelResearchTask = useCallback((taskId) => controlResearchTask(taskId, "cancel"), [controlResearchTask]);
  const promoteResearchTask = useCallback((taskId) => controlResearchTask(taskId, "promote-campaign"), [controlResearchTask]);

  const activeTask = Object.values(tasksById)
    .filter((task) => task.thread_id === thread?.id && task.interaction_lane !== "research" && RUNNING_STATUSES.has(task.status))
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))[0] || null;
  const isRunning = Boolean(activeTask);
  const researchTasks = Object.values(tasksById)
    .filter((task) => task.thread_id === thread?.id && task.interaction_lane === "research")
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")));
  return useMemo(() => ({
    chatError, activeTask, isRunning, researchTasks, sendThreadChat, steerThreadChat, stopThreadChat,
    retryThreadChat, resolveApproval, undoOperationBatch, steerResearchTask, pauseResearchTask,
    resumeResearchTask, cancelResearchTask, promoteResearchTask
  }), [activeTask, cancelResearchTask, chatError, isRunning, pauseResearchTask, promoteResearchTask,
    researchTasks, resolveApproval, resumeResearchTask, retryThreadChat, sendThreadChat, steerResearchTask,
    steerThreadChat, stopThreadChat, undoOperationBatch]);
}
