from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from ..agent_v2.models import AgentTurnRequest
from ..campaign.models import BranchCompareRequest
from ..campaign.service import CampaignService
from ..research.context import build_research_state, evidence_bundle_to_sources, search_for_agent
from ..research.store import ResearchStore
from ..schemas.models import Message, ThreadDoc
from .atlas import AtlasService
from .thread_content import scrub_refs
from .workspace import WorkspaceService


def safe_text(value: Any, limit: int = 240) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def safe_markdown(value: Any, limit: int = 3000) -> str:
    if not value:
        return ""
    text = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in {"\n", "\t"} or ord(char) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()[:limit]


class AgentDomainService:
    def __init__(
        self,
        store: ResearchStore,
        workspace: WorkspaceService,
        atlas: AtlasService,
        *,
        runtime_if_created: Callable[[], Any | None],
        get_campaign: Callable[[], CampaignService],
        thread_lock: threading.RLock,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.atlas = atlas
        self.runtime_if_created = runtime_if_created
        self.get_campaign = get_campaign
        self.thread_lock = thread_lock

    def _load_thread(self, thread_id: str) -> ThreadDoc:
        return self.workspace.load_thread(thread_id)

    @staticmethod
    def _agent_cards(thread: ThreadDoc, limit: int) -> list[Any]:
        cards = [card for card in thread.context_cards if card.include_in_agent]
        return sorted(cards, key=lambda card: (not card.pinned, -card.priority, card.title))[:limit]

    @staticmethod
    def _compact_canvas(thread: ThreadDoc) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": node.id, "type": node.type, "title": node.title,
                    "body": safe_text(node.body, 600), "status": node.status, "priority": node.priority,
                }
                for node in thread.canvas.nodes[:40]
            ],
            "edges": [edge.model_dump(mode="json") for edge in thread.canvas.edges[:60]],
        }

    def load_full_context(self, thread_id: str, request: AgentTurnRequest) -> dict[str, Any]:
        thread = self._load_thread(thread_id)
        project = None
        if thread.project_id:
            try:
                project = self.workspace.load_project(thread.project_id).model_dump(mode="json")
            except (KeyError, ValueError):
                project = {"id": thread.project_id, "missing": True}
        campaigns = []
        runtime = self.runtime_if_created()
        if runtime:
            for snapshot in runtime.store.list_campaign_checkpoints(thread_id)[:6]:
                campaign = snapshot.get("campaign") if isinstance(snapshot, dict) else {}
                if isinstance(campaign, dict):
                    campaigns.append({
                        "id": campaign.get("id"), "title": campaign.get("title"),
                        "status": campaign.get("status"), "current_stage_id": campaign.get("current_stage_id"),
                        "objective": safe_text(campaign.get("objective"), 500),
                    })
        thread_state = {
            "id": thread.id, "title": thread.title, "goal": thread.goal, "revision": thread.revision,
            "active_atlas_id": thread.active_atlas_id, "active_surface": thread.active_surface,
            "conversation_summary": safe_text(thread.conversation_summary, 2400),
        }
        cards = [
            {
                "id": card.id, "type": card.type, "title": card.title,
                "summary": safe_text(card.summary, 600), "source_ref": scrub_refs(card.source_ref),
                "pinned": card.pinned, "priority": card.priority, "agent_note": safe_text(card.agent_note, 500),
            }
            for card in self._agent_cards(thread, 24)
        ]
        canvas = self._compact_canvas(thread)
        memories = [
            item for item in self.store.list_records("object_memory")
            if (item.get("object_ref") or {}).get("atlas_id") in {None, thread.active_atlas_id}
        ][:24]
        research_state = build_research_state(
            thread=thread_state, project=project, context_cards=cards, canvas=canvas,
            lab_runs=[], long_term_memories=memories,
        )
        research_state["normalized_graph"] = self.store.get_research_state(
            thread_id=thread.id, project_id=thread.project_id,
        )
        return {
            "thread": thread_state, "project": project, "context_cards": cards, "canvas": canvas,
            "campaign_summaries": campaigns, "long_term_memories": memories, "research_state": research_state,
            "recent_messages": [
                {"role": message.role, "content": safe_text(message.content, 800), "created_at": message.created_at}
                for message in thread.messages[-16:]
                if message.status not in {"pending", "streaming"} and message.role in {"user", "assistant"}
            ],
            "turn_attachments": [scrub_refs(item) for item in request.turn_attachments[:8]],
        }

    def load_context(self, thread_id: str, request: AgentTurnRequest) -> dict[str, Any]:
        thread = self._load_thread(thread_id)
        project = None
        if thread.project_id:
            try:
                item = self.workspace.load_project(thread.project_id)
                project = {"id": item.id, "title": item.title, "goal": safe_text(item.goal, 1000), "status": item.status}
            except (KeyError, ValueError):
                project = {"id": thread.project_id, "missing": True}
        campaigns = []
        runtime = self.runtime_if_created()
        if runtime:
            for snapshot in runtime.store.list_campaign_checkpoints(thread_id)[:4]:
                campaign = snapshot.get("campaign") if isinstance(snapshot, dict) else {}
                if isinstance(campaign, dict):
                    campaigns.append({
                        "id": campaign.get("id"), "title": campaign.get("title"),
                        "status": campaign.get("status"), "current_stage_id": campaign.get("current_stage_id"),
                    })
        return {
            "thread": {
                "id": thread.id, "title": thread.title, "goal": safe_text(thread.goal, 1200),
                "revision": thread.revision, "active_atlas_id": thread.active_atlas_id,
                "active_surface": thread.active_surface,
                "conversation_summary": safe_text(thread.conversation_summary, 1800),
            },
            "project": project, "campaign_summaries": campaigns,
            "recent_messages": [
                {"role": message.role, "content": safe_text(message.content, 800), "created_at": message.created_at}
                for message in thread.messages[-8:]
                if message.status not in {"pending", "streaming"} and message.role in {"user", "assistant"}
            ],
            "turn_attachments": [scrub_refs(item) for item in request.turn_attachments[:8]],
        }

    def research_search(
        self,
        query: str,
        atlas_id: str,
        task_id: str | None,
        attachments: list[dict[str, Any]],
        limit: int,
    ) -> list[Any]:
        _, sources = search_for_agent(
            self.store, query=query, atlas_id=atlas_id, task_id=task_id,
            attachments=attachments, limit=limit,
        )
        return sources

    def research_execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        thread_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        thread = self._load_thread(thread_id)
        if capability_id == "knowledge.search":
            query = str(arguments.get("query") or "").replace("\x00", "").strip()[:1200]
            if not query:
                raise ValueError("message content is empty")
            bundle = self.store.search(
                query, atlas_ids=[thread.active_atlas_id] if thread.active_atlas_id else [],
                limit=max(1, min(int(arguments.get("limit") or 18), 30)),
            )
            sources = evidence_bundle_to_sources(bundle, task_id)
            return {
                "summary": f"在研究知识库中找到 {len(bundle.works)} 篇论文、{len(bundle.claims)} 条论断和 {len(bundle.evidence)} 个证据片段。",
                "works": [
                    {"id": item.id, "title": item.title, "year": item.year, "evidence_status": item.evidence_status, "atlas_placements": item.atlas_placements[:4]}
                    for item in bundle.works
                ],
                "claims": [item.model_dump(mode="json") for item in bundle.claims[:20]],
                "evidence": [item.model_dump(mode="json") for item in bundle.evidence[:20]],
                "graph_paths": bundle.graph_paths[:20], "conflicts": bundle.conflicts[:10], "missing": bundle.missing,
                "sources": [item.model_dump(mode="json") for item in sources],
            }
        if capability_id == "knowledge.resolve_work":
            work = self.store.resolve_work(str(arguments.get("identifier") or ""), arguments.get("scheme"))
            return {"summary": "已解析论文身份。" if work else "没有找到可靠的论文身份。", "work": work.model_dump(mode="json") if work else None}
        if capability_id == "knowledge.work_profile":
            work = self.store.get_work(str(arguments.get("work_id") or ""))
            if not work: raise ValueError("论文不存在")
            claims, evidence = self.store.claims_for_work(work.id, verified_only=False)
            return {"summary": f"已读取《{work.title}》的证据档案：{len(claims)} 条论断，{len(evidence)} 个可定位证据。", "work": work.model_dump(mode="json"), "claims": [item.model_dump(mode="json") for item in claims[:30]], "evidence": [item.model_dump(mode="json") for item in evidence[:30]]}
        if capability_id == "knowledge.claim_evidence":
            claim, evidence = self.store.get_claim_evidence(str(arguments.get("claim_id") or ""))
            if not claim: raise ValueError("论断不存在")
            return {"summary": f"论断包含 {len(evidence)} 个可定位证据。", "claim": claim.model_dump(mode="json"), "evidence": [item.model_dump(mode="json") for item in evidence]}
        if capability_id == "knowledge.compare_works":
            profiles = []
            for work_id in list(arguments.get("work_ids") or [])[:8]:
                work = self.store.get_work(str(work_id))
                if not work: continue
                claims, evidence = self.store.claims_for_work(work.id)
                profiles.append({"work": work.model_dump(mode="json"), "claims": [item.model_dump(mode="json") for item in claims[:12]], "evidence": [item.model_dump(mode="json") for item in evidence[:16]]})
            return {"summary": f"已装配 {len(profiles)} 篇论文的可比证据。", "question": arguments.get("question") or "", "profiles": profiles}
        if capability_id == "knowledge.traverse_graph":
            value = self.store.graph_neighborhood(str(arguments.get("entity_id") or ""), depth=int(arguments.get("depth") or 1), limit=int(arguments.get("limit") or 40))
            return {"summary": f"研究图邻域包含 {len(value.get('works') or [])} 篇论文和 {len(value.get('relations') or [])} 条关系。", **value}
        if capability_id == "knowledge.research_state":
            value = self.store.get_research_state(thread_id=thread_id, project_id=thread.project_id)
            return {"summary": f"研究状态包含 {len(value.get('entities') or [])} 个对象。", **value}
        if capability_id == "knowledge.inspect_gaps":
            work_ids = list(arguments.get("work_ids") or [])[:20]
            works = [self.store.get_work(str(item)) for item in work_ids] if work_ids else []
            gaps = []
            for work in [item for item in works if item]:
                status = work.evidence_status
                if not status.get("full_text") or not status.get("verified_claims"):
                    gaps.append({"work_id": work.id, "title": work.title, "missing_full_text": not status.get("full_text"), "verified_claims": status.get("verified_claims", 0)})
            return {"summary": f"发现 {len(gaps)} 个论文级证据缺口。", "gaps": gaps, "coverage": self.store.status().coverage}
        raise ValueError(f"Research Store 能力未实现：{capability_id}")

    def campaign_execute(self, capability_id: str, arguments: dict[str, Any], thread_id: str) -> dict[str, Any]:
        service = self.get_campaign()
        if capability_id == "campaign.inspect":
            snapshot = service.get(str(arguments.get("campaign_id") or ""))
            if not snapshot or snapshot.campaign.thread_id != thread_id:
                raise ValueError("Campaign 不存在或不属于当前线程")
            return {
                "summary": f"Campaign《{snapshot.campaign.title}》当前处于 {snapshot.campaign.status}，包含 {len(snapshot.branches)} 个分支。",
                "campaign": snapshot.campaign.model_dump(mode="json"),
                "branches": [{"id": item.id, "stage_id": item.stage_id, "parent_id": item.parent_id, "origin": item.origin, "status": item.status, "title": item.title, "analysis": safe_text(item.analysis, 1200), "is_best": item.is_best} for item in snapshot.branches],
                "metrics": [item.model_dump(mode="json") for item in snapshot.metrics],
                "manuscripts": [{"id": item.id, "version": item.version, "status": item.status, "warnings": item.warnings} for item in snapshot.manuscripts],
                "reviews": [{"id": item.id, "role": item.role, "score": item.score, "decision": item.decision, "summary": item.summary} for item in snapshot.reviews],
            }
        if capability_id == "campaign.compare":
            result = service.compare_branches(str(arguments.get("campaign_id") or ""), BranchCompareRequest(branch_ids=list(arguments.get("branch_ids") or [])))
            return {"summary": "已生成 Campaign 分支比较产物。", **result}
        if capability_id == "campaign.prepare_idea":
            objective = str(arguments.get("objective") or "").replace("\x00", "").strip()[:2000]
            if not objective: raise ValueError("message content is empty")
            preview = service.preview_ideas(thread_id, node_id=arguments.get("node_id"), objective=objective, count=max(1, min(int(arguments.get("count") or 3), 3)))
            return {"summary": f"已准备 {len(preview.ideas)} 个带证据边界的研究想法。", **preview.model_dump(mode="json")}
        raise ValueError(f"Campaign 能力未实现：{capability_id}")

    def persist_assistant(self, thread_id: str, message_id: str, content: str, status: str, refs: dict[str, Any]) -> dict[str, Any]:
        with self.thread_lock:
            thread = self._load_thread(thread_id)
            assistant = next((message for message in thread.messages if message.id == message_id), None)
            if not assistant:
                raise KeyError("message not found")
            assistant.content = safe_markdown(content, 12000) or "本轮任务已完成。"
            assistant.status = safe_text(status, 32) or "done"
            assistant.refs = scrub_refs({**(assistant.refs or {}), **refs})
            updated = self.workspace.write_thread(thread)
        return updated.model_dump(mode="json")
