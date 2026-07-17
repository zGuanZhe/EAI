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


def parse_answer_draft(raw: str) -> AnswerDraft:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else text
    try:
        payload = json.loads(candidate)
        if isinstance(payload, dict) and isinstance(payload.get("answer"), str):
            return AnswerDraft.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return AnswerDraft(answer=text)


def _requires_full_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in DETAIL_MARKERS)


def guard_answer(raw: str, sources: list[SourceRecord]) -> GuardedAnswer:
    draft = parse_answer_draft(raw)
    source_by_id = {source.id: source for source in sources}
    cited_ids: list[str] = []
    bindings: list[CitationBinding] = []
    warnings: list[str] = []
    answer = draft.answer

    for index, claim in enumerate(draft.claims):
        valid_sources = [source_by_id[source_id] for source_id in claim.source_ids if source_id in source_by_id]
        if claim.kind == "fact" and _requires_full_text(claim.text):
            valid_sources = [source for source in valid_sources if source.evidence_level == "full_text"]
        if claim.kind == "fact" and not valid_sources:
            warnings.append(f"论断缺少足够证据：{claim.text[:120]}")
            if claim.text and claim.text in answer and "待查证" not in claim.text:
                answer = answer.replace(claim.text, f"{claim.text}（待查证）", 1)
            continue
        for source in valid_sources:
            if source.id not in cited_ids:
                cited_ids.append(source.id)
            bindings.append(CitationBinding(
                id=f"binding_{index + 1}_{len(bindings) + 1}",
                source_id=source.id,
                evidence_ids=[str(item) for item in (claim.evidence_ids or source.locator.get("evidence_ids") or [])],
                claim_text=claim.text[:1000],
                evidence_level=source.evidence_level,
                locator=source.locator,
                status="verified" if source.evidence_level in {"full_text", "system_truth"} else "limited",
            ))

    marker_ids = re.findall(r"\[\[source:([^\]]+)\]\]", answer)
    for source_id in marker_ids:
        if source_id in source_by_id and source_id not in cited_ids:
            cited_ids.append(source_id)
    legacy_numbers = [int(value) for value in re.findall(r"\[S(\d+)\]", answer)]
    for number in legacy_numbers:
        if 1 <= number <= len(sources) and sources[number - 1].id not in cited_ids:
            cited_ids.append(sources[number - 1].id)

    numbering = {source_id: index + 1 for index, source_id in enumerate(cited_ids)}
    answer = re.sub(
        r"\[\[source:([^\]]+)\]\]",
        lambda match: f"[S{numbering[match.group(1)]}]" if match.group(1) in numbering else "",
        answer,
    )
    if legacy_numbers:
        legacy_map = {index + 1: numbering.get(source.id) for index, source in enumerate(sources)}
        answer = re.sub(
            r"\[S(\d+)\]",
            lambda match: f"[S{legacy_map[int(match.group(1))]}]" if legacy_map.get(int(match.group(1))) else "",
            answer,
        )
    cited_sources = [source_by_id[source_id] for source_id in cited_ids if source_id in source_by_id]
    return GuardedAnswer(answer=answer.strip(), bindings=bindings, cited_sources=cited_sources, warnings=warnings)


def answer_schema_instruction() -> dict[str, Any]:
    return {
        "answer": "Natural Markdown answer. Use [[source:SOURCE_ID]] immediately after supported factual statements.",
        "claims": [{"text": "Exact sentence from answer", "source_ids": ["source id"], "evidence_ids": [], "kind": "fact|inference|recommendation|status"}],
    }
