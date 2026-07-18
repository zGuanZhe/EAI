import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Layers, Paperclip, Send, Square, X } from "lucide-react";
import { COMPOSER_COPY } from "./copy/zh.js";

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
  intentMode = "auto",
  onIntentModeChange,
  readOnly = false
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
  const showCommandPanel = toolOpen || (value.startsWith("/") && commandQuery !== dismissedCommand);
  const [activeIndex, setActiveIndex] = useState(0);
  const textareaRef = useRef(null);

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
    <div className={cx("composer-shell", isHome && "home-composer-shell")}>
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
        <button type="button" className="composer-context-chip" onClick={onOpenContext} title="添加或管理资料">
          <Layers size={14} />
          <span>{attachments.length ? "管理资料" : "添加资料"}</span>
        </button>
        <button type="button" className="tool-toggle" onClick={() => setToolOpen(!toolOpen)} title={COMPOSER_COPY.toolTitle}>
          <ChevronDown size={16} />
        </button>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={readOnly ? "当前版本以只读模式打开此研究数据库" : isRunning ? "追加要求或纠正方向..." : intentMode === "execute" ? "描述要在沙箱中执行的任务，命令会逐次请求确认..." : COMPOSER_COPY.placeholder}
          rows={1}
          disabled={readOnly}
        />
        <label className="composer-mode-select" title="选择 Main Agent 本轮工作方式">
          <span className="sr-only">本轮工作方式</span>
          <select value={intentMode} onChange={(event) => onIntentModeChange?.(event.target.value)} aria-label="本轮工作方式" disabled={readOnly}>
            <option value="auto">自动</option>
            <option value="chat">仅聊天</option>
            <option value="local">仅本地</option>
            <option value="deep_research">深度研究</option>
            <option value="execute">执行任务</option>
          </select>
        </label>
        <div className="composer-run-actions">
          {isRunning && <button type="button" className="send-button stop" title="停止 Main Agent" onClick={onStop}><Square size={14} /></button>}
          <button type="submit" className="send-button" title={isRunning ? "追加要求" : COMPOSER_COPY.sendTitle} disabled={readOnly || !value.trim()}><Send size={16} /></button>
        </div>
      </form>
    </div>
  );
}
