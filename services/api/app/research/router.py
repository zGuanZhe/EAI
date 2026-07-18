from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from .documents import import_document
from .enrichment import KnowledgeEnrichmentService
from .models import (
    ClaimResolutionRequest,
    GraphNeighborhoodRequest,
    IdentityResolutionRequest,
    KnowledgeSearchRequest,
    SyncRequest,
)
from .store import ResearchStore
from .page_preview import PagePreviewService
from ..services.projection import ProjectionService


def create_research_router(
    get_store: Callable[[], ResearchStore],
    get_enrichment: Callable[[], KnowledgeEnrichmentService],
) -> APIRouter:
    router = APIRouter(prefix="/api/vnext/knowledge", tags=["knowledge"])

    @router.get("/status")
    def status():
        return get_store().status()

    @router.get("/projection/status")
    def projection_status():
        store = get_store()
        counts = store.projection_status()
        return {"counts": counts, "requires_rebuild": bool(counts.get("pending", 0) or counts.get("failed", 0))}

    @router.post("/projection/replay")
    def replay_projections(limit: int = Query(default=100, ge=1, le=1000)):
        store = get_store()
        store.ensure_writable()
        result = ProjectionService(store, store.personal_dir).replay(limit=limit)
        return {"result": result, "counts": store.projection_status()}

    @router.get("/quality")
    def quality():
        store = get_store()
        status_value = store.status()
        return {"status": status_value, "integrity": store.integrity_check(), "gaps": {
            "missing_metadata": status_value.counts.get("works", 0) - round(status_value.coverage.get("metadata", 0) * status_value.counts.get("works", 0)),
            "missing_full_text": status_value.counts.get("works", 0) - round(status_value.coverage.get("full_text", 0) * status_value.counts.get("works", 0)),
            "unverified_relations": status_value.counts.get("relations", 0) - round(status_value.coverage.get("verified_relations", 0) * status_value.counts.get("relations", 0)),
        }}

    @router.post("/search")
    def search(payload: KnowledgeSearchRequest):
        return get_store().search(
            payload.query, atlas_ids=payload.atlas_ids, work_ids=payload.work_ids,
            include_graph=payload.include_graph, include_claims=payload.include_claims, limit=payload.limit,
        )

    @router.get("/works/{work_id}")
    def get_work(work_id: str):
        work = get_store().get_work(work_id)
        if not work:
            raise HTTPException(status_code=404, detail="research work not found")
        return work

    @router.get("/works/{work_id}/evidence")
    def get_work_evidence(work_id: str, verified_only: bool = True):
        store = get_store()
        work = store.get_work(work_id)
        if not work:
            raise HTTPException(status_code=404, detail="research work not found")
        claims, evidence = store.claims_for_work(work.id, verified_only=verified_only)
        return {"work": work, "claims": claims, "evidence": evidence}

    @router.get("/evidence/{evidence_id}/locator")
    def evidence_locator(evidence_id: str):
        try:
            return PagePreviewService(get_store()).locator(evidence_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/documents/{document_id}/pages/{page_number}/image")
    def document_page_image(document_id: str, page_number: int, dpi: int = Query(default=144, ge=72, le=180)):
        try:
            payload, etag = PagePreviewService(get_store()).render(document_id, page_number, dpi)
            return Response(
                payload, media_type="image/png",
                headers={"ETag": etag, "Cache-Control": "private, max-age=86400"},
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/graph/neighborhood")
    def graph_neighborhood(payload: GraphNeighborhoodRequest):
        return get_store().graph_neighborhood(payload.entity_id, depth=payload.depth, predicates=payload.predicates, limit=payload.limit)

    @router.post("/sync")
    def start_sync(payload: SyncRequest):
        return get_enrichment().start(payload)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = get_store().get_sync_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="knowledge sync job not found")
        return {"job": job, "events": get_store().sync_events(job_id)}

    @router.get("/jobs/{job_id}/events")
    def job_events(job_id: str, after_seq: int = Query(default=0, ge=0)):
        if not get_store().get_sync_job(job_id):
            raise HTTPException(status_code=404, detail="knowledge sync job not found")

        def stream() -> Iterator[str]:
            seq = after_seq
            while True:
                events = get_store().sync_events(job_id, seq)
                for event in events:
                    seq = event["seq"]
                    yield f"event: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                job = get_store().get_sync_job(job_id)
                if not job or job.status in {"done", "failed", "cancelled"}:
                    break
                if not events:
                    yield ": heartbeat\n\n"
                time.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/jobs/{job_id}/pause")
    def pause_job(job_id: str):
        try:
            return get_enrichment().pause(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge sync job not found") from exc

    @router.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str):
        try:
            return get_enrichment().resume(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge sync job not found") from exc

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        try:
            return get_enrichment().cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="knowledge sync job not found") from exc

    @router.post("/claims/{claim_id}/promote")
    def promote_claim(claim_id: str, payload: ClaimResolutionRequest):
        claim = get_store().resolve_claim(claim_id, "verified", note=payload.note)
        if not claim:
            raise HTTPException(status_code=404, detail="claim not found")
        return claim

    @router.post("/claims/{claim_id}/reject")
    def reject_claim(claim_id: str, payload: ClaimResolutionRequest):
        claim = get_store().resolve_claim(claim_id, "rejected", note=payload.note)
        if not claim:
            raise HTTPException(status_code=404, detail="claim not found")
        return claim

    @router.post("/identity-candidates/{candidate_id}/resolve")
    def resolve_identity(candidate_id: str, payload: IdentityResolutionRequest):
        value = get_store().resolve_identity_candidate(candidate_id, payload.decision, payload.target_work_id)
        if not value:
            raise HTTPException(status_code=404, detail="identity candidate not found")
        return value

    @router.post("/documents/import")
    async def upload_document(
        file: UploadFile = File(...), title: str | None = Form(default=None), work_id: str | None = Form(default=None)
    ):
        content = await file.read(100 * 1024 * 1024 + 1)
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文档超过 100 MB 限制")
        try:
            return import_document(
                get_store(), file_name=file.filename or "document", media_type=file.content_type or "application/octet-stream",
                content=content, title=title, work_id=work_id, source_kind="user", access="local", evictable=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
