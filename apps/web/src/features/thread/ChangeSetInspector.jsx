import { Check, RotateCcw, ShieldAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

function valueText(value) {
  if (value === null || value === undefined || value === "") return "空";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function editedValue(text, original) {
  if (typeof original === "string") return text;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function ChangeSetInspector({ changesets = [], activeId, onSelect, onConfirm, onReject, onUndo }) {
  const visible = changesets.filter((item) => item.status !== "rejected");
  const selected = visible.find((item) => item.id === activeId) || visible[0];
  const [selectedIds, setSelectedIds] = useState([]);
  const [edits, setEdits] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setSelectedIds((selected?.operations || []).filter((item) => item.selected !== false).map((item) => item.id));
    setEdits(Object.fromEntries((selected?.operations || []).map((item) => [item.id, valueText(item.after)])));
    setError("");
  }, [selected?.id, selected?.updated_at]);

  const editedValues = useMemo(() => Object.fromEntries(
    (selected?.operations || []).map((operation) => [operation.id, editedValue(edits[operation.id] ?? valueText(operation.after), operation.after)])
  ), [selected, edits]);

  if (!selected) {
    return (
      <section className="changeset-inspector empty">
        <span className="rail-section-label">变更</span>
        <strong>暂无待处理变更</strong>
        <p>Main Agent 需要修改 Context、Canvas、对象记忆或个人 Atlas 时，会在这里给出真实字段差异。</p>
      </section>
    );
  }

  async function run(action) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (nextError) {
      setError(nextError.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="changeset-inspector">
      <div className="changeset-switcher" aria-label="变更集列表">
        {visible.slice(0, 6).map((item) => (
          <button type="button" className={item.id === selected.id ? "active" : ""} key={item.id} onClick={() => onSelect?.(item.id)}>
            <span className={`changeset-status ${item.status}`} />
            {item.summary}
          </button>
        ))}
      </div>
      <header>
        <div>
          <span>{selected.status === "pending" ? "等待确认" : selected.status === "applied" ? "已应用" : selected.status === "conflicted" ? "存在冲突" : "已撤销"}</span>
          <strong>{selected.summary}</strong>
        </div>
        <em>{selected.operations.length} 项 · {selected.risk === "high" ? "高风险" : "个人数据层"}</em>
      </header>
      {selected.status === "conflicted" && (
        <div className="changeset-conflict"><ShieldAlert size={15} />目标字段已经变化，请重新运行 Agent 或检查冲突后再确认。</div>
      )}
      <div className="changeset-operations">
        {selected.operations.map((operation) => (
          <article key={operation.id}>
            <label className="changeset-operation-head">
              <input
                type="checkbox"
                checked={selectedIds.includes(operation.id)}
                disabled={selected.status !== "pending" && selected.status !== "conflicted"}
                onChange={() => setSelectedIds((current) => current.includes(operation.id) ? current.filter((id) => id !== operation.id) : [...current, operation.id])}
              />
              <span>{operation.target_type}</span>
              <strong>{operation.path}</strong>
              <em>{operation.op}</em>
            </label>
            <div className="changeset-diff">
              <div><span>当前</span><pre>{valueText(operation.before)}</pre></div>
              <div>
                <span>建议</span>
                <textarea
                  value={edits[operation.id] ?? valueText(operation.after)}
                  disabled={selected.status !== "pending" && selected.status !== "conflicted"}
                  onChange={(event) => setEdits((current) => ({ ...current, [operation.id]: event.target.value }))}
                />
              </div>
            </div>
            {operation.reason && <p>{operation.reason}</p>}
          </article>
        ))}
      </div>
      {error && <p className="changeset-error">{error}</p>}
      <footer>
        {(selected.status === "pending" || selected.status === "conflicted") && (
          <>
            <button type="button" className="primary-button" disabled={busy || !selectedIds.length} onClick={() => run(() => onConfirm?.(selected.id, selectedIds, editedValues))}>
              <Check size={14} /> 确认所选修改
            </button>
            <button type="button" className="ghost-button soft" disabled={busy} onClick={() => run(() => onReject?.(selected.id))}>
              <X size={14} /> 驳回
            </button>
          </>
        )}
        {selected.status === "applied" && (
          <button type="button" className="ghost-button soft" disabled={busy} onClick={() => run(() => onUndo?.(selected.id))}>
            <RotateCcw size={14} /> 撤销本次写入
          </button>
        )}
      </footer>
    </section>
  );
}
