from __future__ import annotations

from typing import Any

from ..research.store import ResearchStore


class ThreadDraftService:
    def __init__(self, store: ResearchStore):
        self.store = store

    def get(self, thread_id: str) -> dict[str, Any] | None:
        return self.store.get_thread_draft(thread_id)

    def put(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        text: str,
        agent_mode: str,
        attachment_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        if self.store.get_record("thread", thread_id) is None:
            raise KeyError("thread not found")
        return self.store.put_thread_draft(
            thread_id,
            expected_revision=expected_revision,
            text=text,
            agent_mode=agent_mode,
            attachment_refs=attachment_refs,
        )

    def delete(self, thread_id: str, *, expected_revision: int) -> None:
        self.store.delete_thread_draft(thread_id, expected_revision=expected_revision)
