import { Check, Circle, GitBranch, LoaderCircle, Wrench } from "lucide-react";

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

export function AgentRunTrace({ message, onOpenChangeset }) {
  const refs = message.refs || {};
  const steps = Array.isArray(refs.steps) ? refs.steps : [];
  const skills = Array.isArray(refs.skills) ? refs.skills : [];
  const tools = Array.isArray(refs.tool_calls) ? refs.tool_calls : [];
  const changesets = Array.isArray(refs.changesets) ? refs.changesets : [];
  const running = ["pending", "streaming"].includes(message.status);
  if (!steps.length && !skills.length && !tools.length && !changesets.length) return null;

  return (
    <details className="agent-run-trace" open={running}>
      <summary>
        <span className={cx("run-status-dot", running && "running")} />
        <strong>{running ? currentStatus(steps, tools) : changesets.length ? "调查完成，包含待确认修改" : "查看本轮调查过程"}</strong>
        <em>{skills.length} 个 Skill · {tools.length} 个工具</em>
      </summary>
      <div className="agent-run-lines">
        {skills.map((skill) => (
          <div className="agent-run-line skill" key={skill.id}>
            <GitBranch size={13} />
            <strong>{skill.label}</strong>
            <span>{skill.reason}</span>
          </div>
        ))}
        {steps.map((step) => (
          <div className={cx("agent-run-line step", step.status)} key={step.id || step.label}>
            {step.status === "done" ? <Check size={13} /> : step.status === "running" ? <LoaderCircle className="spin" size={13} /> : <Circle size={11} />}
            <span>{step.label}</span>
          </div>
        ))}
        {tools.map((tool) => (
          <div className={cx("agent-run-line tool", tool.status)} key={tool.id || `${tool.tool}-${tool.input_summary}`}>
            <Wrench size={13} />
            <strong>{toolLabel(tool.tool)}</strong>
            <span>{tool.result_summary || tool.error || tool.input_summary}</span>
            {!!tool.source_ids?.length && <em>{tool.source_ids.join(" · ")}</em>}
          </div>
        ))}
        {changesets.map((changeset) => (
          <button className={cx("agent-run-line changeset", changeset.status)} type="button" key={changeset.id} onClick={() => onOpenChangeset?.(changeset.id)}>
            <span className="run-status-dot warning" />
            <strong>{changeset.summary}</strong>
            <span>{changeset.operations?.length || 0} 项字段修改</span>
          </button>
        ))}
      </div>
    </details>
  );
}

function currentStatus(steps, tools) {
  const activeTool = [...tools].reverse().find((item) => item.status === "running");
  if (activeTool) return `${toolLabel(activeTool.tool)}中`;
  const activeStep = [...steps].reverse().find((item) => item.status === "running");
  return activeStep?.label || "正在理解问题";
}

function toolLabel(tool) {
  return {
    get_thread_state: "读取线程",
    get_context_index: "读取上下文",
    get_atlas_overview: "读取 Atlas",
    search_atlas: "检索论文",
    get_paper: "读取论文",
    get_relation_neighborhood: "读取关系",
    get_object_memory: "读取记忆",
    get_canvas: "读取 Canvas",
    diagnose_canvas: "诊断 Canvas"
  }[tool] || tool;
}
