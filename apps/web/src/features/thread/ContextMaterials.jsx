import { ArrowRight, ArrowUpRight, Paperclip, Pin, Plus, Trash2, X } from "lucide-react";
import { Select } from "../../components/ui/index.jsx";

const PRIORITY_OPTIONS = [
  { value: "0", label: "低" },
  { value: "1", label: "普通" },
  { value: "2", label: "高" },
  { value: "3", label: "关键" }
];

function contextForAgent(card) {
  return card.include_in_agent !== false;
}

export function ContextMaterials({
  attachments = [],
  cards = [],
  onOpen,
  onRemoveAttachment,
  onPromoteAttachment,
  onRemoveCard,
  onTogglePinned,
  onSetPriority,
  onSetNote,
  onOpenAtlas
}) {
  const longTerm = cards.filter(contextForAgent);
  return (
    <div className="context-materials">
      <header>
        <span>对话资料</span>
        <strong>强调材料，而不是发送门槛</strong>
        <p>Main Agent 会自动读取线程并检索 Atlas；这里的材料只用于明确本轮或长期重点。</p>
      </header>

      <section>
        <div className="context-material-heading"><strong>本轮附件</strong><span>发送后自动清空</span></div>
        {!attachments.length && (
          <div className="context-material-empty">
            <p>暂无附件，可从论文预览中选择“本轮询问”。</p>
            <button type="button" onClick={onOpenAtlas}>去 Atlas 选择 <ArrowRight size={13} /></button>
          </div>
        )}
        {attachments.map((attachment, index) => (
          <div className="context-material-row" key={attachment.id || `${attachment.type}-${index}`}>
            <Paperclip size={14} />
            <button type="button" className="context-material-title" onClick={() => onOpen?.(attachment)}>{attachment.title}</button>
            <button type="button" title="转为长期资料" onClick={() => onPromoteAttachment?.(attachment)}><Plus size={13} /></button>
            <button type="button" title="移除本轮附件" onClick={() => onRemoveAttachment?.(attachment)}><X size={13} /></button>
          </div>
        ))}
      </section>

      <section>
        <div className="context-material-heading"><strong>长期资料</strong><span>持续参与后续对话</span></div>
        {!longTerm.length && (
          <div className="context-material-empty">
            <p>暂无长期资料。Agent 仍会自动检索 Atlas。</p>
            <button type="button" onClick={onOpenAtlas}>浏览 Atlas <ArrowRight size={13} /></button>
          </div>
        )}
        {longTerm.map((card) => (
          <div className="context-material-row long-term" key={card.id}>
            <Pin size={14} />
            <button type="button" className="context-material-title" onClick={() => onOpen?.(card)}>{card.title}</button>
            <button type="button" title="打开来源" onClick={() => onOpen?.(card)}><ArrowUpRight size={13} /></button>
            <button type="button" title="移除长期资料" onClick={() => onRemoveCard?.(card)}><Trash2 size={13} /></button>
          </div>
        ))}
      </section>

      {!!longTerm.length && (
        <details className="context-material-advanced">
          <summary>高级资料设置</summary>
          <div>
            {longTerm.map((card) => (
              <article key={card.id}>
                <strong>{card.title}</strong>
                <div>
                  <button type="button" className={card.pinned ? "active" : ""} onClick={() => onTogglePinned?.(card)}>{card.pinned ? "已置顶" : "置顶"}</button>
                  <label>
                    <span>优先级</span>
                    <Select value={card.priority ?? 1} options={PRIORITY_OPTIONS} onChange={(value) => onSetPriority?.(card, value)} ariaLabel={`${card.title}优先级`} compact />
                  </label>
                </div>
                <input defaultValue={card.agent_note || ""} onBlur={(event) => event.target.value !== (card.agent_note || "") && onSetNote?.(card, event.target.value)} placeholder="给 Main Agent 的备注" />
              </article>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
