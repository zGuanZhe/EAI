from __future__ import annotations


AGENT_TOOL_REGISTRY: dict[str, dict[str, str]] = {
    "get_thread_state": {"permission": "read", "label": "读取线程状态"},
    "get_context_index": {"permission": "read", "label": "读取上下文索引"},
    "get_atlas_overview": {"permission": "read", "label": "读取 Atlas 总览"},
    "search_atlas": {"permission": "read", "label": "检索 Atlas"},
    "get_paper": {"permission": "read", "label": "读取论文详情"},
    "get_relation_neighborhood": {"permission": "read", "label": "读取关系邻域"},
    "get_object_memory": {"permission": "read", "label": "读取对象记忆"},
    "get_canvas": {"permission": "read", "label": "读取 Canvas"},
    "diagnose_canvas": {"permission": "read", "label": "诊断 Canvas"},
    "propose_context_changes": {"permission": "preview", "label": "生成上下文变更集"},
    "propose_object_memory_changes": {"permission": "preview", "label": "生成对象记忆变更集"},
    "propose_atlas_candidate_changes": {"permission": "preview", "label": "生成候选论文变更集"},
    "propose_canvas_changes": {"permission": "preview", "label": "生成 Canvas 变更集"},
}


READ_TOOLS = {name for name, spec in AGENT_TOOL_REGISTRY.items() if spec["permission"] == "read"}
PREVIEW_TOOLS = {name for name, spec in AGENT_TOOL_REGISTRY.items() if spec["permission"] == "preview"}
