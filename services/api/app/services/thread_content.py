from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ..schemas.models import (
    Message,
    MessageCreate,
    MessageUpdate,
    ResultCard,
    ThreadDoc,
    ToolRun,
    ToolRunCreate,
)
from .workspace import WorkspaceService


class ThreadContentNotFoundError(KeyError):
    pass


class ThreadContentValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def safe_text(value: Any, limit: int = 240) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def scrub_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key)[:80]: scrub_refs(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in ("key", "secret", "token", "password"))
        }
    if isinstance(value, list):
        return [scrub_refs(item) for item in value[:20]]
    if isinstance(value, str):
        return safe_text(value, 240)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:120]


def safe_message(value: str | None, limit: int = 1200) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise ThreadContentValidationError("message content is empty")
    return text[:limit]


class ThreadContentService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def _load(self, thread_id: str) -> ThreadDoc:
        try:
            return self.workspace.load_thread(thread_id)
        except KeyError as exc:
            raise ThreadContentNotFoundError("thread not found") from exc

    @staticmethod
    def _append_message(thread: ThreadDoc, message: Message) -> Message:
        item = message.model_copy()
        item.id = item.id or new_id("msg")
        item.created_at = item.created_at or utc_now()
        item.content = safe_message(item.content, 3000)
        item.refs = scrub_refs(item.refs) if isinstance(item.refs, dict) else {}
        thread.messages.append(item)
        return item

    def add_message(self, thread_id: str, payload: MessageCreate) -> ThreadDoc:
        thread = self._load(thread_id)
        self._append_message(thread, Message(
            id=new_id("msg"), role=payload.role, kind=payload.kind, content=payload.content,
            created_at=utc_now(), status=safe_text(payload.status, 32) or "done", surface=payload.surface,
            refs=payload.refs, linked_result_id=safe_text(payload.linked_result_id, 120) or None,
            linked_tool_run_id=safe_text(payload.linked_tool_run_id, 120) or None,
        ))
        return self.workspace.write_thread(thread)

    def update_message(self, thread_id: str, message_id: str, payload: MessageUpdate) -> ThreadDoc:
        thread = self._load(thread_id)
        for message in thread.messages:
            if message.id != message_id:
                continue
            if payload.content is not None:
                message.content = safe_message(payload.content)
            if payload.status is not None:
                message.status = safe_text(payload.status, 32) or message.status
            if payload.refs is not None:
                message.refs = scrub_refs(payload.refs)
            if payload.linked_result_id is not None:
                message.linked_result_id = safe_text(payload.linked_result_id, 120) or None
            if payload.linked_tool_run_id is not None:
                message.linked_tool_run_id = safe_text(payload.linked_tool_run_id, 120) or None
            return self.workspace.write_thread(thread)
        raise ThreadContentNotFoundError("message not found")

    def add_tool_run(self, thread_id: str, payload: ToolRunCreate) -> ThreadDoc:
        thread = self._load(thread_id)
        run = ToolRun(
            id=new_id("tool"), tool=safe_text(payload.tool, 80) or "tool_run",
            status=safe_text(payload.status, 32) or "done", summary=safe_text(payload.summary),
            created_at=utc_now(), template_id=safe_text(payload.template_id, 80) or None,
            mode=payload.mode, provider=safe_text(payload.provider, 80) or None,
            model=safe_text(payload.model, 120) or None, token_estimate=payload.token_estimate,
            input_summary=safe_text(payload.input_summary),
        )
        thread.tool_runs.insert(0, run)
        self._append_message(thread, Message(
            role="tool", kind="tool_run", content=f"已记录工具运行：{run.summary or run.tool}",
            status=run.status, surface="tools",
            refs={"template_id": run.template_id, "mode": run.mode, "token_estimate": run.token_estimate},
            linked_tool_run_id=run.id,
        ))
        return self.workspace.write_thread(thread)

    def add_result(self, thread_id: str, payload: ResultCard) -> ThreadDoc:
        thread = self._load(thread_id)
        result = payload.model_copy()
        result.id = result.id or new_id("result")
        result.created_at = result.created_at or utc_now()
        thread.result_cards.insert(0, result)
        tool_run = ToolRun(
            id=new_id("tool"), tool="paste_codex_result", status="done",
            summary=result.title, created_at=utc_now(),
        )
        thread.tool_runs.insert(0, tool_run)
        parsed = result.parsed_json or {}
        self._append_message(thread, Message(
            role="assistant", kind="result", content=f"已确认写入 Codex 返回：{result.title}",
            status="done", surface="thread",
            refs={
                "findings": len(parsed.get("findings") or []) if isinstance(parsed, dict) else 0,
                "next_tasks": len(parsed.get("next_tasks") or []) if isinstance(parsed, dict) else 0,
            },
            linked_result_id=result.id, linked_tool_run_id=tool_run.id,
        ))
        return self.workspace.write_thread(thread)
