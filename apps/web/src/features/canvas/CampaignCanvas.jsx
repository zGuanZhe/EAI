import { useMemo, useState } from "react";
import {
  Archive, BookOpen, Check, ChevronRight, CirclePause, CirclePlay, Code2, Download,
  FileArchive, FileText, FlaskConical, GitBranch, GitCompare, MessageSquareText,
  PackageOpen, Play, Search, ShieldCheck, Sparkles, Square, X
} from "lucide-react";
import { Button, Drawer, EmptyState, InlineNotice, SegmentedControl, StatusDot, cx } from "../../components/ui/index.jsx";
import { useMediaQuery } from "../../app/useMediaQuery.js";
import { buildCampaignTree, CAMPAIGN_STAGE_LABELS } from "./model.js";

const STATUS_LABELS = {
  ready: "就绪", running: "运行中", waiting_approval: "等待确认", paused: "已暂停",
  completed: "已完成", failed: "失败", cancelled: "已取消", archived: "历史归档",
  interrupted: "已中断", proposed: "待执行", succeeded: "成功", discarded: "已暂缓",
  promoted: "已晋升"
};

const VIEW_OPTIONS = [
  { value: "overview", label: "总览" }, { value: "experiments", label: "实验树" },
  { value: "artifacts", label: "产物" }, { value: "manuscript", label: "论文" },
  { value: "reviews", label: "审稿" }
];

function IdeaPreview({ preview, busy, runtimeStatus, executionProfile, onExecutionProfile, onClose, onCreate }) {
  const [seedKind, setSeedKind] = useState("blank");
  const [seedPath, setSeedPath] = useState("");
  return (
    <div className="campaign-idea-preview">
      <header><div><span>研究想法候选</span><strong>{preview.objective}</strong></div><button type="button" aria-label="关闭想法预览" onClick={onClose}><X size={16} /></button></header>
      {!!preview.warnings?.length && <InlineNotice tone="warning" title="证据边界">{preview.warnings[0]}</InlineNotice>}
      <div className="campaign-idea-sources"><strong>证据准备</strong><span>{preview.sources.filter((item) => item.evidence_level === "full_text").length} 个全文来源</span><span>{preview.sources.length} 个相关来源</span></div>
      <div className="campaign-idea-list">
        {preview.ideas.map((idea, index) => (
          <article key={idea.id}>
            <span>方向 {index + 1}</span><h3>{idea.title}</h3><p>{idea.short_hypothesis}</p>
            <dl><div><dt>实验</dt><dd>{idea.experiments.slice(0, 2).join("；")}</dd></div><div><dt>指标</dt><dd>{idea.metrics.join(" · ")}</dd></div><div><dt>证据</dt><dd>{idea.evidence_note}</dd></div></dl>
            <Button variant="primary" disabled={busy || (seedKind === "local_snapshot" && !seedPath.trim())} onClick={() => onCreate(idea, { kind: seedKind, title: seedKind === "blank" ? "空白实验工作区" : "本地代码快照", source_path: seedPath, readonly: true })}><Check size={14} />选择并创建 Campaign</Button>
          </article>
        ))}
      </div>
      <footer className="campaign-idea-footer">
        <label>实验起点<select value={seedKind} onChange={(event) => setSeedKind(event.target.value)}><option value="blank">空白隔离脚手架</option><option value="local_snapshot">本地代码只读快照</option></select></label>
        {seedKind === "local_snapshot" && <label className="campaign-seed-path">本地目录<input value={seedPath} onChange={(event) => setSeedPath(event.target.value)} placeholder="D:\research\my-project" /></label>}
        <label>执行环境<select value={executionProfile} onChange={(event) => onExecutionProfile(event.target.value)}><option value="cpu">CPU</option>{runtimeStatus.cuda_available && <option value="cuda">CUDA</option>}</select></label>
      </footer>
    </div>
  );
}

function StageTrack({ stages, currentStageId }) {
  return <div className="campaign-stage-track" aria-label="Campaign 阶段">{stages.map((stage) => <div className={cx(stage.id === currentStageId && "active", stage.status === "completed" && "completed")} key={stage.id}><i /><span>{stage.title || CAMPAIGN_STAGE_LABELS[stage.kind] || stage.kind}</span><small>{stage.status === "completed" ? "完成" : stage.id === currentStageId ? "当前" : ""}</small></div>)}</div>;
}

function RuntimeNotice({ runtime, profile, onInstall }) {
  if (!runtime.docker_available) return <InlineNotice tone="warning" title="Docker 不可用">可继续证据准备、Idea 和代码预览；EAI 不会回退到宿主机执行。</InlineNotice>;
  const image = runtime[profile] || {};
  if (image.installed) return null;
  const installing = runtime.install?.status === "installing";
  return <InlineNotice tone="info" title={installing ? "正在安装 Campaign Runtime" : "需要安装隔离运行时"}>{installing ? `后台构建进度 ${runtime.install?.progress || 0}%` : "首次运行需要构建固定版本的 AI Scientist v2 Docker 镜像。"}<Button variant="secondary" disabled={installing} onClick={onInstall}>{installing ? "安装中" : "安装 Runtime"}</Button></InlineNotice>;
}

function BranchTree({ snapshot, selectedIds, onToggle, onSelect }) {
  const stage = snapshot.campaign.stages.find((item) => item.id === snapshot.campaign.current_stage_id);
  const tree = useMemo(() => buildCampaignTree(snapshot.branches, stage?.id, stage?.best_branch_id), [snapshot.branches, stage?.id, stage?.best_branch_id]);
  if (!tree.items.length) return <EmptyState icon={<GitBranch size={20} />} title="当前阶段还没有实验分支" description="启动 Campaign 后会生成独立草案；每个分支只需授权一次隔离会话。" />;
  return <div className="campaign-tree" style={{ "--tree-depth": Math.max(1, tree.maxDepth + 1) }}>{tree.items.map(({ branch, depth, lane, onBestPath }) => (
    <article className={cx("campaign-branch", `status-${branch.status}`, onBestPath && "best-path")} style={{ "--branch-depth": depth, "--branch-lane": lane }} key={branch.id}>
      <label className="campaign-branch-check"><input type="checkbox" checked={selectedIds.includes(branch.id)} onChange={() => onToggle(branch.id)} aria-label={`选择 ${branch.title} 比较`} /></label>
      <button type="button" onClick={() => onSelect(branch.id)}>
        <span><StatusDot status={branch.status} />{branch.origin === "draft" ? "草案" : branch.origin === "debug" ? "调试" : branch.origin === "manual" ? "历史" : "改进"}</span>
        <strong>{branch.title}</strong><p>{branch.analysis || branch.plan_summary}</p>
        <small>{branch.is_best ? "当前最佳" : STATUS_LABELS[branch.status] || branch.status}{branch.score != null ? ` · ${branch.score.toFixed(3)}` : ""}</small>
      </button>
    </article>
  ))}</div>;
}

function BranchInspector({ snapshot, branchId, approval, onClose, onPrepare, onPromote, onDiscard, onResolve, onAdvance, onAsk }) {
  const branch = snapshot?.branches.find((item) => item.id === branchId);
  const stage = snapshot?.campaign.stages.find((item) => item.id === branch?.stage_id);
  const metrics = snapshot?.metrics.filter((item) => item.branch_id === branchId) || [];
  const artifacts = snapshot?.artifacts.filter((item) => item.branch_id === branchId) || [];
  const session = snapshot?.sessions?.find((item) => item.id === branch?.session_id);
  const checkpoint = snapshot?.checkpoints?.find((item) => item.id === branch?.checkpoint_id);
  const relevantApproval = approval?.payload?.branch_id === branchId ? approval : null;
  const overlay = useMediaQuery("(max-width: 1119px)");
  return <Drawer open={Boolean(branch)} onClose={onClose} title={branch?.title || "实验分支"} eyebrow={stage?.title || "Campaign"} tone="violet" overlay={overlay} className="campaign-branch-drawer" footer={branch && <div className="campaign-inspector-actions">
    {relevantApproval ? <><Button variant="quiet" onClick={() => onResolve("reject")}>拒绝</Button><Button variant="primary" onClick={() => onResolve("approve")}><ShieldCheck size={14} />授权会话</Button></> : <>
      {branch.status === "proposed" && <Button variant="primary" onClick={() => onPrepare(branch.id)}><Play size={14} />准备分支会话</Button>}
      {branch.status === "succeeded" && <Button variant="secondary" onClick={() => onPromote(branch.id)}><Sparkles size={14} />晋升到 Canvas</Button>}
      {branch.status === "succeeded" && <Button variant="primary" onClick={() => onAdvance(stage.id, branch.id)}><ChevronRight size={14} />推进阶段</Button>}
      {onAsk && <Button variant="quiet" onClick={() => onAsk(branch)}><MessageSquareText size={14} />问 Main Agent</Button>}
      {["proposed", "failed", "succeeded"].includes(branch.status) && <Button variant="quiet" onClick={() => onDiscard(branch.id)}>暂缓</Button>}
    </>}
  </div>}>
    {branch && <div className="campaign-branch-detail">
      <div className="campaign-detail-status"><StatusDot status={branch.status} label={STATUS_LABELS[branch.status] || branch.status} /><span>{branch.origin} · debug {branch.debug_depth}</span></div>
      {relevantApproval && <InlineNotice tone="warning" title={relevantApproval.title}>{relevantApproval.summary}<div className="campaign-permission-list"><span>{session?.image}</span><span>{session?.cpu_count} CPU / {session?.memory_mb} MB</span><span>{session?.timeout_seconds}s</span><span>{session?.network_domains?.length ? session.network_domains.join("、") : "断网"}</span></div></InlineNotice>}
      <section><h3>实验计划</h3><p>{branch.plan_summary}</p></section>
      {branch.score_reason && <section><h3>推荐依据</h3><p>{branch.score_reason}</p></section>}
      {!!metrics.length && <section><h3>指标</h3>{metrics.map((metric) => <div className="campaign-metric" key={metric.id}><strong>{metric.name}</strong><span>{metric.value} {metric.unit}</span><small>{metric.dataset} / {metric.split}{metric.seed != null ? ` / seed ${metric.seed}` : ""}</small></div>)}</section>}
      {branch.analysis && <section><h3>运行分析</h3><p>{branch.analysis}</p></section>}
      {branch.error_summary && <section className="campaign-error"><h3>失败信息</h3><pre>{branch.error_summary}</pre></section>}
      <details><summary><Code2 size={14} />代码与 diff</summary><pre>{branch.code_diff || branch.code}</pre></details>
      {checkpoint && <section><h3>Checkpoint</h3><p>Journal step {checkpoint.journal_step} · {checkpoint.git_commit || "未生成 Git commit"}</p></section>}
      {!!artifacts.length && <section><h3>产物</h3>{artifacts.map((artifact) => <div className="campaign-artifact" key={artifact.id}><strong>{artifact.title}</strong><p>{artifact.summary}</p></div>)}</section>}
    </div>}
  </Drawer>;
}

function OverviewView({ snapshot, runtime, profile, onInstall }) {
  const successful = snapshot.branches.filter((item) => ["succeeded", "promoted"].includes(item.status)).length;
  const fullText = snapshot.campaign.selected_idea.evidence_note?.includes("全文");
  return <div className="campaign-overview">
    <RuntimeNotice runtime={runtime} profile={profile} onInstall={onInstall} />
    <section className="campaign-overview-summary"><div><span>研究假设</span><p>{snapshot.campaign.hypothesis}</p></div><div><span>证据状态</span><p>{fullText ? "包含可定位全文证据" : "仍需补充原文后再声明新颖性"}</p></div></section>
    <div className="campaign-stat-row"><span><strong>{snapshot.branches.length}</strong>分支</span><span><strong>{successful}</strong>成功</span><span><strong>{snapshot.metrics.length}</strong>指标</span><span><strong>{snapshot.artifacts.length}</strong>产物</span></div>
    <section><h3>预算与权限</h3><p>{snapshot.campaign.budget.max_branches} 个分支 · debug 深度 {snapshot.campaign.budget.max_debug_depth} · {snapshot.campaign.budget.cpu_count} CPU · {snapshot.campaign.budget.memory_mb} MB · 单会话 {Math.round(snapshot.campaign.budget.max_runtime_seconds / 60)} 分钟</p><p>网络：{snapshot.campaign.budget.network_domains?.length ? snapshot.campaign.budget.network_domains.join("、") : "默认断网"}</p></section>
  </div>;
}

function ArtifactView({ snapshot, onAsk }) {
  if (!snapshot.artifacts.length) return <EmptyState icon={<PackageOpen size={20} />} title="还没有 Campaign 产物" description="执行输出、比较、图表、论文和发布包会集中出现在这里。" />;
  return <div className="campaign-artifact-list">{snapshot.artifacts.map((artifact) => <article key={artifact.id}><span><PackageOpen size={15} /></span><div><strong>{artifact.title}</strong><p>{artifact.summary || artifact.kind}</p><small>{artifact.kind} · {artifact.content_hash?.slice(0, 10) || "runtime"}</small></div>{onAsk && <button type="button" onClick={() => onAsk(artifact)} title="问 Main Agent"><MessageSquareText size={14} /></button>}</article>)}</div>;
}

function ManuscriptView({ snapshot, busy, onGenerate, onExport, onAsk }) {
  const current = snapshot.manuscripts.find((item) => item.id === snapshot.campaign.current_manuscript_id) || snapshot.manuscripts.at(-1);
  if (!current) return <EmptyState icon={<FileText size={20} />} title="实验完成后生成论文" description="稿件只使用 Campaign 指标、产物和 Research Store 中可解析的来源。" action={<Button variant="primary" disabled={busy} onClick={onGenerate}><Sparkles size={14} />生成论文候选稿</Button>} />;
  return <div className="campaign-manuscript"><header><div><span>v{current.version} · {current.status}</span><h2>{current.title}</h2></div><div><Button variant="secondary" onClick={() => onAsk?.(current)}><MessageSquareText size={14} />问 Main Agent</Button><Button variant="secondary" onClick={() => onExport(true)}><FileArchive size={14} />导出草稿包</Button><Button variant="primary" disabled={current.warnings.length > 0} onClick={() => onExport(false)}><Download size={14} />正式发布包</Button></div></header>
    {!!current.warnings.length && <InlineNotice tone="warning" title="发布前需要处理">{current.warnings.join("；")}</InlineNotice>}
    {current.sections.map((section) => <section key={section.id}><h3>{section.title}</h3><p>{section.content}</p></section>)}
    <aside><strong>中文研究摘要</strong><p>{current.chinese_summary}</p></aside>
  </div>;
}

function ReviewView({ snapshot, busy, onStart, onApply }) {
  const current = snapshot.manuscripts.find((item) => item.id === snapshot.campaign.current_manuscript_id) || snapshot.manuscripts.at(-1);
  if (!current) return <EmptyState icon={<BookOpen size={20} />} title="还没有可审稿论文" description="先在论文视图生成候选稿。" />;
  const reviews = snapshot.reviews.filter((item) => item.manuscript_id === current.id);
  return <div className="campaign-review-view">
    <header><div><span>三角色审稿 + meta-review</span><h2>审稿与修订</h2></div><Button variant="primary" disabled={busy || snapshot.revisions.length >= 2} onClick={() => onStart(current.id)}><Search size={14} />{reviews.length ? "再次审稿" : "开始审稿"}</Button></header>
    {!reviews.length && <EmptyState icon={<BookOpen size={20} />} title="等待审稿" description="方法、证据和表达三个角色会独立检查，再生成 meta-review。" />}
    <div className="campaign-review-list">{reviews.map((review) => <article className={cx(review.role === "meta" && "meta")} key={review.id}><span>{review.role}</span><strong>{review.summary}</strong><p>{review.weaknesses.join("；")}</p><small>{review.score.toFixed(1)} / 10 · {review.decision}</small></article>)}</div>
    {snapshot.revisions.map((revision) => <article className="campaign-revision" key={revision.id}><div><span>修订 {revision.round_number}</span><strong>{revision.summary}</strong></div><pre>{Object.values(revision.section_diffs).join("\n")}</pre>{revision.status === "candidate" && <footer><Button variant="quiet" onClick={() => onApply(revision.id, false)}>驳回</Button><Button variant="primary" onClick={() => onApply(revision.id, true)}><Check size={14} />设为当前稿</Button></footer>}</article>)}
  </div>;
}

export function CampaignCanvas({ controller, sourceNode, onAskMainAgent }) {
  const [view, setView] = useState("overview");
  const active = controller.campaigns.find((item) => item.campaign.id === controller.activeCampaignId) || controller.campaigns[0];
  const ask = (item) => onAskMainAgent?.(`请分析 Campaign 中的“${item.title || item.id}”，结合其指标、证据和失败边界给出下一步建议。`, { type: "campaign", title: item.title || "Campaign 产物", source_ref: { campaign_id: active?.campaign?.id, branch_id: item.branch_id || item.id } });
  if (controller.ideaPreview) return <IdeaPreview preview={controller.ideaPreview} busy={controller.busy} runtimeStatus={controller.runtimeStatus} executionProfile={controller.executionProfile} onExecutionProfile={controller.setExecutionProfile} onClose={() => controller.setIdeaPreview(null)} onCreate={(idea, seed) => controller.createCampaign(idea, sourceNode?.id ? [sourceNode.id] : [], seed)} />;
  if (!active) return <div className="campaign-empty"><EmptyState icon={<FlaskConical size={22} />} title="还没有研究 Campaign" description="从问题或假设生成候选研究想法。Agent 会先检查 Atlas 与可用原文；实验始终在隔离 Runtime 中执行。" action={<Button variant="primary" disabled={controller.busy} onClick={() => controller.previewIdeas(sourceNode)}><Search size={14} />生成研究想法</Button>} /></div>;
  const campaign = active.campaign;
  const currentStage = campaign.stages.find((item) => item.id === campaign.current_stage_id);
  const toggleBranch = (id) => controller.setSelectedBranchIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id].slice(-6));
  return <div className={cx("campaign-workspace", controller.selectedBranchId && "inspector-open")}>
    <header className="campaign-header"><div><span>{campaign.source_kind === "legacy_lab" ? "历史 Campaign" : "AI Scientist v2 Campaign"}</span><h2>{campaign.title}</h2><p>{campaign.hypothesis}</p></div><div className="campaign-header-actions"><select value={campaign.id} onChange={(event) => controller.setActiveCampaignId(event.target.value)}>{controller.campaigns.map((item) => <option value={item.campaign.id} key={item.campaign.id}>{item.campaign.title}</option>)}</select>{campaign.status === "ready" && <Button variant="primary" onClick={() => controller.campaignAction(campaign.id, "start")}><CirclePlay size={14} />启动</Button>}{campaign.status === "running" && <Button variant="secondary" onClick={() => controller.campaignAction(campaign.id, "pause")}><CirclePause size={14} />暂停</Button>}{["paused", "interrupted"].includes(campaign.status) && <Button variant="primary" onClick={() => controller.campaignAction(campaign.id, "resume")}><CirclePlay size={14} />继续</Button>}{["running", "waiting_approval", "paused"].includes(campaign.status) && <Button variant="quiet" onClick={() => controller.campaignAction(campaign.id, "cancel")}><Square size={13} />停止</Button>}</div></header>
    <StageTrack stages={campaign.stages} currentStageId={campaign.current_stage_id} />
    <div className="campaign-viewbar"><SegmentedControl value={view} options={VIEW_OPTIONS} onChange={setView} label="Campaign 工作区" /><div><StatusDot status={campaign.status} label={STATUS_LABELS[campaign.status] || campaign.status} /><span>{currentStage?.title || "尚未开始"}</span></div></div>
    <main className="campaign-view-content">
      {view === "overview" && <OverviewView snapshot={active} runtime={controller.runtimeStatus} profile={controller.executionProfile} onInstall={controller.installRuntime} />}
      {view === "experiments" && <><div className="campaign-comparebar"><span>已选 {controller.selectedBranchIds.length} 个分支</span><Button variant="secondary" disabled={controller.selectedBranchIds.length < 2} onClick={() => controller.compareBranches(campaign.id)}><GitCompare size={14} />比较</Button></div>{controller.comparison && <InlineNotice tone="info" title="比较产物">协议：{controller.comparison.protocol}，包含 {controller.comparison.branches.length} 个分支。</InlineNotice>}<BranchTree snapshot={active} selectedIds={controller.selectedBranchIds} onToggle={toggleBranch} onSelect={controller.setSelectedBranchId} /></>}
      {view === "artifacts" && <ArtifactView snapshot={active} onAsk={ask} />}
      {view === "manuscript" && <ManuscriptView snapshot={active} busy={controller.busy} onGenerate={() => controller.generateManuscript(campaign.id)} onExport={(draft) => controller.exportRelease(campaign.id, campaign.current_manuscript_id, draft)} onAsk={ask} />}
      {view === "reviews" && <ReviewView snapshot={active} busy={controller.busy} onStart={(id) => controller.startReview(campaign.id, id)} onApply={(id, apply) => controller.applyRevision(campaign.id, id, apply)} />}
    </main>
    <BranchInspector snapshot={active} branchId={controller.selectedBranchId} approval={controller.pendingApproval} onClose={() => controller.setSelectedBranchId(null)} onPrepare={(branchId) => controller.prepareExecution(campaign.id, branchId)} onPromote={(branchId) => controller.requestPromotion(campaign.id, branchId)} onDiscard={(branchId) => controller.discardBranch(campaign.id, branchId)} onResolve={controller.resolveApproval} onAdvance={(stageId, branchId) => controller.advanceStage(campaign.id, stageId, branchId)} onAsk={ask} />
  </div>;
}
