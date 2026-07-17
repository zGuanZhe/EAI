from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import AgentTurnRequest, ServiceDecision, SourceRecord


FULL_TEXT_MARKERS = {
    "方法", "技术细节", "实现", "实现细节", "架构", "机制", "算法", "公式", "训练", "推理过程",
    "实验", "结果", "指标", "数据集", "基准", "消融", "表格", "局限", "失败案例", "边界",
    "比较", "对比", "差异", "复现", "method", "implementation", "architecture", "training",
    "experiment", "result", "ablation", "limitation", "benchmark", "compare",
}
ABSTRACT_MARKERS = {
    "贡献", "创新", "结论", "摘要", "概述", "总结", "为什么", "是否", "相关工作", "影响",
    "contribution", "abstract", "summary", "conclusion", "related work",
}


@dataclass(frozen=True)
class EvidenceAssessment:
    requirement: str
    requires_original: bool
    requires_full_text: bool
    sufficient: bool
    counts: dict[str, int] = field(default_factory=dict)
    missing_originals: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_requirement(request: AgentTurnRequest, decision: ServiceDecision) -> tuple[str, str]:
    if decision.service in {"conversation", "workspace_operation", "sandbox_execution"}:
        return "none", "本轮不需要论文证据。"
    if decision.service == "document_reading":
        return "full_text", "阅读或分析具体论文必须取得可定位的原文。"
    text = re.sub(r"\s+", " ", f"{request.message} {decision.objective}").lower()
    if decision.service == "synthesis" or any(marker in text for marker in FULL_TEXT_MARKERS):
        return "full_text", "方法、实验、结果、局限或跨论文比较需要原文证据。"
    if any(marker in text for marker in ABSTRACT_MARKERS):
        return "abstract", "该问题至少需要论文摘要或更高等级的原始来源。"
    return "discovery", "论文发现和书目导航可使用元数据与 Atlas 策展信息。"


def assess_evidence(
    request: AgentTurnRequest,
    decision: ServiceDecision,
    sources: list[SourceRecord],
    *,
    target_sources: list[SourceRecord] | None = None,
) -> EvidenceAssessment:
    requirement, reason = evidence_requirement(request, decision)
    counts = {level: 0 for level in ["metadata", "curated_summary", "abstract", "full_text", "web_content", "user_knowledge", "system_truth"]}
    for source in sources:
        counts[source.evidence_level] = counts.get(source.evidence_level, 0) + 1

    targets = [source for source in (target_sources or []) if source.source_kind == "atlas"]
    originals = [source for source in sources if source.evidence_level in {"abstract", "full_text"}]
    missing = []
    for target in targets:
        matches = [source for source in originals if same_work(target, source)]
        accepted = matches if requirement != "full_text" else [source for source in matches if source.evidence_level == "full_text"]
        if not accepted:
            missing.append(
                {
                    "source_id": target.id,
                    "title": target.title,
                    "doi": str(target.locator.get("doi") or ""),
                    "arxiv_id": str(target.locator.get("arxiv_id") or ""),
                }
            )

    if requirement in {"none", "discovery"}:
        sufficient = requirement == "none" or bool(sources)
    elif requirement == "abstract":
        sufficient = bool(originals) and not missing
    else:
        sufficient = counts.get("full_text", 0) > 0 and not missing
    return EvidenceAssessment(
        requirement=requirement,
        requires_original=requirement in {"abstract", "full_text"},
        requires_full_text=requirement == "full_text",
        sufficient=sufficient,
        counts=counts,
        missing_originals=missing,
        reason=reason,
    )


def original_search_queries(objective: str, targets: list[SourceRecord], limit: int = 4) -> list[str]:
    queries: list[str] = []
    for source in targets:
        locator = source.locator or {}
        for value in [locator.get("doi"), locator.get("arxiv_id"), source.title]:
            cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
            if cleaned and cleaned.lower() not in {item.lower() for item in queries}:
                queries.append(cleaned)
            if len(queries) >= max(1, limit - 1):
                break
        if len(queries) >= max(1, limit - 1):
            break
    cleaned_objective = re.sub(r"\s+", " ", objective or "").strip()
    if cleaned_objective and cleaned_objective.lower() not in {item.lower() for item in queries}:
        queries.append(cleaned_objective)
    return queries[:limit]


def open_full_text_candidates(sources: list[SourceRecord], targets: list[SourceRecord], limit: int = 2) -> list[SourceRecord]:
    candidates = [
        source for source in sources
        if source.access == "open" and source.locator.get("pdf_url") and source.source_kind in {"arxiv", "openalex", "semantic_scholar"}
    ]
    candidates.sort(key=lambda source: (not any(same_work(target, source) for target in targets), source.source_kind != "arxiv"))
    return candidates[:limit]


def same_work(left: SourceRecord, right: SourceRecord) -> bool:
    left_locator = left.locator or {}
    right_locator = right.locator or {}
    for key in ["doi", "arxiv_id"]:
        left_value = normalize_identifier(left_locator.get(key))
        right_value = normalize_identifier(right_locator.get(key))
        if left_value and right_value and left_value == right_value:
            return True
    if left.canonical_key and right.canonical_key and left.canonical_key == right.canonical_key:
        return True
    return normalize_title(left.title) == normalize_title(right.title)


def normalize_identifier(value: Any) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:|arxiv:)", "", str(value or "").strip().lower())


def normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())
