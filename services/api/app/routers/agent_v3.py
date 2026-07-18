from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas.agent_v3 import AgentSteerRequest, AskTurnRequest, ResearchTaskCreate, SourcePolicy
from ..services.agent_api import AgentApiConflictError, AgentApiNotFoundError, AgentApiValidationError
from ..services.agent_v3 import AgentV3Service


def create_agent_v3_router(get_service: Callable[[], AgentV3Service]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext", tags=["agent-v3"])

    def call(action):
        try:
            result = action()
            return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        except AgentApiNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        except AgentApiConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except AgentApiValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/threads/{thread_id}/agent/asks")
    def ask(thread_id: str, payload: AskTurnRequest):
        return call(lambda: get_service().ask(thread_id, payload))

    @router.post("/threads/{thread_id}/research-tasks")
    def create_research_task(thread_id: str, payload: ResearchTaskCreate):
        return call(lambda: get_service().create_research_task(thread_id, payload))

    @router.get("/threads/{thread_id}/research-tasks")
    def list_research_tasks(thread_id: str):
        return call(lambda: get_service().list_research_tasks(thread_id))

    @router.get("/research-tasks/{task_id}")
    def get_research_task(task_id: str):
        return call(lambda: get_service().research_task_view(task_id))

    @router.get("/research-tasks/{task_id}/events")
    def research_events(task_id: str, after_seq: int = 0):
        return StreamingResponse(
            get_service().event_stream(task_id, max(0, after_seq)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/research-tasks/{task_id}/steer")
    def steer(task_id: str, payload: AgentSteerRequest):
        return call(lambda: get_service().steer(task_id, payload.message))

    @router.post("/research-tasks/{task_id}/pause")
    def pause(task_id: str):
        return call(lambda: get_service().pause(task_id))

    @router.post("/research-tasks/{task_id}/resume")
    def resume(task_id: str):
        return call(lambda: get_service().resume(task_id))

    @router.post("/research-tasks/{task_id}/cancel")
    def cancel(task_id: str):
        return call(lambda: get_service().cancel(task_id))

    @router.post("/research-tasks/{task_id}/promote-campaign")
    def promote_campaign(task_id: str):
        return call(lambda: get_service().promote_campaign(task_id))

    @router.get("/threads/{thread_id}/agent-context/preview")
    def context_preview(
        thread_id: str,
        mode: Literal["ask", "research"] = "ask",
        source_policy: SourcePolicy = "local_and_external",
        surface: str | None = None,
    ):
        return call(lambda: get_service().context_preview(
            thread_id, lane=mode, source_policy=source_policy, surface=surface
        ))

    @router.get("/agent/capabilities")
    def capabilities():
        return get_service().capability_status()

    @router.get("/search-connectors/status")
    def connector_status():
        return get_service().connector_status()

    @router.post("/search-connectors/{connector_id}/check")
    def check_connector(connector_id: str):
        return call(lambda: get_service().check_connector(connector_id))

    @router.get("/agent/tasks/{task_id}/ui-commands")
    def ui_commands(task_id: str):
        return call(lambda: get_service().list_ui_commands(task_id))

    @router.post("/agent/ui-commands/{command_id}/resolve")
    def resolve_ui_command(command_id: str, status: Literal["applied", "dismissed"]):
        return call(lambda: get_service().resolve_ui_command(command_id, status))

    return router
