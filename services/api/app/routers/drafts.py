from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ..schemas.drafts import ThreadDraftDelete, ThreadDraftPut
from ..services.drafts import ThreadDraftService


def create_draft_router(get_service: Callable[[], ThreadDraftService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext", tags=["drafts"])

    @router.get("/threads/{thread_id}/draft")
    def get_draft(thread_id: str):
        return {"draft": get_service().get(thread_id)}

    @router.put("/threads/{thread_id}/draft")
    def put_draft(thread_id: str, payload: ThreadDraftPut):
        try:
            return get_service().put(
                thread_id, expected_revision=payload.expected_revision, text=payload.text,
                agent_mode=payload.agent_mode,
                attachment_refs=[item.model_dump(mode="json") for item in payload.attachment_refs],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/threads/{thread_id}/draft")
    def delete_draft(thread_id: str, payload: ThreadDraftDelete):
        get_service().delete(thread_id, expected_revision=payload.expected_revision)
        return {"deleted": True, "thread_id": thread_id}

    return router
