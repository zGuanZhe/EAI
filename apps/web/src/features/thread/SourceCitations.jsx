import { ArrowUpRight, FileSearch } from "lucide-react";

export function SourceCitations({ citations = [], onOpen }) {
  if (!citations.length) return null;
  return (
    <details className="agent-citations" aria-label="本轮引用来源">
      <summary><FileSearch size={13} /> 查看 {citations.length} 个来源</summary>
      <div>
        {citations.map((citation) => (
          <button type="button" key={citation.id} onClick={() => onOpen?.(citation)} title={citation.excerpt || citation.title}>
            <b>{citation.id}</b>
            <span>{citation.title}<small>{evidenceLabel(citation.evidence_level)}</small></span>
            <ArrowUpRight size={12} />
          </button>
        ))}
      </div>
    </details>
  );
}

function evidenceLabel(level) {
  return {
    system_truth: "系统记录",
    user_knowledge: "用户资料",
    curated_summary: "Atlas 摘要",
    metadata: "元数据",
    abstract: "摘要",
    full_text: "全文",
    web_content: "网页"
  }[level] || "";
}
