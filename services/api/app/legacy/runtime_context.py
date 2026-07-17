from __future__ import annotations

from typing import Any


def empty_agent_tool_context() -> dict[str, Any]:
    return {
        "observations": [],
        "thread_state": {},
        "context_index": {},
        "atlas": {},
        "papers": [],
        "paper_details": [],
        "relations": [],
        "object_memories": [],
        "canvas": {},
        "canvas_diagnosis": {},
    }


def record_agent_tool_result(
    context: dict[str, Any],
    *,
    tool: str,
    arguments: dict[str, Any],
    result: Any,
    source_ids: list[str],
) -> None:
    context["observations"].append(
        {
            "tool": tool,
            "arguments": arguments,
            "result": result,
            "source_ids": source_ids,
        }
    )
    if not isinstance(result, dict):
        return
    if tool == "get_thread_state":
        context["thread_state"] = result
    elif tool == "get_context_index":
        context["context_index"] = result
    elif tool == "get_atlas_overview":
        context["atlas"] = result
    elif tool == "search_atlas":
        _merge_papers(context["papers"], result.get("papers") or [])
    elif tool == "get_paper":
        _merge_papers(context["paper_details"], [result])
    elif tool == "get_relation_neighborhood":
        context["relations"].extend(result.get("relations") or [])
    elif tool == "get_object_memory":
        context["object_memories"].append(result)
    elif tool == "get_canvas":
        context["canvas"] = result
    elif tool == "diagnose_canvas":
        context["canvas_diagnosis"] = result


def build_fallback_agent_answer(
    context: dict[str, Any],
    *,
    visible_context_count: int,
) -> tuple[str, list[dict[str, Any]]]:
    atlas = context.get("atlas") if isinstance(context.get("atlas"), dict) else {}
    papers = context.get("papers") if isinstance(context.get("papers"), list) else []
    paper_count = int(atlas.get("paper_count") or 0)
    lines = [
        "模型通道当前不可用，我先基于本地 Atlas、线程和对象记忆完成了检索。",
        f"当前 Atlas 共 {paper_count} 篇论文，主对话长期资料 {visible_context_count} 项。",
    ]
    titles = [str(item.get("title") or "").strip() for item in papers if isinstance(item, dict)]
    titles = [title for title in titles if title]
    if titles:
        lines.append("本轮已找到的相关证据包括：" + "；".join(titles[:4]) + "。")
        lines.append("你可以先查看这些来源；配置模型后可继续生成综合判断和可确认修改。")
        next_actions = [
            {"id": "inspect_sources", "action": "inspect_sources", "label": "查看检索来源", "description": f"查看本轮找到的 {len(titles)} 项证据。"},
            {"id": "open_settings", "action": "open_settings", "label": "配置模型", "description": "配置 OpenRouter 或其他 OpenAI-compatible 通道。"},
        ]
    else:
        lines.append("本轮没有形成可引用的论文来源，可以先进入 Atlas 明确证据范围。")
        next_actions = [
            {"id": "open_atlas", "action": "open_atlas", "label": "打开 Atlas", "description": "浏览当前研究图谱并选择证据。"},
            {"id": "open_settings", "action": "open_settings", "label": "配置模型", "description": "配置 OpenRouter 或其他 OpenAI-compatible 通道。"},
        ]
    return "\n".join(lines), next_actions


def _merge_papers(target: list[dict[str, Any]], incoming: list[Any]) -> None:
    known = {str(item.get("id") or item.get("title") or "") for item in target if isinstance(item, dict)}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("title") or "")
        if not key or key in known:
            continue
        known.add(key)
        target.append(item)
