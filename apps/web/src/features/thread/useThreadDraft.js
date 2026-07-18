import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api.js";

function referenceForAttachment(item) {
  const ref = item?.source_ref || {};
  const candidate = ref.document_id ? { type: "document", id: ref.document_id }
    : (ref.work_id || ref.paper_id) ? { type: "work", id: ref.work_id || ref.paper_id }
      : item?.id ? { type: "context_card", id: item.id } : null;
  return candidate && /^[A-Za-z0-9_.:-]+$/.test(candidate.id) ? candidate : null;
}

function restoredAttachment(ref) {
  if (ref.type === "document") return { id: `draft-${ref.id}`, type: "document", title: "已恢复文档", source_ref: { document_id: ref.id } };
  if (ref.type === "work") return { id: `draft-${ref.id}`, type: "paper", title: "已恢复论文", source_ref: { work_id: ref.id } };
  return { id: ref.id, type: "context_card", title: "已恢复上下文", source_ref: { context_card_id: ref.id } };
}

function interactionMode(value) {
  return value === "research" || value === "deep_research" ? "research" : "ask";
}

export function useThreadDraft({ threadId, text, setText, agentMode, setAgentMode, attachments, replaceAttachments, enabled = true }) {
  const [conflict, setConflict] = useState(null);
  const revisionRef = useRef(0);
  const loadedThreadRef = useRef("");
  const clearedAfterSendRef = useRef(false);
  const stateRef = useRef({ text, agentMode, attachments });
  stateRef.current = { text, agentMode, attachments };

  useEffect(() => {
    if (!threadId || !enabled) return undefined;
    let active = true;
    loadedThreadRef.current = "";
    revisionRef.current = 0;
    setText("");
    setAgentMode("ask");
    replaceAttachments([]);
    api(`/threads/${threadId}/draft`).then(({ draft }) => {
      if (!active) return;
      revisionRef.current = draft?.revision || 0;
      if (draft) {
        setText(draft.text || "");
        setAgentMode(interactionMode(draft.agent_mode));
        replaceAttachments((draft.attachment_refs || []).map(restoredAttachment));
      }
      loadedThreadRef.current = threadId;
      clearedAfterSendRef.current = false;
      setConflict(null);
    }).catch(() => { if (active) loadedThreadRef.current = threadId; });
    return () => { active = false; };
  }, [enabled, replaceAttachments, setAgentMode, setText, threadId]);

  const flush = useCallback(async () => {
    if (!threadId || !enabled || loadedThreadRef.current !== threadId) return revisionRef.current;
    const current = stateRef.current;
    const attachmentRefs = current.attachments.map(referenceForAttachment).filter(Boolean).slice(0, 8);
    if (!revisionRef.current && !current.text.trim() && current.agentMode === "ask" && !attachmentRefs.length) return 0;
    try {
      const saved = await api(`/threads/${threadId}/draft`, {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: revisionRef.current, text: current.text,
          agent_mode: current.agentMode, attachment_refs: attachmentRefs
        })
      });
      revisionRef.current = saved.revision;
      setConflict(null);
      return saved.revision;
    } catch (error) {
      if (error.status === 409) {
        let payload = null;
        try { payload = JSON.parse(error.payload)?.detail; } catch { payload = null; }
        setConflict(payload);
      }
      throw error;
    }
  }, [enabled, threadId]);

  useEffect(() => {
    if (!threadId || !enabled || loadedThreadRef.current !== threadId) return undefined;
    if (clearedAfterSendRef.current && !text.trim() && attachments.length === 0) return undefined;
    if (clearedAfterSendRef.current) clearedAfterSendRef.current = false;
    const timer = window.setTimeout(() => flush().catch(() => {}), 500);
    return () => window.clearTimeout(timer);
  }, [agentMode, attachments, enabled, flush, text, threadId]);

  const clearAfterPersist = useCallback(async (revision) => {
    if (!threadId || !revision) return;
    try {
      await api(`/threads/${threadId}/draft`, {
        method: "DELETE", body: JSON.stringify({ expected_revision: revision })
      });
      if (revisionRef.current === revision) {
        revisionRef.current = 0;
        clearedAfterSendRef.current = true;
      }
    } catch (error) {
      if (error.status !== 409) throw error;
      let payload = null;
      try { payload = JSON.parse(error.payload)?.detail; } catch { payload = null; }
      setConflict(payload);
    }
  }, [threadId]);

  const loadRemoteDraft = useCallback(() => {
    const remote = conflict?.current_draft;
    if (!remote) return;
    revisionRef.current = remote.revision;
    setText(remote.text || "");
    setAgentMode(interactionMode(remote.agent_mode));
    replaceAttachments((remote.attachment_refs || []).map(restoredAttachment));
    setConflict(null);
  }, [conflict, replaceAttachments, setAgentMode, setText]);

  const overwriteRemoteDraft = useCallback(async () => {
    if (!conflict) return;
    revisionRef.current = conflict.current_revision;
    await flush();
  }, [conflict, flush]);

  return {
    flushDraft: flush, clearDraftAfterPersist: clearAfterPersist, draftConflict: conflict,
    loadRemoteDraft, overwriteRemoteDraft,
  };
}
