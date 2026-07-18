from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..agent_v2.models import SourcePolicy
from .models import RetrievalDecision


InteractionKind = Literal[
    "information",
    "conversation",
    "workspace_operation",
    "sandbox_execution",
    "campaign_control",
]


@dataclass(frozen=True)
class InteractionDecision:
    kind: InteractionKind
    reason: str


_NON_INFORMATION_PATTERNS = [
    r"^(你好|您好|嗨|hi|hello|hey|谢谢|thanks)[！!。.\s]*$",
    r"^(请)?(改写|润色|翻译|缩写|扩写|续写|头脑风暴|brainstorm|rewrite|translate)\b",
    r"^(把|将).{0,40}(改成|翻译成|整理成|rewrite|translate)",
]

_NEGATED_ACTION = re.compile(
    r"(?:不要|不必|无需|不需要|别|禁止|仅解释|只解释|如何|怎么|"
    r"do not|don't|never|without|how to|explain)\s*.{0,12}$",
    re.IGNORECASE,
)


def _has_action_intent(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            if not _NEGATED_ACTION.search(text[max(0, index - 32):index]):
                return True
            start = index + len(marker)
    return False


def route_interaction(message: str) -> InteractionDecision:
    """Choose the product lane before retrieval or model-based service routing."""
    text = " ".join(str(message or "").strip().split()).lower()
    campaign_actions = (
        "升级为campaign", "升级到campaign", "创建campaign", "暂停campaign", "恢复campaign",
        "继续campaign", "取消campaign", "推进campaign", "升级为 campaign", "升级到 campaign",
        "创建 campaign", "暂停 campaign", "恢复 campaign", "继续 campaign", "取消 campaign", "推进 campaign",
        "promote to campaign", "create campaign",
        "pause campaign", "resume campaign", "cancel campaign", "advance campaign",
    )
    execution_actions = (
        "运行代码", "执行代码", "运行命令", "执行命令", "运行脚本", "执行脚本", "复现实验", "跑一下",
        "run command", "run code", "run this code", "execute command", "execute code",
        "reproduce the experiment", "launch the test", "docker",
    )
    workspace_actions = (
        "修改", "更新", "创建", "删除", "加入", "长期资料", "保存", "记住", "写入", "重命名",
        "save", "remember", "rename", "add this", "add to", "update", "create", "delete", "write to",
    )
    if _has_action_intent(text, campaign_actions):
        return InteractionDecision("campaign_control", "用户明确要求控制或创建研究 Campaign。")
    if _has_action_intent(text, execution_actions):
        return InteractionDecision("sandbox_execution", "用户明确要求执行代码、命令或实验。")
    if _has_action_intent(text, workspace_actions):
        return InteractionDecision("workspace_operation", "用户明确要求修改 EAI 工作区。")
    if is_information_request(text):
        return InteractionDecision("information", "本轮是信息型询问。")
    return InteractionDecision("conversation", "本轮是闲聊或文本变换。")


def is_information_request(message: str) -> bool:
    text = " ".join(str(message or "").strip().split())
    if not text:
        return False
    lower = text.lower()
    if any(re.search(pattern, lower, re.IGNORECASE) for pattern in _NON_INFORMATION_PATTERNS):
        return False
    if text.endswith(("?", "？")):
        return True
    markers = [
        "什么", "为何", "为什么", "怎么", "如何", "哪些", "是否", "比较", "解释", "分析",
        "查", "搜索", "检索", "最新", "进展", "证据", "论文", "事实", "数据", "来源",
        "what", "why", "how", "which", "compare", "explain", "analyze", "search", "find",
        "latest", "evidence", "paper", "source", "fact",
    ]
    return any(marker in lower for marker in markers)


def retrieval_decision(
    message: str,
    source_policy: SourcePolicy,
    *,
    web_available: bool,
    interaction: InteractionKind | None = None,
) -> RetrievalDecision:
    information = interaction == "information" if interaction is not None else is_information_request(message)
    search_required = information and source_policy != "none"
    scopes: list[str] = []
    unavailable: list[str] = []
    if search_required and source_policy in {"atlas_only", "local_only", "local_and_external"}:
        scopes.extend(["thread", "atlas", "local_documents"])
    if search_required and source_policy in {"external_only", "local_and_external"}:
        scopes.append("academic")
        if web_available:
            scopes.append("web")
        else:
            unavailable.append("web")
    reason = (
        "信息型询问默认检索全部允许来源"
        if search_required else "闲聊或文本变换不需要检索" if not information else "来源策略禁止检索"
    )
    return RetrievalDecision(
        information_request=information, search_required=search_required, source_policy=source_policy,
        scopes=scopes, unavailable_scopes=unavailable, reason=reason,
    )
