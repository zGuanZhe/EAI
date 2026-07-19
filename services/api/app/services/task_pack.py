from __future__ import annotations

import json
import re
from typing import Any

from ..schemas.models import (
    CanvasEdge,
    CanvasNode,
    ContextCard,
    ExportRequest,
    ExportResponse,
    FocusedObject,
    Message,
    ObjectMemory,
    ResearchTemplate,
    ResultCard,
    ResultPreviewRequest,
    ResultPreviewResponse,
    TaskPackPreviewRequest,
    TaskPackPreviewResponse,
    TaskPackRunRequest,
    TaskPackRunResponse,
    ThreadDoc,
    ToolRun,
)
from .atlas import AtlasService
from .provider import ProviderAdapter
from .thread_content import ThreadContentNotFoundError, new_id, scrub_refs, utc_now
from .workspace import WorkspaceService


class TaskPackError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


RESEARCH_TEMPLATES: dict[str, ResearchTemplate] = {
    "atlas_gap": ResearchTemplate(
        id="atlas_gap", title="研究空白分析", short_title="空白分析",
        description="基于当前 Atlas 对象、论证结构和选中材料，找出尚未被充分解释的问题、证据缺口和可切入方向。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["关键空白", "证据边界", "可执行切入点", "下一步任务"],
    ),
    "method_evolution": ResearchTemplate(
        id="method_evolution", title="方法演化梳理", short_title="方法演化",
        description="沿年份、路线和关系解释方法如何演化，区分技术继承、替代、组合与断裂。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["演化阶段", "方法差异", "关键转折", "可写作结构"],
    ),
    "relation_explain": ResearchTemplate(
        id="relation_explain", title="关系/路径解释", short_title="关系解释",
        description="解释论文、关系或路径之间为什么相连，以及这些连接对当前研究问题意味着什么。",
        recommended_for=["relation", "path", "paper"],
        output_focus=["关系类型", "连接理由", "论证价值", "疑点与反例"],
    ),
    "candidate_audit": ResearchTemplate(
        id="candidate_audit", title="候选论文/关系审查", short_title="候选审查",
        description="审查候选论文或关系是否值得纳入当前上下文，给出保留、降级、暂缓或剔除建议。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["纳入判断", "证据质量", "风险", "后续核查任务"],
    ),
}

TEMPLATE_INSTRUCTIONS = {
    "atlas_gap": "请做研究空白分析：先说明当前材料覆盖了什么，再指出仍未被充分回答的问题。区分事实证据、个人判断和你的推测；最后给出 3-5 个可以立刻推进的下一步任务。",
    "method_evolution": "请梳理方法演化：按时间、路线或关系链拆成阶段，说明每一阶段解决了什么、遗留了什么、与下一阶段如何相连。输出要能直接转化为综述段落结构。",
    "relation_explain": "请解释关系或路径：说明连接的依据、可能的关系类型、对当前研究问题的论证价值，以及需要警惕的过度解释。",
    "candidate_audit": "请审查候选对象：判断每篇论文或每条关系是否值得纳入当前上下文，给出保留/降级/暂缓/剔除建议，并列出需要人工核查的证据。",
}

NODE_LABELS = {"question": "问题", "hypothesis": "假设", "conclusion": "结论", "material": "材料", "task": "任务"}
EDGE_LABELS = {"supports": "支持", "challenges": "质疑", "leads_to": "推出", "requires": "需要"}


def parse_result_payload(raw_text: str) -> dict[str, Any] | None:
    match = re.search(r"```(?:json|eai-result/v1)?\s*([\s\S]*?)```", raw_text)
    candidates = [match.group(1)] if match else []
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            continue
    return None


class TaskPackService:
    def __init__(self, workspace: WorkspaceService, atlas: AtlasService, provider: ProviderAdapter) -> None:
        self.workspace = workspace
        self.atlas = atlas
        self.provider = provider

    def _load(self, thread_id: str) -> ThreadDoc:
        try:
            return self.workspace.load_thread(thread_id)
        except KeyError as exc:
            raise ThreadContentNotFoundError("thread not found") from exc

    @staticmethod
    def _append_message(thread: ThreadDoc, message: Message) -> None:
        message.id = message.id or new_id("msg")
        message.created_at = message.created_at or utc_now()
        message.refs = scrub_refs(message.refs)
        thread.messages.append(message)

    @staticmethod
    def _card_ref(card: ContextCard) -> tuple[str, str, str] | None:
        atlas_id = card.source_ref.get("atlas_id")
        if not atlas_id:
            return None
        if card.type == "paper" and card.source_ref.get("paper_id"):
            return str(atlas_id), "paper", str(card.source_ref["paper_id"])
        if card.type == "relation" and card.source_ref.get("relation_id"):
            return str(atlas_id), "relation", str(card.source_ref["relation_id"])
        if card.type == "path":
            object_id = card.source_ref.get("path_id")
            if not object_id and card.source_ref.get("paper_ids"):
                object_id = "path_" + "_".join(str(item) for item in card.source_ref["paper_ids"])
            if object_id:
                return str(atlas_id), "path", str(object_id)
        return None

    @staticmethod
    def _render_memory(memory: ObjectMemory | None) -> str:
        if not memory:
            return ""
        parts = []
        if memory.star: parts.append("- personal_star: true")
        if memory.maturity: parts.append(f"- maturity: {memory.maturity}/5")
        if memory.tags: parts.append(f"- tags: {', '.join(memory.tags)}")
        for field, label in (
            ("judgement", "personal_judgement"), ("note", "personal_note"),
            ("core_innovation", "core_innovation"), ("core_technology", "core_technology"),
            ("evidence", "evidence"), ("limitations", "limitations"),
            ("reusable_insight", "reusable_insight"),
        ):
            value = getattr(memory, field)
            if value: parts.append(f"- {label}: {value}")
        if memory.reading_questions: parts.append(f"- reading_questions: {', '.join(memory.reading_questions)}")
        return "\n".join(parts)

    def _render_card(self, card: ContextCard, index: int) -> str:
        ref = self._card_ref(card)
        memory = self.atlas.effective_object_memory(*ref) if ref else None
        memory_text = self._render_memory(memory)
        return (
            f"### {index}. [{card.type}] {card.title}\n"
            f"- 来源引用: `{json.dumps(card.source_ref, ensure_ascii=False)}`\n"
            f"- 估算 token: {card.token_estimate}\n"
            f"- 摘要: {card.summary or '(无)'}\n"
            + (f"{memory_text}\n" if memory_text else "")
        )

    @staticmethod
    def _render_canvas(thread: ThreadDoc) -> str:
        nodes, edges = thread.canvas.nodes, thread.canvas.edges
        if not nodes:
            return "(暂无 Canvas 节点)"
        by_id = {node.id: node for node in nodes}
        referenced = {edge.target for edge in edges}
        roots = [node for node in nodes if node.type == "question"] or [node for node in nodes if node.id not in referenced] or nodes
        lines: list[str] = []
        seen: set[str] = set()

        def walk(node: CanvasNode, depth: int = 0) -> None:
            if node.id in seen:
                return
            seen.add(node.id)
            indent = "  " * depth
            meta = []
            if node.status: meta.append(f"状态: {node.status}")
            if node.priority is not None: meta.append(f"优先级: {node.priority}")
            suffix = f" ({'; '.join(meta)})" if meta else ""
            lines.append(f"{indent}- {NODE_LABELS.get(node.type, node.type)}: {node.title}{suffix}")
            if node.body: lines.append(f"{indent}  - 说明: {node.body}")
            for edge in edges:
                if edge.source == node.id and edge.target in by_id:
                    label = edge.label if edge.label in EDGE_LABELS else "supports"
                    child = by_id[edge.target]
                    lines.append(f"{indent}  - {EDGE_LABELS.get(label, label)} -> {NODE_LABELS.get(child.type, child.type)}: {child.title}")
                    walk(child, depth + 2)

        for root in roots: walk(root)
        remaining = [node for node in nodes if node.id not in seen]
        if remaining:
            lines.append("\n未连接节点:")
            for node in remaining:
                lines.append(f"- {NODE_LABELS.get(node.type, node.type)}: {node.title}" + (f" — {node.body}" if node.body else ""))
        return "\n".join(lines)

    def _render_project(self, thread: ThreadDoc) -> str:
        if not thread.project_id:
            return "- 成果项目: 未归档成果"
        try:
            project = self.workspace.load_project(thread.project_id)
        except (KeyError, ValueError):
            return f"- 成果项目: {thread.project_id}"
        lines = [f"- 成果项目: {project.title}"]
        if project.goal: lines.append(f"- 项目目标: {project.goal}")
        return "\n".join(lines)

    def _render_focused(self, focused: FocusedObject | None, thread: ThreadDoc) -> str:
        if not focused or not (focused.title or focused.id):
            return "(当前没有聚焦对象，默认使用 Canvas 和已选上下文。)"
        labels = {"paper": "论文", "relation": "关系", "path": "路径", "file": "文件", "canvas_node": "Canvas 节点"}
        lines = [
            f"- 类型: {labels.get(focused.type or '', focused.type or '对象')}",
            f"- 标题: {focused.title or focused.id}", f"- 对象 ID: {focused.id or '(无)'}",
        ]
        if focused.summary: lines.append(f"- 摘要: {focused.summary}")
        if focused.source_ref: lines.append(f"- 来源引用: `{json.dumps(focused.source_ref, ensure_ascii=False)}`")
        if focused.type in {"paper", "relation", "path", "file"} and focused.id:
            atlas_id = str(focused.source_ref.get("atlas_id") or thread.active_atlas_id)
            memory = self._render_memory(self.atlas.effective_object_memory(atlas_id, str(focused.type), str(focused.id)))
            if memory: lines.extend(["### 聚焦对象记忆", memory])
        return "\n".join(lines)

    def list_templates(self) -> list[ResearchTemplate]:
        return list(RESEARCH_TEMPLATES.values())

    def build_task_pack(self, thread: ThreadDoc, payload: TaskPackPreviewRequest) -> TaskPackPreviewResponse:
        template = RESEARCH_TEMPLATES.get(payload.template_id)
        if not template:
            raise TaskPackError(400, "unknown research template")
        cards = [card for card in thread.context_cards if card.selected_for_export or not payload.selected_only]
        warnings = []
        if not cards: warnings.append("当前没有选中的 Context card，Task Pack 将主要依赖 Canvas 和聚焦对象。")
        if not thread.canvas.nodes: warnings.append("当前 Context Canvas 为空，建议先至少保留一个问题节点。")
        instruction = payload.instruction_override or TEMPLATE_INSTRUCTIONS[template.id]
        markdown = "\n".join([
            f"# EAI Task Pack：{template.title}", "", "## 当前研究问题",
            f"- 线程: {thread.title}", f"- 目标: {thread.goal or '(未填写目标)'}",
            self._render_project(thread), f"- 当前 Atlas: {thread.active_atlas_id}", "",
            "## 聚焦对象", self._render_focused(payload.focused_object, thread), "",
            "## Context Canvas 论证结构", self._render_canvas(thread), "", "## 选中上下文材料",
            "\n".join(self._render_card(card, index + 1) for index, card in enumerate(cards)) or "(暂无选中的上下文卡片)",
            "", "## 本次研究动作", f"- 模板: {template.title}",
            f"- 输出重点: {'、'.join(template.output_focus)}", instruction, "", "## 输出要求",
            "- 使用中文输出。", "- 明确区分已有证据、个人判断和推测。",
            "- 不要假装读过未提供的论文全文；缺失信息请标注“未知”。",
            "- 请给出可沉淀到 Context Canvas 的结论和下一步任务。", "", "## 返回格式",
            "如适合结构化返回，请包含一个 eai-result/v1 JSON 代码块：", "```json",
            json.dumps({
                "schema_version": "eai-result/v1", "summary": "...",
                "findings": [{"title": "...", "body": "..."}], "candidate_papers": [],
                "candidate_relations": [], "next_tasks": [{"title": "...", "body": "..."}],
            }, ensure_ascii=False, indent=2), "```",
        ])
        return TaskPackPreviewResponse(
            template_id=template.id, title=template.title, markdown=markdown,
            token_estimate=max(120, len(markdown) // 3), included_cards=len(cards), warnings=warnings,
        )

    def preview_task_pack(self, thread_id: str, payload: TaskPackPreviewRequest) -> TaskPackPreviewResponse:
        return self.build_task_pack(self._load(thread_id), payload)

    @staticmethod
    def _item_text(item: Any) -> tuple[str, str]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("summary") or item.get("task") or item.get("content") or "未命名")
            body = str(item.get("body") or item.get("detail") or item.get("rationale") or item.get("note") or "")
            return title, body
        return str(item), ""

    def preview_result(self, thread_id: str, payload: ResultPreviewRequest) -> ResultPreviewResponse:
        thread = self._load(thread_id)
        raw = payload.raw_text.strip()
        if not raw: raise TaskPackError(400, "empty result text")
        parsed = parse_result_payload(raw)
        preview = ResultCard(
            id=new_id("result"), title=(parsed.get("summary") if parsed else None) or "Codex 返回",
            raw_text=raw, parsed_json=parsed, created_at=utc_now(),
        )
        nodes, edges = [], []
        question = next((node for node in thread.canvas.nodes if node.type == "question"), None)
        if parsed:
            conclusion_ids = []
            for index, item in enumerate(parsed.get("findings") or []):
                title, body = self._item_text(item)
                node = CanvasNode(id=new_id("conclusion"), type="conclusion", title=title[:120] or "未命名结论", body=body, x=520, y=110 + index * 118)
                nodes.append(node); conclusion_ids.append(node.id)
                if question: edges.append(CanvasEdge(id=new_id("edge"), source=question.id, target=node.id, label="leads_to"))
            for index, item in enumerate(parsed.get("next_tasks") or []):
                title, body = self._item_text(item)
                node = CanvasNode(id=new_id("task"), type="task", title=title[:120] or "未命名任务", body=body, x=760, y=110 + index * 118, status="todo", priority=1)
                nodes.append(node)
                source = conclusion_ids[0] if conclusion_ids else question.id if question else ""
                if source: edges.append(CanvasEdge(id=new_id("edge"), source=source, target=node.id, label="requires"))
        return ResultPreviewResponse(result_card_preview=preview, canvas_nodes=nodes, canvas_edges=edges)

    def run_task_pack(self, thread_id: str, payload: TaskPackRunRequest) -> TaskPackRunResponse:
        thread = self._load(thread_id)
        preview = self.build_task_pack(thread, payload)
        raw_text, used_model = self.provider.run_task_pack(preview.markdown, payload.model)
        provider = self.provider.configured_provider() or payload.provider or "openai"
        result_preview = self.preview_result(thread_id, ResultPreviewRequest(raw_text=raw_text))
        tool_run = ToolRun(
            id=new_id("tool"), tool="research_template_run", status="preview",
            summary=f"{preview.title} API 返回已生成预览", created_at=utc_now(),
            template_id=preview.template_id, mode="api", provider=provider, model=used_model,
            token_estimate=preview.token_estimate, input_summary=f"{preview.included_cards} 张上下文卡",
        )
        thread.tool_runs.insert(0, tool_run)
        self._append_message(thread, Message(
            role="tool", kind="task_pack", content=f"已通过 API 发送「{preview.title}」，返回预览待确认。",
            status="preview", surface="thread",
            refs={"template_id": preview.template_id, "provider": provider, "model": used_model, "included_cards": preview.included_cards, "token_estimate": preview.token_estimate},
            linked_tool_run_id=tool_run.id,
        ))
        self.workspace.write_thread(thread)
        return TaskPackRunResponse(
            result_card_preview=result_preview.result_card_preview,
            canvas_nodes=result_preview.canvas_nodes, canvas_edges=result_preview.canvas_edges, tool_run=tool_run,
        )

    def export_thread(self, thread_id: str, payload: ExportRequest) -> ExportResponse:
        thread = self._load(thread_id)
        cards = [card for card in thread.context_cards if card.selected_for_export or not payload.selected_only]
        token_estimate = sum(card.token_estimate for card in cards)
        markdown = "\n".join([
            f"# EAI Task Pack：{thread.title}", "", "## 任务目标", thread.goal or "(未填写目标)", "",
            "## 当前思维板", self._render_canvas(thread), "", "## 选中上下文",
            "\n".join(self._render_card(card, index + 1) for index, card in enumerate(cards)) or "(暂无选中的上下文卡片)",
            "", "## 输出要求", payload.instruction, "", "## 返回格式",
            "如果适合结构化返回，请包含一个 JSON 代码块:", "```json",
            json.dumps({
                "schema_version": "eai-result/v1", "summary": "...", "findings": [],
                "candidate_papers": [], "candidate_relations": [], "next_tasks": [],
            }, ensure_ascii=False, indent=2), "```",
        ])
        tool_run = ToolRun(
            id=new_id("tool"), tool="export_task_pack", status="done",
            summary=f"复制通用 Task Pack：{len(cards)} 张上下文卡片", created_at=utc_now(),
            mode="copy", token_estimate=token_estimate, input_summary=f"{len(cards)} 张上下文卡片",
        )
        thread.tool_runs.insert(0, tool_run)
        self._append_message(thread, Message(
            role="tool", kind="task_pack",
            content=f"已复制通用 Task Pack：{len(cards)} 张上下文卡片，约 {token_estimate} tokens。",
            status="done", surface="thread",
            refs={"selected_only": payload.selected_only, "exported_cards": len(cards), "token_estimate": token_estimate},
            linked_tool_run_id=tool_run.id,
        ))
        self.workspace.write_thread(thread)
        return ExportResponse(markdown=markdown, exported_cards=len(cards), token_estimate=token_estimate)
