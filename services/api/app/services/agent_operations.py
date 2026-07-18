from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..agent_v2.models import ApprovalResolveRequest, OperationBatch
from ..research.store import ResearchStore
from ..schemas.models import (
    AtlasCandidateUpdate,
    AtlasUpdateCandidate,
    AtlasUpdateDoc,
    CanvasEdge,
    CanvasNode,
    CanvasState,
    ContextCard,
    Message,
    ObjectMemory,
    ProjectDoc,
    ThreadDoc,
)
from .agent_domain import safe_markdown, safe_text
from .atlas import AtlasService
from .projection import ProjectionService
from .thread_content import new_id, scrub_refs, utc_now
from .workspace import WorkspaceService


class AgentOperationError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def set_model_field(model: BaseModel, field: str, value: Any) -> None:
    if field not in model.__class__.model_fields:
        raise AgentOperationError(400, f"unsupported field: {field}")
    validated = model.__class__.model_validate({**model.model_dump(mode="json"), field: value})
    setattr(model, field, getattr(validated, field))


class AgentOperationService:
    def __init__(
        self,
        store: ResearchStore,
        workspace: WorkspaceService,
        atlas: AtlasService,
        *,
        get_runtime: Callable[[], Any],
        thread_lock: threading.RLock,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.atlas = atlas
        self.get_runtime = get_runtime
        self.thread_lock = thread_lock
        self.projector = ProjectionService(store, store.personal_dir)

    @staticmethod
    def _append_message(thread: ThreadDoc, message: Message) -> None:
        message.id = message.id or new_id("msg")
        message.created_at = message.created_at or utc_now()
        message.refs = scrub_refs(message.refs)
        thread.messages.append(message)

    @staticmethod
    def _find_message(thread: ThreadDoc, message_id: str) -> Message:
        message = next((item for item in thread.messages if item.id == message_id), None)
        if not message:
            raise AgentOperationError(404, "message not found")
        return message

    def current_value(self, thread: ThreadDoc, operation: Any) -> Any:
        arguments = operation.arguments or {}
        capability = operation.capability
        if capability == "context.add":
            card = next((item for item in thread.context_cards if item.id == arguments.get("id")), None)
            return card.model_dump(mode="json") if card else None
        if capability == "context.remove":
            card = next((item for item in thread.context_cards if item.id == arguments.get("card_id")), None)
            return card.model_dump(mode="json") if card else None
        if capability == "thread.update":
            fields = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
            return {key: getattr(thread, key) for key in fields if key in {"title", "goal", "status", "active_atlas_id", "project_id"}}
        if capability == "project.update":
            project = self.workspace.load_project(safe_text(arguments.get("project_id"), 160))
            fields = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
            return {key: getattr(project, key) for key in fields if key in {"title", "goal", "status", "default_atlas_id"}}
        if capability == "memory.update":
            atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
            object_type = safe_text(arguments.get("object_type") or "paper", 40)
            object_id = safe_text(arguments.get("object_id"), 180)
            memory = self.atlas.effective_object_memory(atlas_id, object_type, object_id)
            return memory.model_dump(mode="json") if memory else None
        if capability == "memory.promote":
            draft = self.get_runtime().store.get_memory_draft(safe_text(arguments.get("draft_id"), 180))
            return draft.model_dump(mode="json") if draft else None
        if capability == "canvas.apply":
            return thread.canvas.model_dump(mode="json")
        if capability == "atlas_candidate.upsert":
            atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
            candidate = next(
                (item for item in self.atlas.load_updates(atlas_id).candidates if item.id == safe_text(arguments.get("candidate_id"), 160)),
                None,
            )
            return candidate.model_dump(mode="json") if candidate else None
        return None

    def prepare(self, batch: OperationBatch) -> OperationBatch:
        thread = self.workspace.load_thread(batch.thread_id)
        batch.base_revision = thread.revision
        for operation in batch.operations:
            if operation.capability == "context.add" and not operation.arguments.get("id"):
                operation.arguments["id"] = new_id("card")
            operation.before = self.current_value(thread, operation)
            operation.after = scrub_refs(operation.arguments)
        batch.updated_at = utc_now()
        return batch

    @staticmethod
    def _apply_canvas(canvas: CanvasState, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        applied = []
        for raw in (arguments.get("operations") or [])[:40]:
            if not isinstance(raw, dict):
                continue
            action = safe_text(raw.get("action"), 40)
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            if action == "add_node":
                node = CanvasNode.model_validate({"id": data.get("id") or new_id("node"), **data})
                canvas.nodes.append(node); applied.append({"action": action, "id": node.id})
            elif action == "update_node":
                node = next((item for item in canvas.nodes if item.id == data.get("id")), None)
                if not node: raise AgentOperationError(409, f"Canvas node missing: {data.get('id')}")
                for field in ("type", "title", "body", "status", "priority", "card_id", "entity_id", "campaign_id", "source_refs", "verification_status"):
                    if field in data: set_model_field(node, field, data[field])
                applied.append({"action": action, "id": node.id})
            elif action == "remove_node":
                node_id = safe_text(data.get("id"), 160)
                canvas.nodes = [item for item in canvas.nodes if item.id != node_id]
                canvas.edges = [item for item in canvas.edges if item.source != node_id and item.target != node_id]
                applied.append({"action": action, "id": node_id})
            elif action == "add_edge":
                edge = CanvasEdge.model_validate({"id": data.get("id") or new_id("edge"), **data})
                if edge.source not in {item.id for item in canvas.nodes} or edge.target not in {item.id for item in canvas.nodes}:
                    raise AgentOperationError(409, "Canvas edge references a missing node")
                canvas.edges.append(edge); applied.append({"action": action, "id": edge.id})
            elif action == "update_edge":
                edge = next((item for item in canvas.edges if item.id == data.get("id")), None)
                if not edge: raise AgentOperationError(409, f"Canvas edge missing: {data.get('id')}")
                for field in ("source", "target", "label"):
                    if field in data: set_model_field(edge, field, data[field])
                applied.append({"action": action, "id": edge.id})
            elif action == "remove_edge":
                edge_id = safe_text(data.get("id"), 160)
                canvas.edges = [item for item in canvas.edges if item.id != edge_id]
                applied.append({"action": action, "id": edge_id})
        return applied

    def _projection_target(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.store.personal_dir):
            raise ValueError("projection target must remain inside personal data")
        return resolved.relative_to(self.store.personal_dir).as_posix()

    def _mutations(
        self,
        before: ThreadDoc,
        after: ThreadDoc,
        projects: dict[str, ProjectDoc],
        memories: dict[tuple[str, str, str], ObjectMemory | None],
        atlas_docs: dict[str, AtlasUpdateDoc],
    ) -> list[dict[str, Any]]:
        after.revision += 1
        after.updated_at = utc_now()
        mutations = [{
            "kind": "thread", "record_id": before.id,
            "expected_payload": self.store.get_record("thread", before.id),
            "payload": after.model_dump(mode="json"),
            "projection_target": f"threads/{before.id}.json",
        }]
        for project_id, project in projects.items():
            project.updated_at = utc_now()
            mutations.append({
                "kind": "project", "record_id": project_id,
                "expected_payload": self.store.get_record("project", project_id),
                "payload": project.model_dump(mode="json"),
                "projection_target": f"projects/{project_id}.json",
            })
        for (atlas_id, object_type, object_id), memory in memories.items():
            record_id = f"{atlas_id}:{object_type}:{object_id}"
            if memory is not None:
                memory.object_ref = {**memory.object_ref, "atlas_id": atlas_id, "object_type": object_type, "object_id": object_id}
                memory.tags = [tag.strip() for tag in memory.tags if tag.strip()]
                memory.updated_at = utc_now()
            mutations.append({
                "kind": "object_memory", "record_id": record_id,
                "expected_payload": self.store.get_record("object_memory", record_id),
                "payload": memory.model_dump(mode="json") if memory is not None else None,
                "projection_target": f"objects/{atlas_id}/{object_type}/{object_id}.json",
            })
        for atlas_id, atlas_doc in atlas_docs.items():
            atlas_doc.updated_at = utc_now()
            mutations.append({
                "kind": "atlas_update", "record_id": atlas_id,
                "expected_payload": self.store.get_record("atlas_update", atlas_id),
                "payload": atlas_doc.model_dump(mode="json"),
                "projection_target": f"atlas_updates/{atlas_id}.json",
            })
        return mutations

    def _reconciled_apply(self, thread: ThreadDoc, batch: OperationBatch) -> dict[str, Any] | None:
        receipt = next((message for message in thread.messages if isinstance(message.refs, dict) and message.refs.get("operation_batch_id") == batch.id and not message.refs.get("undone")), None)
        if not receipt:
            return None
        for operation in batch.operations:
            operation.after = self.current_value(thread, operation)
        batch.status = "applied"
        batch.applied_at = batch.applied_at or receipt.created_at or utc_now()
        batch.updated_at = utc_now()
        batch.receipt = {**batch.receipt, "reconciled": True}
        self.get_runtime().store.save_operation_batch(batch)
        return {"status": "applied", "operation_batch_id": batch.id, "transaction_id": batch.receipt.get("transaction_id", ""), "operations": batch.receipt.get("operations", []), "thread": thread.model_dump(mode="json"), "reconciled": True}

    def apply(self, batch: OperationBatch, resolution: ApprovalResolveRequest) -> dict[str, Any]:
        del resolution
        with self.thread_lock:
            thread = self.workspace.load_thread(batch.thread_id)
            reconciled = self._reconciled_apply(thread, batch)
            if reconciled: return reconciled
            if thread.revision != batch.base_revision:
                raise AgentOperationError(409, {"message": "线程在确认前发生变化", "expected_revision": batch.base_revision, "current_revision": thread.revision})
            conflicts = []
            for operation in batch.operations:
                current = self.current_value(thread, operation)
                if current != operation.before:
                    conflicts.append({"operation_id": operation.id, "capability": operation.capability, "expected": operation.before, "current": current})
            if conflicts: raise AgentOperationError(409, {"message": "操作目标发生变化", "conflicts": conflicts})

            updated = ThreadDoc.model_validate(thread.model_dump(mode="json"))
            projects: dict[str, ProjectDoc] = {}
            memories: dict[tuple[str, str, str], ObjectMemory | None] = {}
            atlas_docs: dict[str, AtlasUpdateDoc] = {}
            promotions, applied = [], []
            for operation in batch.operations:
                arguments, capability = operation.arguments or {}, operation.capability
                if capability == "context.add":
                    card = ContextCard(
                        id=safe_text(arguments.get("id"), 160) or new_id("card"),
                        type=arguments.get("type") if arguments.get("type") in {"paper", "relation", "path", "file"} else "paper",
                        title=safe_text(arguments.get("title"), 240) or "Agent 资料",
                        source_ref=scrub_refs(arguments.get("source_ref") or {}),
                        summary=safe_markdown(arguments.get("summary"), 3000),
                        token_estimate=max(1, len(str(arguments.get("summary") or "")) // 3),
                        include_in_agent=True, selected_for_export=False,
                    )
                    updated.context_cards.append(card); applied.append({"capability": capability, "card_id": card.id})
                elif capability == "context.remove":
                    card_id = safe_text(arguments.get("card_id"), 160)
                    updated.context_cards = [item for item in updated.context_cards if item.id != card_id]
                    applied.append({"capability": capability, "card_id": card_id})
                elif capability == "thread.update":
                    changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                    for field in ("title", "goal", "status", "active_atlas_id", "project_id"):
                        if field in changes: set_model_field(updated, field, changes[field])
                    applied.append({"capability": capability, "fields": list(changes)})
                elif capability == "project.update":
                    project_id = safe_text(arguments.get("project_id"), 160)
                    project = ProjectDoc.model_validate(self.workspace.load_project(project_id).model_dump(mode="json"))
                    changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                    for field in ("title", "goal", "status", "default_atlas_id"):
                        if field in changes: set_model_field(project, field, changes[field])
                    projects[project_id] = project; applied.append({"capability": capability, "project_id": project_id})
                elif capability == "memory.update":
                    atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
                    object_type, object_id = safe_text(arguments.get("object_type") or "paper", 40), safe_text(arguments.get("object_id"), 180)
                    memory = self.atlas.effective_object_memory(atlas_id, object_type, object_id) or ObjectMemory(object_ref={"atlas_id": atlas_id, "object_type": object_type, "object_id": object_id}, title_snapshot=safe_text(arguments.get("title"), 240))
                    memory = ObjectMemory.model_validate(memory.model_dump(mode="json"))
                    changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                    for field in ("judgement", "note", "core_innovation", "core_technology", "evidence", "limitations", "reusable_insight", "tags", "maturity", "star", "reading_status", "reading_questions"):
                        if field in changes: set_model_field(memory, field, changes[field])
                    memories[(atlas_id, object_type, object_id)] = memory; applied.append({"capability": capability, "object_id": object_id})
                elif capability == "memory.promote":
                    draft_id = safe_text(arguments.get("draft_id"), 180)
                    if not self.get_runtime().store.get_memory_draft(draft_id): raise AgentOperationError(409, "长期记忆草稿不存在")
                    promotions.append(draft_id); applied.append({"capability": capability, "draft_id": draft_id})
                elif capability == "canvas.apply":
                    applied.extend(self._apply_canvas(updated.canvas, arguments))
                elif capability == "atlas_candidate.upsert":
                    atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
                    atlas_doc = atlas_docs.get(atlas_id) or AtlasUpdateDoc.model_validate(self.atlas.load_updates(atlas_id).model_dump(mode="json"))
                    candidate_id = safe_text(arguments.get("candidate_id"), 160)
                    candidate = next((item for item in atlas_doc.candidates if item.id == candidate_id), None)
                    changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                    if candidate:
                        for field in AtlasCandidateUpdate.model_fields:
                            if field in changes: set_model_field(candidate, field, changes[field])
                        candidate.updated_at = utc_now()
                    else:
                        candidate = AtlasUpdateCandidate.model_validate({"id": candidate_id or new_id("candidate"), "title": changes.get("title") or "未命名候选论文", "created_at": utc_now(), "updated_at": utc_now(), **changes})
                        atlas_doc.candidates.append(candidate)
                    atlas_docs[atlas_id] = atlas_doc; applied.append({"capability": capability, "candidate_id": candidate.id})
                else:
                    raise AgentOperationError(400, f"unsupported Agent v2 capability: {capability}")

            self._append_message(updated, Message(role="tool", kind="state", content=f"已应用 Agent v2 操作：{batch.summary}", surface="thread", refs={"agent_v2_task_id": batch.task_id, "operation_batch_id": batch.id}))
            journal = self.store.apply_record_batch(self._mutations(thread, updated, projects, memories, atlas_docs))
            self.projector.replay(limit=max(20, len(journal) * 2))
            promoted = []
            try:
                for draft_id in promotions:
                    self.get_runtime().store.promote_memory(draft_id, utc_now()); promoted.append(draft_id)
            except Exception:
                for draft_id in promoted: self.get_runtime().store.revert_promoted_memory(draft_id)
                raise
            updated = self.workspace.load_thread(thread.id)
            for operation in batch.operations: operation.after = self.current_value(updated, operation)
            transaction_id = new_id("agent_v2_transaction")
            batch.status, batch.applied_at = "applied", utc_now()
            batch.updated_at = batch.applied_at
            batch.receipt = {"transaction_id": transaction_id, "operations": applied, "projection_journal": journal}
            self.get_runtime().store.save_operation_batch(batch)
            return {"status": "applied", "operation_batch_id": batch.id, "transaction_id": transaction_id, "operations": applied, "thread": updated.model_dump(mode="json")}

    def undo(self, batch: OperationBatch) -> dict[str, Any]:
        with self.thread_lock:
            thread = self.workspace.load_thread(batch.thread_id)
            receipt = next((message for message in thread.messages if isinstance(message.refs, dict) and message.refs.get("operation_batch_id") == batch.id and message.refs.get("undone") is True), None)
            if receipt:
                batch.status, batch.undone_at, batch.updated_at = "undone", batch.undone_at or receipt.created_at or utc_now(), utc_now()
                batch.receipt = {**batch.receipt, "undo_reconciled": True}
                self.get_runtime().store.save_operation_batch(batch)
                return {"status": "undone", "operation_batch": batch.model_dump(mode="json"), "thread": thread.model_dump(mode="json"), "reconciled": True}
            if batch.status != "applied": raise AgentOperationError(409, "只有已应用的操作批次可以撤销")
            conflicts = []
            for operation in batch.operations:
                current = self.current_value(thread, operation)
                if current != operation.after:
                    conflicts.append({"operation_id": operation.id, "capability": operation.capability, "expected": operation.after, "current": current})
            if conflicts:
                batch.conflicts, batch.updated_at = conflicts, utc_now()
                self.get_runtime().store.save_operation_batch(batch)
                raise AgentOperationError(409, {"message": "目标在应用后发生变化，无法安全撤销", "conflicts": conflicts})

            updated = ThreadDoc.model_validate(thread.model_dump(mode="json"))
            projects: dict[str, ProjectDoc] = {}
            memories: dict[tuple[str, str, str], ObjectMemory | None] = {}
            atlas_docs: dict[str, AtlasUpdateDoc] = {}
            promotions = []
            for operation in reversed(batch.operations):
                arguments, capability = operation.arguments or {}, operation.capability
                if capability == "context.add":
                    card_id = safe_text(arguments.get("id"), 160); updated.context_cards = [item for item in updated.context_cards if item.id != card_id]
                elif capability == "context.remove":
                    if operation.before:
                        restored = ContextCard.model_validate(operation.before)
                        updated.context_cards = [item for item in updated.context_cards if item.id != restored.id] + [restored]
                elif capability == "thread.update":
                    for field, value in (operation.before or {}).items(): set_model_field(updated, field, value)
                elif capability == "project.update":
                    project_id = safe_text(arguments.get("project_id"), 160)
                    project = projects.get(project_id) or ProjectDoc.model_validate(self.workspace.load_project(project_id).model_dump(mode="json"))
                    for field, value in (operation.before or {}).items(): set_model_field(project, field, value)
                    projects[project_id] = project
                elif capability == "memory.update":
                    atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
                    object_type, object_id = safe_text(arguments.get("object_type") or "paper", 40), safe_text(arguments.get("object_id"), 180)
                    memories[(atlas_id, object_type, object_id)] = ObjectMemory.model_validate(operation.before) if operation.before else None
                elif capability == "memory.promote":
                    promotions.append(safe_text(arguments.get("draft_id"), 180))
                elif capability == "canvas.apply":
                    updated.canvas = CanvasState.model_validate(operation.before or {})
                elif capability == "atlas_candidate.upsert":
                    atlas_id = safe_text(arguments.get("atlas_id") or thread.active_atlas_id, 80)
                    atlas_doc = atlas_docs.get(atlas_id) or AtlasUpdateDoc.model_validate(self.atlas.load_updates(atlas_id).model_dump(mode="json"))
                    candidate_id = safe_text(arguments.get("candidate_id") or (operation.after or {}).get("id"), 160)
                    atlas_doc.candidates = [item for item in atlas_doc.candidates if item.id != candidate_id]
                    if operation.before: atlas_doc.candidates.append(AtlasUpdateCandidate.model_validate(operation.before))
                    atlas_docs[atlas_id] = atlas_doc
                else:
                    raise AgentOperationError(400, f"unsupported Agent v2 undo capability: {capability}")
            batch.status, batch.undone_at = "undone", utc_now()
            batch.updated_at = batch.undone_at
            transaction_id = new_id("agent_v2_undo")
            batch.receipt = {**batch.receipt, "undo_transaction_id": transaction_id}
            task = self.get_runtime().store.get_task(batch.task_id)
            if task:
                try:
                    assistant = self._find_message(updated, task.assistant_message_id)
                    existing = assistant.refs.get("agent_v2_operation_batches") if isinstance(assistant.refs, dict) else []
                    assistant.refs = scrub_refs({**(assistant.refs or {}), "agent_v2_operation_batches": [batch.model_dump(mode="json") if item.get("id") == batch.id else item for item in (existing or [])] or [batch.model_dump(mode="json")]})
                except AgentOperationError:
                    pass
            self._append_message(updated, Message(role="tool", kind="state", content=f"已撤销 Agent v2 操作：{batch.summary}", surface="thread", refs={"agent_v2_task_id": batch.task_id, "operation_batch_id": batch.id, "undone": True}))
            journal = self.store.apply_record_batch(self._mutations(thread, updated, projects, memories, atlas_docs))
            self.projector.replay(limit=max(20, len(journal) * 2))
            for draft_id in promotions: self.get_runtime().store.revert_promoted_memory(draft_id)
            batch.receipt = {**batch.receipt, "undo_projection_journal": journal}
            self.get_runtime().store.save_operation_batch(batch)
            return {"status": "undone", "operation_batch": batch.model_dump(mode="json"), "thread": self.workspace.load_thread(thread.id).model_dump(mode="json")}
