import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiFetch } from "../../api.js";

function applySnapshot(list, snapshot) {
  if (!snapshot?.campaign?.id) return list;
  const index = list.findIndex((item) => item.campaign.id === snapshot.campaign.id);
  if (index < 0) return [snapshot, ...list];
  return list.map((item, itemIndex) => itemIndex === index ? snapshot : item);
}

export function useCampaigns({ thread, onThreadChanged, onStatus }) {
  const [campaigns, setCampaigns] = useState([]);
  const [activeCampaignId, setActiveCampaignId] = useState(null);
  const [ideaPreview, setIdeaPreview] = useState(null);
  const [selectedBranchId, setSelectedBranchId] = useState(null);
  const [selectedBranchIds, setSelectedBranchIds] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [runtimeStatus, setRuntimeStatus] = useState({ docker_available: false, profiles: ["cpu"], cpu: {} });
  const [executionProfile, setExecutionProfile] = useState("cpu");
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] = useState(null);
  const streamRef = useRef(null);

  const refreshRuntime = useCallback(async () => {
    const runtime = await api("/campaign-runtime/status");
    setRuntimeStatus(runtime);
    setExecutionProfile((current) => runtime.profiles?.includes(current) ? current : "cpu");
    return runtime;
  }, []);

  const load = useCallback(async () => {
    if (!thread?.id) return [];
    const data = await api(`/threads/${encodeURIComponent(thread.id)}/campaigns`);
    setCampaigns(data);
    setActiveCampaignId((current) => current && data.some((item) => item.campaign.id === current) ? current : data[0]?.campaign?.id || null);
    refreshRuntime().catch(() => {});
    return data;
  }, [refreshRuntime, thread?.id]);

  useEffect(() => {
    setIdeaPreview(null);
    setSelectedBranchId(null);
    setSelectedBranchIds([]);
    setPendingApproval(null);
    setComparison(null);
    load().catch((error) => onStatus?.(`Campaign 加载失败：${error.message}`));
    return () => streamRef.current?.abort();
  }, [load, onStatus]);

  const refreshCampaign = useCallback(async (campaignId) => {
    const snapshot = await api(`/campaigns/${encodeURIComponent(campaignId)}`);
    setCampaigns((current) => applySnapshot(current, snapshot));
    return snapshot;
  }, []);

  const watch = useCallback(async (campaignId, afterSeq = 0) => {
    streamRef.current?.abort();
    const controller = new AbortController();
    streamRef.current = controller;
    try {
      const response = await apiFetch(`/campaigns/${encodeURIComponent(campaignId)}/events?after_seq=${afterSeq}`, { signal: controller.signal });
      if (!response.ok || !response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          if (!block.includes("data:")) continue;
          const eventName = block.split("\n").find((line) => line.startsWith("event:"))?.slice(6).trim();
          if (eventName === "approval_required") {
            const raw = block.split("\n").find((line) => line.startsWith("data:"))?.slice(5).trim();
            try { setPendingApproval(JSON.parse(raw)?.payload || null); } catch { /* malformed event */ }
          }
          await refreshCampaign(campaignId);
        }
      }
    } catch (error) {
      if (error.name !== "AbortError") onStatus?.(`Campaign 事件流中断：${error.message}`);
    }
  }, [onStatus, refreshCampaign]);

  const runMutation = useCallback(async (campaignId, path, body, success) => {
    setBusy(true);
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(body || {}) });
      const snapshot = result?.campaign?.campaign ? result.campaign : result?.campaign?.id ? result : result?.campaign ? result.campaign : result;
      if (snapshot?.campaign?.id) setCampaigns((current) => applySnapshot(current, snapshot));
      else if (campaignId) await refreshCampaign(campaignId);
      if (success) onStatus?.(success);
      return result;
    } finally {
      setBusy(false);
    }
  }, [onStatus, refreshCampaign]);

  async function previewIdeas(node = null) {
    setBusy(true);
    try {
      const data = await api(`/threads/${encodeURIComponent(thread.id)}/campaigns/ideas/preview`, {
        method: "POST", body: JSON.stringify({ node_id: node?.id || null, objective: node?.title || "", count: 3 })
      });
      setIdeaPreview(data);
      onStatus?.("已基于当前证据生成 3 个研究想法候选");
      return data;
    } finally { setBusy(false); }
  }

  async function createCampaign(idea, sourceNodeIds = [], seed = { kind: "blank", title: "空白实验工作区" }) {
    setBusy(true);
    try {
      const snapshot = await api(`/threads/${encodeURIComponent(thread.id)}/campaigns`, {
        method: "POST",
        body: JSON.stringify({
          idea, source_node_ids: sourceNodeIds, workspace_seed: seed,
          budget: { execution_profile: executionProfile, max_runtime_seconds: 1800, max_storage_mb: 10240 }
        })
      });
      setCampaigns((current) => applySnapshot(current, snapshot));
      setActiveCampaignId(snapshot.campaign.id);
      setIdeaPreview(null);
      onStatus?.("研究 Campaign 已创建，尚未执行任何会话");
      return snapshot;
    } finally { setBusy(false); }
  }

  async function campaignAction(campaignId, action) {
    const result = await runMutation(campaignId, `/campaigns/${encodeURIComponent(campaignId)}/${action}`, {}, { start: "Campaign 已启动", pause: "Campaign 已暂停", resume: "Campaign 已继续", cancel: "Campaign 已取消" }[action]);
    if (["start", "resume"].includes(action)) watch(campaignId, result.last_seq || 0);
    return result;
  }

  async function installRuntime() {
    const runtime = await api(`/campaign-runtime/install?profile=${encodeURIComponent(executionProfile)}`, { method: "POST", body: JSON.stringify({}) });
    setRuntimeStatus(runtime);
    onStatus?.("Campaign Runtime 正在后台安装，可在此页面查看进度");
    return runtime;
  }

  async function prepareExecution(campaignId, branchId) {
    const response = await api(`/campaigns/${campaignId}/branches/${branchId}/prepare-session`, { method: "POST", body: JSON.stringify({}) });
    setPendingApproval(response.approval);
    await refreshCampaign(campaignId);
    if (response.runtime_required) onStatus?.("请先安装 Campaign Runtime；当前仅保留会话预览");
    else onStatus?.("分支会话已准备，确认后可在授权边界内自动迭代");
    return response;
  }

  async function resolveApproval(decision) {
    if (!pendingApproval?.id) return null;
    const campaignId = pendingApproval.payload?.campaign_id;
    const response = await api(`/agent-v2/approvals/${pendingApproval.id}/resolve`, {
      method: "POST", body: JSON.stringify({ decision, selected_operation_ids: null, edited_arguments: {} })
    });
    const kind = pendingApproval.kind;
    setPendingApproval(null);
    if (response.result?.thread) onThreadChanged?.(response.result.thread);
    const snapshot = response.campaign || await refreshCampaign(campaignId);
    if (response.campaign) setCampaigns((current) => applySnapshot(current, response.campaign));
    if (decision === "approve" && ["sandbox_command", "execution_session"].includes(kind)) watch(campaignId, snapshot.last_seq || 0);
    onStatus?.(decision === "approve" ? "已批准 Campaign 操作" : "已拒绝 Campaign 操作");
    return response;
  }

  async function compareBranches(campaignId) {
    const result = await runMutation(campaignId, `/campaigns/${campaignId}/branches/compare`, { branch_ids: selectedBranchIds }, "已生成可引用的分支比较产物");
    setComparison(result.comparison);
    return result;
  }

  return {
    campaigns, activeCampaignId, setActiveCampaignId, ideaPreview, setIdeaPreview,
    selectedBranchId, setSelectedBranchId, selectedBranchIds, setSelectedBranchIds,
    pendingApproval, busy, comparison, setComparison,
    runtimeStatus, executionProfile, setExecutionProfile, refreshRuntime, installRuntime,
    previewIdeas, createCampaign, campaignAction, prepareExecution,
    requestPromotion: (campaignId, branchId) => runMutation(campaignId, `/campaigns/${campaignId}/branches/${branchId}/promote`, { include_finding: true, include_task: true }, "晋升内容已生成，确认后才写入 Canvas").then((result) => { setPendingApproval(result.approval); return result; }),
    resolveApproval,
    advanceStage: (campaignId, stageId, branchId) => runMutation(campaignId, `/campaigns/${campaignId}/stages/${stageId}/advance`, { branch_id: branchId || null }, "已确认最佳分支并推进到下一阶段"),
    discardBranch: (campaignId, branchId) => runMutation(campaignId, `/campaigns/${campaignId}/branches/${branchId}/discard`, {}, "分支已暂缓").then((result) => { setSelectedBranchId(null); return result; }),
    compareBranches,
    generateManuscript: (campaignId) => runMutation(campaignId, `/campaigns/${campaignId}/manuscripts/generate`, { draft: true }, "论文候选稿已生成"),
    startReview: (campaignId, manuscriptId) => runMutation(campaignId, `/campaigns/${campaignId}/reviews/start`, { manuscript_id: manuscriptId || null }, "三角色审稿与 meta-review 已完成"),
    applyRevision: (campaignId, revisionId, apply) => runMutation(campaignId, `/campaigns/${campaignId}/revisions/apply`, { revision_id: revisionId, apply }, apply ? "修订候选已设为当前稿" : "修订候选已驳回"),
    exportRelease: (campaignId, manuscriptId, allowWarnings) => runMutation(campaignId, `/campaigns/${campaignId}/release/export`, { manuscript_id: manuscriptId || null, allow_draft_warnings: allowWarnings }, allowWarnings ? "草稿研究包已生成" : "正式研究包已生成"),
    refreshCampaign
  };
}
