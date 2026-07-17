import { Clipboard, Send } from "lucide-react";

export function TaskPackPreview({ preview, selected, onPreview, onCopy, onRun, secrets }) {
  const hasOpenAI = Boolean(secrets?.providers?.some((item) => item === "openai" || item.provider === "openai"));
  if (!preview) {
    return (
      <section className="task-pack-preview-panel">
        <h3>Task Pack 预览</h3>
        <p>先生成预览，确认导出顺序和包含材料后再复制给 Codex。</p>
        <button className="primary-button wide" disabled={!selected} onClick={() => onPreview?.(selected?.id)}>
          生成预览
        </button>
      </section>
    );
  }
  return (
    <section className="task-pack-preview-panel">
      <h3>{preview.title || "Task Pack 预览"}</h3>
      <p>包含 {preview.included_cards || 0} 张材料，约 {preview.token_estimate || 0} tokens。</p>
      {!!preview.warnings?.length && (
        <div className="task-pack-warnings">
          {preview.warnings.map((item, index) => <span key={index}>{item}</span>)}
        </div>
      )}
      <pre>{preview.markdown?.slice(0, 1200)}</pre>
      <div className="task-pack-actions">
        <button className="primary-button wide" onClick={onCopy}><Clipboard size={15} /> 复制 Task Pack</button>
        <button className="ghost-button wide soft" disabled={!hasOpenAI} onClick={onRun}><Send size={15} /> 发送 API</button>
      </div>
    </section>
  );
}
