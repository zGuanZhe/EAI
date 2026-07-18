import { ArrowUpRight, FlaskConical, Pause, Play, Send, Square } from "lucide-react";
import { useState } from "react";

const PHASE_LABELS = {
  scope: "界定问题",
  search_map: "建立检索计划",
  gather: "检索来源",
  read: "阅读原文",
  compare: "比较证据",
  synthesize: "形成综合",
  guard: "核验引用",
  report: "形成报告",
  intake: "界定问题",
  context: "装配上下文",
  planning: "建立检索计划",
  synthesis: "比较与综合",
  evidence_guard: "核验引用",
  policy: "检查后续动作",
  paused: "已暂停"
};

const ACTIVE = new Set(["pending", "running", "waiting_approval", "paused", "interrupted"]);

export function ResearchTaskShelf({ tasks = [], onSteer, onPause, onResume, onCancel, onPromote }) {
  const [steer, setSteer] = useState("");
  const task = tasks.find((item) => ACTIVE.has(item.status)) || tasks[0];
  if (!task) return null;
  const running = ["pending", "running"].includes(task.status);
  const completed = task.status === "done";

  function submit(event) {
    event.preventDefault();
    const value = steer.trim();
    if (!value || !running) return;
    onSteer?.(task.id, value);
    setSteer("");
  }

  return (
    <section className="research-task-shelf" aria-label="研究任务" data-status={task.status}>
      <div className="research-task-summary">
        <FlaskConical size={15} />
        <span><strong>{task.objective || "研究任务"}</strong><em>{PHASE_LABELS[task.phase] || task.status}</em></span>
      </div>
      {running && (
        <form onSubmit={submit} className="research-task-steer">
          <input value={steer} onChange={(event) => setSteer(event.target.value)} placeholder="定向追加要求" aria-label="向研究任务追加要求" />
          <button type="submit" title="追加要求" disabled={!steer.trim()}><Send size={13} /></button>
        </form>
      )}
      <div className="research-task-actions">
        {running && <button type="button" title="暂停研究任务" onClick={() => onPause?.(task.id)}><Pause size={13} /></button>}
        {["paused", "interrupted"].includes(task.status) && <button type="button" title="继续研究任务" onClick={() => onResume?.(task.id)}><Play size={13} /></button>}
        {ACTIVE.has(task.status) && task.status !== "waiting_approval" && <button type="button" title="取消研究任务" onClick={() => onCancel?.(task.id)}><Square size={12} /></button>}
        {completed && <button type="button" className="promote" onClick={() => onPromote?.(task.id)}><ArrowUpRight size={13} />升级为 Campaign</button>}
      </div>
    </section>
  );
}
