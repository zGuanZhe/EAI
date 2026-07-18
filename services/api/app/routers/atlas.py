from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from ..schemas.models import (
    AtlasCandidateUpdate,
    AtlasUpdateDoc,
    AtlasUpdateResultPreviewRequest,
    AtlasUpdateResultPreviewResponse,
    AtlasUpdateTaskPackRequest,
    AtlasUpdateTaskPackResponse,
    CardType,
    ObjectMemory,
    PaperChatRequest,
)
from ..services.atlas import AtlasCandidateNotFoundError, AtlasResourceNotFoundError, AtlasService
from ..services.provider import ProviderError


def create_atlas_router(get_service: Callable[[], AtlasService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def service() -> AtlasService:
        return get_service()

    def call(action):
        try:
            return action()
        except AtlasResourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
        except AtlasCandidateNotFoundError as exc:
            raise HTTPException(status_code=404, detail="candidate not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/atlases")
    def list_atlases() -> list[dict[str, Any]]:
        return call(service().list_atlases)

    @router.get("/atlases/{atlas_id}/bundle")
    def get_atlas_bundle(atlas_id: str) -> dict[str, Any]:
        return call(lambda: service().get_bundle(atlas_id))

    @router.get("/object-memory")
    def list_object_memory(atlas_id: str) -> list[ObjectMemory]:
        return call(lambda: service().list_object_memory(atlas_id))

    @router.put("/object-memory/{atlas_id}/{object_type}/{object_id}", response_model=ObjectMemory)
    def update_object_memory(atlas_id: str, object_type: CardType, object_id: str, payload: ObjectMemory) -> ObjectMemory:
        return call(lambda: service().write_object_memory(atlas_id, object_type, object_id, payload))

    @router.post("/object-memory/{atlas_id}/paper/{paper_id}/chat", response_model=ObjectMemory)
    def chat_with_paper(atlas_id: str, paper_id: str, payload: PaperChatRequest) -> ObjectMemory:
        return call(lambda: service().chat_with_paper(atlas_id, paper_id, payload))

    @router.get("/atlas-updates/{atlas_id}", response_model=AtlasUpdateDoc)
    def get_atlas_updates(atlas_id: str) -> AtlasUpdateDoc:
        return call(lambda: service().load_updates(atlas_id))

    @router.post("/atlas-updates/{atlas_id}/task-pack/preview", response_model=AtlasUpdateTaskPackResponse)
    def preview_atlas_update_task_pack(atlas_id: str, payload: AtlasUpdateTaskPackRequest) -> AtlasUpdateTaskPackResponse:
        return call(lambda: service().build_update_task_pack(atlas_id, payload))

    @router.post("/atlas-updates/{atlas_id}/results/preview", response_model=AtlasUpdateResultPreviewResponse)
    def preview_atlas_update_result(atlas_id: str, payload: AtlasUpdateResultPreviewRequest) -> AtlasUpdateResultPreviewResponse:
        return call(lambda: service().preview_update_result(atlas_id, payload))

    @router.put("/atlas-updates/{atlas_id}/candidates/{candidate_id}", response_model=AtlasUpdateDoc)
    def update_atlas_candidate(atlas_id: str, candidate_id: str, payload: AtlasCandidateUpdate) -> AtlasUpdateDoc:
        return call(lambda: service().update_candidate(atlas_id, candidate_id, payload))

    @router.post("/atlas-updates/{atlas_id}/candidates/{candidate_id}/apply", response_model=AtlasUpdateDoc)
    def apply_atlas_candidate(atlas_id: str, candidate_id: str) -> AtlasUpdateDoc:
        return call(lambda: service().apply_candidate(atlas_id, candidate_id))

    @router.post("/atlas-updates/{atlas_id}/candidates/bulk-apply", response_model=AtlasUpdateResultPreviewResponse)
    def bulk_apply_atlas_candidates(atlas_id: str) -> AtlasUpdateResultPreviewResponse:
        return call(lambda: service().bulk_apply_candidates(atlas_id))

    return router
