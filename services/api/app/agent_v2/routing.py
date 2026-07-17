from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .models import AgentTurnRequest, ServiceDecision


SERVICE_VALUES = {
    "conversation",
    "evidence_research",
    "document_reading",
    "synthesis",
    "workspace_operation",
    "sandbox_execution",
    "research_campaign",
}


def apply_source_constraints(decision: ServiceDecision, request: AgentTurnRequest) -> ServiceDecision:
    lower = _clean_text(request.message).lower()
    atlas_only = any(marker in lower for marker in ["只基于 atlas", "仅基于 atlas", "只查 atlas", "atlas only"])
    latest = any(marker in lower for marker in ["最新", "近期进展", "最近进展", "state of the art", "sota"])
    if atlas_only:
        if decision.service == "conversation":
            decision.service = "evidence_research"
        decision.source_policy = "local_only"
        decision.requested_outputs = list(dict.fromkeys(decision.requested_outputs + ["atlas_only", "evidence_set"]))
        decision.reason = "用户明确要求只使用 Atlas。"
    elif latest and decision.service in {"evidence_research", "document_reading", "synthesis"}:
        decision.source_policy = "local_and_external"
        decision.reason = "问题要求最新进展，必须核验外部学术源。"
    if request.source_policy:
        decision.source_policy = request.source_policy
    return decision


def _clean_text(value: Any, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def parse_service_decision(raw: str, request: AgentTurnRequest) -> ServiceDecision | None:
    candidates = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw or "")
    candidates.append((raw or "").strip())
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("service") not in SERVICE_VALUES:
            continue
        try:
            return ServiceDecision.model_validate(
                {
                    "service": data["service"],
                    "objective": _clean_text(data.get("objective") or request.message),
                    "depth": data.get("depth") or "standard",
                    "source_policy": data.get("source_policy") or "none",
                    "requested_outputs": [_clean_text(item, 80) for item in (data.get("requested_outputs") or [])[:6]],
                    "confidence": data.get("confidence", 0.7),
                    "requires_clarification": bool(data.get("requires_clarification")),
                    "clarification_question": _clean_text(data.get("clarification_question"), 240),
                    "reason": _clean_text(data.get("reason"), 240),
                }
            )
        except ValueError:
            continue
    return None


def fallback_service_decision(request: AgentTurnRequest) -> ServiceDecision:
    text = _clean_text(request.message)
    lower = text.lower()
    override = request.intent_override
    if override == "chat":
        return ServiceDecision(service="conversation", objective=text, depth="quick", source_policy="none", confidence=1, reason="用户指定仅聊天。")
    if override == "local":
        return ServiceDecision(service="evidence_research", objective=text, depth="standard", source_policy="local_only", requested_outputs=["evidence_set"], confidence=1, reason="用户指定仅使用本地资料。")
    if override == "deep_research":
        return ServiceDecision(service="evidence_research", objective=text, depth="deep", source_policy="local_and_external", requested_outputs=["evidence_set", "research_note"], confidence=1, reason="用户指定深度研究。")
    if override == "execute":
        return ServiceDecision(service="sandbox_execution", objective=text, depth="standard", source_policy="local_only", requested_outputs=["command_output"], confidence=1, reason="用户指定执行任务。")

    compact = re.sub(r"[\s，。！？,.!?]", "", lower)
    greetings = {"你好", "您好", "hi", "hello", "hey", "在吗", "谢谢", "thanks", "早上好", "晚上好"}
    if compact in greetings or (len(compact) <= 8 and any(compact.startswith(item) for item in greetings)):
        return ServiceDecision(service="conversation", objective=text, depth="quick", source_policy="none", confidence=0.99, reason="普通交流无需检索。")

    attachment_types = {str(item.get("type") or "").lower() for item in request.turn_attachments if isinstance(item, dict)}
    if any(word in lower for word in ["campaign", "实验树", "研究想法", "实验分支", "最佳分支", "审稿意见", "论文稿件", "研究包"]):
        return ServiceDecision(
            service="research_campaign", objective=text, depth="deep",
            source_policy=request.source_policy or "local_and_external",
            requested_outputs=["campaign_state", "evidence_set"], confidence=0.88,
            reason="问题指向研究 Campaign、实验分支或论文闭环。",
        )
    if any(word in lower for word in ["运行代码", "执行命令", "复现实验", "跑一下", "run command", "docker"]) or re.search(r"(?:运行|执行).{0,12}(?:程序|脚本|命令|代码|测试)", lower):
        return ServiceDecision(service="sandbox_execution", objective=text, depth="standard", source_policy="local_only", requested_outputs=["command_output"], confidence=0.86, reason="问题要求执行代码或命令。")
    if any(word in lower for word in ["修改", "更新", "创建", "删除", "加入", "长期资料", "保存", "记住", "写入", "重命名"]):
        vague_target = len(compact) <= 10 and not request.turn_attachments and any(word in lower for word in ["修改", "更新", "删除", "保存", "加入"])
        if vague_target:
            return ServiceDecision(
                service="workspace_operation", objective=text, depth="quick", source_policy="local_only",
                confidence=0.3, requires_clarification=True,
                clarification_question="你希望修改哪个对象，以及具体要改成什么？",
                reason="操作对象或目标值不明确。",
            )
        return ServiceDecision(service="workspace_operation", objective=text, depth="standard", source_policy="local_only", requested_outputs=["operation_preview"], confidence=0.78, reason="问题要求操作工作区数据。")
    if attachment_types.intersection({"pdf", "document", "file"}) or any(word in lower for word in ["全文", "这篇论文", "这份文档", "附件文档", "第几节", "pdf"]):
        return ServiceDecision(service="document_reading", objective=text, depth="standard", source_policy=request.source_policy or "local_and_external", requested_outputs=["document_analysis"], confidence=0.84, reason="问题指向具体文档或全文。")
    if any(word in lower for word in ["比较", "综合", "论证", "形成假设", "证据链", "canvas", "研究路线"]):
        return ServiceDecision(service="synthesis", objective=text, depth="deep", source_policy=request.source_policy or "local_and_external", requested_outputs=["evidence_set", "canvas_draft"], confidence=0.78, reason="问题需要跨来源综合或论证。")
    if any(word in lower for word in ["论文", "证据", "检索", "搜索", "最新", "进展", "研究", "文献", "arxiv", "doi"]):
        return ServiceDecision(service="evidence_research", objective=text, depth="deep", source_policy=request.source_policy or "local_and_external", requested_outputs=["evidence_set", "research_note"], confidence=0.76, reason="问题具有明确研究和证据需求。")
    return ServiceDecision(service="conversation", objective=text, depth="quick", source_policy="none", confidence=0.58, reason="无法可靠识别工具需求，安全回到普通对话。")


def route_turn(
    request: AgentTurnRequest,
    model_router: Callable[[str], str | None] | None = None,
    *,
    thread_context: dict[str, Any] | None = None,
) -> ServiceDecision:
    if request.intent_override != "auto":
        return fallback_service_decision(request)
    if model_router:
        prompt = json.dumps(
            {
                "instruction": (
                    "判断用户本轮需要哪一种 EAI 服务。普通交流必须选择 conversation 且 source_policy=none。"
                    "只有研究、阅读或综合任务才可联网；操作与执行必须选择对应服务。只返回 JSON。"
                ),
                "services": sorted(SERVICE_VALUES),
                "user_message": request.message,
                "surface": request.surface,
                "attachments": request.turn_attachments[:8],
                "thread": thread_context or {},
                "schema": {
                    "service": "service id",
                    "objective": "本轮目标",
                    "depth": "quick|standard|deep",
                    "source_policy": "none|local_only|local_and_external",
                    "requested_outputs": [],
                    "confidence": 0.0,
                    "requires_clarification": False,
                    "clarification_question": "",
                    "reason": "",
                },
            },
            ensure_ascii=False,
        )
        try:
            parsed = parse_service_decision(model_router(prompt) or "", request)
            if parsed:
                if parsed.confidence < 0.35 and parsed.service != "conversation" and not parsed.requires_clarification:
                    parsed.requires_clarification = True
                    parsed.clarification_question = parsed.clarification_question or "为了准确处理，你希望我聚焦哪个对象或范围？"
                if request.source_policy:
                    parsed.source_policy = request.source_policy
                return apply_source_constraints(parsed, request)
        except Exception:
            pass
    return apply_source_constraints(fallback_service_decision(request), request)
