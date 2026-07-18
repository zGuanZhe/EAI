from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..agent_v2.models import AgentSteerRequest, AgentTurnRequest
from ..agent_v2.runtime import utc_now
from ..agent_v3.context import build_context_manifest
from ..agent_v3.models import (
    AgentV3TurnResponse,
    AskTurnRequest,
    ContextManifest,
    ResearchTaskCreate,
    ResearchTaskView,
)
from ..agent_v3.routing import retrieval_decision, route_interaction
from ..campaign.models import CampaignBudget, CampaignWorkspaceSeed
from ..schemas.models import Message
from .agent_api import AgentApiConflictError, AgentApiNotFoundError, AgentApiService


class AgentV3Service:
    def __init__(
        self,
        agent_api: AgentApiService,
        *,
        load_full_context: Callable[[str, AgentTurnRequest], dict[str, Any]],
    ) -> None:
        self.agent_api = agent_api
        self.runtime = agent_api.runtime
        self.workspace = agent_api.workspace
        self.load_full_context = load_full_context

    def _active_lane(self, thread_id: str, lane: str):
        return next((
            task for task in self.runtime.store.list_tasks(
                thread_id=thread_id, statuses={"pending", "running", "waiting_approval", "paused"}
            ) if task.interaction_lane == lane and task.parent_task_id is None
        ), None)

    @staticmethod
    def _focus_attachment(focus_ref) -> dict[str, Any] | None:
        if not focus_ref:
            return None
        return {
            "type": focus_ref.type,
            "id": focus_ref.id,
            "title": focus_ref.title,
            "source_ref": {"type": focus_ref.type, "id": focus_ref.id},
        }

    def _start(
        self,
        *,
        thread_id: str,
        message: str,
        lane: str,
        source_policy: str,
        surface: str | None,
        focus_ref,
        attachments: list[dict[str, Any]],
        context_refs,
        model_overrides: dict[str, str],
        research_profile: dict[str, Any],
    ) -> AgentV3TurnResponse:
        message = self.agent_api._message(message, 8000)
        active = self._active_lane(thread_id, lane)
        if active:
            raise AgentApiConflictError({
                "message": f"当前线程已有进行中的{'研究任务' if lane == 'research' else '询问'}",
                "task_id": active.id,
                "status": active.status,
                "interaction_lane": lane,
            })
        task_id = self.agent_api._new_id("agent_task")
        web_available = self.runtime.sources.web.available
        interaction = route_interaction(message) if lane == "ask" else None
        retrieval = retrieval_decision(
            message,
            source_policy,
            web_available=web_available,
            interaction=interaction.kind if interaction else "information",
        )
        runtime_surface = "agent-v3:research"
        intent_override = "deep_research"
        if lane == "ask":
            runtime_surface = {
                "information": "agent-v3:ask-search",
                "conversation": "agent-v3:ask-chat",
                "workspace_operation": "agent-v3:ask-operation",
                "sandbox_execution": "agent-v3:ask-execution",
                "campaign_control": "agent-v3:ask-campaign",
            }[interaction.kind]
            intent_override = "auto"
        runtime_attachments = list(attachments[:8])
        focus_attachment = self._focus_attachment(focus_ref)
        if focus_attachment and not any(str(item.get("id")) == focus_ref.id for item in runtime_attachments):
            runtime_attachments.insert(0, focus_attachment)
            runtime_attachments = runtime_attachments[:8]
        runtime_request = AgentTurnRequest(
            message=message,
            surface=runtime_surface,
            intent_override=intent_override,
            source_policy=(
                source_policy
                if retrieval.search_required or lane == "research" or interaction.kind != "conversation"
                else None
            ),
            turn_attachments=runtime_attachments,
            model_overrides=model_overrides,
        )
        raw_context = self.load_full_context(thread_id, runtime_request)
        manifest = build_context_manifest(
            task_id=task_id,
            thread_id=thread_id,
            interaction_lane=lane,
            source_policy=source_policy,
            surface=surface,
            focus_ref=focus_ref,
            raw=raw_context,
            attachments=attachments,
            context_refs=context_refs,
        )
        with self.agent_api.thread_lock:
            active = self._active_lane(thread_id, lane)
            if active:
                raise AgentApiConflictError({
                    "message": f"当前线程已有进行中的{'研究任务' if lane == 'research' else '询问'}",
                    "task_id": active.id,
                    "status": active.status,
                    "interaction_lane": lane,
                })
            self.runtime.store.save_context_manifest(manifest.model_dump(mode="json"))
            thread = self.agent_api._load_thread(thread_id)
            common_refs = {
                "agent_runtime": "v3",
                "agent_v3_task_id": task_id,
                "interaction_lane": lane,
                "context_manifest_id": manifest.id,
            }
            user = self.agent_api._append_message(thread, Message(
                role="user", kind="text", content=message, surface=surface or "thread",
                refs={**common_refs, "turn_attachments": [self.agent_api._scrub_refs(item) for item in attachments[:8]]},
            ))
            initial_status = {
                "agent-v3:ask-search": "正在检索并核对信息...",
                "agent-v3:ask-chat": "正在回答...",
                "agent-v3:ask-operation": "正在准备工作区修改预览...",
                "agent-v3:ask-execution": "正在准备受限执行预览...",
                "agent-v3:ask-campaign": "正在检查 Campaign 状态和审批边界...",
                "agent-v3:research": "正在界定研究问题并建立检索计划...",
            }[runtime_surface]
            assistant = self.agent_api._append_message(thread, Message(
                role="assistant", kind="assistant_reply",
                content=initial_status,
                status="pending", surface="thread", refs={**common_refs, "service_status": "intake"},
            ))
            thread.active_surface = "thread"
            updated = self.workspace.write_thread(thread)
            task = self.runtime.create_task(
                thread_id=thread_id,
                turn_id=user.id or "",
                assistant_message_id=assistant.id or "",
                request=runtime_request,
                task_id=task_id,
                runtime_version="3.0",
                interaction_lane=lane,
                context_manifest_id=manifest.id,
                retrieval_decision=retrieval.model_dump(mode="json"),
                research_profile=research_profile,
            )
        self.runtime.store.append_event(task.id, "context_ready", {
            "manifest_id": manifest.id,
            "item_count": len(manifest.items),
            "source_policy": source_policy,
        }, utc_now())
        if retrieval.unavailable_scopes:
            self.runtime.store.append_event(task.id, "connector_unavailable", {
                "scopes": retrieval.unavailable_scopes,
                "message": "通用网页搜索未配置，本轮继续使用本地与学术来源。",
            }, utc_now())
        if lane == "research":
            self.runtime.store.save_research_checkpoint(
                task.id, "scope", {"objective": message, **research_profile}, utc_now()
            )
        return AgentV3TurnResponse(
            thread=updated.model_dump(mode="json"),
            task=task,
            user_message_id=user.id or "",
            assistant_message_id=assistant.id or "",
            retrieval=retrieval,
            context_manifest=manifest,
        )

    def ask(self, thread_id: str, payload: AskTurnRequest) -> AgentV3TurnResponse:
        return self._start(
            thread_id=thread_id, message=payload.message, lane="ask", source_policy=payload.source_policy,
            surface=payload.surface, focus_ref=payload.focus_ref, attachments=payload.attachments,
            context_refs=[], model_overrides=payload.model_overrides, research_profile={},
        )

    def create_research_task(self, thread_id: str, payload: ResearchTaskCreate) -> AgentV3TurnResponse:
        return self._start(
            thread_id=thread_id, message=payload.objective, lane="research", source_policy=payload.source_policy,
            surface=payload.surface, focus_ref=payload.focus_ref, attachments=payload.attachments,
            context_refs=payload.context_refs, model_overrides=payload.model_overrides,
            research_profile={"deliverable": payload.deliverable, "depth": payload.depth},
        )

    def _research_task(self, task_id: str):
        task = self.runtime.store.get_task(task_id)
        if not task or task.interaction_lane != "research":
            raise AgentApiNotFoundError("research task not found")
        return task

    def list_research_tasks(self, thread_id: str) -> list[dict[str, Any]]:
        self.agent_api._load_thread(thread_id)
        return [
            task.model_dump(mode="json") for task in self.runtime.store.list_tasks(thread_id=thread_id)
            if task.interaction_lane == "research"
        ]

    def research_task_view(self, task_id: str) -> ResearchTaskView:
        task = self._research_task(task_id)
        manifest = self.runtime.store.get_context_manifest(task.context_manifest_id) if task.context_manifest_id else None
        return ResearchTaskView(
            task=task,
            context_manifest=ContextManifest.model_validate(manifest) if manifest else None,
            checkpoints=self.runtime.store.list_research_checkpoints(task.id),
            sources=[item.model_dump(mode="json") for item in self.runtime.store.list_sources(task.id)],
            artifacts=[
                item.model_dump(mode="json") for artifact_id in task.artifact_ids
                if (item := self.runtime.store.get_artifact(artifact_id))
            ],
            approvals=[
                item.model_dump(mode="json") for approval_id in task.approval_ids
                if (item := self.runtime.store.get_approval(approval_id))
            ],
        )

    def steer(self, task_id: str, message: str) -> dict[str, Any]:
        self._research_task(task_id)
        return self.agent_api.steer_task(task_id, AgentSteerRequest(message=message))

    def pause(self, task_id: str) -> dict[str, Any]:
        self._research_task(task_id)
        return {"task": self.runtime.pause(task_id).model_dump(mode="json")}

    def resume(self, task_id: str) -> dict[str, Any]:
        self._research_task(task_id)
        return self.agent_api.resume_task(task_id)

    def cancel(self, task_id: str) -> dict[str, Any]:
        self._research_task(task_id)
        return self.agent_api.cancel_task(task_id)

    def event_stream(self, task_id: str, after_seq: int):
        self._research_task(task_id)
        return self.agent_api.event_stream(task_id, after_seq)

    def promote_campaign(self, task_id: str) -> dict[str, Any]:
        task = self._research_task(task_id)
        if task.status != "done":
            raise AgentApiConflictError({"message": "研究任务尚未完成", "task_id": task.id, "status": task.status})
        sources = self.runtime.store.list_sources(task.id)
        if not sources:
            raise AgentApiConflictError({"message": "研究任务没有可用于 Campaign 的来源", "task_id": task.id})
        campaign = self.agent_api.get_campaign_service()
        preview = campaign.preview_ideas(task.thread_id, node_id=None, objective=task.objective, count=1)
        if not preview.ideas:
            raise AgentApiConflictError({"message": "无法从当前研究结果形成可检验假设", "task_id": task.id})
        idea = preview.ideas[0]
        idea.related_work_source_ids = [item.id for item in sources[:12]]
        snapshot = campaign.create(
            task.thread_id, idea, [], CampaignBudget(), CampaignWorkspaceSeed()
        )
        self.runtime.store.append_event(task.id, "campaign_ready", {
            "campaign_id": snapshot.campaign.id, "title": snapshot.campaign.title,
        }, utc_now())
        return snapshot.model_dump(mode="json")

    def context_preview(self, thread_id: str, *, lane: str, source_policy: str, surface: str | None) -> ContextManifest:
        request = AgentTurnRequest(message="context preview", surface=surface, source_policy=source_policy)
        raw = self.load_full_context(thread_id, request)
        return build_context_manifest(
            task_id="preview", thread_id=thread_id, interaction_lane=lane, source_policy=source_policy,
            surface=surface, focus_ref=None, raw=raw, attachments=[], context_refs=[],
        )

    def connector_status(self) -> list[dict[str, Any]]:
        return [
            {"id": "local", "provider": "Research Store", "available": True, "reason": ""},
            {"id": "academic", "provider": "OpenAlex / arXiv / Crossref", "available": True, "reason": ""},
            self.runtime.sources.web.status(),
        ]

    def capability_status(self) -> list[dict[str, Any]]:
        values = self.agent_api.list_capabilities()
        for value in values:
            permission = value.get("permission")
            value["control_level"] = {
                "read": "automatic_read", "temporary": "preview", "write": "confirmed_write", "execute": "approved_execution",
            }.get(permission, "forbidden")
        return values

    def check_connector(self, connector_id: str) -> dict[str, Any]:
        if connector_id != "web":
            raise AgentApiNotFoundError("connector not found")
        return self.runtime.sources.web.check()

    def list_ui_commands(self, task_id: str) -> list[dict[str, Any]]:
        if not self.runtime.store.get_task(task_id):
            raise AgentApiNotFoundError("task not found")
        return self.runtime.store.list_ui_commands(task_id)

    def resolve_ui_command(self, command_id: str, status: str) -> dict[str, Any]:
        try:
            return self.runtime.store.resolve_ui_command(command_id, status, utc_now())
        except KeyError as exc:
            raise AgentApiNotFoundError("UI command not found") from exc
