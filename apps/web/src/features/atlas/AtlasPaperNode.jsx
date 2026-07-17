import { Check, GitBranch, MessageSquare, PanelRight, Plus } from "lucide-react";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function AtlasPaperNode({
  paper,
  position,
  cardWidth,
  route,
  routeColor,
  routeBackground,
  memory,
  selected,
  related,
  relationHighlighted,
  inPath,
  pathIndex,
  inContext,
  relationCount,
  nodeRef,
  onSelect,
  onOpen,
  onAsk,
  onToggleContext,
  routeLabel
}) {
  function handleKeyDown(event) {
    if (event.key === "Enter") {
      event.preventDefault();
      onOpen(event);
    } else if (event.key === " ") {
      event.preventDefault();
      onSelect(event);
    }
  }

  return (
    <article
      ref={nodeRef}
      data-paper-id={paper.id}
      className={cx(
        "timeline-paper",
        paper.is_candidate && "candidate-paper",
        paper.candidate_status && `candidate-${paper.candidate_status}`,
        selected && "selected",
        related && "related",
        relationHighlighted && "relation-highlighted",
        inPath && "in-path",
        inContext && "context-added",
        memory && "has-memory",
        memory?.star && "starred"
      )}
      style={{
        left: position.x,
        top: position.y,
        width: cardWidth,
        "--route": routeColor,
        "--route-bg": routeBackground
      }}
      role="button"
      tabIndex="0"
      aria-label={`${paper.title}，${relationCount} 条关系`}
      onClick={onSelect}
      onDoubleClick={onOpen}
      onKeyDown={handleKeyDown}
    >
      <span className="paper-route-line" aria-hidden="true" />
      <span
        className={cx(
          "paper-evidence-dot",
          paper.evidence_status?.full_text ? "full-text" : paper.evidence_status?.verified_claims ? "claims" : paper.evidence_status?.metadata ? "metadata" : "missing"
        )}
        title={paper.evidence_status?.full_text ? "已有全文证据" : paper.evidence_status?.verified_claims ? `${paper.evidence_status.verified_claims} 条可验证论断` : paper.evidence_status?.metadata ? "身份元数据已核验" : "证据待补全"}
        aria-label="论文证据状态"
      />
      <span className="paper-port left" aria-hidden="true" />
      <span className="paper-port right" aria-hidden="true" />
      {inPath && <span className="path-index">{pathIndex + 1}</span>}
      <button
        type="button"
        className={cx("paper-add", inContext && "added")}
        title={inContext ? "从长期资料移除" : "加入长期资料"}
        onClick={(event) => {
          event.stopPropagation();
          onToggleContext();
        }}
      >
        {inContext ? <Check size={14} /> : <Plus size={14} />}
      </button>
      {paper.is_candidate && (
        <div className="candidate-badge">
          <span>{paper.candidate_status === "applied" ? "已应用" : paper.candidate_status === "deferred" ? "暂缓" : "候选"}</span>
          <em>{Math.round((paper.confidence || 0) * 100)}%</em>
        </div>
      )}
      <div className="paper-title">{paper.title}</div>
      <div className="paper-meta">{paper.year || "----"} · {paper.venue || "未知来源"}</div>
      <p className="paper-summary">{paper.summary || paper.why || paper.local_role || paper.why_included || "尚无摘要"}</p>
      <div className="paper-foot">
        <span>{routeLabel(route)}</span>
        <button type="button" title="查看关系" onClick={(event) => { event.stopPropagation(); onSelect(event); }}><GitBranch size={12} />{relationCount}</button>
      </div>
      <div className="paper-node-actions" aria-label="论文操作">
        <button type="button" onClick={(event) => { event.stopPropagation(); onAsk(); }}><MessageSquare size={13} />本轮询问</button>
        <button type="button" onClick={(event) => { event.stopPropagation(); onOpen(event); }}><PanelRight size={13} />阅读</button>
      </div>
    </article>
  );
}
