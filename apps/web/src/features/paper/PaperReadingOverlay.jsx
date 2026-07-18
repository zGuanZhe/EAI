import { ArrowLeft, BookOpenCheck, Check, ExternalLink, FileSearch, LoaderCircle, MessageSquareText, Pencil, Pin, Quote, ShieldCheck, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";

const READING_FIELDS = [
  ["judgement", "个人判断"],
  ["core_innovation", "核心创新"],
  ["core_technology", "核心技术"],
  ["evidence", "证据与实验支撑"],
  ["limitations", "局限与边界"],
  ["reusable_insight", "可复用启发"],
  ["reading_questions", "待查问题"],
  ["note", "个人笔记"]
];

function initialDraft(memory = {}) {
  return Object.fromEntries(READING_FIELDS.map(([key]) => [key, memory[key] || ""]));
}

export function PaperReadingOverlay({
  detail,
  onClose,
  onSaveMemory,
  onUpdateCandidate,
  onApplyCandidate,
  onAskMainAgent,
  onRetainPaper
}) {
  const [draft, setDraft] = useState(() => initialDraft(detail?.memory));
  const [savedDraft, setSavedDraft] = useState(() => initialDraft(detail?.memory));
  const [editing, setEditing] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const readerRef = useRef(null);
  const closeButtonRef = useRef(null);
  const closeConfirmRef = useRef(null);
  const continueEditingRef = useRef(null);
  const previousFocusRef = useRef(null);
  const dirtyRef = useRef(false);
  const confirmCloseRef = useRef(false);
  const onCloseRef = useRef(onClose);
  const paper = detail?.value;
  const memory = detail?.memory || {};
  const relations = detail?.relations || [];
  const knowledge = detail?.knowledge || {};
  const evidenceById = useMemo(
    () => Object.fromEntries((knowledge.evidence || []).map((item) => [item.id, item])),
    [knowledge.evidence]
  );
  const routeName = detail?.route?.title_cn || detail?.route?.title || detail?.route?.label || paper?.route_id || "未定路线";
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft);
  dirtyRef.current = dirty;
  confirmCloseRef.current = confirmClose;
  onCloseRef.current = onClose;
  const closeReader = () => {
    if (dirtyRef.current) {
      setConfirmClose(true);
      return;
    }
    onCloseRef.current?.();
  };

  useEffect(() => {
    const next = initialDraft(detail?.memory);
    setDraft(next);
    setSavedDraft(next);
    setEditing("");
    setConfirmClose(false);
  }, [detail?.value?.id, detail?.memory?.updated_at]);

  useEffect(() => {
    if (!detail) return undefined;
    previousFocusRef.current = document.activeElement;
    const appRoot = document.getElementById("root");
    if (appRoot) appRoot.inert = true;
    const onKey = (event) => {
      if (event.key === "Escape") {
        if (confirmCloseRef.current) setConfirmClose(false);
        else closeReader();
        return;
      }
      if (event.key !== "Tab") return;
      const scope = confirmCloseRef.current ? closeConfirmRef.current : readerRef.current;
      if (!scope) return;
      const focusable = [...scope.querySelectorAll(FOCUSABLE)];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const frame = requestAnimationFrame(() => closeButtonRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      if (appRoot) appRoot.inert = false;
      previousFocusRef.current?.focus?.();
    };
  }, [detail?.value?.id]);

  useEffect(() => {
    if (!confirmClose) return undefined;
    if (readerRef.current) readerRef.current.inert = true;
    const frame = requestAnimationFrame(() => continueEditingRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      if (readerRef.current) readerRef.current.inert = false;
    };
  }, [confirmClose]);

  const memoryPayload = useMemo(() => ({
    object_ref: { atlas_id: detail?.atlas?.id, object_type: "paper", object_id: paper?.id },
    title_snapshot: paper?.title || "",
    star: Boolean(memory.star),
    maturity: memory.maturity || 0,
    tags: memory.tags || [],
    ...draft,
    paper_chat: memory.paper_chat || []
  }), [detail?.atlas?.id, paper?.id, paper?.title, memory, draft]);
  const visibleFields = READING_FIELDS.filter(([key]) => draft[key] || editing === key);
  const pendingFields = READING_FIELDS.filter(([key]) => !draft[key] && editing !== key);

  if (!detail || !paper) return null;

  async function saveField(key) {
    setSaving(true);
    try {
      const updated = await onSaveMemory?.(
        { atlasId: detail.atlas?.id, type: "paper", id: paper.id, title: paper.title },
        memoryPayload
      );
      if (updated) {
        const next = initialDraft(updated);
        setDraft(next);
        setSavedDraft(next);
      }
      setEditing("");
    } finally {
      setSaving(false);
    }
  }

  const askPrompt = `请围绕《${paper.title}》整理核心创新、核心技术、关键证据、局限与可复用启发。对缺少原文支撑的内容明确标注“需要原文确认”，并将建议修改生成可确认变更集。`;

  return createPortal(
    <div className="paper-reader-layer">
      <article ref={readerRef} className="paper-reader" style={{ "--route": detail.routeColor || "#2563eb" }} role="dialog" aria-modal="true" aria-labelledby="paper-reader-title" tabIndex={-1}>
        <header className="paper-reader-header">
          <button ref={closeButtonRef} type="button" className="paper-reader-back" onClick={closeReader}><ArrowLeft size={16} />收起</button>
          <div>
            <span>Atlas {detail.atlas?.id || "-"} · {routeName}</span>
            <h1 id="paper-reader-title">{paper.title}</h1>
            <p>{paper.year || "未知年份"} · {paper.venue || "未知来源"}</p>
          </div>
          <div className="paper-reader-actions">
            <button type="button" className="primary-button" onClick={() => onAskMainAgent?.(detail)}><MessageSquareText size={15} />本轮询问</button>
            <button type="button" className="ghost-button soft" onClick={() => onRetainPaper?.(detail)}><Pin size={15} />长期保留</button>
            {paper.url && <a href={paper.url} target="_blank" rel="noreferrer" title="打开论文来源"><ExternalLink size={15} /></a>}
          </div>
        </header>

        <div className="paper-reader-layout">
          <main className="paper-reader-content">
            {paper.is_candidate && (
              <section className="paper-candidate-line">
                <div><strong>候选论文</strong><span>{paper.why || paper.relevance || "等待审查"}</span></div>
                <div>
                  <button type="button" onClick={() => onUpdateCandidate?.(paper.id, { status: "deferred" })}>暂缓</button>
                  <button type="button" onClick={() => onUpdateCandidate?.(paper.id, { status: "rejected" })}>驳回</button>
                  <button type="button" onClick={() => onApplyCandidate?.(paper.id)}>应用</button>
                </div>
              </section>
            )}

            <section className="paper-reader-lead" id="paper-overview">
              <span>摘要与 Atlas 定位</span>
              <p>{paper.summary || paper.abstract || "当前论文卡尚未提供摘要，需要原文确认。"}</p>
              <dl>
                <div><dt>纳入理由</dt><dd>{paper.why_included || paper.why || paper.local_role || "尚未整理"}</dd></div>
                <div><dt>路线位置</dt><dd>{routeName}</dd></div>
              </dl>
            </section>

            <PaperEvidence knowledge={knowledge} evidenceById={evidenceById} />

            <div className="paper-reader-section-list">
              {visibleFields.map(([key, title]) => (
                <ReadingSection
                  key={key}
                  fieldKey={key}
                  title={title}
                  value={draft[key]}
                  editing={editing === key}
                  saving={saving}
                  onEdit={() => setEditing(key)}
                  onChange={(value) => setDraft((current) => ({ ...current, [key]: value }))}
                  onSave={() => saveField(key)}
                  onCancel={() => {
                    setDraft((current) => ({ ...current, [key]: savedDraft[key] || "" }));
                    setEditing("");
                  }}
                />
              ))}
            </div>

            {!!pendingFields.length && (
              <section className="paper-pending-fields">
                <header><div><span>待整理字段</span><strong>{pendingFields.length} 项尚未形成笔记</strong></div><button type="button" onClick={() => onAskMainAgent?.(detail, askPrompt)}><Sparkles size={14} />让 Main Agent 整理</button></header>
                <div>{pendingFields.map(([key, title]) => <button type="button" key={key} onClick={() => setEditing(key)}><Pencil size={13} /><span>{title}</span><em>尚未整理</em></button>)}</div>
              </section>
            )}

            <button type="button" className="paper-agent-organize" onClick={() => onAskMainAgent?.(detail, askPrompt)}>
              <Sparkles size={15} />让 Main Agent 整理这篇论文
            </button>

            <section className="paper-reader-relations" id="paper-relations">
              <h2>关系邻域</h2>
              {!relations.length && <p>暂无关系记录。</p>}
              {relations.map((relation) => {
                const other = relation.direction === "out" ? relation.targetPaper : relation.sourcePaper;
                return <div key={relation.id}><span>{relation.direction === "out" ? "推出" : "承接"}</span><strong>{other?.title || relation.target || relation.source}</strong><p>{relation.evidence_rationale || relation.reason || relation.label || relation.type}</p><em className={`relation-evidence-status ${relation.verification_status || "unverified"}`}>{relation.verification_status === "verified" ? "有策展依据" : "关系待核验"}</em></div>;
              })}
            </section>

            {!!memory.paper_chat?.length && (
              <details className="legacy-paper-chat">
                <summary>历史论文问答 · {memory.paper_chat.length} 条</summary>
                <div>{memory.paper_chat.map((message, index) => <p key={message.id || index}><strong>{message.role === "user" ? "你" : "AI"}</strong>{message.content}</p>)}</div>
              </details>
            )}
          </main>

          <aside className="paper-reader-outline">
            <strong>阅读提纲</strong>
            <a href="#paper-overview">摘要与定位</a>
            <a href="#paper-evidence">证据档案</a>
            {READING_FIELDS.map(([key, title]) => <a key={key} href={`#paper-${key}`}>{title}</a>)}
            <a href="#paper-relations">关系邻域</a>
          </aside>
        </div>
      </article>
      {confirmClose && (
        <div className="paper-reader-confirm-layer">
          <section ref={closeConfirmRef} className="paper-reader-close-confirm" role="alertdialog" aria-modal="true" aria-labelledby="paper-reader-close-title">
            <h2 id="paper-reader-close-title">笔记尚未保存</h2>
            <p>关闭阅读器会放弃本次未保存修改。</p>
            <div>
              <button ref={continueEditingRef} type="button" onClick={() => setConfirmClose(false)}>继续编辑</button>
              <button type="button" onClick={() => { setConfirmClose(false); onCloseRef.current?.(); }}>放弃修改并关闭</button>
            </div>
          </section>
        </div>
      )}
    </div>,
    document.body
  );
}

function PaperEvidence({ knowledge, evidenceById }) {
  if (knowledge.loading) {
    return <section className="paper-evidence-loading"><LoaderCircle size={16} />正在装配论文证据档案</section>;
  }
  if (knowledge.unavailable) {
    return <section className="paper-evidence-unavailable"><FileSearch size={16} /><div><strong>证据档案尚未建立</strong><span>{knowledge.message}</span></div></section>;
  }
  const work = knowledge.work;
  if (!work) return null;
  const status = work.evidence_status || {};
  const claims = knowledge.claims || [];
  return (
    <section className="paper-evidence" id="paper-evidence">
      <header>
        <div><span>证据档案</span><h2>结构化论断与可核查来源</h2></div>
        <div className="paper-evidence-coverage">
          <span className={status.metadata ? "ready" : ""}><ShieldCheck size={13} />身份</span>
          <span className={status.full_text ? "ready" : ""}><BookOpenCheck size={13} />全文</span>
          <span className={status.verified_claims ? "ready" : ""}><Quote size={13} />{status.verified_claims || 0} 条论断</span>
        </div>
      </header>
      {!claims.length ? (
        <p className="paper-evidence-empty">当前只有论文身份与 Atlas 策展位置，尚无可定位的结构化论断。</p>
      ) : (
        <div className="paper-claim-list">
          {claims.map((claim) => {
            const spans = (claim.evidence_ids || []).map((id) => evidenceById[id]).filter(Boolean);
            return (
              <details key={claim.id} className={`paper-claim claim-${claim.status}`}>
                <summary>
                  <span>{claim.predicate.replaceAll("_", " ")}</span>
                  <strong>{claim.text}</strong>
                  <em>{claim.layer === "personal" ? "个人确认" : claim.layer === "curated" ? "Atlas 策展" : "机器抽取"}</em>
                </summary>
                <div>
                  {!spans.length && <p>该论断尚未绑定可定位原文，不能作为全文证据引用。</p>}
                  {spans.map((span) => (
                    <blockquote key={span.id} data-document-id={span.document_id}>
                      <p>{span.quote}</p>
                      <footer>
                        <span>{span.evidence_level === "full_text" ? "全文" : span.evidence_level === "curated_summary" ? "策展摘要" : span.evidence_level}</span>
                        <strong>{span.section || "未标章节"}{span.page ? ` · 第 ${span.page} 页` : ""}</strong>
                      </footer>
                    </blockquote>
                  ))}
                </div>
              </details>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ReadingSection({ fieldKey, title, value, editing, saving, onEdit, onChange, onSave, onCancel }) {
  const id = `paper-${fieldKey}`;
  return (
    <section className="paper-reading-section" id={id}>
      <header><h2>{title}</h2>{!editing && <button type="button" title={`编辑${title}`} onClick={onEdit}><Pencil size={14} /></button>}</header>
      {editing ? (
        <div className="paper-reading-editor">
          <textarea autoFocus value={value} onChange={(event) => onChange(event.target.value)} placeholder="记录你的判断；不确定的信息可标注需要原文确认。" />
          <div><button type="button" onClick={onCancel}><X size={14} />取消</button><button type="button" disabled={saving} onClick={onSave}><Check size={14} />保存</button></div>
        </div>
      ) : <p className={value ? "" : "empty"}>{value || "尚未整理"}</p>}
    </section>
  );
}
