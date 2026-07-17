import { Check, CircleStop, Clock3, Cpu, HardDrive, Network, ShieldCheck, Terminal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "未设置";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function FieldDiff({ operation }) {
  const hasDiff = operation.before !== undefined || operation.after !== undefined;
  if (!hasDiff) return <pre className="approval-arguments">{displayValue(operation.arguments)}</pre>;
  return (
    <div className="approval-field-diff">
      <div><span>当前值</span><pre>{displayValue(operation.before)}</pre></div>
      <div><span>确认后</span><pre>{displayValue(operation.after ?? operation.arguments)}</pre></div>
    </div>
  );
}

function SessionScope({ payload }) {
  const command = payload.command || payload.operation_batch?.operations?.find((item) => item.capability === "sandbox.command")?.arguments || {};
  const resources = payload.resources || payload.resource_limits || command.resources || {};
  const mounts = payload.mounts || command.mounts || [];
  const network = payload.network || command.network || {};
  if (!command.command && !Object.keys(resources).length && !mounts.length && !Object.keys(network).length) return null;
  return (
    <section className="approval-session-scope">
      <h3>执行范围</h3>
      {command.command && <div className="approval-command"><Terminal size={15} /><code>{command.command}</code></div>}
      <dl>
        <div><dt><HardDrive size={14} />工作目录</dt><dd>{command.workdir || payload.workdir || "Campaign 隔离工作区"}</dd></div>
        <div><dt><Cpu size={14} />资源</dt><dd>{resources.cpu || resources.cpus || "2 CPU"} · {resources.memory || "4 GB"}</dd></div>
        <div><dt><Clock3 size={14} />时限</dt><dd>{resources.timeout || command.timeout || "30 分钟"}</dd></div>
        <div><dt><Network size={14} />网络</dt><dd>{network.enabled ? (network.domains || []).join("、") || "仅审批域名" : "禁用"}</dd></div>
      </dl>
      {!!mounts.length && <div className="approval-mounts"><span>只读挂载</span>{mounts.map((mount, index) => <code key={`${mount.path || mount}-${index}`}>{displayValue(mount.path || mount)}</code>)}</div>}
    </section>
  );
}

export function AgentApprovalInspector({ approval, onResolve, onClose }) {
  const operations = approval?.payload?.operation_batch?.operations || [];
  const [selectedIds, setSelectedIds] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const pending = approval?.status === "pending";

  useEffect(() => {
    setSelectedIds(operations.filter((item) => item.selected !== false).map((item) => item.id));
  }, [approval?.id]);

  const confirmationLabel = useMemo(() => {
    if (approval?.kind === "sandbox_command") return "授权本次命令";
    if (approval?.kind === "execution_session") return "授权分支会话";
    if (approval?.level === "strong") return "确认高风险操作";
    return "确认所选修改";
  }, [approval?.kind, approval?.level]);

  if (!approval) return null;

  async function resolve(decision) {
    setSubmitting(true);
    try {
      await onResolve?.(approval.id, decision, {
        selectedOperationIds: decision === "approve" && operations.length ? selectedIds : null
      });
      onClose?.();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="agent-approval-inspector">
      <header>
        <span className="detail-type"><ShieldCheck size={14} />需要确认</span>
        <h2>{approval.title || "检查 Agent 操作"}</h2>
        <p>{approval.summary || "确认前请检查将要写入或执行的具体内容。"}</p>
      </header>

      {!!operations.length && (
        <section className="approval-operation-list">
          <h3>操作内容</h3>
          {operations.map((operation) => {
            const selected = selectedIds.includes(operation.id);
            return (
              <article key={operation.id} className={!selected ? "excluded" : ""}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!pending || approval.level === "strong"}
                    onChange={() => setSelectedIds((current) => selected ? current.filter((id) => id !== operation.id) : [...current, operation.id])}
                  />
                  <span><strong>{operation.summary || operation.capability}</strong><em>{operation.capability} · {operation.risk || "medium"}</em></span>
                </label>
                <FieldDiff operation={operation} />
              </article>
            );
          })}
        </section>
      )}

      <SessionScope payload={approval.payload || {}} />

      <section className="approval-policy-note">
        <h3>权限边界</h3>
        <p>只有这里展示的操作会被执行。长期数据写入会检查 revision 和真实 before 值；冲突时整批保持零写入。</p>
      </section>

      <footer>
        {!pending ? <span className={`approval-resolved ${approval.status}`}>该审批已{approval.status === "approved" ? "确认" : "拒绝"}</span> : (
          <>
            <button type="button" disabled={submitting} onClick={() => resolve("reject")}><CircleStop size={15} />拒绝</button>
            <button className="primary" type="button" disabled={submitting || (operations.length > 0 && selectedIds.length === 0)} onClick={() => resolve("approve")}><Check size={15} />{confirmationLabel}</button>
          </>
        )}
      </footer>
    </div>
  );
}
