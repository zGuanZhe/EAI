from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ..schemas.legacy import ChangeSet, ChangeSetConfirmRequest
from ..schemas.models import ChangeSetResponse, ContextInjectionRequest, ProposalConfirmResponse, ThreadDoc
from ..services.change_review import ChangeReviewError, ChangeReviewService


def create_change_review_router(get_service: Callable[[], ChangeReviewService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def call(action):
        try:
            return action()
        except ChangeReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/threads/{thread_id}/changesets/{changeset_id}", response_model=ChangeSet)
    def get_changeset(thread_id: str, changeset_id: str) -> ChangeSet:
        return call(lambda: get_service().get_changeset(thread_id, changeset_id))

    @router.post("/threads/{thread_id}/changesets/{changeset_id}/confirm", response_model=ChangeSetResponse)
    def confirm_changeset(thread_id: str, changeset_id: str, payload: ChangeSetConfirmRequest) -> ChangeSetResponse:
        return call(lambda: get_service().confirm_changeset(thread_id, changeset_id, payload))

    @router.post("/threads/{thread_id}/changesets/{changeset_id}/reject", response_model=ChangeSetResponse)
    def reject_changeset(thread_id: str, changeset_id: str) -> ChangeSetResponse:
        return call(lambda: get_service().reject_changeset(thread_id, changeset_id))

    @router.post("/threads/{thread_id}/changesets/{changeset_id}/undo", response_model=ChangeSetResponse)
    def undo_changeset(thread_id: str, changeset_id: str) -> ChangeSetResponse:
        return call(lambda: get_service().undo_changeset(thread_id, changeset_id))

    @router.post("/threads/{thread_id}/action-proposals/{proposal_id}/confirm", response_model=ProposalConfirmResponse)
    def confirm_action_proposal(thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
        return call(lambda: get_service().confirm_action_proposal(thread_id, proposal_id))

    @router.post("/threads/{thread_id}/action-proposals/{proposal_id}/reject", response_model=ProposalConfirmResponse)
    def reject_action_proposal(thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
        return call(lambda: get_service().reject_action_proposal(thread_id, proposal_id))

    @router.post("/threads/{thread_id}/context-injections", response_model=ThreadDoc)
    def add_context_injection(thread_id: str, payload: ContextInjectionRequest) -> ThreadDoc:
        return call(lambda: get_service().add_context_injection(thread_id, payload))

    return router
