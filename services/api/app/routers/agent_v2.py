from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..schemas.agent_v2 import (
    AgentSteerRequest,
    AgentTurnRequest,
    AgentTurnResponse,
    ApprovalResolveRequest,
    DocumentRecord,
    SourceSearchRequest,
    SourceSearchResponse,
)
from ..services.agent_api import (
    AgentApiConflictError,
    AgentApiNotFoundError,
    AgentApiService,
    AgentApiValidationError,
)


def create_agent_v2_router(get_service: Callable[[], AgentApiService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def service() -> AgentApiService:
        return get_service()

    def call(action):
        try:
            return action()
        except AgentApiNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        except AgentApiConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except AgentApiValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

    @router.post("/threads/{thread_id}/agent-v2/turns", response_model=AgentTurnResponse)
    def start_agent_v2_turn(thread_id: str, payload: AgentTurnRequest) -> AgentTurnResponse:
        return call(lambda: service().start_turn(thread_id, payload))

    @router.get("/agent-v2/tasks/{task_id}")
    def get_agent_v2_task(task_id: str) -> dict[str, Any]:
        return call(lambda: service().get_task(task_id))

    @router.get("/agent-v2/tasks/{task_id}/audit")
    def get_agent_v2_task_audit(task_id: str, attempt_id: str | None = None) -> dict[str, Any]:
        return call(lambda: service().get_task_audit(task_id, attempt_id))

    @router.post("/agent-v2/tasks/{task_id}/steer")
    def steer_agent_v2_task(task_id: str, payload: AgentSteerRequest) -> dict[str, Any]:
        return call(lambda: service().steer_task(task_id, payload))

    @router.get("/agent-v2/tasks/{task_id}/events")
    def stream_agent_v2_events(task_id: str, after_seq: int = 0) -> StreamingResponse:
        return StreamingResponse(
            service().event_stream(task_id, after_seq),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/agent-v2/tasks/{task_id}/cancel")
    def cancel_agent_v2_task(task_id: str) -> dict[str, Any]:
        return call(lambda: service().cancel_task(task_id))

    @router.post("/agent-v2/tasks/{task_id}/resume")
    def resume_agent_v2_task(task_id: str) -> dict[str, Any]:
        return call(lambda: service().resume_task(task_id))

    @router.post("/agent-v2/approvals/{approval_id}/resolve")
    def resolve_agent_v2_approval(approval_id: str, payload: ApprovalResolveRequest) -> dict[str, Any]:
        return call(lambda: service().resolve_approval(approval_id, payload))

    @router.get("/agent-v2/approvals/{approval_id}")
    def get_agent_v2_approval(approval_id: str) -> dict[str, Any]:
        return call(lambda: service().get_approval(approval_id))

    @router.get("/agent-v2/operation-batches/{batch_id}")
    def get_agent_v2_operation_batch(batch_id: str) -> dict[str, Any]:
        return call(lambda: service().get_operation_batch(batch_id))

    @router.post("/agent-v2/operation-batches/{batch_id}/undo")
    def undo_agent_v2_operation_batch(batch_id: str) -> dict[str, Any]:
        return call(lambda: service().undo_operation_batch(batch_id))

    @router.get("/agent-v2/capabilities")
    def list_agent_v2_capabilities() -> list[dict[str, Any]]:
        return service().list_capabilities()

    @router.post("/sources/search", response_model=SourceSearchResponse)
    def search_agent_v2_sources(payload: SourceSearchRequest) -> SourceSearchResponse:
        return call(lambda: service().search_sources(payload))

    @router.get("/sources/{source_id}")
    def get_agent_v2_source(source_id: str) -> dict[str, Any]:
        return call(lambda: service().get_source(source_id))

    @router.post("/documents/import", response_model=DocumentRecord)
    async def import_agent_v2_document(
        file: UploadFile = File(...),
        title: str | None = Form(default=None),
    ) -> DocumentRecord:
        content = await file.read(100 * 1024 * 1024 + 1)
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文档超过 100 MB 限制")
        return call(lambda: service().import_document(
            file_name=file.filename or "document",
            media_type=file.content_type or "application/octet-stream",
            content=content,
            title=title,
        ))

    @router.post("/documents/import-source/{source_id}", response_model=DocumentRecord)
    def import_agent_v2_open_source(source_id: str) -> DocumentRecord:
        return call(lambda: service().import_open_source(source_id))

    return router
