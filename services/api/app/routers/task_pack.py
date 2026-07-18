from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ..schemas.models import (
    ExportRequest, ExportResponse, ResearchTemplate, ResultPreviewRequest, ResultPreviewResponse,
    TaskPackPreviewRequest, TaskPackPreviewResponse, TaskPackRunRequest, TaskPackRunResponse,
)
from ..services.provider import ProviderError
from ..services.task_pack import TaskPackError, TaskPackService
from ..services.thread_content import ThreadContentNotFoundError


def create_task_pack_router(get_service: Callable[[], TaskPackService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def call(action):
        try:
            return action()
        except ThreadContentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TaskPackError, ProviderError) as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/research-templates", response_model=list[ResearchTemplate])
    def list_research_templates() -> list[ResearchTemplate]:
        return get_service().list_templates()

    @router.post("/threads/{thread_id}/task-pack/preview", response_model=TaskPackPreviewResponse)
    def preview_task_pack(thread_id: str, payload: TaskPackPreviewRequest) -> TaskPackPreviewResponse:
        return call(lambda: get_service().preview_task_pack(thread_id, payload))

    @router.post("/threads/{thread_id}/task-pack/run", response_model=TaskPackRunResponse)
    def run_task_pack(thread_id: str, payload: TaskPackRunRequest) -> TaskPackRunResponse:
        return call(lambda: get_service().run_task_pack(thread_id, payload))

    @router.post("/threads/{thread_id}/results/preview", response_model=ResultPreviewResponse)
    def preview_result(thread_id: str, payload: ResultPreviewRequest) -> ResultPreviewResponse:
        return call(lambda: get_service().preview_result(thread_id, payload))

    @router.post("/threads/{thread_id}/export", response_model=ExportResponse)
    def export_thread(thread_id: str, payload: ExportRequest) -> ExportResponse:
        return call(lambda: get_service().export_thread(thread_id, payload))

    return router
