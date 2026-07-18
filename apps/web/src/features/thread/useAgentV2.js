import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiFetch } from "../../api.js";

const RESTORABLE_STATUSES = new Set(["pending", "running", "waiting_approval", "interrupted"]);
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

export function useAgentV2({ thread, surface, setThread, refreshThreads, setSurface, setToolsPage, setRailOpen, setStatus }) {
  const [tasksById, setTasksById] = useState({});
  const [chatError, setChatError] = useState("");
  const controllersRef = useRef(new Map());
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

  const applyEvent = useCallback((threadId, assistantMessageId, event, payload, seq) => {
    patchCurrentThread(threadId, (current) => updateAssistant(current, assistantMessageId, (message) => {
      const refs = { ...(message.refs || {}) };
      const v2 = { ...(refs.agent_v2 || {}), last_seq: Math.max(Number(refs.agent_v2?.last_seq || 0), seq || 0) };
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
        return { ...message, status: "streaming", content: started ? `${message.content || ""}${payload.text || ""}` : (payload.text || ""), refs };
      } else if (event === "operation_applied") {
        v2.operation = payload;
        v2.status = "已应用并验证操作";
      } else if (event === "error") {
        refs.error = payload.message || "Agent Runtime v2 运行失败";
        v2.status = refs.error;
      }
      refs.agent_v2 = v2;
      return { ...message, status: event === "error" ? "failed" : "streaming", refs };
    }));
  }, [patchCurrentThread]);

  const consumeEvents = useCallback(async (threadId, taskId, assistantMessageId, afterSeq = 0) => {
    const controller = new AbortController();
    controllersRef.current.set(taskId, controller);
    const response = await apiFetch(`/agent-v2/tasks/${taskId}/events?after_seq=${afterSeq}`, { signal: controller.signal });
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
            untrackTask(taskId);
            setStatus(parsed.payload.status === "cancelled" ? "已停止本轮任务" : "Main Agent 已完成本轮任务");
          } else {
            if (parsed.event === "approval_required") {
              trackTask({ id: taskId, thread_id: threadId, status: "waiting_approval" });
            } else if (["objective_confirmed", "status", "capability_started", "capability_completed", "steer_applied"].includes(parsed.event)) {
              trackTask({ id: taskId, thread_id: threadId, status: "running" });
            } else if (parsed.event === "error") {
              untrackTask(taskId);
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
  }, [applyEvent, patchCurrentThread, refreshThreads, setStatus, trackTask, untrackTask]);

  const sendThreadChat = useCallback(async (text, options = {}) => {
    if (!thread?.id || !text?.trim()) return null;
    const threadId = thread.id;
    setChatError("");
    enterThreadMode();
    try {
      const started = await api(`/threads/${threadId}/agent-v2/turns`, {
        method: "POST",
        body: JSON.stringify({
          message: text.trim(),
          surface: options.surface || surface,
          intent_override: options.intentOverride || "auto",
          source_policy: options.sourcePolicy || null,
          turn_attachments: options.turnAttachments || []
        })
      });
      options.onStarted?.(started);
      patchCurrentThread(threadId, () => started.thread);
      trackTask(started.task);
      setStatus("Main Agent 正在理解本轮目标");
      consumeEvents(threadId, started.task.id, started.assistant_message_id, 0).catch((error) => {
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
      .filter((task) => task.thread_id === thread?.id && RUNNING_STATUSES.has(task.status))
      .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))[0];
    if (!active?.id) return;
    try {
      await api(`/agent-v2/tasks/${active.id}/cancel`, { method: "POST", body: JSON.stringify({}) });
      controllersRef.current.get(active.id)?.abort();
      untrackTask(active.id);
      setStatus("已停止本轮任务");
      const latest = await api(`/threads/${thread.id}`);
      patchCurrentThread(thread.id, () => latest);
    } catch (error) {
      setChatError(error.message || "停止任务失败");
    }
  }, [patchCurrentThread, setStatus, tasksById, thread?.id, untrackTask]);

  const steerThreadChat = useCallback(async (text) => {
    const active = Object.values(tasksById)
      .filter((task) => task.thread_id === thread?.id && RUNNING_STATUSES.has(task.status))
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
    const taskId = message?.refs?.agent_v2_task_id;
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
    return consumeEvents(thread.id, taskId, assistantMessageId, Number(message.refs?.agent_v2?.last_seq || 0));
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
    const pending = [...(thread?.messages || [])].reverse().find((message) =>
      message.kind === "assistant_reply" && ["pending", "streaming"].includes(message.status) && message.refs?.agent_v2_task_id
    );
    if (!pending || tasksById[pending.refs.agent_v2_task_id]) return;
    let cancelled = false;
    api(`/agent-v2/tasks/${pending.refs.agent_v2_task_id}`).then(({ task }) => {
      if (cancelled || !RESTORABLE_STATUSES.has(task.status)) return;
      trackTask(task);
      consumeEvents(thread.id, task.id, pending.id, Number(pending.refs?.agent_v2?.last_seq || 0)).catch(() => {});
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [consumeEvents, tasksById, thread, trackTask]);

  const activeTask = Object.values(tasksById)
    .filter((task) => task.thread_id === thread?.id && RUNNING_STATUSES.has(task.status))
    .sort((left, right) => String(right.created_at || "").localeCompare(String(left.created_at || "")))[0] || null;
  const isRunning = Boolean(activeTask);
  return useMemo(() => ({ chatError, activeTask, isRunning, sendThreadChat, steerThreadChat, stopThreadChat, retryThreadChat, resolveApproval, undoOperationBatch }), [activeTask, chatError, isRunning, resolveApproval, retryThreadChat, sendThreadChat, steerThreadChat, stopThreadChat, undoOperationBatch]);
}
