from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from ..legacy.models import AgentRun
from ..schemas.models import Message, ThreadDoc


class LegacyRecordNotFoundError(KeyError):
    pass


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class LegacyReadService:
    def __init__(self, load_thread: Callable[[str], ThreadDoc]) -> None:
        self._load_thread = load_thread

    @staticmethod
    def _find_run(thread: ThreadDoc, run_id: str) -> AgentRun:
        for run in thread.agent_runs:
            if run.id == run_id:
                return run
        raise LegacyRecordNotFoundError("agent run not found")

    @staticmethod
    def _find_message(thread: ThreadDoc, message_id: str) -> Message:
        for message in thread.messages:
            if message.id == message_id:
                return message
        raise LegacyRecordNotFoundError("message not found")

    def get_agent_run(self, thread_id: str, run_id: str) -> AgentRun:
        return self._find_run(self._load_thread(thread_id), run_id)

    def replay_agent_run_events(self, thread_id: str, run_id: str, after_seq: int = 0) -> Iterator[str]:
        thread = self._load_thread(thread_id)
        run = self._find_run(thread, run_id)
        for step in run.steps:
            yield sse_event("step", {**step.model_dump(mode="json"), "seq": after_seq + 1})
        for call in run.tool_calls:
            yield sse_event("tool_call", {**call.model_dump(mode="json"), "seq": after_seq + 2})
        if run.status in {"done", "waiting_confirmation", "failed", "cancelled", "interrupted"}:
            assistant = self._find_message(thread, run.assistant_message_id)
            yield sse_event(
                "done",
                {
                    "thread": thread.model_dump(mode="json"),
                    "run": run.model_dump(mode="json"),
                    "assistant_message": assistant.model_dump(mode="json"),
                    "degraded": bool((assistant.refs or {}).get("degraded")),
                    "seq": max(run.event_seq, after_seq + 3),
                },
            )
