from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from ..legacy.models import ActionProposal, ChangeOperation, ChangeSet, ChangeSetConfirmRequest
from ..research.store import ResearchStore
from ..schemas.models import (
    AtlasUpdateCandidate,
    AtlasUpdateDoc,
    CanvasEdge,
    CanvasNode,
    ChangeSetResponse,
    ContextCard,
    ContextInjectionRequest,
    Message,
    ObjectMemory,
    ProposalConfirmResponse,
    ThreadDoc,
)
from .atlas import AtlasService
from .projection import ProjectionService
from .thread_content import new_id, safe_text, scrub_refs, utc_now
from .workspace import WorkspaceService


class ChangeReviewError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def json_pointer_field(path: str) -> str:
    return path.strip("/").split("/")[-1].replace("~1", "/").replace("~0", "~")


def set_model_field(model: BaseModel, field: str, value: Any) -> None:
    if field not in model.__class__.model_fields:
        raise ChangeReviewError(400, f"unsupported field: {field}")
    validated = model.__class__.model_validate({**model.model_dump(mode="json"), field: value})
    setattr(model, field, getattr(validated, field))


class ChangeReviewService:
    def __init__(self, store: ResearchStore, workspace: WorkspaceService, atlas: AtlasService) -> None:
        self.store = store
        self.workspace = workspace
        self.atlas = atlas
        self.projector = ProjectionService(store, store.personal_dir)

    def _load_thread(self, thread_id: str) -> ThreadDoc:
        try:
            return self.workspace.load_thread(thread_id)
        except KeyError as exc:
            raise ChangeReviewError(404, "thread not found") from exc

    @staticmethod
    def _find_changeset(thread: ThreadDoc, changeset_id: str) -> ChangeSet:
        for changeset in thread.changesets:
            if changeset.id == changeset_id:
                return changeset
        raise ChangeReviewError(404, "changeset not found")

    @staticmethod
    def _find_proposal(thread: ThreadDoc, proposal_id: str) -> ActionProposal:
        for proposal in thread.action_proposals:
            if proposal.id == proposal_id:
                return proposal
        raise ChangeReviewError(404, "proposal not found")

    @staticmethod
    def _append_message(thread: ThreadDoc, message: Message) -> None:
        message.id = message.id or new_id("msg")
        message.created_at = message.created_at or utc_now()
        message.refs = scrub_refs(message.refs)
        thread.messages.append(message)

    def _current_value(self, thread: ThreadDoc, operation: ChangeOperation) -> Any:
        target = operation.target or {}
        field = json_pointer_field(operation.path)
        if operation.op == "add" and operation.path.endswith("/-"):
            if operation.target_type == "context" and target.get("card_id"):
                return operation.after if any(item.id == target.get("card_id") for item in thread.context_cards) else None
            if operation.target_type == "canvas" and target.get("node_id"):
                return operation.after if any(item.id == target.get("node_id") for item in thread.canvas.nodes) else None
            if operation.target_type == "canvas" and target.get("edge_id"):
                return operation.after if any(item.id == target.get("edge_id") for item in thread.canvas.edges) else None
            if operation.target_type == "atlas_candidate" and target.get("candidate_id"):
                atlas_doc = self.atlas.load_updates(safe_text(target.get("atlas_id") or thread.active_atlas_id, 80))
                return operation.after if any(item.id == target.get("candidate_id") for item in atlas_doc.candidates) else None
            return None
        if operation.target_type == "context":
            card = next((item for item in thread.context_cards if item.id == target.get("card_id")), None)
            return card.model_dump(mode="json").get(field) if card else None
        if operation.target_type == "canvas":
            collection = thread.canvas.edges if target.get("edge_id") else thread.canvas.nodes
            item_id = target.get("edge_id") or target.get("node_id")
            item = next((entry for entry in collection if entry.id == item_id), None)
            return item.model_dump(mode="json").get(field) if item else None
        if operation.target_type == "object_memory":
            atlas_id = safe_text(target.get("atlas_id") or thread.active_atlas_id, 80)
            object_type = safe_text(target.get("object_type") or "paper", 40)
            object_id = safe_text(target.get("object_id"), 160)
            memory = self.atlas.effective_object_memory(atlas_id, object_type, object_id)
            if memory:
                return memory.model_dump(mode="json").get(field)
            return {"tags": [], "maturity": 0, "star": False}.get(field, "")
        atlas_id = safe_text(target.get("atlas_id") or thread.active_atlas_id, 80)
        candidate_id = safe_text(target.get("candidate_id"), 160)
        candidate = next((item for item in self.atlas.load_updates(atlas_id).candidates if item.id == candidate_id), None)
        return candidate.model_dump(mode="json").get(field) if candidate else None

    def build_changeset_from_raw(
        self,
        thread: ThreadDoc,
        run_id: str,
        raw: dict[str, Any],
    ) -> ChangeSet | None:
        allowed_fields = {
            "context": {"summary", "priority", "pinned", "agent_note", "include_in_agent", "selected_for_export", "cards"},
            "object_memory": {"judgement", "note", "core_innovation", "core_technology", "evidence", "limitations", "reusable_insight", "tags", "maturity", "star"},
            "atlas_candidate": {"title", "authors", "year", "venue", "url", "abstract", "suggested_route_id", "why", "relevance", "confidence", "status", "candidates"},
            "canvas": {"title", "body", "status", "priority", "nodes", "edges", "label"},
        }
        operations = []
        for index, item in enumerate((raw.get("operations") or [])[:24]):
            if not isinstance(item, dict):
                continue
            target_type = safe_text(item.get("target_type"), 40)
            path = safe_text(item.get("path"), 160)
            op = safe_text(item.get("op") or "replace", 20)
            if target_type not in allowed_fields or not path.startswith("/") or op not in {"add", "replace", "remove"}:
                continue
            if json_pointer_field(path) not in allowed_fields[target_type]:
                continue
            operation = ChangeOperation(
                id=safe_text(item.get("id"), 100) or new_id(f"change_{index + 1}"),
                target_type=target_type,
                target=scrub_refs(item.get("target") or {}),
                op=op,
                path=path,
                after=scrub_refs(item.get("after")),
                reason=safe_text(item.get("reason"), 320),
                risk=safe_text(item.get("risk"), 40) or "low",
                selected=bool(item.get("selected", True)),
            )
            operation.before = self._current_value(thread, operation)
            operations.append(operation)
        if not operations:
            return None
        now = utc_now()
        return ChangeSet(
            id=new_id("changeset"), thread_id=thread.id, source_run_id=run_id,
            base_revision=thread.revision,
            summary=safe_text(raw.get("summary"), 300) or "Main Agent 生成的可确认修改",
            risk=safe_text(raw.get("risk"), 40) or max((item.risk for item in operations), default="low"),
            operations=operations, created_at=now, updated_at=now,
        )

    def _conflicts(
        self,
        thread: ThreadDoc,
        operations: list[ChangeOperation],
        *,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        conflicts = []
        for operation in operations:
            current = self._current_value(thread, operation)
            expected = operation.after if reverse else operation.before
            if current != expected:
                conflicts.append({
                    "operation_id": operation.id,
                    "path": operation.path,
                    "expected": expected,
                    "current": current,
                })
        return conflicts

    def _apply_operation(
        self,
        thread: ThreadDoc,
        operation: ChangeOperation,
        memories: dict[tuple[str, str, str], ObjectMemory],
        atlas_docs: dict[str, AtlasUpdateDoc],
        *,
        reverse: bool = False,
    ) -> dict[str, Any]:
        target = operation.target or {}
        value = operation.before if reverse else operation.after
        op = ({"add": "remove", "remove": "add"}.get(operation.op, operation.op) if reverse else operation.op)
        field = json_pointer_field(operation.path)
        if operation.target_type == "context":
            if operation.path.endswith("/-"):
                if op == "add":
                    raw = value if isinstance(value, dict) else {}
                    card = ContextCard(
                        id=safe_text(raw.get("id"), 120) or new_id("card"),
                        type=raw.get("type") if raw.get("type") in {"paper", "relation", "path", "file"} else "paper",
                        title=safe_text(raw.get("title"), 220) or "Main Agent 上下文",
                        source_ref=scrub_refs(raw.get("source_ref") or target),
                        summary=safe_text(raw.get("summary"), 1800),
                        token_estimate=max(80, len(str(raw.get("summary") or "")) // 2),
                        include_in_agent=True,
                        selected_for_export=bool(raw.get("selected_for_export", False)),
                    )
                    thread.context_cards.append(card)
                    operation.target = {**target, "card_id": card.id}
                    return {"context_card_id": card.id}
                card_id = safe_text(target.get("card_id") or (operation.after or {}).get("id"), 120)
                thread.context_cards = [item for item in thread.context_cards if item.id != card_id]
                return {"removed_context_card_id": card_id}
            card = next((item for item in thread.context_cards if item.id == target.get("card_id")), None)
            if not card:
                raise ChangeReviewError(409, "context card no longer exists")
            if op == "remove":
                value = False if isinstance(getattr(card, field, None), bool) else ""
            set_model_field(card, field, value)
            return {"context_card_id": card.id, "field": field}
        if operation.target_type == "canvas":
            if operation.path.endswith("/-"):
                raw = value if isinstance(value, dict) else {}
                if field == "nodes":
                    if op == "add":
                        node = CanvasNode.model_validate({"id": raw.get("id") or new_id("node"), **raw})
                        thread.canvas.nodes.append(node)
                        operation.target = {**target, "node_id": node.id}
                        return {"canvas_node_id": node.id}
                    node_id = safe_text(target.get("node_id") or (operation.after or {}).get("id"), 120)
                    thread.canvas.nodes = [item for item in thread.canvas.nodes if item.id != node_id]
                    thread.canvas.edges = [item for item in thread.canvas.edges if item.source != node_id and item.target != node_id]
                    return {"removed_canvas_node_id": node_id}
                if op == "add":
                    edge = CanvasEdge.model_validate({"id": raw.get("id") or new_id("edge"), **raw})
                    node_ids = {item.id for item in thread.canvas.nodes}
                    if edge.source not in node_ids or edge.target not in node_ids:
                        raise ChangeReviewError(409, "canvas edge references a missing node")
                    thread.canvas.edges.append(edge)
                    operation.target = {**target, "edge_id": edge.id}
                    return {"canvas_edge_id": edge.id}
                edge_id = safe_text(target.get("edge_id") or (operation.after or {}).get("id"), 120)
                thread.canvas.edges = [item for item in thread.canvas.edges if item.id != edge_id]
                return {"removed_canvas_edge_id": edge_id}
            collection = thread.canvas.edges if target.get("edge_id") else thread.canvas.nodes
            item_id = target.get("edge_id") or target.get("node_id")
            item = next((entry for entry in collection if entry.id == item_id), None)
            if not item:
                raise ChangeReviewError(409, "canvas target no longer exists")
            set_model_field(item, field, value)
            return {"canvas_id": item_id, "field": field}
        if operation.target_type == "object_memory":
            atlas_id = safe_text(target.get("atlas_id") or thread.active_atlas_id, 80)
            object_type = safe_text(target.get("object_type") or "paper", 40)
            object_id = safe_text(target.get("object_id"), 160)
            key = (atlas_id, object_type, object_id)
            memory = memories.get(key) or self.atlas.effective_object_memory(atlas_id, object_type, object_id) or ObjectMemory(
                object_ref={"atlas_id": atlas_id, "object_type": object_type, "object_id": object_id},
                title_snapshot=safe_text(target.get("title"), 220),
            )
            set_model_field(memory, field, value)
            memory.object_ref = {**memory.object_ref, "atlas_id": atlas_id, "object_type": object_type, "object_id": object_id}
            memory.tags = [tag.strip() for tag in memory.tags if tag.strip()]
            memory.updated_at = utc_now()
            memories[key] = memory
            return {"object_ref": memory.object_ref, "field": field}
        atlas_id = safe_text(target.get("atlas_id") or thread.active_atlas_id, 80)
        atlas_doc = atlas_docs.get(atlas_id) or self.atlas.load_updates(atlas_id)
        atlas_docs[atlas_id] = atlas_doc
        if operation.path.endswith("/-"):
            if op == "add":
                raw = value if isinstance(value, dict) else {}
                candidate = AtlasUpdateCandidate.model_validate({
                    "id": raw.get("id") or new_id("candidate"),
                    "title": raw.get("title") or "未命名候选论文",
                    "source_run_id": operation.target.get("source_run_id"),
                    "created_at": utc_now(), "updated_at": utc_now(), **raw,
                })
                atlas_doc.candidates.append(candidate)
                operation.target = {**target, "candidate_id": candidate.id}
                return {"candidate_id": candidate.id, "atlas_id": atlas_id}
            candidate_id = safe_text(target.get("candidate_id") or (operation.after or {}).get("id"), 160)
            atlas_doc.candidates = [item for item in atlas_doc.candidates if item.id != candidate_id]
            return {"removed_candidate_id": candidate_id, "atlas_id": atlas_id}
        candidate = next((item for item in atlas_doc.candidates if item.id == target.get("candidate_id")), None)
        if not candidate:
            raise ChangeReviewError(409, "atlas candidate no longer exists")
        set_model_field(candidate, field, value)
        candidate.updated_at = utc_now()
        return {"candidate_id": candidate.id, "field": field, "atlas_id": atlas_id}

    def _commit(
        self,
        original: ThreadDoc,
        changeset: ChangeSet | None,
        operations: list[ChangeOperation],
        *,
        reverse: bool = False,
    ) -> tuple[ThreadDoc, dict[str, Any]]:
        thread = ThreadDoc.model_validate(original.model_dump(mode="json"))
        memories: dict[tuple[str, str, str], ObjectMemory] = {}
        atlas_docs: dict[str, AtlasUpdateDoc] = {}
        applied = [self._apply_operation(thread, operation, memories, atlas_docs, reverse=reverse) for operation in operations]
        if changeset is not None:
            stored_changeset = self._find_changeset(thread, changeset.id)
            operation_map = {item.id: item for item in operations}
            stored_changeset.operations = [operation_map.get(item.id, item) for item in stored_changeset.operations]
            stored_changeset.status = "undone" if reverse else "applied"
            stored_changeset.updated_at = utc_now()
            if reverse:
                stored_changeset.undone_at = stored_changeset.updated_at
            else:
                stored_changeset.applied_at = stored_changeset.updated_at
            self._append_message(thread, Message(
                role="tool", kind="state",
                content=("已撤销变更集：" if reverse else "已应用变更集：") + changeset.summary,
                status="done", surface="thread",
                refs={"changeset_id": changeset.id, "operation_ids": [item.id for item in operations], "undo": reverse},
            ))
        thread.revision += 1
        thread.updated_at = utc_now()

        thread_before = self.store.get_record("thread", original.id)
        mutations = [{
            "kind": "thread", "record_id": original.id, "expected_payload": thread_before,
            "payload": thread.model_dump(mode="json"), "projection_target": f"threads/{original.id}.json",
        }]
        for (atlas_id, object_type, object_id), memory in memories.items():
            record_id = f"{atlas_id}:{object_type}:{object_id}"
            mutations.append({
                "kind": "object_memory", "record_id": record_id,
                "expected_payload": self.store.get_record("object_memory", record_id),
                "payload": memory.model_dump(mode="json"),
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
        journal = self.store.apply_record_batch(mutations)
        self.projector.replay(limit=max(20, len(journal) * 2))
        return thread, {"transaction_id": new_id("transaction"), "operations": applied, "journal": journal}

    def get_changeset(self, thread_id: str, changeset_id: str) -> ChangeSet:
        return self._find_changeset(self._load_thread(thread_id), changeset_id)

    def confirm_changeset(self, thread_id: str, changeset_id: str, payload: ChangeSetConfirmRequest) -> ChangeSetResponse:
        thread = self._load_thread(thread_id)
        changeset = self._find_changeset(thread, changeset_id)
        if changeset.status not in {"pending", "conflicted"}:
            raise ChangeReviewError(400, "changeset is not pending")
        if payload.expected_revision is not None and payload.expected_revision != thread.revision:
            raise ChangeReviewError(409, {"message": "线程版本已变化", "current_revision": thread.revision})
        selected_ids = set(payload.selected_operation_ids or [item.id for item in changeset.operations if item.selected])
        for item in changeset.operations:
            item.selected = item.id in selected_ids
            if item.id in payload.edited_values:
                item.after = scrub_refs(payload.edited_values[item.id])
        operations = [
            ChangeOperation.model_validate(item.model_dump(mode="json"))
            for item in changeset.operations if item.id in selected_ids
        ]
        conflicts = self._conflicts(thread, operations)
        if conflicts:
            raise ChangeReviewError(409, {"message": "变更目标已发生变化", "conflicts": conflicts})
        updated, applied = self._commit(thread, changeset, operations)
        return ChangeSetResponse(thread=updated, changeset=self._find_changeset(updated, changeset_id), applied=applied)

    def reject_changeset(self, thread_id: str, changeset_id: str) -> ChangeSetResponse:
        thread = self._load_thread(thread_id)
        changeset = self._find_changeset(thread, changeset_id)
        if changeset.status not in {"pending", "conflicted"}:
            raise ChangeReviewError(400, "changeset is not pending")
        changeset.status = "rejected"
        changeset.updated_at = utc_now()
        self._append_message(thread, Message(
            role="tool", kind="state", content=f"已驳回变更集：{changeset.summary}",
            surface="thread", refs={"changeset_id": changeset.id},
        ))
        updated = self.workspace.write_thread(thread)
        return ChangeSetResponse(thread=updated, changeset=self._find_changeset(updated, changeset_id), applied={})

    def undo_changeset(self, thread_id: str, changeset_id: str) -> ChangeSetResponse:
        thread = self._load_thread(thread_id)
        changeset = self._find_changeset(thread, changeset_id)
        if changeset.status != "applied":
            raise ChangeReviewError(400, "only an applied changeset can be undone")
        operations = [item for item in changeset.operations if item.selected]
        conflicts = self._conflicts(thread, operations, reverse=True)
        if conflicts:
            raise ChangeReviewError(409, {"message": "当前值已变化，无法安全撤销", "conflicts": conflicts})
        updated, applied = self._commit(thread, changeset, list(reversed(operations)), reverse=True)
        return ChangeSetResponse(thread=updated, changeset=self._find_changeset(updated, changeset_id), applied=applied)

    def add_context_injection(self, thread_id: str, payload: ContextInjectionRequest) -> ThreadDoc:
        thread = self._load_thread(thread_id)
        source = scrub_refs(payload.source or {})
        card_type = source.get("type") if isinstance(source, dict) and source.get("type") in {"paper", "relation", "path", "file"} else "paper"
        title = safe_text(payload.title or (source.get("title") if isinstance(source, dict) else "") or "注入的上下文", 180)
        summary = safe_text(payload.summary or (source.get("summary") if isinstance(source, dict) else "") or "", 900)
        thread.context_cards.append(ContextCard(
            id=new_id("card"), type=card_type, title=title, source_ref=source if isinstance(source, dict) else {},
            summary=summary, token_estimate=max(80, len(summary) // 2), selected_for_export=True, include_in_agent=True,
        ))
        self._append_message(thread, Message(
            role="tool", kind="state", content=f"已把上下文送入主对话：{title}",
            surface="thread", refs={"source": source},
        ))
        return self.workspace.write_thread(thread)

    @staticmethod
    def _update_proposal_run(thread: ThreadDoc, proposal: ActionProposal) -> None:
        for run in thread.agent_runs:
            if run.id == proposal.source_run_id:
                run.proposals = [proposal if item.id == proposal.id else item for item in run.proposals]
                run.updated_at = utc_now()
                if not any(item.status == "pending" for item in run.proposals):
                    run.status = "done"

    def confirm_action_proposal(self, thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
        thread = self._load_thread(thread_id)
        proposal = self._find_proposal(thread, proposal_id)
        if proposal.status != "pending":
            raise ChangeReviewError(400, "proposal is not pending")
        proposal.status = "confirmed"
        proposal.updated_at = utc_now()
        self._update_proposal_run(thread, proposal)
        self._append_message(thread, Message(
            role="tool", kind="state", content=f"已确认智能体提案：{proposal.summary}",
            status="done", surface="thread",
            refs={"proposal_id": proposal.id, "proposal_type": proposal.type},
        ))
        if proposal.type == "task_pack_preview":
            updated = self.workspace.write_thread(thread)
            return ProposalConfirmResponse(thread=updated, proposal=self._find_proposal(updated, proposal_id), applied={"preview": True})
        changeset = self._proposal_as_changeset(thread, proposal)
        updated, applied = self._commit(thread, None, changeset.operations)
        return ProposalConfirmResponse(
            thread=updated,
            proposal=self._find_proposal(updated, proposal_id),
            applied=applied,
        )

    def _proposal_as_changeset(self, thread: ThreadDoc, proposal: ActionProposal) -> ChangeSet:
        target_type = {
            "context_injection": "context", "object_memory": "object_memory",
            "paper_card_update": "object_memory", "atlas_candidate": "atlas_candidate",
        }.get(proposal.type)
        if not target_type:
            raise ChangeReviewError(400, "unsupported proposal type")
        operations: list[ChangeOperation] = []
        if proposal.type == "context_injection":
            after = {
                "type": proposal.target.get("type", "paper"),
                "title": proposal.target.get("title") or proposal.summary,
                "summary": next((item.after for item in proposal.diff if item.field in {"summary", "note", "judgement"}), proposal.summary),
                "source_ref": proposal.target.get("source_ref") or proposal.target,
            }
            operations.append(ChangeOperation(
                id=new_id("change"), target_type="context", target=proposal.target,
                op="add", path="/cards/-", before=None, after=after, reason=proposal.summary,
            ))
        elif proposal.type == "atlas_candidate" and not proposal.diff:
            operations.append(ChangeOperation(
                id=new_id("change"), target_type=target_type, target=proposal.target,
                op="add", path="/candidates/-", before=None, after=proposal.target, reason=proposal.summary,
            ))
        else:
            for item in proposal.diff:
                operation = ChangeOperation(
                    id=new_id("change"), target_type=target_type, target=proposal.target,
                    op="replace", path=f"/{item.field}", after=item.after,
                    reason=item.reason or proposal.summary,
                )
                operation.before = self._current_value(thread, operation)
                operations.append(operation)
        changeset = ChangeSet(
            id=new_id("changeset"), thread_id=thread.id, source_run_id=proposal.source_run_id,
            base_revision=thread.revision, summary=proposal.summary, risk=proposal.risk,
            operations=operations, created_at=utc_now(), updated_at=utc_now(),
        )
        return changeset

    def reject_action_proposal(self, thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
        thread = self._load_thread(thread_id)
        proposal = self._find_proposal(thread, proposal_id)
        if proposal.status != "pending":
            raise ChangeReviewError(400, "proposal is not pending")
        proposal.status = "rejected"
        proposal.updated_at = utc_now()
        self._append_message(thread, Message(
            role="tool", kind="state", content=f"已驳回智能体提案：{proposal.summary}",
            status="done", surface="thread", refs={"proposal_id": proposal.id, "proposal_type": proposal.type},
        ))
        self._update_proposal_run(thread, proposal)
        updated = self.workspace.write_thread(thread)
        return ProposalConfirmResponse(thread=updated, proposal=self._find_proposal(updated, proposal_id), applied={})
