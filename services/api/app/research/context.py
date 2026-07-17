from __future__ import annotations

from typing import Any

from ..agent_v2.models import SourceRecord
from .models import EvidenceBundle
from .store import ResearchStore, content_hash, stable_id, utc_now


def build_research_state(
    *,
    thread: dict[str, Any],
    project: dict[str, Any] | None,
    context_cards: list[dict[str, Any]],
    canvas: dict[str, Any],
    lab_runs: list[dict[str, Any]],
    long_term_memories: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = canvas.get("nodes") or []
    edges = canvas.get("edges") or []
    return {
        "project": project,
        "thread": thread,
        "long_term_materials": context_cards,
        "canvas": {
            "nodes": nodes,
            "edges": edges,
            "questions": [node for node in nodes if node.get("type") == "question"],
            "hypotheses": [node for node in nodes if node.get("type") == "hypothesis"],
            "decisions": [node for node in nodes if node.get("type") in {"conclusion", "decision"}],
            "tasks": [node for node in nodes if node.get("type") == "task"],
        },
        "lab_runs": lab_runs,
        "long_term_memories": long_term_memories,
        "data_gaps": {
            "missing_thread_goal": not bool(thread.get("goal")),
            "unlinked_canvas_nodes": len(nodes) if nodes and not edges else 0,
            "empty_context": not bool(context_cards),
            "empty_conversation_summary": not bool(thread.get("conversation_summary")),
        },
    }


def evidence_bundle_to_sources(bundle: EvidenceBundle, task_id: str | None) -> list[SourceRecord]:
    claim_by_work: dict[str, list[Any]] = {}
    evidence_by_id = {item.id: item for item in bundle.evidence}
    for claim in bundle.claims:
        claim_by_work.setdefault(claim.subject_id, []).append(claim)
    sources: list[SourceRecord] = []
    for work in bundle.works:
        claims = claim_by_work.get(work.id, [])
        cited_evidence = [evidence_by_id[evidence_id] for claim in claims for evidence_id in claim.evidence_ids if evidence_id in evidence_by_id]
        full_text = next((item for item in cited_evidence if item.evidence_level == "full_text"), None)
        excerpt = full_text.quote if full_text else "\n".join(claim.text for claim in claims[:4])
        if not excerpt:
            excerpt = str(work.resolved_fields.get("summary") or "")
        evidence_level = "full_text" if full_text else "curated_summary"
        identifiers = work.identifiers
        canonical_key = work.canonical_key or f"research-work:{work.id}"
        sources.append(SourceRecord(
            id=stable_id("source", canonical_key), task_id=task_id, source_kind="atlas",
            evidence_level=evidence_level, title=work.title,
            locator={
                "work_id": work.id,
                "paper_id": work.id.removeprefix("work_"),
                "atlas_ids": [item.get("atlas_id") for item in work.atlas_placements],
                "evidence_ids": [item.id for item in cited_evidence],
                "claim_ids": [item.id for item in claims],
                "doi": (identifiers.get("doi") or [""])[0],
                "arxiv_id": (identifiers.get("arxiv") or [""])[0],
            },
            authors=[str(item) for item in (work.resolved_fields.get("authors") or [])], year=work.year,
            abstract=str(work.resolved_fields.get("abstract") or ""), excerpt=excerpt[:2400],
            canonical_key=canonical_key, content_hash=content_hash(excerpt or work.title), retrieved_at=utc_now(),
            confidence=0.94 if full_text else 0.84, access="local", provider="research_store",
        ))
    return sources


def search_for_agent(
    store: ResearchStore,
    *,
    query: str,
    atlas_id: str,
    task_id: str | None,
    attachments: list[dict[str, Any]],
    limit: int = 18,
) -> tuple[EvidenceBundle, list[SourceRecord]]:
    work_ids = []
    for attachment in attachments:
        source_ref = attachment.get("source_ref") if isinstance(attachment.get("source_ref"), dict) else {}
        paper_id = source_ref.get("paper_id") or source_ref.get("work_id")
        if paper_id:
            work_ids.append(str(paper_id) if str(paper_id).startswith("work_") else f"work_{paper_id}")
    bundle = store.search(query, atlas_ids=[atlas_id] if atlas_id else [], work_ids=[] if not work_ids else None, limit=limit)
    if work_ids:
        attached = store.search(" ".join(attachment.get("title", "") for attachment in attachments), work_ids=work_ids, limit=len(work_ids))
        existing = {work.id for work in bundle.works}
        bundle.works = attached.works + [work for work in bundle.works if work.id not in {item.id for item in attached.works}]
        bundle.claims = attached.claims + [claim for claim in bundle.claims if claim.subject_id not in existing]
        bundle.evidence = list({item.id: item for item in attached.evidence + bundle.evidence}.values())
    sources = evidence_bundle_to_sources(bundle, task_id)
    for chunk in store.search_document_chunks(query, limit=8):
        sources.append(SourceRecord(
            id=stable_id("source", "document", chunk["chunk_id"]), task_id=task_id,
            source_kind="local_document", evidence_level="full_text", title=chunk.get("title") or "本地文档",
            locator={"document_id": chunk["document_id"], "chunk_id": chunk["chunk_id"], "work_id": chunk.get("work_id"),
                     "section": chunk.get("section"), "page": chunk.get("page"), **(chunk.get("locator") or {})},
            excerpt=str(chunk.get("text") or "")[:2400], canonical_key=f"document:{chunk['document_id']}",
            content_hash=chunk.get("content_hash") or content_hash(str(chunk.get("text") or "")), retrieved_at=utc_now(),
            confidence=0.92, access="local", provider="research_store_fts",
        ))
    deduped = list({source.id: source for source in sources}.values())
    return bundle, deduped[:limit]
