from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agent_v2.capabilities import capability_specs
from ..agent_v2.documents import import_open_source as import_open_runtime_source
from ..agent_v2.models import (
    AgentSteerRequest,
    AgentTurnRequest,
    AgentTurnResponse,
    ApprovalResolveRequest,
    DocumentRecord,
    OperationBatch,
    SourceSearchRequest,
    SourceSearchResponse,
)
from ..agent_v2.runtime import AgentRuntimeV2
from ..research.context import search_for_agent
from ..research.documents import import_document as import_research_document
from ..research.store import ResearchStore
from ..schemas.models import Message
from .workspace import WorkspaceService


class AgentApiError(Exception):
    def __init__(self, detail: str | dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


class AgentApiNotFoundError(AgentApiError):
    pass


class AgentApiConflictError(AgentApiError):
    pass


class AgentApiValidationError(AgentApiError):
    pass


class AgentApiService:
    def __init__(
        self,
        runtime: AgentRuntimeV2,
        workspace: WorkspaceService,
        research_store: ResearchStore,
        *,
        get_campaign_service: Callable[[], Any],
        undo_operation_batch: Callable[[OperationBatch], dict[str, Any]],
        thread_lock: Any,
    ) -> None:
        self.runtime = runtime
        self.workspace = workspace
        self.research_store = research_store
        self.get_campaign_service = get_campaign_service
        self.undo_operation_batch_callback = undo_operation_batch
        self.thread_lock = thread_lock

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _message(value: str | None, limit: int) -> str:
        if not value:
            raise AgentApiValidationError("message content is empty")
        text = str(value).replace("\x00", "").strip()
        if not text:
            raise AgentApiValidationError("message content is empty")
        return text[:limit]

    @classmethod
    def _scrub_refs(cls, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                lowered = str(key).lower()
                if any(part in lowered for part in ("key", "secret", "token", "password")):
                    continue
                cleaned[str(key)[:80]] = cls._scrub_refs(item)
            return cleaned
        if isinstance(value, list):
            return [cls._scrub_refs(item) for item in value[:20]]
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip()[:240]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:120]

    def _load_thread(self, thread_id: str):
        try:
            return self.workspace.load_thread(thread_id)
        except KeyError as exc:
            raise AgentApiNotFoundError("thread not found") from exc
        except ValueError as exc:
            raise AgentApiValidationError(str(exc)) from exc

    def _append_message(self, thread, message: Message) -> Message:
        item = message.model_copy()
        item.id = item.id or self._new_id("msg")
        item.created_at = item.created_at or self._now()
        item.content = self._message(item.content, 3000)
        if item.refs:
            item.refs = self._scrub_refs(item.refs) if isinstance(item.refs, dict) else {}
        thread.messages.append(item)
        return item

    def start_turn(self, thread_id: str, payload: AgentTurnRequest) -> AgentTurnResponse:
        message = self._message(payload.message, 8000)
        with self.thread_lock:
            active = next(
                (
                    task
                    for task in self.runtime.store.list_tasks(thread_id=thread_id, statuses={"pending", "running"})
                    if task.parent_task_id is None
                ),
                None,
            )
            if active:
                raise AgentApiConflictError({
                    "message": "当前线程已有进行中的 Main Agent 任务",
                    "task_id": active.id,
                    "status": active.status,
                })
            thread = self._load_thread(thread_id)
            task_id = self._new_id("agent_task")
            user = self._append_message(thread, Message(
                role="user",
                kind="text",
                content=message,
                surface=payload.surface or "thread",
                refs={
                    "turn_attachments": [self._scrub_refs(item) for item in payload.turn_attachments[:8]],
                    "agent_runtime": "v2",
                    "agent_v2_task_id": task_id,
                },
            ))
            assistant = self._append_message(thread, Message(
                role="assistant",
                kind="assistant_reply",
                content="正在理解你的目标...",
                status="pending",
                surface="thread",
                refs={"agent_runtime": "v2", "agent_v2_task_id": task_id, "service_status": "intake"},
            ))
            thread.active_surface = "thread"
            updated = self.workspace.write_thread(thread)
        task = self.runtime.create_task(
            thread_id=thread_id,
            turn_id=user.id or "",
            assistant_message_id=assistant.id or "",
            request=AgentTurnRequest.model_validate({**payload.model_dump(mode="json"), "message": message}),
            task_id=task_id,
        )
        return AgentTurnResponse(
            thread=updated.model_dump(mode="json"),
            task=task,
            user_message_id=user.id or "",
            assistant_message_id=assistant.id or "",
        )

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.runtime.store.get_task(task_id)
        if not task:
            raise AgentApiNotFoundError("Agent v2 task not found")
        attempts = self.runtime.store.list_attempts(task_id)
        active_attempt = self.runtime.store.get_attempt(task.active_attempt_id) if task.active_attempt_id else None
        return {
            "task": task.model_dump(mode="json"),
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "tool_calls": [item.model_dump(mode="json") for item in self.runtime.store.list_tool_calls(task.active_attempt_id)] if task.active_attempt_id else [],
            "observations": [item.model_dump(mode="json") for item in self.runtime.store.list_observations(task.active_attempt_id)] if task.active_attempt_id else [],
            "active_attempt": active_attempt.model_dump(mode="json") if active_attempt else None,
            "sources": [source.model_dump(mode="json") for source in self.runtime.store.list_sources(task_id)],
            "artifacts": [artifact.model_dump(mode="json") for artifact_id in task.artifact_ids if (artifact := self.runtime.store.get_artifact(artifact_id))],
            "approvals": [approval.model_dump(mode="json") for approval_id in task.approval_ids if (approval := self.runtime.store.get_approval(approval_id))],
            "operation_batches": [batch.model_dump(mode="json") for batch in self.runtime.store.list_operation_batches(task_id)],
        }

    def get_task_audit(self, task_id: str, attempt_id: str | None = None) -> dict[str, Any]:
        task = self.runtime.store.get_task(task_id)
        if not task:
            raise AgentApiNotFoundError("Agent v2 task not found")
        selected = attempt_id or task.active_attempt_id
        attempt = self.runtime.store.get_attempt(selected) if selected else None
        if not attempt or attempt.task_id != task_id:
            raise AgentApiNotFoundError("Agent attempt not found")
        return {
            "task_id": task_id,
            "attempt": attempt.model_dump(mode="json"),
            "tool_calls": [item.model_dump(mode="json") for item in self.runtime.store.list_tool_calls(selected)],
            "observations": [item.model_dump(mode="json") for item in self.runtime.store.list_observations(selected)],
            "events": [item.model_dump(mode="json") for item in self.runtime.store.list_events(task_id)],
        }

    def steer_task(self, task_id: str, payload: AgentSteerRequest) -> dict[str, Any]:
        message = self._message(payload.message, 2000)
        try:
            task = self.runtime.steer(task_id, message)
        except KeyError as exc:
            raise AgentApiNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise AgentApiConflictError(str(exc)) from exc
        with self.thread_lock:
            thread = self._load_thread(task.thread_id)
            self._append_message(thread, Message(
                role="user",
                kind="text",
                content=message,
                surface="thread",
                refs={
                    "agent_runtime": "v2",
                    "steers_task_id": task.id,
                    "agent_v2_attempt_id": task.active_attempt_id,
                },
            ))
            updated = self.workspace.write_thread(thread)
        return {"task": task.model_dump(mode="json"), "thread": updated.model_dump(mode="json")}

    def event_stream(self, task_id: str, after_seq: int) -> Iterator[str]:
        return self.runtime.event_stream(task_id, max(0, after_seq))

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        try:
            task = self.runtime.cancel(task_id)
        except KeyError as exc:
            raise AgentApiNotFoundError(str(exc)) from exc
        return {"task": task.model_dump(mode="json")}

    def resume_task(self, task_id: str) -> dict[str, Any]:
        try:
            task = self.runtime.resume(task_id)
        except KeyError as exc:
            raise AgentApiNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise AgentApiValidationError(str(exc)) from exc
        return {"task": task.model_dump(mode="json")}

    def resolve_approval(self, approval_id: str, payload: ApprovalResolveRequest) -> dict[str, Any]:
        existing = self.runtime.store.get_approval(approval_id)
        if existing and existing.task_id.startswith("campaign:"):
            try:
                return self.get_campaign_service().resolve_approval(approval_id, payload)
            except KeyError as exc:
                raise AgentApiNotFoundError(str(exc)) from exc
            except ValueError as exc:
                raise AgentApiConflictError(str(exc)) from exc
        try:
            approval, task = self.runtime.resolve_approval(approval_id, payload)
        except KeyError as exc:
            raise AgentApiNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise AgentApiValidationError(str(exc)) from exc
        return {"approval": approval.model_dump(mode="json"), "task": task.model_dump(mode="json")}

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        approval = self.runtime.store.get_approval(approval_id)
        if not approval:
            raise AgentApiNotFoundError("approval not found")
        return approval.model_dump(mode="json")

    def get_operation_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.runtime.store.get_operation_batch(batch_id)
        if not batch:
            raise AgentApiNotFoundError("Agent v2 operation batch not found")
        return {"operation_batch": batch.model_dump(mode="json")}

    def undo_operation_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.runtime.store.get_operation_batch(batch_id)
        if not batch:
            raise AgentApiNotFoundError("Agent v2 operation batch not found")
        return self.undo_operation_batch_callback(batch)

    @staticmethod
    def list_capabilities() -> list[dict[str, Any]]:
        return [spec.model_dump(mode="json") for spec in capability_specs()]

    def search_sources(self, payload: SourceSearchRequest) -> SourceSearchResponse:
        atlas_id = "G"
        if payload.thread_id:
            atlas_id = self._load_thread(payload.thread_id).active_atlas_id
        query = self._message(payload.query, 1200)
        local_sources = []
        if payload.source_policy == "atlas_only":
            local_sources = self.runtime.sources.search_atlas(atlas_id, query, None, payload.limit)
        elif payload.source_policy in {"local_only", "local_and_external"}:
            _, local_sources = search_for_agent(
                self.research_store,
                query=query,
                atlas_id=atlas_id,
                task_id=None,
                attachments=[],
                limit=payload.limit,
            )
        warnings: list[str] = []
        sources = local_sources
        if payload.source_policy in {"external_only", "local_and_external"}:
            external, warnings = self.runtime.sources.search(
                query=query,
                atlas_id=atlas_id,
                task_id=None,
                source_policy="external_only",
                providers=payload.providers or None,
                limit=payload.limit,
            )
            sources = self.runtime.sources.deduplicate(local_sources + external)[:payload.limit]
        return SourceSearchResponse(query=payload.query, sources=sources, warnings=warnings)

    def get_source(self, source_id: str) -> dict[str, Any]:
        source = self.runtime.store.get_source(source_id)
        if not source:
            raise AgentApiNotFoundError("source not found")
        return source.model_dump(mode="json")

    def import_document(
        self,
        *,
        file_name: str,
        media_type: str,
        content: bytes,
        title: str | None,
    ) -> DocumentRecord:
        try:
            document = import_research_document(
                self.research_store,
                file_name=file_name,
                media_type=media_type,
                content=content,
                title=title,
            )
        except ValueError as exc:
            raise AgentApiValidationError(str(exc)) from exc
        return DocumentRecord(
            id=document["id"],
            title=document["title"],
            file_name=document["data"].get("file_name") or file_name,
            media_type=document["media_type"],
            path=document["blob_path"],
            content_hash=document["content_hash"],
            page_count=document["page_count"],
            chunk_count=document["data"].get("chunk_count") or 0,
            created_at=document["created_at"],
        )

    def import_open_source(self, source_id: str) -> DocumentRecord:
        source = self.runtime.store.get_source(source_id)
        if not source:
            raise AgentApiNotFoundError("source not found")
        try:
            imported = import_open_runtime_source(
                self.runtime.store,
                source,
                created_at=self._now(),
                client_factory=self.runtime.sources.client_factory,
            )
            source_ref = source.locator if isinstance(source.locator, dict) else {}
            document = import_research_document(
                self.research_store,
                file_name=imported.file_name,
                media_type=imported.media_type,
                content=Path(imported.path).read_bytes(),
                title=imported.title,
                work_id=source_ref.get("work_id"),
                source_kind="open_access",
                access="open",
                evictable=True,
            )
        except ValueError as exc:
            raise AgentApiValidationError(str(exc)) from exc
        return DocumentRecord(
            id=document["id"],
            title=document["title"],
            file_name=document["data"].get("file_name") or imported.file_name,
            media_type=document["media_type"],
            path=document["blob_path"],
            content_hash=document["content_hash"],
            page_count=document["page_count"],
            chunk_count=document["data"].get("chunk_count") or 0,
            created_at=document["created_at"],
        )
