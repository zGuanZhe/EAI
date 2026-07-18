from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .capabilities import validate_capability_arguments
from .documents import import_open_source
from .models import AgentTask, AgentTurnRequest, CapabilitySpec, Observation, SourceRecord, ToolCall
from .sources import SourceService, clean_text
from .store import RuntimeStore


@dataclass
class DispatcherDependencies:
    load_full_context: Callable[[str, AgentTurnRequest], dict[str, Any]]
    research_execute: Callable[[str, dict[str, Any], str, str], dict[str, Any]] | None = None
    campaign_execute: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None


class CapabilityDispatcher:
    def __init__(self, store: RuntimeStore, sources: SourceService, dependencies: DispatcherDependencies):
        self.store = store
        self.sources = sources
        self.dependencies = dependencies

    def execute(
        self,
        *,
        specification: CapabilitySpec,
        call: ToolCall,
        task: AgentTask,
        request: AgentTurnRequest,
        seed: dict[str, Any],
    ) -> tuple[Observation, list[SourceRecord]]:
        arguments = validate_capability_arguments(specification, call.arguments)
        capability_id = specification.id
        sources: list[SourceRecord] = []
        data: dict[str, Any] = {}
        summary = specification.label

        if specification.permission in {"write", "execute"}:
            return self._observation(call, "preview", f"已准备{specification.label}，等待策略与审批。", {"arguments": arguments}), []
        if capability_id == "workspace.snapshot":
            data = {"thread": seed.get("thread"), "project": seed.get("project"), "campaign_summaries": seed.get("campaign_summaries") or []}
            summary = "已读取当前线程、项目和 Campaign 摘要。"
        elif capability_id == "context.list":
            full = self.dependencies.load_full_context(task.thread_id, request)
            limit = max(1, min(int(arguments.get("limit") or 12), 24))
            data = {
                "context_cards": list(full.get("context_cards") or [])[:limit],
                "turn_attachments": list(request.turn_attachments or [])[:8],
                "canvas_summary": full.get("canvas") or {},
                "long_term_memories": list(full.get("long_term_memories") or [])[:limit],
            }
            summary = f"已读取 {len(data['context_cards'])} 条长期资料和当前附件。"
        elif capability_id == "atlas.search":
            sources = self.sources.search_atlas(
                str(arguments.get("atlas_id") or "G"), str(arguments["query"]), task.id,
                int(arguments.get("limit") or 18),
            )
            summary = f"在 Atlas 中找到 {len(sources)} 个策展来源。"
        elif capability_id == "documents.search":
            sources = self.sources.search_documents(str(arguments["query"]), task.id, int(arguments.get("limit") or 10))
            summary = f"在本地全文中找到 {len(sources)} 个证据片段。"
        elif capability_id == "sources.search_external":
            sources, warnings = self.sources.search_external(
                str(arguments["query"]), task.id, providers=list(arguments.get("providers") or []) or None,
                limit=int(arguments.get("limit") or 18),
            )
            data = {"warnings": warnings}
            summary = f"从外部学术源找到 {len(sources)} 个来源。"
        elif capability_id == "web.search":
            sources = self.sources.search_web(str(arguments["query"]), task.id, int(arguments.get("limit") or 8))
            summary = f"从通用网页搜索找到 {len(sources)} 个来源。"
        elif capability_id == "web.read":
            sources = [self.sources.read_web(str(arguments["url"]), task.id)]
            summary = "已读取并清洗公开网页正文。"
        elif capability_id == "documents.import_open":
            source = self.store.get_source(str(arguments["source_id"]))
            if not source:
                raise ValueError("开放全文来源不存在")
            document = import_open_source(self.store, source, created_at=call.created_at, client_factory=self.sources.client_factory)
            sources = [item for item in self.sources.search_documents(source.title, task.id, 8) if item.locator.get("document_id") == document.id]
            data = {"source_id": source.id, "document_id": document.id, "page_count": document.page_count}
            summary = f"已导入并索引《{source.title}》的开放全文。"
        elif capability_id.startswith("knowledge."):
            if not self.dependencies.research_execute:
                raise ValueError("Research Store 能力执行器不可用")
            result = self.dependencies.research_execute(capability_id, arguments, task.thread_id, task.id)
            sources = [SourceRecord.model_validate(item) for item in (result.pop("sources", []) or [])]
            data = result
            summary = clean_text(result.get("summary") or specification.label, 500)
        elif capability_id.startswith("campaign."):
            if not self.dependencies.campaign_execute:
                raise ValueError("Campaign 能力执行器不可用")
            data = self.dependencies.campaign_execute(capability_id, arguments, task.thread_id)
            summary = clean_text(data.get("summary") or specification.label, 500)
        elif capability_id == "navigation.open":
            command = {
                "id": f"ui_command_{call.id.removeprefix('tool_call_')}",
                "task_id": task.id,
                "action": "open_object",
                "target": {
                    "type": clean_text(arguments.get("type"), 80),
                    "id": clean_text(arguments.get("id"), 240),
                    "title": clean_text(arguments.get("title"), 500),
                },
                "status": "pending",
                "created_at": call.created_at,
                "resolved_at": None,
            }
            self.store.save_ui_command(command)
            data = {"ui_command": command}
            summary = f"已准备打开 {arguments.get('title') or arguments.get('type')}。"
        else:
            raise ValueError(f"能力未绑定执行器：{capability_id}")
        return self._observation(call, "ok", summary, data, sources), sources

    @staticmethod
    def _observation(
        call: ToolCall,
        status: str,
        summary: str,
        data: dict[str, Any],
        sources: list[SourceRecord] | None = None,
    ) -> Observation:
        return Observation(
            id=f"observation_{call.id.removeprefix('tool_call_')}",
            task_id=call.task_id,
            attempt_id=call.attempt_id,
            tool_call_id=call.id,
            capability=call.capability,
            status=status,
            summary=summary,
            data=data,
            source_ids=[source.id for source in (sources or [])],
            warnings=list(data.get("warnings") or []),
            created_at=call.completed_at or call.created_at,
        )
