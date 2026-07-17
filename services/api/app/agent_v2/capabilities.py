from __future__ import annotations

import shutil
from copy import deepcopy
from typing import Any

from .models import CapabilitySpec, ServiceType


def object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


STRING = {"type": "string"}
STRING_LIST = {"type": "array", "items": {"type": "string"}, "maxItems": 20}


def spec(
    capability_id: str,
    label: str,
    description: str,
    services: list[ServiceType],
    permission: str,
    input_schema: dict[str, Any],
    *,
    risk: str = "low",
    approval: str = "auto",
    scopes: list[str] | None = None,
    idempotent: bool = True,
    reversible: bool = False,
    timeout_seconds: int = 30,
    budget_cost: int = 1,
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        label=label,
        description=description,
        services=services,
        permission=permission,
        risk=risk,
        approval=approval,
        scopes=scopes or [],
        idempotent=idempotent,
        reversible=reversible,
        timeout_seconds=timeout_seconds,
        budget_cost=budget_cost,
        input_schema=input_schema,
        output_schema=object_schema(),
    )


RESEARCH = ["evidence_research", "document_reading", "synthesis", "research_campaign"]
WORKSPACE = ["workspace_operation", "synthesis", "research_campaign"]


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    item.id: item
    for item in [
        spec("workspace.snapshot", "读取工作区摘要", "读取当前项目、线程、焦点和安全摘要。", ["conversation", *WORKSPACE], "read", object_schema(), scopes=["project", "thread"]),
        spec("context.list", "读取线程资料", "按需读取本轮附件与长期资料。", [*RESEARCH, "workspace_operation"], "read", object_schema({"limit": {"type": "integer", "minimum": 1, "maximum": 24}}), scopes=["thread.context"]),
        spec("knowledge.search", "检索研究知识库", "检索 Atlas、全文、Claim、EvidenceSpan 和研究状态。", RESEARCH, "read", object_schema({"query": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, ["query"]), scopes=["knowledge"]),
        spec("knowledge.resolve_work", "解析论文身份", "通过 DOI、arXiv、OpenAlex 或标题解析论文。", RESEARCH, "read", object_schema({"identifier": STRING, "scheme": STRING}, ["identifier"]), scopes=["knowledge.identity"]),
        spec("knowledge.work_profile", "读取论文证据档案", "读取论文身份、Atlas 位置、论断和证据缺口。", RESEARCH, "read", object_schema({"work_id": STRING}, ["work_id"]), scopes=["knowledge.work"]),
        spec("knowledge.claim_evidence", "核验论断证据", "读取原子论断对应的页码、章节或表格定位。", RESEARCH, "read", object_schema({"claim_id": STRING}, ["claim_id"]), scopes=["knowledge.evidence"]),
        spec("knowledge.compare_works", "比较论文", "基于证据比较多篇论文。", ["evidence_research", "synthesis", "research_campaign"], "read", object_schema({"work_ids": STRING_LIST, "question": STRING}, ["work_ids"]), scopes=["knowledge.work"]),
        spec("knowledge.traverse_graph", "遍历研究图", "沿研究关系执行有界邻域检索。", ["evidence_research", "synthesis", "research_campaign"], "read", object_schema({"entity_id": STRING, "depth": {"type": "integer", "minimum": 1, "maximum": 2}, "limit": {"type": "integer", "minimum": 1, "maximum": 80}}, ["entity_id"]), scopes=["knowledge.graph"]),
        spec("knowledge.research_state", "读取研究状态", "读取问题、假设、判断、Campaign 和下一步任务。", ["conversation", *WORKSPACE], "read", object_schema(), scopes=["knowledge.state"]),
        spec("knowledge.inspect_gaps", "检查证据缺口", "识别缺失全文、关系理由和实验支撑。", RESEARCH, "read", object_schema({"work_ids": STRING_LIST}), scopes=["knowledge.quality"]),
        spec("sources.search_external", "检索外部学术源", "检索 OpenAlex、arXiv 和 Crossref。", RESEARCH, "read", object_schema({"query": STRING, "providers": STRING_LIST, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}, ["query"]), scopes=["network.academic"], timeout_seconds=45, budget_cost=2),
        spec("documents.search", "检索本地全文", "检索已导入 PDF、笔记和文本片段。", RESEARCH, "read", object_schema({"query": STRING, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]), scopes=["documents"]),
        spec("documents.import_open", "获取开放全文", "从可信学术域名获取并索引开放 PDF。", ["evidence_research", "document_reading", "research_campaign"], "temporary", object_schema({"source_id": STRING}, ["source_id"]), scopes=["documents", "network.academic"], timeout_seconds=90, budget_cost=3),
        spec("campaign.inspect", "检查研究 Campaign", "读取阶段、分支、指标、稿件和审稿状态。", ["research_campaign", "synthesis", "workspace_operation"], "read", object_schema({"campaign_id": STRING}, ["campaign_id"]), scopes=["research_campaign"]),
        spec("campaign.compare", "比较实验分支", "比较评价协议兼容的 Campaign 分支。", ["research_campaign", "synthesis"], "read", object_schema({"campaign_id": STRING, "branch_ids": STRING_LIST}, ["campaign_id", "branch_ids"]), scopes=["research_campaign"]),
        spec("campaign.prepare_idea", "准备研究想法", "基于证据生成可检查的 Campaign Idea 预览。", ["research_campaign", "synthesis", "workspace_operation"], "temporary", object_schema({"objective": STRING, "node_id": STRING, "count": {"type": "integer", "minimum": 1, "maximum": 3}}, ["objective"]), scopes=["research_campaign"], timeout_seconds=90, budget_cost=2),
        spec("navigation.open", "建议打开对象", "生成可由用户执行的界面导航动作。", ["conversation", *RESEARCH, "workspace_operation"], "temporary", object_schema({"type": STRING, "id": STRING, "title": STRING}, ["type", "id"]), scopes=["ui"], budget_cost=0),
        spec("context.add", "加入长期资料", "把来源加入线程长期资料。", ["workspace_operation", "evidence_research", "document_reading"], "write", object_schema({"title": STRING, "source_ref": {"type": "object"}, "summary": STRING}, ["title", "source_ref"]), risk="medium", approval="confirm", scopes=["thread.context"], reversible=True),
        spec("context.remove", "移除长期资料", "移除指定线程资料。", ["workspace_operation"], "write", object_schema({"card_id": STRING}, ["card_id"]), risk="medium", approval="confirm", scopes=["thread.context"], reversible=True),
        spec("thread.update", "更新线程", "更新线程标题、目标或当前 Atlas。", ["workspace_operation"], "write", object_schema({"changes": {"type": "object"}}, ["changes"]), risk="medium", approval="confirm", scopes=["thread"], reversible=True),
        spec("project.update", "更新项目", "更新项目标题、目标或状态。", ["workspace_operation"], "write", object_schema({"project_id": STRING, "changes": {"type": "object"}}, ["project_id", "changes"]), risk="medium", approval="confirm", scopes=["project"], reversible=True),
        spec("memory.update", "更新对象记忆", "更新论文或关系的个人判断。", ["workspace_operation", "document_reading", "synthesis"], "write", object_schema({"atlas_id": STRING, "object_type": STRING, "object_id": STRING, "changes": {"type": "object"}}, ["object_type", "object_id", "changes"]), risk="medium", approval="confirm", scopes=["object_memory"], reversible=True),
        spec("memory.promote", "晋升长期记忆", "晋升已生成的记忆草稿。", ["workspace_operation", "conversation", "synthesis"], "write", object_schema({"draft_id": STRING}, ["draft_id"]), risk="medium", approval="confirm", scopes=["long_term_memory"], reversible=True),
        spec("canvas.apply", "更新 Canvas", "预览添加、修改或移除论证节点与连线。", ["workspace_operation", "synthesis"], "write", object_schema({"operations": {"type": "array", "items": {"type": "object"}, "maxItems": 40}}, ["operations"]), risk="medium", approval="confirm", scopes=["thread.canvas"], reversible=True),
        spec("atlas_candidate.upsert", "更新候选论文", "创建或完善个人候选论文。", ["workspace_operation", "evidence_research"], "write", object_schema({"atlas_id": STRING, "candidate_id": STRING, "changes": {"type": "object"}}, ["changes"]), risk="medium", approval="confirm", scopes=["atlas.personal"], reversible=True),
        spec("sandbox.command", "执行沙箱命令", "在隔离 Docker 工作区执行已批准命令。", ["sandbox_execution"], "execute", object_schema({"command": STRING, "image": STRING, "network": {"type": "boolean"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}, "cpus": {"type": "number", "minimum": 0.1, "maximum": 8}, "memory_mb": {"type": "integer", "minimum": 256, "maximum": 16384}, "input_document_ids": STRING_LIST}, ["command"]), risk="high", approval="command", scopes=["sandbox"], idempotent=False, timeout_seconds=1800, budget_cost=3),
    ]
}


def capability_specs(*, service: ServiceType | None = None, include_write: bool = True) -> list[CapabilitySpec]:
    specs = [CapabilitySpec.model_validate(deepcopy(item.model_dump(mode="json"))) for item in CAPABILITY_REGISTRY.values()]
    docker_available = shutil.which("docker") is not None
    for item in specs:
        if item.id == "sandbox.command" and not docker_available:
            item.available = False
            item.unavailable_reason = "Docker 不可用，只能生成命令预览。"
    return [
        item for item in specs
        if (service is None or service in item.services) and (include_write or item.permission in {"read", "temporary"})
    ]


def capability(capability_id: str) -> CapabilitySpec | None:
    return next((item for item in capability_specs() if item.id == capability_id), None)


def validate_capability_arguments(specification: CapabilitySpec, arguments: dict[str, Any]) -> dict[str, Any]:
    schema = specification.input_schema
    allowed = set((schema.get("properties") or {}).keys())
    unknown = set(arguments) - allowed
    if unknown and schema.get("additionalProperties") is False:
        raise ValueError(f"能力 {specification.id} 包含未知字段：{', '.join(sorted(unknown))}")
    for field in schema.get("required") or []:
        if arguments.get(field) is None or arguments.get(field) == "":
            raise ValueError(f"能力 {specification.id} 缺少必填字段：{field}")
    return {key: value for key, value in arguments.items() if key in allowed}
