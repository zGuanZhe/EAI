import { ArrowRight, Blocks, FileText, GitBranch, Library, ListChecks, Settings2, Sparkles } from "lucide-react";

const ACTION_META = {
  open_atlas: { icon: GitBranch, fallback: "打开 Atlas" },
  add_evidence_hint: { icon: GitBranch, fallback: "选择证据" },
  seed_canvas: { icon: Blocks, fallback: "生成 Canvas 初稿" },
  preview_task_pack: { icon: FileText, fallback: "打开上下文包编辑器" },
  open_templates: { icon: Sparkles, fallback: "研究动作模板" },
  inspect_proposal: { icon: ListChecks, fallback: "查看修改提案" },
  inspect_changeset: { icon: ListChecks, fallback: "查看变更集" },
  inspect_sources: { icon: Library, fallback: "查看检索来源" },
  open_settings: { icon: Settings2, fallback: "配置模型" },
  open_context_editor: { icon: FileText, fallback: "上下文编辑器" },
  open_task_pack_editor: { icon: FileText, fallback: "Task Pack 编辑器" }
};

export function AssistantSuggestions({ suggestions = [], onAction }) {
  const visible = suggestions.filter((item) => item?.action).slice(0, 2);
  if (!visible.length) return null;
  return (
    <div className="assistant-suggestions" aria-label="智能体建议动作">
      {visible.map((item, index) => {
        const meta = ACTION_META[item.action] || { icon: ArrowRight, fallback: item.action };
        const Icon = meta.icon;
        return (
          <button
            type="button"
            key={item.id || `${item.action}-${index}`}
            onClick={() => onAction?.(item)}
          >
            <Icon size={14} />
            <span>
              <strong>{item.label || meta.fallback}</strong>
              {item.description && <em>{item.description}</em>}
            </span>
            <ArrowRight size={13} />
          </button>
        );
      })}
    </div>
  );
}
