import { useState } from "react";
import { Check, ChevronRight, Database, LoaderCircle, RotateCcw, ShieldCheck } from "lucide-react";
import { api } from "../../api.js";

const SERVICE_LABELS = {
  conversation: "普通对话",
  evidence_research: "证据研究",
  document_reading: "论文阅读",
  synthesis: "综合与论证",
  workspace_operation: "工作区操作",
  sandbox_execution: "沙箱执行",
  research_campaign: "研究 Campaign"
};

export function AgentV2Activity({ message, onInspectApproval, onUndoOperationBatch }) {
  const refs = message.refs || {};
  const v2 = refs.agent_v2 || {};
  const summary = refs.agent_v2_summary || {};
  const service = summary.service || v2.service || refs.service_decision?.service;
  const running = ["pending", "streaming"].includes(message.status);
  const approvals = refs.agent_v2_approvals || v2.approvals || [];
  const pendingApproval = approvals.find((item) => item.status === "pending");
  const operationBatches = refs.agent_v2_operation_batches || [];
  const appliedBatch = operationBatches.find((item) => item.status === "applied");
  const undoneBatch = operationBatches.find((item) => item.status === "undone");
  const sourceCount = summary.source_count ?? v2.sources?.length ?? 0;
  const artifactCount = summary.artifact_count ?? v2.artifacts?.length ?? 0;
  const artifacts = refs.agent_v2_artifacts || v2.artifacts || [];
  const commandPreview = artifacts.find((item) => item.kind === "command_preview");
  const [audit, setAudit] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);

  async function loadAudit(event) {
    if (!event.currentTarget.open || audit || auditLoading || !refs.agent_v2_task_id) return;
    setAuditLoading(true);
    try {
      setAudit(await api(`/agent-v2/tasks/${refs.agent_v2_task_id}/audit${refs.agent_v2_attempt_id ? `?attempt_id=${encodeURIComponent(refs.agent_v2_attempt_id)}` : ""}`));
    } finally {
      setAuditLoading(false);
    }
  }

  if (!running && service === "conversation" && !pendingApproval) return null;
  if (!running && !service && !pendingApproval) return null;

  return (
    <div className="agent-v2-activity">
      <div className="agent-v2-status-row">
        {running ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
        <span>{running ? (v2.status || "正在理解你的目标") : `${SERVICE_LABELS[service] || summary.service_label || "任务"}已完成`}</span>
        {!running && (sourceCount > 0 || artifactCount > 0) && (
          <em>{sourceCount ? `${sourceCount} 个来源` : ""}{sourceCount && artifactCount ? " · " : ""}{artifactCount ? `${artifactCount} 个产物` : ""}</em>
        )}
      </div>
      {!running && service !== "conversation" ? (
        <details className="agent-v2-audit" onToggle={loadAudit}>
          <summary><ChevronRight size={13} />查看审计记录</summary>
          <div>
            {auditLoading && <p><LoaderCircle className="spin" size={13} />正在加载审计记录</p>}
            {(audit?.tool_calls || []).map((item) => {
              const observation = (audit?.observations || []).find((value) => value.tool_call_id === item.id);
              return (
                <article key={item.id} className="agent-v2-audit-call">
                  <span><Database size={13} /><strong>{item.capability}</strong><em>{item.duration_ms}ms</em></span>
                  <p>{observation?.summary || item.input_summary}</p>
                  {!!observation?.warnings?.length && <small>{observation.warnings.join(" · ")}</small>}
                </article>
              );
            })}
            {!auditLoading && audit && !audit.tool_calls?.length && <p>本轮没有调用研究能力。</p>}
          </div>
        </details>
      ) : null}
      {pendingApproval && (
        <section className="agent-v2-approval">
          <div>
            <ShieldCheck size={16} />
            <span><strong>{pendingApproval.title}</strong><em>{pendingApproval.summary}</em></span>
          </div>
          <div className="agent-v2-approval-actions">
            <button type="button" className="primary" onClick={() => onInspectApproval?.(pendingApproval)}><ShieldCheck size={14} />检查并确认</button>
          </div>
        </section>
      )}
      {!pendingApproval && (appliedBatch || undoneBatch) && (
        <div className="agent-v2-operation-receipt">
          <span>{appliedBatch ? "工作区修改已应用" : "工作区修改已撤销"}</span>
          {appliedBatch && (
            <button type="button" onClick={() => onUndoOperationBatch?.(appliedBatch.id)}>
              <RotateCcw size={13} />撤销
            </button>
          )}
        </div>
      )}
      {commandPreview && (
        <div className="agent-v2-command-preview">
          <span><strong>命令未执行</strong><em>{commandPreview.summary}</em></span>
          <code>{commandPreview.payload?.command?.command}</code>
        </div>
      )}
    </div>
  );
}
