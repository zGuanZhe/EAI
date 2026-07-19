import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, FlaskConical, Layers, MessageCircle, Paperclip, Search, Send, Square, X } from "lucide-react";
import { COMPOSER_COPY } from "./copy/zh.js";
import { Select } from "./components/ui/index.jsx";

const SOURCE_OPTIONS = [
  { value: "local_and_external", label: "全部来源" },
  { value: "local_only", label: "仅本地" },
  { value: "atlas_only", label: "仅 Atlas" },
  { value: "external_only", label: "仅外部" },
  { value: "none", label: "不检索" }
];

const DELIVERABLE_OPTIONS = [
  { value: "research_note", label: "研究纪要" },
  { value: "answer", label: "直接回答" },
  { value: "comparison", label: "证据比较" },
  { value: "literature_map", label: "文献图谱" },
  { value: "evidence_audit", label: "证据审计" }
];

const DEPTH_OPTIONS = [
  { value: "standard", label: "标准" },
  { value: "deep", label: "深入" }
];

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function Composer({
  value,
  setValue,
  onSubmit,
  toolOpen,
  setToolOpen,
  onOpenContext,
  isHome,
  commands,
  onRunCommand,
  isRunning = false,
  onStop,
  attachments = [],
  onRemoveAttachment,
  interactionMode = "ask",
  onInteractionModeChange,
  sourcePolicy = "local_and_external",
  onSourcePolicyChange,
  researchDeliverable = "research_note",
  onResearchDeliverableChange,
  researchDepth = "standard",
  onResearchDepthChange,
  readOnly = false,
  bootstrapMode = false,
  submitBusy = false
}) {
  const commandQuery = value.startsWith("/") ? value.trim().toLowerCase() : "";
  const [dismissedCommand, setDismissedCommand] = useState("");
  const visibleCommands = useMemo(() => (commands || [])
    .filter((item) => {
      if (!commandQuery) return item.id !== "atlas";
      if (commandQuery.startsWith(`${item.command} `)) return true;
      const haystack = `${item.command} ${item.label} ${item.description}`.toLowerCase();
      return haystack.includes(commandQuery);
    })
    .slice(0, commandQuery ? 6 : 4), [commands, commandQuery]);
  const showCommandPanel = !bootstrapMode && (toolOpen || (value.startsWith("/") && commandQuery !== dismissedCommand));
  const [activeIndex, setActiveIndex] = useState(0);
  const textareaRef = useRef(null);
  const sourceSummary = {
    none: "不检索",
    atlas_only: "Atlas",
    local_only: "本地与 Atlas",
    external_only: "学术与已配置网页",
    local_and_external: "本地、Atlas、学术与已配置网页"
  }[sourcePolicy] || "未知来源范围";
  const mayWrite = /(?:创建|更新|修改|删除|保存|记住|加入|create|update|delete|save|remember)/i.test(value);
  const contextSummary = attachments.length ? `本线程 + ${attachments.length} 项本轮资料` : "本线程";
  const boundarySummary = interactionMode === "research" || !mayWrite ? "不修改工作区" : "修改前需确认";

  useEffect(() => {
    setActiveIndex(0);
  }, [commandQuery, toolOpen, visibleCommands.length]);

  useEffect(() => {
    if (!value.startsWith("/") || commandQuery !== dismissedCommand) return;
    if (value.trim() !== dismissedCommand) setDismissedCommand("");
  }, [value, commandQuery, dismissedCommand]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }, [value]);

  function runVisibleCommand(index = activeIndex) {
    const command = visibleCommands[index];
    if (command) onRunCommand(command.id);
  }

  function handleKeyDown(event) {
    if (event.key === "Escape" && isRunning) {
      event.preventDefault();
      onStop?.();
      return;
    }
    if (!showCommandPanel) {
      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) {
        event.preventDefault();
        event.currentTarget.form?.requestSubmit();
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setToolOpen(false);
      if (commandQuery) setDismissedCommand(commandQuery);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!visibleCommands.length) return;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => (current + delta + visibleCommands.length) % visibleCommands.length);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      const raw = value.trim();
      const hasAtlasArgument = /^\/atlas\s+\S+/i.test(raw);
      const shouldRunPanel = toolOpen || raw === "/" || raw === visibleCommands[activeIndex]?.command;
      if (visibleCommands.length && shouldRunPanel && !hasAtlasArgument) {
        event.preventDefault();
        runVisibleCommand();
        return;
      }
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className={cx("composer-shell", isHome && "home-composer-shell")} data-onboarding="composer">
      {!!attachments.length && (
        <div className="composer-attachments" aria-label="本轮附件">
          {attachments.map((attachment, index) => (
            <span key={attachment.id || `${attachment.type}-${index}`}>
              <Paperclip size={12} />
              <b>{attachment.title || "未命名资料"}</b>
              <button type="button" title="移除本轮附件" onClick={() => onRemoveAttachment?.(attachment)}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}
      <form className="composer" onSubmit={onSubmit}>
        {showCommandPanel && (
          <div className="tool-picker command-palette">
            <div className="command-palette-head">
              <strong>{commandQuery ? COMPOSER_COPY.commandMatched : COMPOSER_COPY.commonActions}</strong>
              <span>{commandQuery || COMPOSER_COPY.commandHint}</span>
            </div>
            {visibleCommands.map((item) => {
              const Icon = item.icon;
              const isActive = visibleCommands[activeIndex]?.id === item.id;
              return (
                <button
                  type="button"
                  key={item.id}
                  className={cx(isActive && "active")}
                  onMouseEnter={() => setActiveIndex(visibleCommands.findIndex((command) => command.id === item.id))}
                  onClick={() => onRunCommand(item.id)}
                >
                  <Icon size={15} />
                  <span>
                    <strong>{item.label}</strong>
                    <em>{item.command} · {item.description}</em>
                  </span>
                </button>
              );
            })}
            {!visibleCommands.length && <p className="command-empty">{COMPOSER_COPY.commandEmpty}</p>}
          </div>
        )}
        <button type="button" className="composer-context-chip" onClick={onOpenContext} title={bootstrapMode ? "发送第一个问题后可以添加资料" : "添加或管理资料"} disabled={bootstrapMode || readOnly}>
          <Layers size={14} />
          <span>{attachments.length ? "管理资料" : "添加资料"}</span>
        </button>
        <button type="button" className="tool-toggle" onClick={() => setToolOpen(!toolOpen)} title={bootstrapMode ? "发送第一个问题后可以使用命令" : COMPOSER_COPY.toolTitle} disabled={bootstrapMode || readOnly}>
          <ChevronDown size={16} />
        </button>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={readOnly ? "当前数据以只读模式打开" : isRunning ? "追加要求或纠正方向..." : interactionMode === "research" ? "描述研究目标、判断标准或期望产物..." : "询问问题；信息型问题会默认检索可用来源..."}
          rows={1}
          disabled={readOnly || submitBusy}
        />
        <div className="composer-policy-row" data-onboarding="mode">
          <div className="composer-lane-switch" role="group" aria-label="工作方式">
            <button type="button" className={cx(interactionMode === "ask" && "active")} onClick={() => onInteractionModeChange?.("ask")} disabled={readOnly || submitBusy} title="日常询问"><MessageCircle size={13} />询问</button>
            <button type="button" className={cx(interactionMode === "research" && "active")} onClick={() => onInteractionModeChange?.("research")} disabled={readOnly || submitBusy} title="后台研究任务"><FlaskConical size={13} />研究任务</button>
          </div>
          <label className="composer-source-select" title="本轮允许检索的来源">
            <Search size={12} />
            <span className="sr-only">来源范围</span>
            <Select value={sourcePolicy} options={SOURCE_OPTIONS} onChange={onSourcePolicyChange} disabled={readOnly || submitBusy} ariaLabel="来源范围" compact />
          </label>
        </div>
        <div className="composer-run-actions">
          {isRunning && <button type="button" className="send-button stop" title="停止 Main Agent" onClick={onStop}><Square size={14} /></button>}
          <button type="submit" className="send-button" title={submitBusy ? "正在建立研究问题" : isRunning ? "追加要求" : COMPOSER_COPY.sendTitle} disabled={readOnly || submitBusy || !value.trim()}><Send size={16} /></button>
        </div>
      </form>
      <div className="composer-execution-summary" aria-live="polite">
        <span>{contextSummary} · {sourceSummary} · {boundarySummary}</span>
        {interactionMode === "research" && (
          <div className="composer-research-brief" aria-label="研究任务简报">
            <label>
              <span>产物</span>
              <Select
                value={researchDeliverable}
                options={DELIVERABLE_OPTIONS}
                onChange={onResearchDeliverableChange}
                disabled={readOnly || submitBusy}
                ariaLabel="期望产物"
                compact
              />
            </label>
            <label>
              <span>深度</span>
              <Select
                value={researchDepth}
                options={DEPTH_OPTIONS}
                onChange={onResearchDepthChange}
                disabled={readOnly || submitBusy}
                ariaLabel="研究深度"
                compact
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
