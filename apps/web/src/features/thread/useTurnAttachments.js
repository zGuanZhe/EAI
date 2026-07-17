import { useCallback, useMemo, useState } from "react";

function attachmentKey(item) {
  const ref = item?.source_ref || {};
  return item?.id || `${item?.type || "material"}:${ref.atlas_id || ""}:${ref.paper_id || ref.relation_id || ref.path_id || ref.candidate_id || item?.title || ""}`;
}

export function useTurnAttachments(threadId) {
  const [byThread, setByThread] = useState({});
  const attachments = useMemo(() => byThread[threadId] || [], [byThread, threadId]);

  const addAttachment = useCallback((attachment) => {
    if (!threadId || !attachment) return;
    setByThread((current) => {
      const list = current[threadId] || [];
      const key = attachmentKey(attachment);
      const next = [...list.filter((item) => attachmentKey(item) !== key), attachment];
      return { ...current, [threadId]: next };
    });
  }, [threadId]);

  const removeAttachment = useCallback((attachment) => {
    if (!threadId) return;
    const key = attachmentKey(attachment);
    setByThread((current) => ({ ...current, [threadId]: (current[threadId] || []).filter((item) => attachmentKey(item) !== key) }));
  }, [threadId]);

  const clearAttachments = useCallback(() => {
    if (!threadId) return;
    setByThread((current) => ({ ...current, [threadId]: [] }));
  }, [threadId]);

  return { attachments, addAttachment, removeAttachment, clearAttachments };
}
