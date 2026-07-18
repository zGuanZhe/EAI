from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas.legacy import AgentRun
from ..schemas.models import (
    AgentRunCompleteRequest,
    AgentRunResponse,
    AgentRunStartResponse,
    ThreadChatCompleteRequest,
    ThreadChatRequest,
    ThreadChatResponse,
    ThreadChatRetryRequest,
    ThreadChatStartResponse,
)
from ..services.legacy_read import LegacyReadService, LegacyRecordNotFoundError


CHAT_GONE = "旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口"
CHAT_RETRY_GONE = "旧 chat 重试接口已停用，请使用 Agent Runtime v2 resume 接口"
AGENT_GONE = "Agent v1 已转为只读历史，请使用 Agent Runtime v2"
AGENT_RETRY_GONE = "Agent v1 已转为只读历史，请使用 Agent Runtime v2 resume 接口"
AGENT_CANCEL_GONE = "Agent v1 已转为只读历史"


def create_legacy_router(get_service: Callable[[], LegacyReadService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    @router.post("/threads/{thread_id}/chat/start", response_model=ThreadChatStartResponse)
    def start_thread_chat(thread_id: str, payload: ThreadChatRequest) -> ThreadChatStartResponse:
        raise HTTPException(status_code=410, detail=CHAT_GONE)

    @router.post("/threads/{thread_id}/chat/complete", response_model=ThreadChatResponse)
    def complete_thread_chat(thread_id: str, payload: ThreadChatCompleteRequest) -> ThreadChatResponse:
        raise HTTPException(status_code=410, detail=CHAT_GONE)

    @router.post("/threads/{thread_id}/chat/{assistant_message_id}/retry", response_model=ThreadChatResponse)
    def retry_thread_chat(
        thread_id: str,
        assistant_message_id: str,
        payload: ThreadChatRetryRequest | None = None,
    ) -> ThreadChatResponse:
        raise HTTPException(status_code=410, detail=CHAT_RETRY_GONE)

    @router.post("/threads/{thread_id}/agent-runs/start", response_model=AgentRunStartResponse)
    def start_agent_run(thread_id: str, payload: ThreadChatRequest) -> AgentRunStartResponse:
        raise HTTPException(status_code=410, detail=AGENT_GONE)

    @router.post("/threads/{thread_id}/agent-runs/{run_id}/complete", response_model=AgentRunResponse)
    def complete_agent_run_endpoint(
        thread_id: str,
        run_id: str,
        payload: AgentRunCompleteRequest | None = None,
    ) -> AgentRunResponse:
        raise HTTPException(status_code=410, detail=AGENT_GONE)

    @router.post("/threads/{thread_id}/agent-runs/{run_id}/stream")
    def stream_agent_run_endpoint(
        thread_id: str,
        run_id: str,
        payload: AgentRunCompleteRequest | None = None,
    ) -> StreamingResponse:
        raise HTTPException(status_code=410, detail=AGENT_GONE)

    @router.post("/threads/{thread_id}/agent-runs/{run_id}/retry", response_model=AgentRunResponse)
    def retry_agent_run_endpoint(
        thread_id: str,
        run_id: str,
        payload: AgentRunCompleteRequest | None = None,
    ) -> AgentRunResponse:
        raise HTTPException(status_code=410, detail=AGENT_RETRY_GONE)

    @router.get("/threads/{thread_id}/agent-runs/{run_id}/events")
    def replay_agent_run_events(thread_id: str, run_id: str, after_seq: int = 0) -> StreamingResponse:
        try:
            events = get_service().replay_agent_run_events(thread_id, run_id, after_seq)
        except LegacyRecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/threads/{thread_id}/agent-runs/{run_id}/cancel", response_model=AgentRunResponse)
    def cancel_agent_run(thread_id: str, run_id: str) -> AgentRunResponse:
        raise HTTPException(status_code=410, detail=AGENT_CANCEL_GONE)

    @router.get("/threads/{thread_id}/agent-runs/{run_id}", response_model=AgentRun)
    def get_agent_run(thread_id: str, run_id: str) -> AgentRun:
        try:
            return get_service().get_agent_run(thread_id, run_id)
        except LegacyRecordNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/threads/{thread_id}/chat", response_model=ThreadChatResponse)
    def chat_with_thread(thread_id: str, payload: ThreadChatRequest) -> ThreadChatResponse:
        raise HTTPException(status_code=410, detail=CHAT_GONE)

    return router
