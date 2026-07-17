import { Archive, BookOpenCheck, Clock3, Database, FileText, KeyRound, Pause, Play, RefreshCcw, RotateCcw, ShieldCheck, Sparkles, Square } from "lucide-react";
import { ActivityRow, EmptyState, StatusDot, SurfaceHeader } from "../../components/ui/index.jsx";
import "./tools.css";

function percentage(value = 0) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function fileSize(value = 0) {
  if (value < 1024 ** 2) return `${Math.round(value / 1024)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

export function RunCenter({
  thread,
  knowledgeStatus,
  onAction,
  onRefreshKnowledge,
  onStartKnowledgeSync,
  onControlKnowledgeJob
}) {
  const runs = thread?.tool_runs || [];
  const changes = (thread?.changesets || []).filter((item) => ["pending", "conflicted"].includes(item.status));
  return (
    <section className="run-center">
      <SurfaceHeader tone="cyan" eyebrow="低频工作区" title="运行中心" description="回看知识同步、Agent 写入和维护任务；Campaign 实验统一在 Canvas 中推进。" />
      <div className="run-center-body">
        <KnowledgeStatus
          value={knowledgeStatus}
          onRefresh={onRefreshKnowledge}
          onStartSync={onStartKnowledgeSync}
          onControlJob={onControlKnowledgeJob}
        />
        <section>
          <header><ShieldCheck size={16} /><strong>待确认变更</strong><span>{changes.length} 项</span></header>
          {changes.map((item) => <ActivityRow key={item.id} tone="orange" icon={<ShieldCheck size={15} />} title={item.summary} meta={item.status === "conflicted" ? "存在冲突" : `${item.operations?.length || 0} 项修改`} description={item.risk === "high" ? "高风险变更，请逐字段检查。" : "等待用户确认后写入个人数据。"} />)}
          {!changes.length && <EmptyState title="没有待确认变更" description="Main Agent 的修改建议会以可撤销的 ChangeSet 出现在这里。" />}
        </section>
        <section>
          <header><Clock3 size={16} /><strong>最近活动</strong><span>{runs.length} 条</span></header>
          {runs.slice(0, 10).map((run) => <ActivityRow key={run.id || `${run.tool}-${run.created_at}`} icon={<Clock3 size={15} />} title={run.summary || run.tool} meta={run.created_at || run.mode} description={run.input_summary} />)}
          {!runs.length && <EmptyState title="还没有运行记录" description="Agent、实验和高级互操作活动会记录在这里。" />}
        </section>
        <section>
          <header><Sparkles size={16} /><strong>维护与高级能力</strong><span>按需使用</span></header>
          <div className="maintenance-list">
            <ActivityRow tone="cyan" icon={<RotateCcw size={15} />} title="质量与 Bundle 健康" description="检查 Atlas 缓存和基础数据状态。" />
            <ActivityRow tone="violet" icon={<KeyRound size={15} />} title="模型通道" description="密钥由桌面系统凭据管理。" />
            <ActivityRow tone="green" icon={<Archive size={15} />} title="个人数据备份" description="查看线程、项目和对象记忆的安全快照。" />
          </div>
          <details className="run-center-advanced">
            <summary>高级互操作与旧系统归档</summary>
            <button type="button" onClick={() => onAction?.("templates")}><FileText size={14} />上下文包与研究模板</button>
            <a href="/admin/">旧 Admin</a><a href="/site/ai_studio.html">旧 AI Studio</a>
          </details>
        </section>
      </div>
    </section>
  );
}

function KnowledgeStatus({ value, onRefresh, onStartSync, onControlJob }) {
  const coverage = value?.coverage || {};
  const counts = value?.counts || {};
  const jobs = value?.active_jobs || [];
  return (
    <section className="knowledge-status-section">
      <header><Database size={16} /><strong>研究知识库</strong><span>{counts.works || 0} 篇论文 · {counts.relations || 0} 条关系</span></header>
      {!value ? (
        <EmptyState title="知识库状态暂不可用" description="Atlas 仍可只读使用；稍后重试状态检查。" action={<button type="button" onClick={() => onRefresh?.()}><RefreshCcw size={14} />重试</button>} />
      ) : (
        <>
          <div className="knowledge-metrics" aria-label="知识库覆盖率">
            <KnowledgeMetric label="身份元数据" value={coverage.metadata} meta={`${counts.works || 0} Work`} tone="cyan" />
            <KnowledgeMetric label="全文证据" value={coverage.full_text} meta={`${counts.documents || 0} 文档`} tone="green" />
            <KnowledgeMetric label="关系可验证" value={coverage.verified_relations} meta={`${counts.relations || 0} 关系`} tone="orange" />
            <KnowledgeMetric label="论断有依据" value={Math.min(1, coverage.claim_evidence || 0)} meta={`${counts.claims || 0} Claim`} tone="violet" />
          </div>
          <div className="knowledge-runtime-line">
            <span><BookOpenCheck size={14} />{value.embedding?.ready ? "多语言语义索引就绪" : "FTS5 + 图检索运行中"}</span>
            <span>缓存 {fileSize(value.cache?.bytes || 0)} / {fileSize(value.cache?.limit_bytes || 0)}</span>
            <div>
              <button type="button" onClick={() => onStartSync?.("metadata")}><RefreshCcw size={14} />核验元数据</button>
              <button type="button" onClick={() => onStartSync?.("hot_fulltext")}><BookOpenCheck size={14} />补全热集全文</button>
              {!value.embedding?.ready && <button type="button" onClick={() => onStartSync?.("reindex")}><Database size={14} />启用语义索引</button>}
            </div>
          </div>
          {jobs.map((job) => (
            <div className="knowledge-job" key={job.id}>
              <StatusDot status={job.status} />
              <div><strong>{job.scope === "hot_fulltext" ? "热集全文" : job.scope === "reindex" ? "重建索引" : "元数据同步"}</strong><span>{job.summary || "正在准备"}</span></div>
              <progress max={Math.max(1, job.total || 1)} value={job.progress || 0} />
              <em>{job.total ? `${job.progress}/${job.total}` : job.status}</em>
              <div className="knowledge-job-actions">
                {job.status === "paused" ? (
                  <button type="button" title="继续" onClick={() => onControlJob?.(job.id, "resume")}><Play size={14} /></button>
                ) : (
                  <button type="button" title="暂停" onClick={() => onControlJob?.(job.id, "pause")}><Pause size={14} /></button>
                )}
                <button type="button" title="取消" onClick={() => onControlJob?.(job.id, "cancel")}><Square size={13} /></button>
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

function KnowledgeMetric({ label, value, meta, tone }) {
  return (
    <div className={`knowledge-metric tone-${tone}`}>
      <div><span>{label}</span><strong>{percentage(value)}</strong></div>
      <div className="knowledge-meter"><i style={{ width: percentage(value) }} /></div>
      <small>{meta}</small>
    </div>
  );
}
