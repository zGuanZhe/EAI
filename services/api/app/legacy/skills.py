from __future__ import annotations

from typing import Any


SKILL_REGISTRY: dict[str, dict[str, Any]] = {
    "atlas_evidence_search": {
        "version": "1.0",
        "label": "Atlas 证据检索",
        "description": "围绕研究问题检索论文、路线和关系，并区分支持、反证与待查证证据。",
        "allowed_tools": {
            "get_atlas_overview",
            "search_atlas",
            "get_paper",
            "get_relation_neighborhood",
            "get_object_memory",
            "propose_context_changes",
            "propose_atlas_candidate_changes",
        },
    },
    "paper_card_completion": {
        "version": "1.0",
        "label": "论文卡补全",
        "description": "读取论文和个人记忆，生成需要用户确认的字段级补全建议。",
        "allowed_tools": {
            "search_atlas",
            "get_paper",
            "get_relation_neighborhood",
            "get_object_memory",
            "propose_object_memory_changes",
        },
    },
    "canvas_argument_builder": {
        "version": "1.0",
        "label": "Canvas 论证编排",
        "description": "检查问题、材料、假设、结论和任务之间的结构缺口，并生成可确认修改。",
        "allowed_tools": {
            "get_thread_state",
            "get_context_index",
            "get_canvas",
            "diagnose_canvas",
            "propose_canvas_changes",
        },
    },
}


def choose_skills(user_text: str, *, has_canvas: bool, has_context: bool) -> list[dict[str, Any]]:
    text = user_text.lower()
    selected: list[tuple[str, str]] = []
    if any(word in text for word in ["canvas", "论证", "假设", "结论", "结构", "任务"]):
        selected.append(("canvas_argument_builder", "问题涉及论证结构或 Canvas。"))
    if any(word in text for word in ["论文卡", "论文", "创新", "技术", "方法", "记忆", "笔记"]):
        selected.append(("paper_card_completion", "问题需要读取或补全论文级信息。"))
    if any(word in text for word in ["证据", "搜索", "检索", "路线", "关系", "趋势", "候选", "更新"]):
        selected.append(("atlas_evidence_search", "问题需要在 Atlas 中调查证据。"))
    if not selected:
        if has_canvas or has_context:
            selected.append(("canvas_argument_builder", "当前线程已有研究结构，先检查其完整性。"))
        selected.append(("atlas_evidence_search", "默认从当前 Atlas 获取可核查证据。"))

    result = []
    for skill_id, reason in selected:
        if any(item["id"] == skill_id for item in result):
            continue
        spec = SKILL_REGISTRY[skill_id]
        result.append({"id": skill_id, "label": spec["label"], "version": spec["version"], "reason": reason})
        if len(result) == 2:
            break
    return result


def allowed_tools_for_skills(skills: list[dict[str, Any]]) -> set[str]:
    allowed = {"get_thread_state", "get_context_index"}
    for item in skills:
        spec = SKILL_REGISTRY.get(str(item.get("id")))
        if spec:
            allowed.update(spec["allowed_tools"])
    return allowed
