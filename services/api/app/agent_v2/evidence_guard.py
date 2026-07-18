from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .models import AnswerClaim, AnswerDraft, CitationBinding, SourceRecord


DETAIL_MARKERS = {
    "方法", "实现", "架构", "算法", "训练", "实验", "结果", "指标", "消融", "局限", "比较",
    "method", "implementation", "training", "experiment", "result", "ablation", "limitation", "compare",
}


@dataclass(frozen=True)
class GuardedAnswer:
    answer: str
    bindings: list[CitationBinding]
    cited_sources: list[SourceRecord]
    warnings: list[str]
    guard_status: str
    citation_integrity: str
    evidence_sufficiency: str


def parse_answer_draft(raw: str) -> tuple[AnswerDraft, bool]:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict) and isinstance(payload.get("answer"), str):
            return AnswerDraft.model_validate(payload), True
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return AnswerDraft(answer=text), False


def _requires_full_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in DETAIL_MARKERS)


def _locator_resolves(source: SourceRecord) -> bool:
    locator = source.locator or {}
    if source.evidence_level == "system_truth":
        return True
    if source.source_kind == "atlas":
        atlas_scope = locator.get("atlas_id") or locator.get("atlas_ids")
        return bool(atlas_scope and (locator.get("paper_id") or locator.get("work_id")))
    if source.source_kind == "local_document":
        return bool(locator.get("document_id") and (locator.get("chunk_id") or locator.get("page")))
    return bool(locator.get("url") or locator.get("doi") or locator.get("arxiv_id") or locator.get("openalex_id"))


def _known_evidence_ids(source: SourceRecord) -> set[str]:
    locator = source.locator or {}
    values = list(locator.get("evidence_ids") or [])
    if locator.get("evidence_id"):
        values.append(locator["evidence_id"])
    return {str(value) for value in values if value}


def guard_answer(raw: str, sources: list[SourceRecord]) -> GuardedAnswer:
    draft, structured = parse_answer_draft(raw)
    source_by_id = {source.id: source for source in sources}
    cited_ids: list[str] = []
    bindings: list[CitationBinding] = []
    warnings: list[str] = []
    rendered_claims: list[str] = []

    if not structured or not draft.claims:
        return GuardedAnswer(
            answer="", bindings=[], cited_sources=[],
            warnings=["模型回答没有通过结构化证据协议，事实性正文已阻止。"],
            guard_status="invalid_draft", citation_integrity="missing", evidence_sufficiency="missing",
        )

    for index, claim in enumerate(draft.claims):
        valid_sources = [
            source_by_id[source_id] for source_id in claim.source_ids
            if source_id in source_by_id and _locator_resolves(source_by_id[source_id])
        ]
        requested_evidence = {str(item) for item in claim.evidence_ids if item}
        if requested_evidence:
            known_evidence = set().union(*(_known_evidence_ids(source) for source in valid_sources))
            if not requested_evidence.issubset(known_evidence):
                valid_sources = []
            else:
                valid_sources = [source for source in valid_sources if requested_evidence & _known_evidence_ids(source)]
        if claim.kind == "fact" and _requires_full_text(claim.text):
            valid_sources = [source for source in valid_sources if source.evidence_level == "full_text"]
        if claim.kind == "fact" and not valid_sources:
            warnings.append(f"论断缺少足够证据：{claim.text[:120]}")
            continue
        claim_text = re.sub(r"\[\[source:[^\]]+\]\]|\[S\d+\]", "", claim.text).strip()
        if not claim_text:
            continue
        claim_markers: list[str] = []
        for source in valid_sources:
            if source.id not in cited_ids:
                cited_ids.append(source.id)
            known_evidence = _known_evidence_ids(source)
            evidence_ids = sorted((requested_evidence & known_evidence) if requested_evidence else known_evidence)
            bindings.append(CitationBinding(
                id=f"binding_{index + 1}_{len(bindings) + 1}",
                source_id=source.id,
                evidence_ids=evidence_ids,
                claim_text=claim.text[:1000],
                evidence_level=source.evidence_level,
                locator=source.locator,
                status="verified" if source.evidence_level == "system_truth" or evidence_ids else "limited",
            ))
            claim_markers.append(f"[[source:{source.id}]]")
        rendered_claims.append(f"{claim_text}{''.join(claim_markers)}")

    answer = "\n\n".join(rendered_claims)
    marker_ids = re.findall(r"\[\[source:([^\]]+)\]\]", answer)
    for source_id in marker_ids:
        if source_id in source_by_id and source_id not in cited_ids:
            cited_ids.append(source_id)
    numbering = {source_id: index + 1 for index, source_id in enumerate(cited_ids)}
    answer = re.sub(
        r"\[\[source:([^\]]+)\]\]",
        lambda match: f"[S{numbering[match.group(1)]}]" if match.group(1) in numbering else "",
        answer,
    )
    cited_sources = [source_by_id[source_id] for source_id in cited_ids if source_id in source_by_id]
    integrity = "missing" if not bindings else "verified" if all(item.status == "verified" for item in bindings) else "limited"
    status = "blocked" if not answer.strip() else "limited" if warnings else "passed"
    return GuardedAnswer(
        answer=answer.strip(), bindings=bindings, cited_sources=cited_sources, warnings=warnings,
        guard_status=status, citation_integrity=integrity,
        evidence_sufficiency="sufficient" if status == "passed" else "limited" if answer.strip() else "missing",
    )


def answer_schema_instruction() -> dict[str, Any]:
    return {
        "answer": "Natural Markdown answer. Use [[source:SOURCE_ID]] immediately after supported factual statements.",
        "claims": [{"text": "Exact sentence from answer", "source_ids": ["source id"], "evidence_ids": [], "kind": "fact|inference|recommendation|status"}],
    }
