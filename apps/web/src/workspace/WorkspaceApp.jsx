import React, { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  Archive,
  Blocks,
  BookOpen,
  Brain,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Clock3,
  Copy,
  FileText,
  Folder,
  GitBranch,
  KeyRound,
  Layers,
  Link2,
  ListChecks,
  PanelRight,
  Pencil,
  Plus,
  RefreshCcw,
  Search,
  Send,
  Sparkles,
  SquareDashedMousePointer,
  Trash2,
  Wrench,
  X
} from "lucide-react";
import { api } from "../api.js";
import { buildAtlasOverview } from "../atlasUtils.js";
import { Composer } from "../Composer.jsx";
import { HomeSurface } from "../HomeSurface.jsx";
import { ThreadSurface } from "../ThreadSurface.jsx";
import { ContextCanvas } from "../features/canvas/ContextCanvas.jsx";
import { layoutCanvas, NODE_LABELS, normalizeEdgeLabel, TASK_STATUS_LABELS } from "../features/canvas/model.js";
import { AtlasPaperNode } from "../features/atlas/AtlasPaperNode.jsx";
import { AtlasRelationLayer } from "../features/atlas/AtlasRelationLayer.jsx";
import { AtlasToolbar } from "../features/atlas/AtlasToolbar.jsx";
import { computeAtlasPositions, computeHorizontalRevealDelta } from "../features/atlas/atlasLayout.js";
import { AtlasSurface } from "../features/atlas/AtlasSurface.jsx";
import { useAgentV2 } from "../features/thread/useAgentV2.js";
import { useTurnAttachments } from "../features/thread/useTurnAttachments.js";
import { useThreadDraft } from "../features/thread/useThreadDraft.js";
import { ChangeSetInspector } from "../features/thread/ChangeSetInspector.jsx";
import { ContextDrawer } from "../features/thread/ContextDrawer.jsx";
import { TaskPackPreview } from "../features/taskPack/TaskPackPreview.jsx";
import { PaperReadingOverlay } from "../features/paper/PaperReadingOverlay.jsx";
import { DesktopSettings } from "../features/settings/DesktopSettings.jsx";
import { RunCenter } from "../features/tools/RunCenter.jsx";
import { AgentApprovalInspector } from "../features/inspector/AgentApprovalInspector.jsx";
import { ConfirmDialog, RightRail } from "../features/inspector/RightRail.jsx";
import { InlineNotice } from "../components/ui/index.jsx";
import { useNotifications } from "../app/Notifications.jsx";
import { Sidebar } from "../layout/Sidebar.jsx";
import { initialWorkspaceState, workspaceReducer } from "../state/workspaceReducer.js";
import "../styles/tokens.css";
import "../styles/components.css";
import "../styles.css";
import "../features/thread/agent.css";
import "../features/thread/activity.css";
import "../features/atlas/atlas.css";
import "../styles/shell.css";
import "../features/paper/paper.css";
import "../features/thread/context.css";
import "../features/inspector/inspector.css";
import "../features/home/home.css";

const COMPOSER_COMMANDS = [
  { id: "atlas", label: "切换 Atlas", command: "/atlas", description: "例如 /atlas H", icon: GitBranch },
  { id: "context", label: "上下文", command: "/context", description: "检查 Main Agent 可见材料", icon: Layers },
  { id: "tools", label: "运行中心", command: "/tools", description: "查看实验、变更和低频入口", icon: Wrench }
];

const FALLBACK_COLORS = [
  "#64748b",
  "#b45309",
  "#0d9669",
  "#7c3aed",
  "#1d4ed8",
  "#dc2626",
  "#0891b2",
  "#7f1d1d",
  "#0369a1",
  "#4d7c0f"
];

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

function tokenEstimate(text = "") {
  return Math.max(120, Math.ceil(text.length / 3));
}

function cardKey(card) {
  return `${card.type}:${JSON.stringify(card.source_ref || {})}`;
}

function contextForAgent(card) {
  return card?.include_in_agent !== false;
}

function objectMemoryKey(type, id) {
  return type && id ? `${type}:${id}` : "";
}

function objectRefFromCard(card) {
  const ref = card?.source_ref || {};
  if (card?.type === "paper" && ref.paper_id) return { atlasId: ref.atlas_id, type: "paper", id: ref.paper_id };
  if (card?.type === "relation" && ref.relation_id) return { atlasId: ref.atlas_id, type: "relation", id: ref.relation_id };
  if (card?.type === "path" && ref.paper_ids?.length) return { atlasId: ref.atlas_id, type: "path", id: `path_${ref.paper_ids.join("_")}` };
  return null;
}

function objectRefFromDetail(detail, activeAtlas) {
  if (!detail) return null;
  if (detail.type === "paper") return { atlasId: activeAtlas, type: "paper", id: detail.value.id, title: detail.value.title };
  if (detail.type === "relation") {
    return {
      atlasId: activeAtlas,
      type: "relation",
      id: detail.value.id,
      title: `${detail.source?.title || detail.value.source} -> ${detail.target?.title || detail.value.target}`
    };
  }
  return null;
}

function buildMemoryMap(items = []) {
  return new Map(
    items
      .map((item) => {
        const ref = item.object_ref || {};
        return [objectMemoryKey(ref.object_type, ref.object_id), item];
      })
      .filter(([key]) => key)
  );
}

function routeLabel(route, index) {
  return route?.cn || route?.name || route?.id || `Route ${index + 1}`;
}

function routeShort(route, index) {
  return route?.short || route?.raw_fields?.routeShort || routeLabel(route, index).split(/[ /]/)[0];
}

function getPaperRouteName(paper) {
  return paper.raw_fields?.routeCN || paper.route_name || paper.route_id || "未归类";
}

function getPaperRouteId(paper) {
  return paper.route_id || paper.raw_fields?.routeName || paper.raw_fields?.routeCN || "unrouted";
}

function getPaperLevel(paper) {
  return paper.raw_fields?.level || paper.tier || paper.include_type || "论文";
}

function displayThreadTitle(title) {
  return title === "New research thread" ? "未命名研究线程" : title;
}

function titleFromQuestion(text = "") {
  const compact = String(text).replace(/\s+/g, " ").trim();
  if (!compact) return "新的研究问题";
  return compact.length > 32 ? `${compact.slice(0, 32)}...` : compact;
}

function detailKind(detail) {
  if (!detail) return "canvas";
  if (detail.type === "canvas_node") return "canvas";
  return detail.type || "canvas";
}

function recommendedTemplateIds(detail) {
  const kind = detailKind(detail);
  if (kind === "paper") return ["atlas_gap", "method_evolution", "candidate_audit"];
  if (kind === "relation" || kind === "path") return ["relation_explain", "candidate_audit", "atlas_gap"];
  return ["atlas_gap", "candidate_audit", "method_evolution"];
}

function focusedTitleFromDetail(detail) {
  if (!detail) return "当前 Canvas";
  if (detail.type === "paper") return detail.value?.title || "聚焦论文";
  if (detail.type === "relation") return `${detail.source?.title || detail.value?.source} -> ${detail.target?.title || detail.value?.target}`;
  return detail.value?.title || "当前对象";
}

function runTemplateName(run, templates = []) {
  const template = templates.find((item) => item.id === run.template_id);
  return template?.short_title || template?.title || run.template_id || "通用 Task Pack";
}

function usesCompactInspector() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
}

export function App() {
  const { announce } = useNotifications();
  const [workspaceState, dispatchWorkspace] = useReducer(workspaceReducer, initialWorkspaceState);
  const { surface, rail, railOpen, sidebarOpen, toolsPage } = workspaceState;
  const [projects, setProjects] = useState([]);
  const [activeProjectId, setActiveProjectId] = useState("unfiled");
  const [threads, setThreads] = useState([]);
  const [thread, setThread] = useState(null);
  const [atlases, setAtlases] = useState([]);
  const [bundle, setBundle] = useState(null);
  const [atlasUpdates, setAtlasUpdates] = useState(null);
  const [objectMemories, setObjectMemories] = useState([]);
  const [detail, setDetail] = useState(null);
  const [paperOverlay, setPaperOverlay] = useState(null);
  const [atlasControls, setAtlasControls] = useState(null);
  const [toolOpen, setToolOpen] = useState(false);
  const [composer, setComposer] = useState("");
  const [agentMode, setAgentMode] = useState("auto");
  const [exported, setExported] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [resultPreview, setResultPreview] = useState(null);
  const [status, setStatus] = useState("就绪");
  const [secrets, setSecrets] = useState({ configured: false, providers: [] });
  const [systemInfo, setSystemInfo] = useState(null);
  const [knowledgeStatus, setKnowledgeStatus] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [contextDrawerOpen, setContextDrawerOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState([]);
  const [researchTemplates, setResearchTemplates] = useState([]);
  const [taskPackPreview, setTaskPackPreview] = useState(null);
  const [atlasUpdatePreview, setAtlasUpdatePreview] = useState(null);
  const [atlasUpdatePaste, setAtlasUpdatePaste] = useState("");
  const [selectedUpdateAction, setSelectedUpdateAction] = useState("recent");
  const [selectedTemplateId, setSelectedTemplateId] = useState("atlas_gap");
  const [confirmAction, setConfirmAction] = useState(null);
  const [activeProposalId, setActiveProposalId] = useState(null);
  const [activeChangesetId, setActiveChangesetId] = useState(null);
  const [atlasFocusPaperId, setAtlasFocusPaperId] = useState(null);
  const researchReadOnly = Boolean(systemInfo?.research_store?.read_only);
  useEffect(() => {
    if (!status || status === "就绪" || status.startsWith("正在")) return;
    const tone = /失败|无法|错误|冲突/.test(status) ? "error" : "info";
    announce(status, tone);
  }, [announce, status]);
  const {
    attachments: turnAttachments,
    addAttachment: addTurnAttachment,
    removeAttachment: removeTurnAttachment,
    clearAttachments: clearTurnAttachments,
    replaceAttachments: replaceTurnAttachments
  } = useTurnAttachments(thread?.id);
  const {
    flushDraft, clearDraftAfterPersist, draftConflict, loadRemoteDraft, overwriteRemoteDraft
  } = useThreadDraft({
    threadId: thread?.id, text: composer, setText: setComposer,
    agentMode, setAgentMode, attachments: turnAttachments,
    replaceAttachments: replaceTurnAttachments, enabled: !researchReadOnly,
  });
  const bundleCacheRef = useRef(new Map());
  const bundleRequestRef = useRef(0);
  const memoryRequestRef = useRef(0);
  const updateRequestRef = useRef(0);
  const threadRequestRef = useRef(0);
  const paperKnowledgeRequestRef = useRef(0);

  function setSurface(value) {
    dispatchWorkspace({ type: "setSurface", value });
  }

  function setRail(value) {
    dispatchWorkspace({ type: "setRail", value });
  }

  function setRailOpen(value) {
    dispatchWorkspace({ type: "setRailOpen", value: typeof value === "function" ? value(railOpen) : value });
  }

  function setSidebarOpen(value) {
    dispatchWorkspace({ type: "setSidebarOpen", value: typeof value === "function" ? value(sidebarOpen) : value });
  }

  function setToolsPage(value) {
    dispatchWorkspace({ type: "setToolsPage", value: typeof value === "function" ? value(toolsPage) : value });
  }

  function openSettings() {
    setContextDrawerOpen(false);
    setRailOpen(false);
    setPaperOverlay(null);
    setSettingsOpen(true);
  }

  const { chatError, isRunning, sendThreadChat, steerThreadChat, stopThreadChat, retryThreadChat, resolveApproval, undoOperationBatch } = useAgentV2({
    thread,
    surface,
    setThread,
    refreshThreads,
    setSurface,
    setToolsPage,
    setRail,
    setRailOpen,
    setStatus
  });

  function inspectAgentApproval(approval) {
    setDetail({ type: "agent_approval", value: approval });
    setRail("detail");
    setRailOpen(true);
    setContextDrawerOpen(false);
    setStatus("请检查 Agent 将要执行的具体操作");
  }

  async function resolveAgentApproval(approvalId, decision, options) {
    const result = await resolveApproval(approvalId, decision, options);
    if (detail?.type === "agent_approval" && detail.value?.id === approvalId) {
      setDetail(result?.approval ? { type: "agent_approval", value: result.approval } : null);
    }
    return result;
  }

  useEffect(() => {
    bootstrap();
  }, []);

  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 899px)");
    const wide = window.matchMedia("(min-width: 1120px)");
    const syncSidebar = () => {
      if (narrow.matches) setSidebarOpen(false);
      else if (wide.matches) setSidebarOpen(true);
    };
    syncSidebar();
    narrow.addEventListener("change", syncSidebar);
    wide.addEventListener("change", syncSidebar);
    return () => {
      narrow.removeEventListener("change", syncSidebar);
      wide.removeEventListener("change", syncSidebar);
    };
  }, []);

  useEffect(() => {
    if (thread?.active_atlas_id) {
      loadBundle(thread.active_atlas_id);
      loadObjectMemories(thread.active_atlas_id);
      loadAtlasUpdates(thread.active_atlas_id);
    }
  }, [thread?.active_atlas_id]);

  useEffect(() => {
    if (!knowledgeStatus?.active_jobs?.length) return undefined;
    const timer = window.setInterval(() => refreshKnowledgeStatus().catch(() => {}), 1200);
    return () => window.clearInterval(timer);
  }, [knowledgeStatus?.active_jobs?.length]);

  async function bootstrap() {
    setStatus("正在加载 vNext 工作区");
    let [atlasList, projectList, threadList, secretStatus, templateList, info, knowledge] = await Promise.all([
      api("/atlases"),
      api("/projects"),
      api("/threads"),
      api("/secrets/status"),
      api("/research-templates"),
      api("/system/info").catch(() => null),
      api("/knowledge/status").catch(() => null)
    ]);
    if (!projectList.length) {
      const createdProject = await api("/projects", {
        method: "POST",
        body: JSON.stringify({
          title: "我的研究成果",
          goal: "沉淀一个可持续推进的研究成果目标。",
          default_atlas_id: "G"
        })
      });
      projectList = [createdProject];
    }
    setAtlases(atlasList);
    setProjects(projectList);
    setSecrets(secretStatus);
    setSystemInfo(info);
    setKnowledgeStatus(knowledge);
    setResearchTemplates(templateList);
    if (threadList.length) {
      setThreads(threadList);
      setActiveProjectId(threadList[0].project_id || "unfiled");
      await openThread(threadList[0].id, "home");
    } else {
      const created = await api("/threads", {
        method: "POST",
        body: JSON.stringify({
          title: "新的研究问题",
          goal: "围绕成果目标收集图谱证据，并导出可交给 Codex 的上下文包。",
          active_atlas_id: projectList[0]?.default_atlas_id || "G",
          project_id: projectList[0]?.id
        })
      });
      setThreads([created]);
      setThread(created);
      setActiveProjectId(created.project_id || "unfiled");
    }
    setStatus("就绪");
  }

  async function refreshThreads() {
    const list = await api("/threads");
    setThreads(list);
  }

  async function refreshProjects() {
    const list = await api("/projects");
    setProjects(list);
  }

  async function openThread(id, nextSurface = null) {
    const requestId = ++threadRequestRef.current;
    const doc = await api(`/threads/${id}`);
    if (requestId !== threadRequestRef.current) return;
    const next = nextSurface || doc.active_surface || "atlas";
    setThread(doc);
    setActiveProjectId(doc.project_id || "unfiled");
    setSurface(next);
    if (next !== "home") setRailOpen(false);
    if (next === "atlas") setRail("atlas");
    setToolsPage(false);
    setPathDraft([]);
    setDetail(null);
    setPaperOverlay(null);
    setExported("");
    setResultPreview(null);
    setTaskPackPreview(null);
    setAtlasUpdatePreview(null);
    setAtlasUpdatePaste("");
    setStatus(`已打开：${displayThreadTitle(doc.title)}`);
  }

  async function createThread() {
    const currentProject = projects.find((project) => project.id === activeProjectId);
    const created = await api("/threads", {
      method: "POST",
      body: JSON.stringify({
        title: "新的研究问题",
        goal: "围绕当前成果目标收集图谱证据，并导出可执行上下文。",
        active_atlas_id: currentProject?.default_atlas_id || thread?.active_atlas_id || "G",
        project_id: activeProjectId === "unfiled" ? null : activeProjectId
      })
    });
    await refreshThreads();
    setThread(created);
    setSurface("home");
    setStatus("已新建研究问题");
  }

  async function createProject() {
    const created = await api("/projects", {
      method: "POST",
      body: JSON.stringify({
        title: "新的成果目标",
        goal: "定义一个需要持续推进的研究产出。",
        default_atlas_id: thread?.active_atlas_id || "G"
      })
    });
    await refreshProjects();
    setActiveProjectId(created.id);
    setStatus("已新建成果目标");
  }

  function switchProject(projectId) {
    setActiveProjectId(projectId);
    const nextThread = threads.find((item) => (item.project_id || "unfiled") === projectId);
    if (nextThread) openThread(nextThread.id, "home");
  }

  function requestDeleteProject(projectId) {
    if (!projectId || projectId === "unfiled") return;
    const target = projects.find((project) => project.id === projectId);
    const title = target?.title || "这个成果目标";
    const affected = threads.filter((item) => item.project_id === projectId).length;
    setConfirmAction({
      tone: "danger",
      title: "删除成果目标",
      message: `删除「${title}」后，${affected} 个研究问题会转入“未归档成果”，不会被删除。`,
      confirmLabel: "删除成果目标",
      onConfirm: () => deleteProject(projectId)
    });
  }

  async function deleteProject(projectId) {
    if (!projectId || projectId === "unfiled") return;
    const target = projects.find((project) => project.id === projectId);
    const title = target?.title || "这个成果目标";
    await api(`/projects/${projectId}`, { method: "DELETE" });
    const [projectList, threadList] = await Promise.all([api("/projects"), api("/threads")]);
    setProjects(projectList);
    setThreads(threadList);
    setActiveProjectId("unfiled");
    if (thread?.project_id === projectId) {
      await openThread(thread.id, "home");
    }
    setStatus(`已删除成果目标：${title}`);
  }

  function requestDeleteThread(threadId) {
    if (!threadId) return;
    const target = threads.find((item) => item.id === threadId);
    const title = displayThreadTitle(target?.title || "这个研究问题");
    setConfirmAction({
      tone: "danger",
      title: "删除研究问题",
      message: `删除「${title}」前会备份线程文件，但它会立即从当前列表移除。`,
      confirmLabel: "删除研究问题",
      onConfirm: () => deleteThread(threadId)
    });
  }

  async function deleteThread(threadId) {
    if (!threadId) return;
    const target = threads.find((item) => item.id === threadId);
    const title = displayThreadTitle(target?.title || "这个研究问题");
    await api(`/threads/${threadId}`, { method: "DELETE" });
    const list = await api("/threads");
    setThreads(list);
    setStatus(`已删除研究问题：${title}`);
    if (thread?.id !== threadId) return;
    const nextThread =
      list.find((item) => (item.project_id || "unfiled") === activeProjectId) ||
      list[0];
    if (nextThread) {
      await openThread(nextThread.id, "home");
    } else {
      await createThread();
    }
  }

  async function saveThread(patch, quiet = false) {
    if (!thread) return null;
    const updated = await api(`/threads/${thread.id}`, {
      method: "PUT",
      body: JSON.stringify(patch)
    });
    setThread(updated);
    await refreshThreads();
    if (!quiet) setStatus("已保存到 data/personal");
    return updated;
  }

  async function appendMessage(message) {
    if (!thread?.id) return null;
    const updated = await api(`/threads/${thread.id}/messages`, {
      method: "POST",
      body: JSON.stringify(message)
    });
    setThread(updated);
    await refreshThreads();
    return updated;
  }

  async function refreshKnowledgeStatus() {
    const next = await api("/knowledge/status");
    setKnowledgeStatus(next);
    return next;
  }

  async function startKnowledgeSync(scope = "metadata") {
    const job = await api("/knowledge/sync", {
      method: "POST",
      body: JSON.stringify({ scope, atlas_ids: thread?.active_atlas_id ? [thread.active_atlas_id] : [] })
    });
    setStatus(scope === "hot_fulltext" ? "已开始补全当前 Atlas 热集全文" : scope === "reindex" ? "已开始下载并构建本地语义索引" : "已开始核验知识库元数据");
    await refreshKnowledgeStatus();
    return job;
  }

  async function controlKnowledgeJob(jobId, action) {
    await api(`/knowledge/jobs/${encodeURIComponent(jobId)}/${action}`, { method: "POST" });
    await refreshKnowledgeStatus();
  }

  async function confirmAgentProposal(proposalId) {
    if (!thread?.id || !proposalId) return null;
    const response = await api(`/threads/${thread.id}/action-proposals/${encodeURIComponent(proposalId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({})
    });
    setThread(response.thread);
    await refreshThreads();
    if (thread.active_atlas_id) {
      await loadObjectMemories(thread.active_atlas_id);
      await loadAtlasUpdates(thread.active_atlas_id);
    }
    setActiveProposalId(null);
    setSurface("thread");
    setStatus(`已确认智能体提案：${response.proposal?.summary || proposalId}`);
    if (response.proposal?.type === "atlas_candidate" && response.applied?.candidate_id) {
      focusAtlasCandidate(response.applied.candidate_id);
    }
    return response;
  }

  async function rejectAgentProposal(proposalId) {
    if (!thread?.id || !proposalId) return null;
    const response = await api(`/threads/${thread.id}/action-proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body: JSON.stringify({})
    });
    setThread(response.thread);
    await refreshThreads();
    setActiveProposalId(null);
    setSurface("thread");
    setStatus(`已驳回智能体提案：${response.proposal?.summary || proposalId}`);
    return response;
  }

  async function confirmChangeSet(changesetId, selectedOperationIds, editedValues = {}) {
    if (!thread?.id || !changesetId) return null;
    try {
      const response = await api(`/threads/${thread.id}/changesets/${encodeURIComponent(changesetId)}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          selected_operation_ids: selectedOperationIds,
          edited_values: editedValues,
          expected_revision: thread.revision
        })
      });
      setThread(response.thread);
      await refreshThreads();
      await Promise.all([
        loadObjectMemories(thread.active_atlas_id),
        loadAtlasUpdates(thread.active_atlas_id)
      ]);
      setActiveChangesetId(changesetId);
      setSurface("thread");
      setStatus(`已原子应用变更集：${response.changeset?.summary || changesetId}`);
      const candidate = response.applied?.operations?.find((item) => item?.candidate_id);
      if (candidate?.candidate_id) focusAtlasCandidate(candidate.candidate_id);
      return response;
    } catch (error) {
      setStatus(error.status === 409 ? `变更集存在冲突：${error.message}` : `应用变更集失败：${error.message}`);
      throw error;
    }
  }

  async function rejectChangeSet(changesetId) {
    if (!thread?.id || !changesetId) return null;
    const response = await api(`/threads/${thread.id}/changesets/${encodeURIComponent(changesetId)}/reject`, {
      method: "POST",
      body: JSON.stringify({})
    });
    setThread(response.thread);
    await refreshThreads();
    setStatus(`已驳回变更集：${response.changeset?.summary || changesetId}`);
    return response;
  }

  async function undoChangeSet(changesetId) {
    if (!thread?.id || !changesetId) return null;
    try {
      const response = await api(`/threads/${thread.id}/changesets/${encodeURIComponent(changesetId)}/undo`, {
        method: "POST",
        body: JSON.stringify({})
      });
      setThread(response.thread);
      await refreshThreads();
      await Promise.all([
        loadObjectMemories(thread.active_atlas_id),
        loadAtlasUpdates(thread.active_atlas_id)
      ]);
      setStatus(`已撤销变更集：${response.changeset?.summary || changesetId}`);
      return response;
    } catch (error) {
      setStatus(`无法安全撤销：${error.message}`);
      throw error;
    }
  }

  async function loadBundle(atlasId) {
    const requestId = ++bundleRequestRef.current;
    const cached = bundleCacheRef.current.get(atlasId);
    if (cached) setBundle(cached);
    const data = await api(`/atlases/${atlasId}/bundle`);
    if (requestId !== bundleRequestRef.current) return;
    bundleCacheRef.current.set(atlasId, data);
    setBundle(data);
  }

  async function loadObjectMemories(atlasId) {
    const requestId = ++memoryRequestRef.current;
    const data = await api(`/object-memory?atlas_id=${encodeURIComponent(atlasId)}`);
    if (requestId !== memoryRequestRef.current) return;
    setObjectMemories(data);
  }

  async function loadAtlasUpdates(atlasId) {
    const requestId = ++updateRequestRef.current;
    const data = await api(`/atlas-updates/${encodeURIComponent(atlasId)}`);
    if (requestId !== updateRequestRef.current) return;
    setAtlasUpdates(data);
  }

  async function saveObjectMemory(ref, memory) {
    if (!ref?.atlasId || !ref?.type || !ref?.id) return;
    const updated = await api(`/object-memory/${ref.atlasId}/${ref.type}/${encodeURIComponent(ref.id)}`, {
      method: "PUT",
      body: JSON.stringify(memory)
    });
    setObjectMemories((items) => {
      const key = objectMemoryKey(ref.type, ref.id);
      const next = items.filter((item) => {
        const itemRef = item.object_ref || {};
        return objectMemoryKey(itemRef.object_type, itemRef.object_id) !== key;
      });
      return [updated, ...next];
    });
    setStatus("对象记忆已保存");
    return updated;
  }

  function contextCards() {
    return thread?.context_cards || [];
  }

  async function addContextCard(card) {
    const exists = contextCards().some((item) => cardKey(item) === cardKey(card));
    if (exists) {
      await removeContextCard(card);
      return;
    }
    const next = [...contextCards(), card];
    const preparedCard = {
      include_in_agent: true,
      include_in_lab: false,
      selected_for_export: true,
      priority: 1,
      pinned: false,
      agent_note: "",
      ...card
    };
    next[next.length - 1] = preparedCard;
    const materialNode = {
      id: `node_${preparedCard.id}`,
      type: "material",
      title: preparedCard.title,
      body: preparedCard.summary,
      x: 420 + (next.length % 3) * 180,
      y: 120 + Math.floor(next.length / 3) * 120,
      card_id: preparedCard.id
    };
    await saveThread({
      context_cards: next,
      canvas: {
        nodes: [...(thread.canvas?.nodes || []), materialNode],
        edges: thread.canvas?.edges || []
      }
    });
    setStatus(`已加入上下文：${preparedCard.title || NODE_LABELS[preparedCard.type] || preparedCard.type}`);
  }

  async function removeContextCard(card) {
    const key = cardKey(card);
    const nextCards = contextCards().filter((item) => cardKey(item) !== key);
    const removedIds = new Set(contextCards().filter((item) => cardKey(item) === key).map((item) => item.id));
    const nextCanvas = {
      nodes: (thread.canvas?.nodes || []).filter((node) => !removedIds.has(node.card_id)),
      edges: (thread.canvas?.edges || []).filter((edge) => {
        const source = (thread.canvas?.nodes || []).find((node) => node.id === edge.source);
        const target = (thread.canvas?.nodes || []).find((node) => node.id === edge.target);
        return !removedIds.has(source?.card_id) && !removedIds.has(target?.card_id);
      })
    };
    await saveThread({ context_cards: nextCards, canvas: nextCanvas }, true);
    setStatus(`已从上下文移除：${card.title || NODE_LABELS[card.type] || card.type}`);
  }

  async function setAtlas(atlasId) {
    await saveThread({ active_atlas_id: atlasId, active_surface: "atlas" }, true);
    setDetail(null);
    setTaskPackPreview(null);
    setSurface("atlas");
    setToolsPage(false);
    setRail("atlas");
    setRailOpen(false);
    setContextDrawerOpen(false);
  }

  async function switchSurface(nextSurface) {
    setToolsPage(false);
    setSurface(nextSurface);
    if (nextSurface !== "home") setRailOpen(false);
    if (nextSurface === "atlas") setRail("atlas");
    setContextDrawerOpen(false);
    if (nextSurface !== "home") {
      await saveThread({ active_surface: nextSurface }, true);
    }
  }

  async function updateContextCard(card, patch, message = "上下文范围已更新") {
    const key = cardKey(card);
    const next = contextCards().map((item) =>
      cardKey(item) === key ? { ...item, ...patch } : item
    );
    await saveThread({ context_cards: next }, true);
    setStatus(message);
  }

  async function toggleCardExport(card) {
    await updateContextCard(
      card,
      { selected_for_export: !card.selected_for_export },
      card.selected_for_export ? "已从 Task Pack 范围排除" : "已加入 Task Pack 范围"
    );
  }

  async function toggleCardAgent(card) {
    await updateContextCard(
      card,
      { include_in_agent: !contextForAgent(card) },
      contextForAgent(card) ? "已从主对话上下文排除" : "已加入主对话上下文"
    );
  }

  async function toggleCardPinned(card) {
    await updateContextCard(
      card,
      { pinned: !card.pinned },
      card.pinned ? "已取消主对话置顶" : "已置顶到主对话上下文"
    );
  }

  async function setCardPriority(card, priority) {
    await updateContextCard(
      card,
      { priority: Number(priority) || 0 },
      `已将主对话优先级设为 ${Number(priority) || 0}`
    );
  }

  async function setCardAgentNote(card, agentNote) {
    await updateContextCard(
      card,
      { agent_note: agentNote },
      agentNote.trim() ? "已更新给智能体的备注" : "已清空给智能体的备注"
    );
  }

  async function exportTaskPack() {
    const data = await api(`/threads/${thread.id}/export`, {
      method: "POST",
      body: JSON.stringify({ selected_only: true })
    });
    setExported(data.markdown);
    const updated = await api(`/threads/${thread.id}`);
    setThread(updated);
    await refreshThreads();
    setContextDrawerOpen(true);
    setRailOpen(false);
    setStatus(`已导出 ${data.exported_cards} 张卡片 / ${data.token_estimate} tokens`);
    try {
      await navigator.clipboard.writeText(data.markdown);
      setStatus(`已复制 ${data.exported_cards} 张上下文卡片到剪贴板`);
    } catch {
      setStatus("Task Pack 已生成，请从右侧面板手动复制");
    }
  }

  function focusedObjectForTaskPack() {
    if (!detail) return null;
    if (detail.type === "paper") {
      const paper = detail.value;
      return {
        type: "paper",
        id: paper.id,
        title: paper.title,
        summary: paper.summary || paper.local_role || paper.why_included || "",
        source_ref: { atlas_id: thread.active_atlas_id, paper_id: paper.id }
      };
    }
    if (detail.type === "relation") {
      const rel = detail.value;
      return {
        type: "relation",
        id: rel.id,
        title: `${detail.source?.title || rel.source} -> ${detail.target?.title || rel.target}`,
        summary: rel.reason || rel.label || rel.type || "",
        source_ref: { atlas_id: thread.active_atlas_id, relation_id: rel.id }
      };
    }
    if (detail.type === "canvas_node") {
      const node = detail.value;
      return {
        type: "canvas_node",
        id: node.id,
        title: node.title,
        summary: node.body || "",
        source_ref: { node_id: node.id }
      };
    }
    return null;
  }

  async function recordToolRun(run) {
    if (!thread?.id) return null;
    const updated = await api(`/threads/${thread.id}/tool-runs`, {
      method: "POST",
      body: JSON.stringify(run)
    });
    setThread(updated);
    await refreshThreads();
    return updated;
  }

  async function previewResearchTaskPack(templateId = selectedTemplateId, override = "") {
    try {
      const data = await api(`/threads/${thread.id}/task-pack/preview`, {
        method: "POST",
        body: JSON.stringify({
          template_id: templateId,
          focused_object: focusedObjectForTaskPack(),
          selected_only: true,
          instruction_override: override || null
        })
      });
      setSelectedTemplateId(templateId);
      setTaskPackPreview(data);
      setExported(data.markdown);
      setRail("templates");
      setRailOpen(true);
      setStatus(`已生成「${data.title}」Task Pack 预览`);
      return data;
    } catch (error) {
      setStatus(`生成 Task Pack 失败：${error.message}`);
      return null;
    }
  }

  async function copyResearchTaskPack() {
    const preview = taskPackPreview || await previewResearchTaskPack();
    if (!preview) return;
    try {
      await navigator.clipboard.writeText(preview.markdown);
    } catch {
      setStatus("Task Pack 已生成，请从右侧模板面板手动复制");
      return;
    }
    try {
      await recordToolRun({
        tool: "research_template_copy",
        status: "done",
        summary: `复制「${preview.title}」Task Pack`,
        template_id: preview.template_id,
        mode: "copy",
        token_estimate: preview.token_estimate,
        input_summary: `${preview.included_cards} 张上下文卡片 · 聚焦 ${focusedTitleFromDetail(detail)}`
      });
      setStatus(`已复制「${preview.title}」Task Pack，并记录到当前线程`);
    } catch (error) {
      setStatus(`已复制 Task Pack，但记录运行失败：${error.message}`);
    }
  }

  async function runResearchTaskPack() {
    try {
      setStatus("正在发送 Task Pack 给 API...");
      const preview = await api(`/threads/${thread.id}/task-pack/run`, {
        method: "POST",
        body: JSON.stringify({
          template_id: selectedTemplateId,
          focused_object: focusedObjectForTaskPack(),
          selected_only: true,
          provider: "openai"
        })
      });
      setResultPreview(preview);
      const updated = await api(`/threads/${thread.id}`);
      setThread(updated);
      await refreshThreads();
      setContextDrawerOpen(true);
      setRailOpen(false);
      setStatus("API 返回已生成预览，请确认是否写入");
    } catch (error) {
      setStatus(`API 发送失败：${error.message}`);
    }
  }

  async function previewAtlasUpdatePack(action = selectedUpdateAction) {
    if (!thread?.active_atlas_id) return null;
    try {
      const data = await api(`/atlas-updates/${thread.active_atlas_id}/task-pack/preview`, {
        method: "POST",
        body: JSON.stringify({ action })
      });
      setSelectedUpdateAction(action);
      setAtlasUpdatePreview(data);
      setExported(data.markdown);
      setRail("atlas");
      setRailOpen(true);
      setStatus(`已生成「${data.run?.title || "论文更新"}」Task Pack`);
      return data;
    } catch (error) {
      setStatus(`生成论文更新包失败：${error.message}`);
      return null;
    }
  }

  async function copyAtlasUpdatePack(action = selectedUpdateAction) {
    const preview = atlasUpdatePreview?.run?.action === action
      ? atlasUpdatePreview
      : await previewAtlasUpdatePack(action);
    if (!preview) return;
    try {
      await navigator.clipboard.writeText(preview.markdown);
      await recordToolRun({
        tool: "atlas_update_copy",
        status: "done",
        summary: `复制「${preview.run?.title || "论文更新"}」Task Pack`,
        mode: "copy",
        token_estimate: preview.token_estimate,
        input_summary: `Atlas ${thread.active_atlas_id} · ${preview.run?.title || "论文更新"}`
      });
      setStatus(`已复制「${preview.run?.title || "论文更新"}」Task Pack`);
    } catch (error) {
      setStatus(`论文更新包已生成，但复制或记录失败：${error.message}`);
    }
  }

  async function pasteAtlasUpdateResult() {
    const raw = atlasUpdatePaste.trim();
    if (!raw || !thread?.active_atlas_id) return;
    try {
      const data = await api(`/atlas-updates/${thread.active_atlas_id}/results/preview`, {
        method: "POST",
        body: JSON.stringify({ raw_text: raw, action: selectedUpdateAction })
      });
      setAtlasUpdatePreview((current) => ({ ...(current || {}), parsed: data }));
      setAtlasUpdatePaste("");
      await loadAtlasUpdates(thread.active_atlas_id);
      const firstCandidate = data.candidates?.[0];
      if (firstCandidate?.id) {
        setSurface("atlas");
        setAtlasFocusPaperId(firstCandidate.id);
      }
      await recordToolRun({
        tool: "atlas_update_paste",
        status: "done",
        summary: `解析论文更新返回：${data.candidates?.length || 0} 篇候选`,
        mode: "copy",
        input_summary: `Atlas ${thread.active_atlas_id} · 重复 ${data.duplicate_count || 0}`
      });
      setRail(firstCandidate?.id ? "detail" : "atlas");
      setRailOpen(true);
      setStatus(
        firstCandidate?.id
          ? `已解析 ${data.candidates?.length || 0} 篇候选，已跳转到第一张虚线卡`
          : "没有解析到新的候选论文"
      );
    } catch (error) {
      setStatus(`解析论文更新返回失败：${error.message}`);
    }
  }

  async function updateAtlasCandidate(candidateId, patch) {
    if (!thread?.active_atlas_id || !candidateId) return;
    const data = await api(`/atlas-updates/${thread.active_atlas_id}/candidates/${encodeURIComponent(candidateId)}`, {
      method: "PUT",
      body: JSON.stringify(patch)
    });
    setAtlasUpdates(data);
    setStatus("候选论文已更新");
    return data;
  }

  async function applyAtlasCandidate(candidateId) {
    if (!thread?.active_atlas_id || !candidateId) return;
    const data = await api(`/atlas-updates/${thread.active_atlas_id}/candidates/${encodeURIComponent(candidateId)}/apply`, {
      method: "POST"
    });
    setAtlasUpdates(data);
    await loadObjectMemories(thread.active_atlas_id);
    setStatus("候选论文已应用到个人 Atlas 层");
    return data;
  }

  async function bulkApplyAtlasCandidates() {
    if (!thread?.active_atlas_id) return;
    const data = await api(`/atlas-updates/${thread.active_atlas_id}/candidates/bulk-apply`, {
      method: "POST"
    });
    await loadAtlasUpdates(thread.active_atlas_id);
    await loadObjectMemories(thread.active_atlas_id);
    setStatus(`已批量应用 ${data.applied_count || 0} 篇高置信候选`);
  }

  function focusAtlasCandidate(candidateId) {
    if (!candidateId) return;
    setSurface("atlas");
    setToolsPage(false);
    setRail("detail");
    setRailOpen(!usesCompactInspector());
    setAtlasFocusPaperId(candidateId);
    setStatus("已跳转到候选虚线卡");
  }

  async function pasteResult() {
    const raw = pasteText.trim();
    if (!raw) return;
    const preview = await api(`/threads/${thread.id}/results/preview`, {
      method: "POST",
      body: JSON.stringify({ raw_text: raw })
    });
    setResultPreview(preview);
    setContextDrawerOpen(true);
    setRailOpen(false);
    setStatus("已生成 Codex 返回预览，请确认是否写入");
  }

  async function confirmResultPreview() {
    if (!resultPreview) return;
    const currentCanvas = thread.canvas || { nodes: [], edges: [] };
    const nextCanvas = {
      nodes: layoutCanvas([...(currentCanvas.nodes || []), ...(resultPreview.canvas_nodes || [])]),
      edges: [
        ...(currentCanvas.edges || []),
        ...(resultPreview.canvas_edges || []).map((edge) => ({ ...edge, label: normalizeEdgeLabel(edge.label) }))
      ]
    };
    await saveThread({ canvas: nextCanvas }, true);
    const updated = await api(`/threads/${thread.id}/results`, {
      method: "POST",
      body: JSON.stringify(resultPreview.result_card_preview)
    });
    setThread(updated);
    setPasteText("");
    setResultPreview(null);
    setSurface("thread");
    setStatus("Codex 返回已写入结果卡和 Context Canvas");
  }

  function cancelResultPreview() {
    setResultPreview(null);
    setStatus("已取消写入预览");
  }

  function openToolsAction(action) {
    setToolsPage(false);
    if (action === "copy") {
      setSurface("thread");
      setContextDrawerOpen(true);
      setRailOpen(false);
      exportTaskPack();
      return;
    }
    if (action === "templates") {
      setSurface(surface === "home" ? "atlas" : surface);
      setRail("templates");
      return;
    }
    if (action === "paste") {
      setSurface("thread");
      setContextDrawerOpen(true);
      setRailOpen(false);
    }
  }

  function runComposerCommand(commandId) {
    setToolOpen(false);
    if (commandId === "context") {
      setComposer("");
      setSurface("thread");
      setContextDrawerOpen(true);
      setRailOpen(false);
      return;
    }
    if (commandId === "tools") {
      setComposer("");
      setToolsPage(true);
      setSurface("atlas");
      setRailOpen(false);
      return;
    }
    if (commandId === "atlas") {
      setComposer("/atlas ");
      setStatus("输入 Atlas 编号，例如 /atlas H");
    }
  }

  function activateHomeAction(actionId) {
    if (actionId === "tools") {
      setToolsPage(true);
      setSurface("atlas");
      setRailOpen(false);
      return;
    }
    switchSurface(actionId);
  }

  async function recordAssistantAction(label, refs = {}) {
    await appendMessage({
      role: "tool",
      kind: "state",
      content: label,
      surface,
      refs
    });
  }

  async function handleAssistantSuggestion(suggestion) {
    const action = suggestion?.action;
    setToolsPage(false);
    if (action === "inspect_proposal") {
      const proposalId = suggestion?.payload?.proposal_id || suggestion?.id;
      setActiveProposalId(proposalId || null);
      setSurface("thread");
      setRail("changes");
      setRailOpen(true);
      setContextDrawerOpen(false);
      setStatus("已打开智能体提案确认面板");
      await recordAssistantAction("已根据智能体建议打开提案确认面板", { action, proposal_id: proposalId });
      return;
    }
    if (action === "inspect_changeset") {
      const changesetId = suggestion?.payload?.changeset_id || suggestion?.id;
      setActiveChangesetId(changesetId || null);
      setSurface("thread");
      setRail("changes");
      setRailOpen(true);
      setContextDrawerOpen(false);
      setStatus("已打开字段级变更集");
      await recordAssistantAction("已打开 Main Agent 变更集", { action, changeset_id: changesetId });
      return;
    }
    if (action === "open_context_editor" || action === "open_task_pack_editor") {
      setSurface("thread");
      setContextDrawerOpen(true);
      setRailOpen(false);
      setStatus("已打开上下文包编辑器");
      await recordAssistantAction("已打开上下文包编辑器", { action });
      return;
    }
    if (action === "open_atlas" || action === "add_evidence_hint") {
      switchSurface("atlas");
      setRail("atlas");
      setRailOpen(false);
      setContextDrawerOpen(false);
      setStatus(action === "add_evidence_hint" ? "已打开 Atlas：优先选择能支撑或质疑当前问题的证据" : "已打开 Atlas 总览");
      await recordAssistantAction(action === "add_evidence_hint" ? "已根据 AI 建议打开 Atlas 证据选择" : "已根据 AI 建议打开 Atlas 总览", { action });
      return;
    }
    if (action === "seed_canvas") {
      switchSurface("canvas");
      setRailOpen(false);
      setContextDrawerOpen(false);
      setStatus("已打开 Context Canvas：可以从当前问题、已选材料和假设开始编排");
      await recordAssistantAction("已根据 AI 建议打开 Context Canvas", { action });
      return;
    }
    if (action === "preview_task_pack") {
      setRail("templates");
      setRailOpen(true);
      const preview = await previewResearchTaskPack(selectedTemplateId);
      setStatus("已生成 Task Pack 预览，请确认后再复制或发送");
      await recordAssistantAction(
        preview
          ? `已根据 AI 建议生成 Task Pack 预览：${preview.title}，包含 ${preview.included_cards || 0} 张材料，约 ${preview.token_estimate || 0} tokens`
          : "已根据 AI 建议打开 Task Pack 预览",
        { action, template_id: selectedTemplateId, token_estimate: preview?.token_estimate, included_cards: preview?.included_cards }
      );
      return;
    }
    if (action === "open_templates") {
      openToolsAction("templates");
      setStatus("已打开研究动作模板");
      await recordAssistantAction("已根据 AI 建议打开研究动作模板", { action });
      return;
    }
    if (action === "paste_codex_return") {
      setSurface("thread");
      setContextDrawerOpen(true);
      setRailOpen(false);
      setStatus("手动导入已放入上下文高级区，主流程仍由智能体提案确认驱动");
      await recordAssistantAction("已根据 AI 建议打开高级手动导入入口", { action });
      return;
    }
    if (action === "inspect_sources") {
      setSurface("thread");
      setRailOpen(false);
      setContextDrawerOpen(false);
      await recordAssistantAction("已展开本轮检索来源", { action });
      requestAnimationFrame(() => {
        const items = document.querySelectorAll(".agent-citations");
        const latest = items[items.length - 1];
        if (latest && !latest.open) latest.open = true;
        latest?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
      setStatus("已展开本轮检索来源");
      return;
    }
    if (action === "open_settings") {
      openSettings();
      setStatus("请配置模型通道；密钥只写入系统凭据");
      await recordAssistantAction("已打开模型通道设置", { action });
    }
  }

  async function appendUserMessage(text, note = "已写入当前研究线程") {
    const shouldPromoteQuestion =
      (thread?.messages || []).length === 0 &&
      (!thread?.goal || thread.goal === "围绕当前成果目标收集图谱证据，并导出可执行上下文。") &&
      (!thread?.title || thread.title === "新的研究问题" || thread.title === "New research thread");
    const updated = await appendMessage({
      role: "user",
      kind: "text",
      content: text,
      surface,
      refs: {
        active_atlas_id: thread.active_atlas_id,
        selected_context_cards: selectedCards.length
      }
    });
    if (shouldPromoteQuestion) {
      const promoted = await api(`/threads/${thread.id}`, {
        method: "PUT",
        body: JSON.stringify({
          title: titleFromQuestion(text),
          goal: text,
          active_surface: "thread"
        })
      });
      setThread(promoted);
      await refreshThreads();
    } else if (updated) {
      await saveThread({ active_surface: "thread" }, true);
    }
    setSurface("thread");
    setToolsPage(false);
    setContextDrawerOpen(false);
    setRailOpen(false);
    setStatus(note);
  }

  function currentTurnAttachments() {
    return turnAttachments;
  }

  async function openAgentCitation(citation) {
    const ref = citation?.source_ref || {};
    if (ref.source_id) {
      try {
        const source = await api(`/sources/${encodeURIComponent(ref.source_id)}`);
        setDetail({ type: "agent_source", value: source });
        setRail("detail");
        setRailOpen(true);
        setContextDrawerOpen(false);
        setStatus(`已打开引用来源：${citation.title}`);
      } catch (error) {
        setStatus(`来源暂时无法打开：${error.message}`);
      }
      return;
    }
    if (ref.paper_id) {
      setAtlasFocusPaperId(ref.paper_id);
      switchSurface("atlas");
      setRail("detail");
      setRailOpen(true);
      setStatus(`已定位引用来源：${citation.title}`);
      return;
    }
    if (ref.canvas_node_id) {
      switchSurface("canvas");
      setRail("detail");
      setRailOpen(true);
      setStatus(`已打开 Canvas 来源：${citation.title}`);
      return;
    }
    setContextDrawerOpen(true);
    setRailOpen(false);
    setStatus(`已打开引用来源：${citation?.title || "上下文"}`);
  }

  function openTurnAttachment(attachment) {
    openAgentCitation({ title: attachment?.title || "本轮附件", source_ref: attachment?.source_ref || {} });
  }

  function askMainAgentAboutPaper(paperDetail, suggestedPrompt = "") {
    const paper = paperDetail?.value || paperDetail?.paper || paperDetail;
    if (!paper?.id) return;
    addTurnAttachment({
      id: `turn_${thread.active_atlas_id}_${paper.id}`,
      type: paper.is_candidate ? "candidate_paper" : "paper",
      title: paper.title || "未命名论文",
      source_ref: { atlas_id: thread.active_atlas_id, paper_id: paper.id },
      summary: paper.summary || paper.why_included || paper.why || paper.local_role || ""
    });
    setPaperOverlay(null);
    setRailOpen(false);
    setSurface("thread");
    setToolsPage(false);
    if (suggestedPrompt) setComposer(suggestedPrompt);
    setStatus(`已作为本轮附件：${paper.title || "未命名论文"}`);
    requestAnimationFrame(() => document.querySelector(".composer textarea")?.focus());
  }

  async function retainPaperForThread(paperDetail) {
    const card = paperDetail?.card || paperDetail?.fullDetail?.card;
    if (!card) return;
    if (contextCards().some((item) => cardKey(item) === cardKey(card))) {
      setStatus(`已在长期资料中：${card.title}`);
      return;
    }
    await addContextCard({ ...card, include_in_agent: true, selected_for_export: false });
    setStatus(`已长期保留：${card.title}`);
  }

  async function openPaperReading(paperDetail) {
    if (!paperDetail) return;
    const requestId = ++paperKnowledgeRequestRef.current;
    const paper = paperDetail.value || paperDetail.paper || paperDetail;
    setPaperOverlay({ ...paperDetail, knowledge: { loading: true } });
    setRailOpen(false);
    setContextDrawerOpen(false);
    if (!paper?.id) return;
    const workId = String(paper.id).startsWith("work_") ? String(paper.id) : `work_${paper.id}`;
    try {
      const evidence = await api(`/knowledge/works/${encodeURIComponent(workId)}/evidence?verified_only=false`);
      if (requestId !== paperKnowledgeRequestRef.current) return;
      setPaperOverlay((current) => current ? { ...current, knowledge: { ...evidence, loading: false } } : current);
    } catch (error) {
      if (requestId !== paperKnowledgeRequestRef.current) return;
      setPaperOverlay((current) => current ? {
        ...current,
        knowledge: { loading: false, unavailable: true, message: error.status === 404 ? "该候选尚未进入研究知识库" : error.message }
      } : current);
    }
  }

  async function promoteTurnAttachment(attachment) {
    const card = {
      id: `card_${attachment.type || "material"}_${attachment.source_ref?.paper_id || attachment.source_ref?.relation_id || Date.now()}`,
      type: attachment.type === "candidate_paper" ? "paper" : attachment.type || "material",
      title: attachment.title || "未命名资料",
      summary: attachment.summary || "",
      source_ref: attachment.source_ref || {},
      token_estimate: 240,
      include_in_agent: true,
      include_in_lab: false,
      selected_for_export: false
    };
    if (!contextCards().some((item) => cardKey(item) === cardKey(card))) await addContextCard(card);
    removeTurnAttachment(attachment);
    setStatus(`已转为长期资料：${card.title}`);
  }

  async function onComposerSubmit(event) {
    event.preventDefault();
    if (researchReadOnly) {
      setStatus("当前版本以只读模式打开研究数据库，不能创建新消息。");
      return;
    }
    const text = composer.trim();
    if (!text) return;
    try {
      if (isRunning) {
        const draftRevision = await flushDraft();
        const steered = await steerThreadChat(text);
        if (steered) {
          await clearDraftAfterPersist(draftRevision);
          setComposer("");
        }
        return;
      }
      if (text === "/context") {
        setSurface("thread");
        setContextDrawerOpen(true);
        setRailOpen(false);
      } else if (text === "/tools") {
        setToolsPage(true);
        setSurface("atlas");
        setRailOpen(false);
      } else if (/^\/atlas\s+\S+/i.test(text)) {
        const atlasId = text.split(/\s+/)[1]?.toUpperCase();
        const exists = atlases.some((atlas) => atlas.id === atlasId);
        if (exists) {
          await setAtlas(atlasId);
          setStatus(`已切换到 Atlas ${atlasId}`);
        } else {
          setStatus(`没有找到 Atlas ${atlasId}`);
          return;
        }
      } else if (text.startsWith("/")) {
        setContextDrawerOpen(false);
        const draftRevision = await flushDraft();
        const completed = await sendThreadChat(text, { surface, turnAttachments: currentTurnAttachments(), intentOverride: agentMode });
        if (completed) {
          await clearDraftAfterPersist(draftRevision);
          clearTurnAttachments();
          setComposer("");
          setStatus("未识别命令，已作为问题询问 AI");
        }
        return;
      } else {
        setContextDrawerOpen(false);
        const draftRevision = await flushDraft();
        const completed = await sendThreadChat(text, { surface, turnAttachments: currentTurnAttachments(), intentOverride: agentMode });
        if (completed) {
          await clearDraftAfterPersist(draftRevision);
          clearTurnAttachments();
          setComposer("");
        }
        return;
      }
      setComposer("");
    } catch (error) {
      setStatus(`处理输入失败：${error.message}`);
    }
  }

  const selectedCards = contextCards().filter((card) => card.selected_for_export);
  const memoryMap = useMemo(() => buildMemoryMap(objectMemories), [objectMemories]);
  const projectOptions = useMemo(
    () => [
      ...projects,
      { id: "unfiled", title: "未归档成果", goal: "尚未归入成果目标的研究问题。", default_atlas_id: thread?.active_atlas_id || "G" }
    ],
    [projects, thread?.active_atlas_id]
  );
  const activeProject = projectOptions.find((project) => project.id === activeProjectId) || projectOptions[0];
  const isHome = surface === "home" && !toolsPage;

  if (!thread) {
    return <div className="boot">正在加载 EAI vNext...</div>;
  }

  return (
    <div className={cx("app", isHome && "home-mode", researchReadOnly && "research-read-only", !sidebarOpen && "sidebar-collapsed", !isHome && railOpen && "inspector-open", !isHome && !railOpen && "rail-collapsed")}>
      <Sidebar
        collapsed={!sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((value) => !value)}
        threads={threads}
        activeThread={thread}
        projects={projectOptions}
        activeProject={activeProject}
        atlases={atlases}
        onOpenThread={openThread}
        onCreateThread={createThread}
        onCreateProject={createProject}
        onSwitchProject={switchProject}
        onDeleteProject={requestDeleteProject}
        onDeleteThread={requestDeleteThread}
        onSetAtlas={setAtlas}
        onHome={() => switchSurface("home")}
        onSwitchSurface={switchSurface}
        onTools={() => {
          setToolsPage(true);
          setSurface("atlas");
          setRailOpen(false);
        }}
        onSettings={openSettings}
        activeAtlas={thread.active_atlas_id}
        activeSurface={toolsPage ? "tools" : surface}
        isRunning={isRunning}
        readOnly={researchReadOnly}
      />

      <main className={cx("main", isHome && "home-main", !isHome && surface === "thread" && !toolsPage && "thread-main-shell")}>
        {researchReadOnly && (
          <div className="research-read-only-banner" role="status">
            <InlineNotice tone="warning" title="只读模式">
              数据库 schema {systemInfo.research_store.schema_version} 高于当前支持的 {systemInfo.research_store.supported_schema_version}；浏览和导出可用，写入与执行已停用。
            </InlineNotice>
          </div>
        )}
        {draftConflict && (
          <div className="research-read-only-banner" role="alert">
            <InlineNotice tone="warning" title="另一窗口更新了草稿" actions={<><button type="button" onClick={loadRemoteDraft}>载入新版本</button><button type="button" onClick={() => overwriteRemoteDraft().catch((error) => setStatus(`覆盖草稿失败：${error.message}`))}>使用本窗口内容</button></>}>
              为防止静默覆盖，当前输入仍保留在本窗口。
            </InlineNotice>
          </div>
        )}
        {isHome ? (
          <HomeSurface
            thread={thread}
            threads={threads}
            project={activeProject}
            bundle={bundle}
            cards={contextCards()}
            onPrompt={(text) => {
              setComposer(text);
              setStatus("已放入输入框，回车即可创建研究消息。");
            }}
            systemInfo={systemInfo}
            onOpenThread={(threadId) => threadId && openThread(threadId, "thread")}
            composer={(
              <Composer
                value={composer}
                setValue={setComposer}
                onSubmit={onComposerSubmit}
                toolOpen={toolOpen}
                setToolOpen={setToolOpen}
                onOpenContext={() => { setContextDrawerOpen(true); setRailOpen(false); }}
                isHome
                commands={COMPOSER_COMMANDS}
                onRunCommand={runComposerCommand}
                isRunning={isRunning}
                onStop={stopThreadChat}
                attachments={turnAttachments}
                onRemoveAttachment={removeTurnAttachment}
                intentMode={agentMode}
                onIntentModeChange={setAgentMode}
                readOnly={researchReadOnly}
              />
            )}
          />
        ) : (
          <>
            <section className="workspace">
              {toolsPage ? (
                <RunCenter
                  thread={thread}
                  knowledgeStatus={knowledgeStatus}
                  onRefreshKnowledge={refreshKnowledgeStatus}
                  onStartKnowledgeSync={startKnowledgeSync}
                  onControlKnowledgeJob={controlKnowledgeJob}
                  onAction={openToolsAction}
                />
              ) : surface === "atlas" ? (
                <AtlasSurface
                  bundle={bundle}
                  activeAtlas={thread.active_atlas_id}
                  onExport={exportTaskPack}
                  onControls={setAtlasControls}
                  onDetail={(payload) => {
                    setDetail(payload);
                    setRail("detail");
                    setRailOpen(true);
                    setContextDrawerOpen(false);
                  }}
                  onClearDetail={() => {
                    setDetail(null);
                    setRail("atlas");
                  }}
                  onOpenPaperDetail={openPaperReading}
                  onAskPaper={askMainAgentAboutPaper}
                  onAddCard={addContextCard}
                  onRemoveCard={removeContextCard}
                  cards={contextCards()}
                  selectedCount={selectedCards.length}
                  onContinueToCanvas={() => switchSurface("canvas")}
                  onOpenOverview={() => {
                    setRail("atlas");
                    setRailOpen(true);
                  }}
                  memoryMap={memoryMap}
                  atlasUpdates={atlasUpdates}
                  inspectorOpen={railOpen}
                  focusPaperId={atlasFocusPaperId}
                  onFocusHandled={() => setAtlasFocusPaperId(null)}
                  pathDraft={pathDraft}
                  setPathDraft={setPathDraft}
                />
              ) : surface === "canvas" ? (
                <ContextCanvas
                  thread={thread}
                  onDetail={(payload) => {
                    setDetail(payload);
                    setRail("detail");
                    setRailOpen(true);
                    setContextDrawerOpen(false);
                  }}
                  onSave={(canvas) => saveThread({ canvas })}
                  onThreadChanged={(updated) => {
                    setThread(updated);
                    refreshThreads();
                  }}
                  onStatus={setStatus}
                  onAskAgent={(prompt, attachment) => {
                    if (attachment) addTurnAttachment({ id: `turn_campaign_${Date.now()}`, ...attachment });
                    setComposer(prompt || "请检查当前 Context Canvas 的论证结构，指出证据缺口，并把必要修改生成可确认变更集。");
                    setSurface("thread");
                    setStatus(attachment ? "已附加 Campaign 对象，回车后由 Main Agent 调查" : "已准备 Canvas 结构检查问题，回车后由 Main Agent 调查");
                  }}
                />
              ) : (
                <ThreadSurface
                  thread={thread}
                  chatError={chatError}
                  onOpenCanvas={() => switchSurface("canvas")}
                  onSuggestionAction={handleAssistantSuggestion}
                  onRetryAssistant={retryThreadChat}
                  onCitation={openAgentCitation}
                  onOpenAttachment={openTurnAttachment}
                  onOpenChangeset={(changesetId) => {
                    setActiveChangesetId(changesetId);
                    setRail("changes");
                    setRailOpen(true);
                  }}
                  onInspectApproval={inspectAgentApproval}
                  onUndoOperationBatch={undoOperationBatch}
                />
              )}
            </section>
          </>
        )}

        {!isHome && !toolsPage && surface === "thread" && <Composer
          value={composer}
          setValue={setComposer}
          onSubmit={onComposerSubmit}
          toolOpen={toolOpen}
          setToolOpen={setToolOpen}
          onExport={exportTaskPack}
          onOpenTools={() => {
            setToolsPage(true);
            setSurface("atlas");
            setRailOpen(false);
          }}
          onOpenContext={() => {
            setContextDrawerOpen(true);
            setRailOpen(false);
          }}
          isHome={isHome}
          commands={COMPOSER_COMMANDS}
          onRunCommand={runComposerCommand}
          isRunning={isRunning}
          onStop={stopThreadChat}
          attachments={turnAttachments}
          onRemoveAttachment={removeTurnAttachment}
          intentMode={agentMode}
          onIntentModeChange={setAgentMode}
          readOnly={researchReadOnly}
        />}
      </main>

      {!isHome && !toolsPage && (
        <RightRail
          open={railOpen}
          mode={rail}
          setMode={setRail}
          onClose={() => setRailOpen(false)}
          onOpen={() => setRailOpen(true)}
          atlasControls={atlasControls}
          detail={detail}
          activeAtlas={thread.active_atlas_id}
          memoryMap={memoryMap}
          onSaveMemory={saveObjectMemory}
          cards={contextCards()}
          turnAttachments={turnAttachments}
          onRemoveTurnAttachment={removeTurnAttachment}
          onPromoteTurnAttachment={promoteTurnAttachment}
          onOpenMaterial={openTurnAttachment}
          selectedCards={selectedCards}
          onToggleCard={toggleCardExport}
          onToggleAgentCard={toggleCardAgent}
          onTogglePinnedCard={toggleCardPinned}
          onSetCardPriority={setCardPriority}
          onSetCardAgentNote={setCardAgentNote}
          onRemoveCard={removeContextCard}
          exported={exported}
          pasteText={pasteText}
          setPasteText={setPasteText}
          onPasteResult={pasteResult}
          resultPreview={resultPreview}
          onConfirmResultPreview={confirmResultPreview}
          onCancelResultPreview={cancelResultPreview}
          status={status}
          onExport={exportTaskPack}
          templates={researchTemplates}
          selectedTemplateId={selectedTemplateId}
          onSelectTemplate={(id) => {
            setSelectedTemplateId(id);
            setTaskPackPreview(null);
          }}
          taskPackPreview={taskPackPreview}
          onPreviewTaskPack={previewResearchTaskPack}
          onCopyTaskPack={copyResearchTaskPack}
          onRunTaskPack={runResearchTaskPack}
          secrets={secrets}
          onRecommendedTemplate={previewResearchTaskPack}
          onOpenPaperDetail={openPaperReading}
          onAskPaper={askMainAgentAboutPaper}
          onRetainPaper={retainPaperForThread}
          proposals={thread.action_proposals || []}
          activeProposalId={activeProposalId}
          onSelectProposal={setActiveProposalId}
          onConfirmProposal={confirmAgentProposal}
          onRejectProposal={rejectAgentProposal}
          changesets={thread.changesets || []}
          activeChangesetId={activeChangesetId}
          onSelectChangeset={setActiveChangesetId}
          onConfirmChangeset={confirmChangeSet}
          onRejectChangeset={rejectChangeSet}
          onUndoChangeset={undoChangeSet}
          atlasUpdates={atlasUpdates}
          atlasUpdatePreview={atlasUpdatePreview}
          atlasUpdatePaste={atlasUpdatePaste}
          setAtlasUpdatePaste={setAtlasUpdatePaste}
          selectedUpdateAction={selectedUpdateAction}
          onSelectUpdateAction={setSelectedUpdateAction}
          onPreviewAtlasUpdate={previewAtlasUpdatePack}
          onCopyAtlasUpdate={copyAtlasUpdatePack}
          onPasteAtlasUpdate={pasteAtlasUpdateResult}
          onUpdateAtlasCandidate={updateAtlasCandidate}
          onApplyAtlasCandidate={applyAtlasCandidate}
          onBulkApplyAtlasCandidates={bulkApplyAtlasCandidates}
          onFocusAtlasCandidate={focusAtlasCandidate}
          onAddCandidateCard={addContextCard}
          onResolveApproval={resolveAgentApproval}
        />
      )}
      {!toolsPage && (
        <ContextDrawer
          open={contextDrawerOpen}
          onClose={() => setContextDrawerOpen(false)}
          onOpenAtlas={() => {
            setContextDrawerOpen(false);
            switchSurface("atlas");
          }}
          attachments={turnAttachments}
          cards={contextCards()}
          onOpenMaterial={openTurnAttachment}
          onRemoveAttachment={removeTurnAttachment}
          onPromoteAttachment={promoteTurnAttachment}
          onRemoveCard={removeContextCard}
          onTogglePinned={toggleCardPinned}
          onSetPriority={setCardPriority}
          onSetNote={setCardAgentNote}
          onExport={exportTaskPack}
          exported={exported}
          pasteText={pasteText}
          setPasteText={setPasteText}
          onPasteResult={pasteResult}
          resultPreview={resultPreview}
          onConfirmResultPreview={confirmResultPreview}
          onCancelResultPreview={cancelResultPreview}
          nodeLabels={NODE_LABELS}
          status={status}
        />
      )}
      {confirmAction && (
        <ConfirmDialog
          action={confirmAction}
          onCancel={() => setConfirmAction(null)}
          onConfirm={async () => {
            const action = confirmAction;
            setConfirmAction(null);
            await action.onConfirm();
          }}
        />
      )}
      <DesktopSettings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        systemInfo={systemInfo}
        secrets={secrets}
      />
      <PaperReadingOverlay
        detail={paperOverlay}
        cards={contextCards()}
        onClose={() => setPaperOverlay(null)}
        onAddCard={addContextCard}
        onRemoveCard={removeContextCard}
        onSaveMemory={saveObjectMemory}
        onUpdateCandidate={updateAtlasCandidate}
        onApplyCandidate={applyAtlasCandidate}
        onAskMainAgent={askMainAgentAboutPaper}
        onRetainPaper={retainPaperForThread}
      />
    </div>
  );
}
