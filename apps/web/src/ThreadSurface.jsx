import { ArrowDown, Blocks, Copy, GitBranch, RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { THREAD_COPY } from "./copy/zh.js";
import { AssistantSuggestions } from "./features/thread/AssistantSuggestions.jsx";
import { ActivityRow } from "./features/thread/ActivityRow.jsx";
import { AgentRunTrace } from "./features/thread/AgentRunTrace.jsx";
import { AgentV2Activity } from "./features/thread/AgentV2Activity.jsx";
import { MarkdownMessage } from "./features/thread/MarkdownMessage.jsx";
import { MessageAttachments } from "./features/thread/MessageAttachments.jsx";
import { SourceCitations } from "./features/thread/SourceCitations.jsx";
import { ResearchTaskShelf } from "./features/thread/ResearchTaskShelf.jsx";
import { messageMeta, resultItems, resultItemTitle, syntheticMessages } from "./messages.js";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

function localizeMessageContent(content = "") {
  return String(content)
    .replace(/Exported\s+(\d+)\s+context cards?/i, THREAD_COPY.exportedCards)
    .replace(/(\d+)\s+context cards?/gi, "$1 张上下文卡片")
    .replace(/No summary\.?/gi, THREAD_COPY.noSummary);
}

function isActivity(message) {
  return message.role === "tool" || ["state", "tool_run", "task_pack"].includes(message.kind);
}

function transcriptGroups(messages) {
  const groups = [];
  for (const message of messages) {
    if (!isActivity(message)) {
      groups.push({ type: "message", message });
      continue;
    }
    const previous = groups[groups.length - 1];
    if (previous?.type === "activity") previous.messages.push(message);
    else groups.push({ type: "activity", messages: [message] });
  }
  return groups;
}

function readableGoal(goal, fallback) {
  if (!goal) return fallback;
  if (goal.includes("导出可交给 Codex") || goal.includes("可交给 Codex 的上下文包")) {
    return "围绕当前成果目标检索证据，形成可核查、可确认的研究判断。";
  }
  return goal;
}

export function ThreadSurface({
  thread,
  chatError,
  onOpenCanvas,
  onSuggestionAction,
  onRetryAssistant,
  onCitation,
  onOpenAttachment,
  onOpenChangeset,
  onInspectApproval,
  onUndoOperationBatch,
  researchTasks,
  onSteerResearch,
  onPauseResearch,
  onResumeResearch,
  onCancelResearch,
  onPromoteResearch
}) {
  const results = thread.result_cards || [];
  const runs = thread.tool_runs || [];
  const messages = syntheticMessages(thread);
  const groups = useMemo(() => transcriptGroups(messages), [messages]);
  const rootRef = useRef(null);
  const logRef = useRef(null);
  const nearBottomRef = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const running = (thread.agent_runs || []).some((run) => ["pending", "planning", "running"].includes(run.status))
    || messages.some((message) => message.kind === "assistant_reply" && ["pending", "streaming"].includes(message.status));
  const messageSignal = messages.map((item) => `${item.id}:${item.status}:${String(item.content || "").length}`).join("|");

  useEffect(() => {
    const scroller = logRef.current;
    if (!scroller) return undefined;
    const update = () => {
      const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
      nearBottomRef.current = nearBottom;
      setShowLatest(!nearBottom);
    };
    update();
    scroller.addEventListener("scroll", update, { passive: true });
    return () => scroller.removeEventListener("scroll", update);
  }, []);

  useEffect(() => {
    const scroller = logRef.current;
    if (!scroller || !nearBottomRef.current) return;
    requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: running ? "auto" : "smooth" }));
  }, [messageSignal, running]);

  function scrollToLatest() {
    const scroller = logRef.current;
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }

  return (
    <div className="thread-surface agent-thread-surface" ref={rootRef}>
      <header className="thread-chat-header">
        <div>
          <span>研究线程</span>
          <strong>{thread.title || "未命名研究问题"}</strong>
        </div>
        <p>{readableGoal(thread.goal, THREAD_COPY.defaultGoal)}</p>
      </header>
      <ResearchTaskShelf
        tasks={researchTasks}
        onSteer={onSteerResearch}
        onPause={onPauseResearch}
        onResume={onResumeResearch}
        onCancel={onCancelResearch}
        onPromote={onPromoteResearch}
      />
      <section className="chat-log" ref={logRef} aria-live="polite" aria-busy={running}>
        {groups.map((group, index) => group.type === "activity" ? (
          <ActivityRow key={`activity-${index}`} messages={group.messages} />
        ) : (
          <MessageTurn
            key={group.message.id || `${group.message.kind}-${group.message.created_at}`}
            message={group.message}
            results={results}
            runs={runs}
            onOpenCanvas={onOpenCanvas}
            onSuggestionAction={onSuggestionAction}
            onRetryAssistant={onRetryAssistant}
            onCitation={onCitation}
            onOpenAttachment={onOpenAttachment}
            onOpenChangeset={onOpenChangeset}
            onInspectApproval={onInspectApproval}
            onUndoOperationBatch={onUndoOperationBatch}
          />
        ))}
        {chatError && <div className="chat-error-row">{chatError}</div>}
        {!messages.length && (
          <div className="chat-empty-state">
            <strong>{THREAD_COPY.waitingTitle}</strong>
            <p>{THREAD_COPY.waitingBody}</p>
          </div>
        )}
      </section>
      {showLatest && <button type="button" className="jump-latest" onClick={scrollToLatest}><ArrowDown size={13} />最新消息</button>}
    </div>
  );
}

function MessageTurn({ message, results, runs, onOpenCanvas, onSuggestionAction, onRetryAssistant, onCitation, onOpenAttachment, onOpenChangeset, onInspectApproval, onUndoOperationBatch }) {
  const result = results.find((item) => item.id === message.linked_result_id);
  const run = runs.find((item) => item.id === message.linked_tool_run_id);
  const isUser = message.role === "user";
  const isAgentReply = message.kind === "assistant_reply";
  const meta = messageMeta(message);
  const proposals = Array.isArray(message.refs?.action_proposals) ? message.refs.action_proposals : [];
  const firstProposal = proposals.find((proposal) => proposal.status === "pending") || proposals[0];
  const changesets = Array.isArray(message.refs?.changesets) ? message.refs.changesets : [];
  const firstChangesetId = message.refs?.changeset_ids?.[0] || changesets[0]?.id;
  const citations = message.refs?.citations || [];
  const suggestions = (message.refs?.suggestions || []).length
    ? message.refs.suggestions
    : (message.refs?.next_actions || []);
  const attachments = message.refs?.turn_attachments || message.refs?.context_snapshot?.turn_attachments || [];
  const content = result ? result.raw_text.slice(0, 1200) : localizeMessageContent(message.content);
  const processing = isAgentReply && ["pending", "streaming"].includes(message.status);
  const isAgentV2 = ["v2", "v3"].includes(message.refs?.agent_runtime) || Boolean(message.refs?.agent_v2_task_id || message.refs?.agent_v3_task_id);

  return (
    <article
      className={cx("conversation-turn", isUser ? "user-turn" : "assistant-turn", isAgentReply && "agent-turn")}
      data-message-status={message.status || "done"}
      data-agent-task-id={message.refs?.agent_v2_task_id || ""}
    >
      {isUser && <MessageAttachments attachments={attachments} onOpen={onOpenAttachment} />}
      <div className="turn-meta">{isUser ? "你" : isAgentReply ? "Main Agent" : "助手"}{meta ? ` · ${meta}` : ""}</div>
      <div className={cx("turn-body", isUser && "user-bubble")}>
        {isUser ? <div className="user-message-body">{content}</div> : (
          <MarkdownMessage content={content} citations={citations} onCitation={onCitation} />
        )}
      </div>
      {isAgentV2 && <AgentV2Activity message={message} onInspectApproval={onInspectApproval} onUndoOperationBatch={onUndoOperationBatch} />}
      {processing && !isAgentV2 && <AgentRunTrace message={message} onOpenChangeset={onOpenChangeset} />}
      {result && <ResultSummary card={result} />}
      {!processing && isAgentReply && !isAgentV2 && <AgentRunTrace message={message} onOpenChangeset={onOpenChangeset} />}
      {isAgentReply && <SourceCitations citations={citations} onOpen={onCitation} />}
      {run && <div className="legacy-run-summary">{run.input_summary || run.summary || THREAD_COPY.emptyInputSummary}</div>}
      <AssistantSuggestions suggestions={suggestions.slice(0, 2)} onAction={onSuggestionAction} />
      {isAgentReply && !processing && (
        <div className="assistant-actions">
          <button type="button" onClick={() => onRetryAssistant?.(message.id)}><RefreshCcw size={14} />重试</button>
          <button type="button" onClick={() => navigator.clipboard?.writeText(message.content || "")}><Copy size={14} />复制</button>
          {firstChangesetId && <button type="button" onClick={() => onOpenChangeset?.(firstChangesetId)}><GitBranch size={14} />查看修改</button>}
          {firstProposal && (
            <button type="button" onClick={() => onSuggestionAction?.({ action: "inspect_proposal", id: firstProposal.id, payload: { proposal_id: firstProposal.id, type: firstProposal.type } })}>
              <GitBranch size={14} />查看提案
            </button>
          )}
        </div>
      )}
      {message.kind === "result" && <button className="inline-result-action" type="button" onClick={onOpenCanvas}><Blocks size={14} />打开 Canvas</button>}
    </article>
  );
}

function ResultSummary({ card }) {
  const findings = resultItems(card, "findings");
  const tasks = resultItems(card, "next_tasks");
  if (!findings.length && !tasks.length) return null;
  return (
    <div className="result-summary">
      {!!findings.length && <div><strong>{THREAD_COPY.newFindings}</strong>{findings.slice(0, 3).map((item, index) => <span key={`finding-${index}`}>{resultItemTitle(item)}</span>)}</div>}
      {!!tasks.length && <div><strong>{THREAD_COPY.nextTasks}</strong>{tasks.slice(0, 3).map((item, index) => <span key={`task-${index}`}>{resultItemTitle(item)}</span>)}</div>}
    </div>
  );
}
