from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ..schemas.models import MessageCreate, MessageUpdate, ResultCard, ThreadDoc, ToolRunCreate
from ..services.thread_content import (
    ThreadContentNotFoundError,
    ThreadContentService,
    ThreadContentValidationError,
)


def create_thread_content_router(get_service: Callable[[], ThreadContentService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def call(action):
        try:
            return action()
        except ThreadContentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ThreadContentValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/threads/{thread_id}/messages", response_model=ThreadDoc)
    def add_message(thread_id: str, payload: MessageCreate) -> ThreadDoc:
        return call(lambda: get_service().add_message(thread_id, payload))

    @router.put("/threads/{thread_id}/messages/{message_id}", response_model=ThreadDoc)
    def update_message(thread_id: str, message_id: str, payload: MessageUpdate) -> ThreadDoc:
        return call(lambda: get_service().update_message(thread_id, message_id, payload))

    @router.post("/threads/{thread_id}/tool-runs", response_model=ThreadDoc)
    def add_tool_run(thread_id: str, payload: ToolRunCreate) -> ThreadDoc:
        return call(lambda: get_service().add_tool_run(thread_id, payload))

    @router.post("/threads/{thread_id}/results", response_model=ThreadDoc)
    def add_result(thread_id: str, payload: ResultCard) -> ThreadDoc:
        return call(lambda: get_service().add_result(thread_id, payload))

    return router
