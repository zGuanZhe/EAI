from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .models import (
    BranchCompareRequest,
    BranchPromoteRequest,
    CampaignCreateRequest,
    IdeaPreviewRequest,
    ManuscriptGenerateRequest,
    ReleaseExportRequest,
    ReviewStartRequest,
    RevisionApplyRequest,
    StageAdvanceRequest,
)
from .service import CampaignService


def create_campaign_router(get_service) -> APIRouter:
    router = APIRouter(prefix="/api/vnext", tags=["campaign"])

    def service() -> CampaignService:
        return get_service()

    def writable_service() -> CampaignService:
        selected = service()
        selected.ensure_writable()
        return selected

    @router.get("/campaigns/runtime")
    def campaign_runtime():
        return service().runtime_status()

    @router.get("/campaign-runtime/status")
    def campaign_runtime_status():
        return service().runtime_status()

    @router.post("/campaign-runtime/install")
    def campaign_runtime_install(profile: str = "cpu"):
        selected = writable_service()
        try:
            return selected.install_runtime(profile)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/campaign-runtime/events")
    def campaign_runtime_events(after_seq: int = 0):
        return StreamingResponse(
            service().runtime_events(after_seq), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/campaign-runtime/cancel")
    def campaign_runtime_cancel():
        return writable_service().cancel_runtime_install()

    @router.get("/threads/{thread_id}/campaigns")
    def list_campaigns(thread_id: str):
        return [item.model_dump(mode="json") for item in service().list_for_thread(thread_id)]

    @router.post("/threads/{thread_id}/campaigns/ideas/preview")
    def preview_ideas(thread_id: str, payload: IdeaPreviewRequest):
        try:
            return service().preview_ideas(
                thread_id, node_id=payload.node_id, objective=payload.objective, count=payload.count
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/threads/{thread_id}/campaigns")
    def create_campaign(thread_id: str, payload: CampaignCreateRequest):
        try:
            return writable_service().create(
                thread_id, payload.idea, payload.source_node_ids, payload.budget, payload.workspace_seed
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str):
        snapshot = service().get(campaign_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return snapshot.model_dump(mode="json")

    @router.get("/campaigns/{campaign_id}/events")
    def campaign_events(campaign_id: str, after_seq: int = 0):
        if not service().get(campaign_id):
            raise HTTPException(status_code=404, detail="Campaign not found")
        return StreamingResponse(
            service().event_stream(campaign_id, after_seq), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def action(campaign_id: str, name: str):
        try:
            return getattr(writable_service(), name)(campaign_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/start")
    def start(campaign_id: str): return action(campaign_id, "start")

    @router.post("/campaigns/{campaign_id}/pause")
    def pause(campaign_id: str): return action(campaign_id, "pause")

    @router.post("/campaigns/{campaign_id}/resume")
    def resume(campaign_id: str): return action(campaign_id, "resume")

    @router.post("/campaigns/{campaign_id}/cancel")
    def cancel(campaign_id: str): return action(campaign_id, "cancel")

    @router.post("/campaigns/{campaign_id}/stages/{stage_id}/advance")
    def advance(campaign_id: str, stage_id: str, payload: StageAdvanceRequest):
        try:
            selected = writable_service()
            snapshot = selected.get(campaign_id)
            if not snapshot or snapshot.campaign.current_stage_id != stage_id:
                raise KeyError("Campaign stage not found")
            return selected.advance(campaign_id, payload.branch_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/branches/{branch_id}")
    def branch(campaign_id: str, branch_id: str):
        snapshot = service().get(campaign_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Campaign not found")
        item = next((value for value in snapshot.branches if value.id == branch_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Campaign branch not found")
        metrics = [value for value in snapshot.metrics if value.branch_id == branch_id]
        artifacts = [value for value in snapshot.artifacts if value.branch_id == branch_id]
        session = next((value for value in snapshot.sessions if value.id == item.session_id), None)
        checkpoint = next((value for value in snapshot.checkpoints if value.id == item.checkpoint_id), None)
        return {
            "branch": item.model_dump(mode="json"),
            "metrics": [value.model_dump(mode="json") for value in metrics],
            "artifacts": [value.model_dump(mode="json") for value in artifacts],
            "session": session.model_dump(mode="json") if session else None,
            "checkpoint": checkpoint.model_dump(mode="json") if checkpoint else None,
        }

    @router.post("/campaigns/{campaign_id}/branches/{branch_id}/prepare-execution")
    def prepare_execution(campaign_id: str, branch_id: str):
        try:
            return writable_service().prepare_execution(campaign_id, branch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/branches/{branch_id}/prepare-session")
    def prepare_session(campaign_id: str, branch_id: str):
        return prepare_execution(campaign_id, branch_id)

    @router.post("/campaigns/{campaign_id}/branches/compare")
    def compare_branches(campaign_id: str, payload: BranchCompareRequest):
        try:
            return service().compare_branches(campaign_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/manuscripts/generate")
    def generate_manuscript(campaign_id: str, payload: ManuscriptGenerateRequest):
        try:
            return writable_service().generate_manuscript(campaign_id, payload).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/reviews/start")
    def start_review(campaign_id: str, payload: ReviewStartRequest):
        try:
            return writable_service().start_review(campaign_id, payload).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/revisions/apply")
    def apply_revision(campaign_id: str, payload: RevisionApplyRequest):
        try:
            return writable_service().apply_revision(campaign_id, payload).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/release/export")
    def export_release(campaign_id: str, payload: ReleaseExportRequest):
        try:
            return writable_service().export_release(campaign_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/campaigns/{campaign_id}/artifacts/{artifact_id}/content")
    def artifact_content(campaign_id: str, artifact_id: str):
        snapshot = service().get(campaign_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Campaign not found")
        artifact = next((item for item in snapshot.artifacts if item.id == artifact_id), None)
        if not artifact or not artifact.path:
            raise HTTPException(status_code=404, detail="Campaign artifact not found")
        try:
            path = service().safe_artifact_path(campaign_id, artifact.path)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=artifact.media_type, filename=path.name)

    @router.post("/campaigns/{campaign_id}/branches/{branch_id}/promote")
    def promote(campaign_id: str, branch_id: str, payload: BranchPromoteRequest):
        try:
            return writable_service().promote(campaign_id, branch_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/campaigns/{campaign_id}/branches/{branch_id}/discard")
    def discard(campaign_id: str, branch_id: str):
        try:
            return writable_service().discard(campaign_id, branch_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
