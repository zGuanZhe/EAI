import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Check, Clipboard, Clock3, Copy, FileText, GitBranch, Link2, ListChecks,
  PanelRight, Pencil, Plus, Search, Send, Sparkles, SquareDashedMousePointer, X
} from "lucide-react";
import { buildAtlasOverview } from "../../atlasUtils.js";
import { InlineNotice } from "../../components/ui/index.jsx";
import { NODE_LABELS, TASK_STATUS_LABELS } from "../canvas/model.js";
import { AgentApprovalInspector } from "./AgentApprovalInspector.jsx";
import { ChangeSetInspector } from "../thread/ChangeSetInspector.jsx";
import { TaskPackPreview } from "../taskPack/TaskPackPreview.jsx";
import {
  cx, getPaperRouteName, objectMemoryKey, objectRefFromDetail, recommendedTemplateIds, tokenEstimate
} from "../../workspace/shared.js";

export function RightRail({
  open,
  mode,
  setMode,
  onClose,
  onOpen,
  atlasControls,
  detail,
  activeAtlas,
  memoryMap,
  onSaveMemory,
  cards,
  turnAttachments,
  onRemoveTurnAttachment,
  onPromoteTurnAttachment,
  onOpenMaterial,
  selectedCards,
  onToggleCard,
  onToggleAgentCard,
  onTogglePinnedCard,
  onSetCardPriority,
  onSetCardAgentNote,
  onRemoveCard,
  exported,
  pasteText,
  setPasteText,
  onPasteResult,
  resultPreview,
  onConfirmResultPreview,
  onCancelResultPreview,
  status,
  onExport,
  templates,
  selectedTemplateId,
  onSelectTemplate,
  taskPackPreview,
  onPreviewTaskPack,
  onCopyTaskPack,
  onRunTaskPack,
  secrets,
  onRecommendedTemplate,
  onOpenPaperDetail,
  onAskPaper,
  onRetainPaper,
  proposals = [],
  activeProposalId,
  onSelectProposal,
  onConfirmProposal,
  onRejectProposal,
  changesets = [],
  activeChangesetId,
  onSelectChangeset,
  onConfirmChangeset,
  onRejectChangeset,
  onUndoChangeset,
  atlasUpdates,
  atlasUpdatePreview,
  atlasUpdatePaste,
  setAtlasUpdatePaste,
  selectedUpdateAction,
  onSelectUpdateAction,
  onPreviewAtlasUpdate,
  onCopyAtlasUpdate,
  onPasteAtlasUpdate,
  onUpdateAtlasCandidate,
  onApplyAtlasCandidate,
  onBulkApplyAtlasCandidates,
  onFocusAtlasCandidate,
  onAddCandidateCard,
  onResolveApproval
}) {
  if (!open) return null;

  return (
    <aside className="right-rail ui-drawer">
      <div className="rail-tabs">
        <button className={cx(mode === "atlas" && "active")} onClick={() => setMode("atlas")}>
          <GitBranch size={15} /> Atlas
        </button>
        <button className={cx(mode === "detail" && "active")} onClick={() => setMode("detail")}>
          <PanelRight size={15} /> 详情
        </button>
        <button className={cx(mode === "changes" && "active")} onClick={() => setMode("changes")}>
          <ListChecks size={15} /> 变更
        </button>
        <button className="rail-close" onClick={onClose} title="关闭右栏">
          <X size={15} />
        </button>
      </div>
      {mode === "atlas" ? (
        <AtlasControlRail
          controls={atlasControls}
          selectedCards={selectedCards}
          onExport={onExport}
          onOpenTemplates={() => setMode("templates")}
          atlasUpdates={atlasUpdates}
          preview={atlasUpdatePreview}
          pasteText={atlasUpdatePaste}
          setPasteText={setAtlasUpdatePaste}
          selectedAction={selectedUpdateAction}
          onSelectAction={onSelectUpdateAction}
          onPreviewUpdate={onPreviewAtlasUpdate}
          onCopyUpdate={onCopyAtlasUpdate}
          onPasteUpdate={onPasteAtlasUpdate}
          onBulkApply={onBulkApplyAtlasCandidates}
          onFocusCandidate={onFocusAtlasCandidate}
        />
      ) : mode === "detail" ? (
        <DetailInspector
          detail={detail}
          activeAtlas={activeAtlas}
          memoryMap={memoryMap}
          templates={templates}
          onSaveMemory={onSaveMemory}
          onOpenTemplates={() => setMode("templates")}
          onRecommendedTemplate={onRecommendedTemplate}
          onOpenPaperDetail={onOpenPaperDetail}
          onAskPaper={onAskPaper}
          onRetainPaper={onRetainPaper}
          onUpdateAtlasCandidate={onUpdateAtlasCandidate}
          onApplyAtlasCandidate={onApplyAtlasCandidate}
          onAddCandidateCard={onAddCandidateCard}
          onResolveApproval={onResolveApproval}
          onCloseApproval={onClose}
        />
      ) : mode === "templates" ? (
        <TemplateRail
          templates={templates}
          selectedTemplateId={selectedTemplateId}
          onSelectTemplate={onSelectTemplate}
          preview={taskPackPreview}
          onPreview={onPreviewTaskPack}
          onCopy={onCopyTaskPack}
          onRun={onRunTaskPack}
          secrets={secrets}
          detail={detail}
          selectedCards={selectedCards}
          status={status}
        />
      ) : mode === "changes" ? (
        <div className="change-rail">
          <AgentProposalPanel
            proposals={proposals}
            activeProposalId={activeProposalId}
            onSelectProposal={onSelectProposal}
            onConfirmProposal={onConfirmProposal}
            onRejectProposal={onRejectProposal}
          />
          <ChangeSetInspector
            changesets={changesets}
            activeId={activeChangesetId}
            onSelect={onSelectChangeset}
            onConfirm={onConfirmChangeset}
            onReject={onRejectChangeset}
            onUndo={onUndoChangeset}
          />
        </div>
      ) : (
        <AtlasControlRail
          controls={atlasControls}
          selectedCards={selectedCards}
          onExport={onExport}
          onOpenTemplates={() => setMode("templates")}
          atlasUpdates={atlasUpdates}
          preview={atlasUpdatePreview}
          pasteText={atlasUpdatePaste}
          setPasteText={setAtlasUpdatePaste}
          selectedAction={selectedUpdateAction}
          onSelectAction={onSelectUpdateAction}
          onPreviewUpdate={onPreviewAtlasUpdate}
          onCopyUpdate={onCopyAtlasUpdate}
          onPasteUpdate={onPasteAtlasUpdate}
          onBulkApply={onBulkApplyAtlasCandidates}
          onFocusCandidate={onFocusAtlasCandidate}
        />
      )}
    </aside>
  );
}

function AgentProposalPanel({ proposals = [], activeProposalId, onSelectProposal, onConfirmProposal, onRejectProposal }) {
  const visible = proposals.filter((proposal) => proposal.status === "pending");
  const selected = visible.find((proposal) => proposal.id === activeProposalId) || visible[0];
  if (!visible.length) {
    return (
      <section className="agent-proposal-panel empty">
        <span>智能体提案</span>
        <strong>暂无待确认修改</strong>
        <p>主对话智能体会把 Atlas 更新、论文卡补全和对象记忆草稿放在这里，确认后才写入个人数据层。</p>
      </section>
    );
  }
  return (
    <section className="agent-proposal-panel">
      <div className="agent-proposal-head">
        <div>
          <span>智能体提案</span>
          <strong>{visible.length} 个待确认修改</strong>
        </div>
        <em>只写入 data/personal</em>
      </div>
      <div className="agent-proposal-tabs">
        {visible.map((proposal) => (
          <button
            type="button"
            key={proposal.id}
            className={cx(selected?.id === proposal.id && "active")}
            onClick={() => onSelectProposal?.(proposal.id)}
          >
            {proposalTypeLabel(proposal.type)}
          </button>
        ))}
      </div>
      {selected && (
        <article className="agent-proposal-detail">
          <span>{proposalTypeLabel(selected.type)} · {selected.risk || "low"}</span>
          <h3>{selected.summary}</h3>
          <div className="agent-diff-list">
            {(selected.diff || []).map((item, index) => (
              <div className="agent-diff-row" key={`${item.field}-${index}`}>
                <strong>{fieldLabel(item.field)}</strong>
                <p>
                  <em>当前</em>
                  <span>{formatDiffValue(item.before) || "空"}</span>
                </p>
                <p>
                  <em>建议</em>
                  <span>{formatDiffValue(item.after) || "空"}</span>
                </p>
                {item.reason && <small>{item.reason}</small>}
              </div>
            ))}
            {!selected.diff?.length && <p className="empty-panel">这个提案没有字段级 diff，建议先驳回并让智能体重新生成。</p>}
          </div>
          <div className="proposal-actions">
            <button className="primary-button" type="button" onClick={() => onConfirmProposal?.(selected.id)}>
              <Check size={15} /> 确认写入
            </button>
            <button className="ghost-button soft" type="button" onClick={() => onRejectProposal?.(selected.id)}>
              <X size={15} /> 驳回
            </button>
          </div>
        </article>
      )}
    </section>
  );
}

function proposalTypeLabel(type) {
  return {
    context_injection: "上下文注入",
    object_memory: "对象记忆",
    paper_card_update: "论文卡补全",
    atlas_candidate: "候选论文",
    task_pack_preview: "Task Pack 预览"
  }[type] || "智能体提案";
}

function fieldLabel(field) {
  return {
    title: "标题",
    summary: "摘要",
    judgement: "核心判断",
    note: "短笔记",
    tags: "标签",
    maturity: "成熟度",
    star: "星标",
    core_innovation: "核心创新",
    core_technology: "核心技术",
    evidence: "证据",
    limitations: "局限",
    reusable_insight: "可复用启发",
    why: "推荐理由",
    relevance: "相关性",
    suggested_route_id: "建议路线",
    confidence: "置信度"
  }[field] || field;
}

function formatDiffValue(value) {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function TemplateRail({
  templates = [],
  selectedTemplateId,
  onSelectTemplate,
  preview,
  onPreview,
  onCopy,
  onRun,
  secrets,
  detail,
  selectedCards,
  status
}) {
  const selected = templates.find((item) => item.id === selectedTemplateId) || templates[0];
  const focusedTitle = detail?.type === "paper"
    ? detail.value?.title
    : detail?.type === "relation"
      ? `${detail.source?.title || detail.value?.source} -> ${detail.target?.title || detail.value?.target}`
      : detail?.value?.title;

  return (
    <div className="template-rail">
      <div className="rail-heading">
        <span>研究动作</span>
        <h2>{selected?.title || "研究动作模板"}</h2>
        <p>基于当前对象、Context Canvas 和已选材料生成 Task Pack。</p>
      </div>

      <div className="template-focus">
        <span>当前焦点</span>
        <strong>{focusedTitle || "整张 Atlas / 当前线程"}</strong>
        <em>{selectedCards.length} 张 Context card</em>
      </div>

      <div className="template-list">
        {templates.map((item) => (
          <button
            type="button"
            key={item.id}
            className={cx(selectedTemplateId === item.id && "active")}
            onClick={() => onSelectTemplate(item.id)}
          >
            <strong>{item.short_title || item.title}</strong>
            <span>{item.description}</span>
          </button>
        ))}
      </div>

      <TaskPackPreview
        preview={preview}
        selected={selected}
        onPreview={onPreview}
        onCopy={onCopy}
        onRun={onRun}
        secrets={secrets}
      />
      <div className="status-line">{status}</div>
    </div>
  );
}

const ATLAS_UPDATE_ACTIONS = [
  { id: "recent", label: "更新最新论文", hint: "最近 1-2 年" },
  { id: "all", label: "更新全部论文", hint: "补齐重要缺口" },
  { id: "complete_cards", label: "补全旧论文卡", hint: "摘要/贡献/局限" },
  { id: "evidence", label: "补充证据", hint: "代码/项目/后续" }
];

function AtlasUpdateRail({
  updates,
  preview,
  pasteText,
  setPasteText,
  selectedAction,
  onSelectAction,
  onPreview,
  onCopy,
  onPaste,
  onBulkApply,
  onFocusCandidate
}) {
  const candidates = updates?.candidates || [];
  const pending = candidates.filter((item) => item.status === "pending");
  const applied = candidates.filter((item) => item.status === "applied");
  const deferred = candidates.filter((item) => item.status === "deferred");
  const highConfidence = pending.filter((item) => !item.duplicate_of && (item.confidence || 0) >= 0.65).length;
  return (
    <section className="atlas-update-rail">
      <div className="atlas-update-head">
        <div>
          <span>论文更新</span>
          <strong>把候选直接放回这张 Atlas 表</strong>
        </div>
        <button type="button" onClick={onBulkApply} disabled={!highConfidence}>
          <Check size={14} /> 一键应用 {highConfidence}
        </button>
      </div>
      <div className="atlas-update-actions">
        {ATLAS_UPDATE_ACTIONS.map((action) => (
          <button
            type="button"
            key={action.id}
            className={cx(selectedAction === action.id && "active")}
            onClick={() => {
              onSelectAction?.(action.id);
              onPreview?.(action.id);
            }}
          >
            <strong>{action.label}</strong>
            <span>{action.hint}</span>
          </button>
        ))}
      </div>
      <details className="atlas-update-advanced">
        <summary>更新包编辑器 / 手动导入</summary>
        <div className="atlas-update-runbar">
          <button type="button" className="ghost-button soft" onClick={() => onCopy?.(selectedAction)}>
            <Copy size={14} /> 复制更新包
          </button>
          <span>{preview?.token_estimate || 0} tokens</span>
        </div>
        <label className="atlas-update-paste">
          <span>手动导入返回</span>
          <textarea
            value={pasteText}
            onChange={(event) => setPasteText?.(event.target.value)}
            placeholder="粘贴 eai-atlas-update/v1 JSON，候选会以虚框卡出现在中间表格。"
          />
        </label>
        <button type="button" className="primary-button wide" disabled={!pasteText?.trim()} onClick={onPaste}>
          <Clipboard size={15} /> 解析并显示虚框候选
        </button>
      </details>
      <div className="atlas-update-stats">
        <span><b>{pending.length}</b> 待审</span>
        <span><b>{applied.length}</b> 已应用</span>
        <span><b>{deferred.length}</b> 暂缓</span>
      </div>
      {!!candidates.length && (
        <div className="atlas-update-mini-list">
          {candidates.slice(0, 5).map((candidate) => (
            <button type="button" key={candidate.id} onClick={() => onFocusCandidate?.(candidate.id)}>
              <i className={candidate.status} />
              <span>{candidate.title}</span>
              <em>{candidate.suggested_route_id || "未定路线"}</em>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

function AtlasControlRail({
  controls,
  selectedCards,
  onExport,
  onOpenTemplates,
  atlasUpdates,
  preview,
  pasteText,
  setPasteText,
  selectedAction,
  onSelectAction,
  onPreviewUpdate,
  onCopyUpdate,
  onPasteUpdate,
  onBulkApply,
  onFocusCandidate
}) {
  if (!controls) {
    return <div className="empty-panel">正在加载 Atlas 控制项...</div>;
  }
  const overview = buildAtlasOverview(controls);
  return (
    <div className="atlas-control-rail">
      <div className="rail-heading">
        <span>Atlas {controls.activeAtlas}</span>
        <h2>{controls.titleCn || controls.title}</h2>
        <p>{controls.totalCount} 篇正式 · {controls.candidateCount || 0} 篇候选 · {controls.edgeCount} 条关系</p>
      </div>

      <section className="atlas-overview">
        <div className="atlas-overview-block">
          <span>研究方向</span>
          <p>{overview.focus}</p>
        </div>
        <div className="atlas-overview-block trend">
          <span>趋势判断</span>
          <p>{overview.trend}</p>
        </div>
        {!!overview.activeRoutes.length && (
          <div className="atlas-route-list">
            <strong>主要路线</strong>
            {overview.activeRoutes.map((route) => (
              <div key={route.id}>
                <i style={{ "--route": route.color || "#8b93a0" }} />
                <span>{route.label}</span>
                <em>{route.count} 篇</em>
              </div>
            ))}
          </div>
        )}
        {!!overview.latestYears.length && (
          <div className="atlas-year-list">
            <strong>近期活跃</strong>
            {overview.latestYears.map((year) => (
              <span key={year.year}>{year.year} · {year.count} 篇</span>
            ))}
          </div>
        )}
      </section>

      <AtlasUpdateRail
        updates={atlasUpdates}
        preview={preview}
        pasteText={pasteText}
        setPasteText={setPasteText}
        selectedAction={selectedAction}
        onSelectAction={onSelectAction}
        onPreview={onPreviewUpdate}
        onCopy={onCopyUpdate}
        onPaste={onPasteUpdate}
        onBulkApply={onBulkApply}
        onFocusCandidate={onFocusCandidate}
      />

      <details className="atlas-control-details">
        <summary>筛选与视图</summary>
        <label className="rail-search">
          <Search size={15} />
          <input
            value={controls.query}
            onChange={(event) => controls.setQuery(event.target.value)}
            placeholder="搜索论文、路线、关键词..."
          />
        </label>

        <div className="rail-control-grid">
          <label>
            <span>层级</span>
            <select value={controls.tierFilter} onChange={(event) => controls.setTierFilter(event.target.value)}>
              <option value="all">全部</option>
              {controls.tiers.map((tier) => (
                <option key={tier} value={tier}>{tier}</option>
              ))}
            </select>
          </label>
          <label>
            <span>路线</span>
            <select value={controls.routeFilter} onChange={(event) => controls.setRouteFilter(event.target.value)}>
              <option value="all">全部</option>
              {controls.routes.map((route) => (
                <option key={route.id} value={route.id}>{route.label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>年份</span>
            <select value={controls.yearFilter} onChange={(event) => controls.setYearFilter(event.target.value)}>
              <option value="all">全部</option>
              {controls.years.map((year) => (
                <option key={year} value={year}>{year}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="rail-actions-stack">
          <button className="ghost-button wide soft" onClick={controls.toggleLinkMode}>
            <Link2 size={15} /> 关系线：{controls.linkMode === "focus" ? "聚焦" : controls.linkMode === "all" ? "全部" : "隐藏"}
          </button>
          <button className="ghost-button wide soft" disabled={controls.pathCount < 2} onClick={controls.addPathCard}>
            <SquareDashedMousePointer size={15} /> 加入路径 ({controls.pathCount})
          </button>
        </div>
      </details>

      <div className="rail-mini-context">
        <strong>{selectedCards.length}</strong>
        <span>已选材料</span>
      </div>
    </div>
  );
}

function ObjectMemoryEditor({ detail, activeAtlas, memoryMap, onSaveMemory }) {
  const ref = objectRefFromDetail(detail, activeAtlas);
  const memory = ref ? memoryMap.get(objectMemoryKey(ref.type, ref.id)) : null;
  const [draft, setDraft] = useState({ star: false, maturity: 0, tags: "", judgement: "", note: "" });
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    setDraft({
      star: Boolean(memory?.star),
      maturity: memory?.maturity || 0,
      tags: (memory?.tags || []).join(", "),
      judgement: memory?.judgement || "",
      note: memory?.note || ""
    });
    setEditing(false);
  }, [memory?.updated_at, memory?.judgement, memory?.note, memory?.star, memory?.maturity, ref?.id]);

  if (!ref) return null;

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    await onSaveMemory(ref, {
      object_ref: { atlas_id: ref.atlasId, object_type: ref.type, object_id: ref.id },
      title_snapshot: ref.title || "",
      star: draft.star,
      maturity: Number(draft.maturity) || 0,
      tags: draft.tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      judgement: draft.judgement.trim(),
      note: draft.note.trim(),
      core_innovation: memory?.core_innovation || "",
      core_technology: memory?.core_technology || "",
      evidence: memory?.evidence || "",
      limitations: memory?.limitations || "",
      reusable_insight: memory?.reusable_insight || "",
      reading_status: memory?.reading_status || "unread",
      reading_questions: memory?.reading_questions || [],
      paper_chat: memory?.paper_chat || []
    });
    setEditing(false);
  }

  const tags = draft.tags.split(",").map((tag) => tag.trim()).filter(Boolean);
  const hasSummary = Boolean(draft.judgement.trim() || draft.note.trim() || tags.length || draft.star || Number(draft.maturity));

  return (
    <section className={cx("memory-editor", !editing && "memory-summary-mode")}>
      <div className="memory-editor-head">
        <h3>全局对象记忆</h3>
        {!editing && <span>{draft.star ? "已星标 · " : ""}成熟度 {draft.maturity}/5</span>}
      </div>
      {!editing ? (
        <>
          <div className={cx("memory-summary", !hasSummary && "empty")}>
            <p>{draft.judgement || "尚未形成对象判断。"}</p>
            {draft.note && <small>{draft.note}</small>}
            {!!tags.length && <div>{tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
          </div>
          <button className="ghost-button wide soft" type="button" onClick={() => setEditing(true)}>
            <Pencil size={14} /> {hasSummary ? "编辑对象记忆" : "添加对象记忆"}
          </button>
        </>
      ) : (
        <>
          <div className="memory-star-row">
            <label><input type="checkbox" checked={draft.star} onChange={(event) => update("star", event.target.checked)} />星标</label>
            <label><span>成熟度</span><input type="range" min="0" max="5" value={draft.maturity} onChange={(event) => update("maturity", event.target.value)} /><em>{draft.maturity}/5</em></label>
          </div>
          <label><span>标签</span><input value={draft.tags} onChange={(event) => update("tags", event.target.value)} placeholder="逗号分隔，例如 baseline, core" /></label>
          <label><span>核心判断</span><textarea value={draft.judgement} onChange={(event) => update("judgement", event.target.value)} placeholder="这篇论文/关系对你的成果目标意味着什么？" /></label>
          <label><span>短笔记</span><textarea value={draft.note} onChange={(event) => update("note", event.target.value)} placeholder="补充限制、疑点、可复用句子或下一步。" /></label>
          <div className="memory-editor-actions">
            <button className="ghost-button soft" type="button" onClick={() => setEditing(false)}>取消</button>
            <button className="primary-button" type="button" onClick={save}><Check size={15} />保存</button>
          </div>
        </>
      )}
    </section>
  );
}

function RecommendedActionStrip({ detail, templates = [], onRun }) {
  if (!detail) return null;
  const ids = recommendedTemplateIds(detail);
  const recommended = ids
    .map((id) => templates.find((item) => item.id === id))
    .filter(Boolean)
    .slice(0, 3);
  if (!recommended.length) return null;
  return (
    <section className="recommended-actions">
      <div>
        <span>推荐研究动作</span>
        <strong>{focusedTitleFromDetail(detail)}</strong>
      </div>
      <div>
        {recommended.map((template) => (
          <button type="button" key={template.id} onClick={() => onRun(template.id)}>
            <Sparkles size={13} />
            {template.short_title || template.title}
          </button>
        ))}
      </div>
    </section>
  );
}

function CandidateInspector({ candidate, onUpdate, onApply, onAddCard, onOpenPaperDetail, onAskPaper, onRetainPaper, activeAtlas, detail }) {
  const [draft, setDraft] = useState({
    title: candidate.title || "",
    year: candidate.year || "",
    venue: candidate.venue || "",
    suggested_route_id: candidate.route_id || candidate.suggested_route_id || "",
    why: candidate.why || "",
    relevance: candidate.relevance || "",
    judgement: candidate.judgement || "",
    confidence: candidate.confidence ?? 0.5,
    status: candidate.candidate_status || "pending"
  });

  useEffect(() => {
    setDraft({
      title: candidate.title || "",
      year: candidate.year || "",
      venue: candidate.venue || "",
      suggested_route_id: candidate.route_id || candidate.suggested_route_id || "",
      why: candidate.why || "",
      relevance: candidate.relevance || "",
      judgement: candidate.judgement || "",
      confidence: candidate.confidence ?? 0.5,
      status: candidate.candidate_status || "pending"
    });
  }, [candidate.id]);

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function candidateCard() {
    return {
      id: `card_candidate_${candidate.id}`,
      type: "paper",
      title: `候选：${draft.title || candidate.title}`,
      source_ref: { atlas_id: activeAtlas, candidate_id: candidate.id, status: draft.status },
      summary: draft.why || draft.relevance || candidate.summary || "",
      token_estimate: tokenEstimate(`${draft.title} ${draft.why} ${draft.relevance}`),
      selected_for_export: true,
      include_in_agent: true,
      include_in_lab: false
    };
  }

  async function save(patch = {}) {
    await onUpdate?.(candidate.id, { ...draft, ...patch });
  }

  return (
    <div className="detail-panel candidate-detail-panel">
      <span className="detail-type">候选论文</span>
      <h2>{candidate.title}</h2>
      <div className="detail-sub">
        {candidate.year || "未知年份"} · {candidate.venue || "未知来源"} · {candidate.suggested_route_id || candidate.route_id || "未定路线"}
      </div>
      {candidate.duplicate_of && <p className="candidate-warning">可能重复：{candidate.duplicate_of}</p>}
      <div className="candidate-edit-grid">
        <label>
          <span>标题</span>
          <input value={draft.title} onChange={(event) => update("title", event.target.value)} />
        </label>
        <label>
          <span>年份</span>
          <input value={draft.year} onChange={(event) => update("year", event.target.value)} />
        </label>
        <label>
          <span>来源</span>
          <input value={draft.venue} onChange={(event) => update("venue", event.target.value)} />
        </label>
        <label>
          <span>建议路线</span>
          <input value={draft.suggested_route_id} onChange={(event) => update("suggested_route_id", event.target.value)} />
        </label>
        <label>
          <span>置信度</span>
          <input type="range" min="0" max="1" step="0.05" value={draft.confidence} onChange={(event) => update("confidence", Number(event.target.value))} />
          <em>{Math.round((draft.confidence || 0) * 100)}%</em>
        </label>
        <label>
          <span>状态</span>
          <select value={draft.status} onChange={(event) => update("status", event.target.value)}>
            <option value="pending">待审</option>
            <option value="deferred">暂缓</option>
            <option value="rejected">驳回</option>
            <option value="applied">已应用</option>
          </select>
        </label>
        <label className="wide">
          <span>为什么值得纳入</span>
          <textarea value={draft.why} onChange={(event) => update("why", event.target.value)} />
        </label>
        <label className="wide">
          <span>与当前 Atlas 的关系</span>
          <textarea value={draft.relevance} onChange={(event) => update("relevance", event.target.value)} />
        </label>
        <label className="wide">
          <span>我的审查判断</span>
          <textarea value={draft.judgement} onChange={(event) => update("judgement", event.target.value)} placeholder="保留、暂缓或驳回的理由..." />
        </label>
      </div>
      <div className="candidate-actions">
        <button className="primary-button wide" type="button" onClick={() => onAskPaper?.(detail?.fullDetail || detail)}>
          <Send size={15} /> 本轮询问
        </button>
        <button className="ghost-button wide soft" type="button" onClick={() => onRetainPaper?.(detail?.fullDetail || detail)}>
          <Plus size={15} /> 长期保留
        </button>
        <button className="primary-button wide" onClick={async () => { await save(); await onApply?.(candidate.id); }}>
          <Check size={15} /> 应用到个人 Atlas 层
        </button>
        <button className="ghost-button wide soft" onClick={() => save()}>
          <FileText size={15} /> 保存审查
        </button>
        <button className="ghost-button wide soft" onClick={() => save({ status: "deferred" })}>
          <Clock3 size={15} /> 暂缓
        </button>
        <button className="ghost-button wide soft" onClick={() => save({ status: "rejected" })}>
          <X size={15} /> 驳回并隐藏
        </button>
        <button className="ghost-button wide soft" onClick={() => onAddCard?.(candidateCard())}>
          <Plus size={15} /> 加入 Context
        </button>
        {detail?.fullDetail && (
          <button className="ghost-button wide soft" onClick={() => onOpenPaperDetail?.(detail.fullDetail)}>
            <PanelRight size={15} /> 打开候选预览页
          </button>
        )}
      </div>
      {candidate.url && <a className="detail-link" href={candidate.url} target="_blank" rel="noreferrer">打开来源</a>}
    </div>
  );
}

function DetailInspector({
  detail,
  activeAtlas,
  memoryMap,
  templates,
  onSaveMemory,
  onOpenTemplates,
  onRecommendedTemplate,
  onOpenPaperDetail,
  onAskPaper,
  onRetainPaper,
  onUpdateAtlasCandidate,
  onApplyAtlasCandidate,
  onAddCandidateCard,
  onResolveApproval,
  onCloseApproval
}) {
  if (!detail) return <div className="empty-panel">点击论文、关系或 Canvas 节点后，会在这里查看详情。</div>;
  if (detail.type === "agent_approval") {
    return <AgentApprovalInspector approval={detail.value} onResolve={onResolveApproval} onClose={onCloseApproval} />;
  }
  if (detail.type === "agent_source") {
    const source = detail.value;
    const evidenceLabel = {
      system_truth: "系统记录",
      user_knowledge: "用户资料",
      curated_summary: "Atlas 策展摘要",
      metadata: "元数据",
      abstract: "论文摘要",
      full_text: "全文片段",
      web_content: "网页内容"
    }[source.evidence_level] || source.evidence_level;
    const targetUrl = source.locator?.pdf_url || source.locator?.url;
    return (
      <div className="detail-panel agent-source-detail">
        <span className="detail-type">{evidenceLabel}</span>
        <h2>{source.title}</h2>
        <div className="detail-sub">{source.provider || source.source_kind}{source.year ? ` · ${source.year}` : ""}</div>
        {!!source.authors?.length && <p className="detail-note">{source.authors.join(" · ")}</p>}
        <section>
          <h3>本轮使用的证据</h3>
          <p>{source.excerpt || source.abstract || "该来源当前只提供元数据。"}</p>
          {(source.locator?.page != null || source.locator?.section || source.locator?.chunk_id) && (
            <div className="detail-grid">
              {source.locator?.page != null && <div><b>页码</b>{source.locator.page}</div>}
              {source.locator?.section && <div><b>章节</b>{source.locator.section}</div>}
              {source.locator?.chunk_id && <div><b>证据片段</b>{source.locator.chunk_id}</div>}
            </div>
          )}
        </section>
        <section>
          <h3>证据边界</h3>
          <p>{source.evidence_level === "full_text" ? "来自已解析全文片段，可用于定位具体陈述。" : source.evidence_level === "abstract" ? "仅来自论文摘要，不能据此声称已核验方法和实验细节。" : source.evidence_level === "curated_summary" ? "来自 Atlas 的策展摘要，不等同于论文全文。" : "当前来源只支持书目信息或有限内容。"}</p>
        </section>
        {targetUrl && <a className="detail-link" href={targetUrl} target="_blank" rel="noreferrer">打开原始来源</a>}
      </div>
    );
  }
  if (detail.type === "paper") {
    const p = detail.value;
    if (p.is_candidate) {
      return (
        <CandidateInspector
          candidate={p}
          onUpdate={onUpdateAtlasCandidate}
          onApply={onApplyAtlasCandidate}
          onAddCard={onAddCandidateCard}
          onOpenPaperDetail={onOpenPaperDetail}
          onAskPaper={onAskPaper}
          onRetainPaper={onRetainPaper}
          activeAtlas={activeAtlas}
          detail={detail}
        />
      );
    }
    return (
      <div className="detail-panel">
        <span className="detail-type">论文</span>
        <h2>{p.title}</h2>
        <div className="detail-sub">{p.year || "-"} · {p.venue || "-"} · {getPaperRouteName(p)}</div>
        <p className="detail-note">{p.summary || p.local_role || "暂无摘要。"}</p>
        <div className="paper-preview-actions">
          <button className="primary-button" type="button" onClick={() => onAskPaper?.(detail.fullDetail || detail)}>
            <Send size={14} /> 本轮询问
          </button>
          <button className="ghost-button soft" type="button" onClick={() => onRetainPaper?.(detail.fullDetail || detail)}>
            <Plus size={14} /> 长期保留
          </button>
        </div>
        <section>
          <h3>研究判断</h3>
          <p>{p.raw_fields?.judgement || p.local_role || p.why_included || "暂无研究判断。"}</p>
        </section>
        <section>
          <h3>为什么归入此方向</h3>
          <p>{p.why_included || p.raw_fields?.key || p.raw_fields?.role || "暂无归类理由。"}</p>
        </section>
        <section>
          <h3>对象 / 信号 / 接口</h3>
          <div className="detail-grid">
            <div><b>表示路线</b>{p.raw_fields?.routeRationale || getPaperRouteName(p)}</div>
            <div><b>训练信号</b>{p.raw_fields?.signal || "待补"}</div>
            <div><b>下游接口</b>{p.raw_fields?.interface || "待补"}</div>
            <div><b>边界风险</b>{p.boundary_note || p.raw_fields?.boundary || "暂无"}</div>
          </div>
        </section>
        {detail.fullDetail && (
          <button className="primary-button wide" onClick={() => onOpenPaperDetail?.(detail.fullDetail)}>
            <PanelRight size={15} /> 展开论文阅读页
          </button>
        )}
        {p.url && <a className="detail-link" href={p.url} target="_blank" rel="noreferrer">打开来源</a>}
      </div>
    );
  }
  if (detail.type === "relation") {
    const rel = detail.value;
    return (
      <div className="detail-panel relation-detail-panel">
        <span className="detail-type">关系</span>
        <div className="relation-detail-flow">
          <strong>{detail.source?.title || rel.source}</strong>
          <span><Link2 size={15} />{rel.label || rel.type || "关联"}</span>
          <strong>{detail.target?.title || rel.target}</strong>
        </div>
        <p className="relation-detail-reason">{rel.evidence_rationale || rel.reason || "暂无关系说明。"}</p>
        <section>
          <h3>证据状态</h3>
          <div className="detail-grid">
            <div><b>验证</b>{rel.verification_status === "verified" ? "已有策展依据" : "待全文或人工核验"}</div>
            <div><b>来源层</b>{rel.evidence_layer === "personal" ? "个人确认" : rel.evidence_layer === "derived" ? "机器派生" : "Atlas 策展"}</div>
            <div><b>置信度</b>{rel.evidence_confidence != null ? `${Math.round(rel.evidence_confidence * 100)}%` : "未标注"}</div>
            <div><b>边界</b>{rel.verification_status === "verified" ? "可用于图路径解释" : "不能视为全文已证实"}</div>
          </div>
        </section>
        <ObjectMemoryEditor detail={detail} activeAtlas={activeAtlas} memoryMap={memoryMap} onSaveMemory={onSaveMemory} />
      </div>
    );
  }
  return <CanvasNodeInspector detail={detail} templates={templates} onRecommendedTemplate={onRecommendedTemplate} />;
}

function CanvasNodeInspector({ detail, templates, onRecommendedTemplate }) {
  const node = detail.value;
  const [title, setTitle] = useState(node.title || "");
  const [body, setBody] = useState(node.body || "");
  const [status, setNodeStatus] = useState(node.status || "todo");
  const [priority, setPriority] = useState(node.priority ?? 1);

  useEffect(() => {
    setTitle(node.title || "");
    setBody(node.body || "");
    setNodeStatus(node.status || "todo");
    setPriority(node.priority ?? 1);
  }, [node.id, node.title, node.body, node.status, node.priority]);

  return (
    <div className="detail-panel canvas-node-inspector">
      <span className="detail-type">{NODE_LABELS[node.type] || node.type}</span>
      <label><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label><span>说明</span><textarea value={body} onChange={(event) => setBody(event.target.value)} /></label>
      {node.type === "task" && <div className="canvas-node-fields"><label><span>状态</span><select value={status} onChange={(event) => setNodeStatus(event.target.value)}>{Object.entries(TASK_STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>优先级</span><input type="number" min="0" max="3" value={priority} onChange={(event) => setPriority(Number(event.target.value))} /></label></div>}
      <button className="primary-button wide" type="button" onClick={() => detail.onUpdate?.({ title: title.trim() || node.title, body, ...(node.type === "task" ? { status, priority } : {}) })}><Check size={14} />保存节点</button>
      {["task", "hypothesis"].includes(node.type) && <InlineNotice tone="info" title="Campaign 入口">在 Canvas 顶部切换到 Campaign，从当前问题或假设生成研究想法并启动隔离实验。</InlineNotice>}
    </div>
  );
}

export function ConfirmDialog({ action, onCancel, onConfirm }) {
  const cancelRef = useRef(null);
  const dialogRef = useRef(null);
  const previousFocus = useRef(document.activeElement);
  const titleId = `confirm-${action.id || "action"}-title`;
  useEffect(() => {
    const appRoot = document.getElementById("root");
    if (appRoot) appRoot.inert = true;
    const onKeyDown = (event) => {
      if (event.key === "Escape") onCancel();
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    cancelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (appRoot) appRoot.inert = false;
      previousFocus.current?.focus?.();
    };
  }, [onCancel]);
  return createPortal(
    <div className="confirm-layer" role="presentation" onMouseDown={onCancel}>
      <section ref={dialogRef} className={cx("confirm-dialog", action.tone === "danger" && "danger")} role="dialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
        <span>请确认</span>
        <h2 id={titleId}>{action.title}</h2>
        <p>{action.message}</p>
        <div>
          <button ref={cancelRef} className="ghost-button soft" type="button" onClick={onCancel}>取消</button>
          <button className="danger-button" type="button" onClick={onConfirm}>{action.confirmLabel || "确认"}</button>
        </div>
      </section>
    </div>,
    document.body
  );
}
