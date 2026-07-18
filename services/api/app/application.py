from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .legacy.models import (
    ActionProposal,
    AgentCitation,
    AgentContextSnapshot,
    AgentRun,
    AgentStep,
    AgentToolCall,
    ChangeOperation,
    ChangeSet,
    ChangeSetConfirmRequest,
    ProposalDiffItem,
)
from .legacy.skills import allowed_tools_for_skills, choose_skills
from .legacy.tools import AGENT_TOOL_REGISTRY, PREVIEW_TOOLS, READ_TOOLS
from .legacy.runtime_context import (
    build_fallback_agent_answer,
    empty_agent_tool_context,
    record_agent_tool_result,
)
from .agent_v2.models import (
    AgentTurnRequest as AgentV2TurnRequest,
    ApprovalResolveRequest as AgentV2ApprovalResolveRequest,
    OperationBatch as AgentV2OperationBatch,
)
from .agent_v2.runtime import AgentRuntimeV2, RuntimeDependencies
from .agent_v2.provider import request_text_stream, request_tool_decision
from .agent_v2.store import RuntimeStore
from .campaign.router import create_campaign_router
from .campaign.models import BranchCompareRequest
from .campaign.service import CampaignService
from .core.paths import resolve_runtime_paths
from .core.secrets import read_secret_data, secret_candidates
from .core.storage import atomic_write_json_file, backup_file, read_json_file
from .factory import create_app
from .routers.system import create_system_router
from .routers.drafts import create_draft_router
from .routers.atlas import create_atlas_router
from .routers.agent_v2 import create_agent_v2_router
from .routers.workspace import create_workspace_router
from .repositories.personal import safe_document_path, safe_object_memory_path
from .services.projection import ProjectionService
from .services.drafts import ThreadDraftService
from .services.container import AppServices
from .services.atlas import AtlasService
from .services.agent_api import AgentApiService
from .services.workspace import WorkspaceService
from .research.context import build_research_state, evidence_bundle_to_sources, search_for_agent
from .research.enrichment import KnowledgeEnrichmentService
from .research.page_preview import PagePreviewService
from .research.router import create_research_router
from .research.store import ResearchStore

SERVICE_VERSION = "0.5.0"
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATHS = resolve_runtime_paths(DEFAULT_ROOT)
ROOT = RUNTIME_PATHS.root
WEB_DATA_DIR = RUNTIME_PATHS.atlas_cache_dir
PERSONAL_DIR = RUNTIME_PATHS.personal_dir
THREADS_DIR = RUNTIME_PATHS.threads_dir
BACKUPS_DIR = RUNTIME_PATHS.backups_dir
PROJECTS_DIR = RUNTIME_PATHS.projects_dir
OBJECTS_DIR = RUNTIME_PATHS.objects_dir
ATLAS_UPDATES_DIR = RUNTIME_PATHS.atlas_updates_dir
LAB_RUNS_DIR = RUNTIME_PATHS.lab_runs_dir
RUNTIME_V2_DIR = PERSONAL_DIR.parent / "runtime"
APP_SERVICES = AppServices()

SECRET_CANDIDATES = secret_candidates()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    OBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    ATLAS_UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    LAB_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (PERSONAL_DIR / "transactions").mkdir(parents=True, exist_ok=True)


def get_research_store() -> ResearchStore:
    expected_dir = (PERSONAL_DIR.parent / "research").resolve()
    return APP_SERVICES.research_store(expected_dir, WEB_DATA_DIR, PERSONAL_DIR)


def get_knowledge_enrichment() -> KnowledgeEnrichmentService:
    store = get_research_store()
    return APP_SERVICES.enrichment(
        store,
        start_initial_sync=(
            os.environ.get("EAI_DESKTOP_MODE") == "1"
            and os.environ.get("EAI_DISABLE_AUTO_KNOWLEDGE_SYNC") != "1"
        ),
    )


def get_page_preview_service() -> PagePreviewService:
    return APP_SERVICES.page_preview(get_research_store())


def reset_app_services() -> None:
    APP_SERVICES.close()


def reset_agent_services() -> None:
    APP_SERVICES.reset_runtime()


def get_workspace_service() -> WorkspaceService:
    return WorkspaceService(
        get_research_store(),
        projects_dir=PROJECTS_DIR,
        threads_dir=THREADS_DIR,
        backups_dir=BACKUPS_DIR,
    )


def get_atlas_service() -> AtlasService:
    return AtlasService(
        get_research_store(),
        atlas_cache_dir=WEB_DATA_DIR,
        personal_dir=PERSONAL_DIR,
        objects_dir=OBJECTS_DIR,
        atlas_updates_dir=ATLAS_UPDATES_DIR,
        paper_model=call_openai_task_pack,
    )


def read_json(path: Path) -> Any:
    return read_json_file(path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_json_file(path, data, ensure_dirs)


def projection_target(path: Path) -> str:
    resolved = path.resolve()
    personal = PERSONAL_DIR.resolve()
    if not resolved.is_relative_to(personal):
        raise ValueError("projection target must remain inside personal data")
    return resolved.relative_to(personal).as_posix()


def save_projected_record(kind: str, record_id: str, payload: dict[str, Any], path: Path) -> dict[str, Any]:
    store = get_research_store()
    saved = store.save_record(kind, record_id, payload, projection_target=projection_target(path))
    ProjectionService(store, PERSONAL_DIR).replay(entity_kind=kind, entity_id=record_id, limit=20)
    return saved


def delete_projected_record(kind: str, record_id: str, path: Path) -> None:
    store = get_research_store()
    store.delete_record(kind, record_id, projection_target=projection_target(path))
    ProjectionService(store, PERSONAL_DIR).replay(entity_kind=kind, entity_id=record_id, limit=20)


def backup_thread_file(path: Path) -> None:
    backup_file(path, BACKUPS_DIR)


def backup_deleted_file(path: Path, kind: str) -> None:
    backup_file(path, BACKUPS_DIR, prefix=f"deleted-{kind}-")


def slug_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def safe_thread_path(thread_id: str) -> Path:
    return safe_document_path(THREADS_DIR, thread_id, "thread")


def safe_project_path(project_id: str) -> Path:
    return safe_document_path(PROJECTS_DIR, project_id, "project")


def safe_object_path(atlas_id: str, object_type: str, object_id: str) -> Path:
    return safe_object_memory_path(OBJECTS_DIR, atlas_id, object_type, object_id)


def safe_atlas_update_path(atlas_id: str) -> Path:
    return safe_document_path(ATLAS_UPDATES_DIR, atlas_id, "atlas")


def safe_lab_run_path(run_id: str) -> Path:
    return safe_document_path(LAB_RUNS_DIR, run_id, "lab run")


from .schemas.models import (
    NodeType,
    SurfaceType,
    ContextCard,
    CanvasNode,
    CanvasEdge,
    CanvasState,
    ProjectDoc,
    ProjectCreate,
    ProjectUpdate,
    ObjectMemory,
    ResultCard,
    Message,
    ToolRun,
    ToolRunCreate,
    MessageCreate,
    ThreadChatRequest,
    ThreadChatResponse,
    ThreadChatStartResponse,
    ThreadChatCompleteRequest,
    ThreadChatRetryRequest,
    MessageUpdate,
    AgentRunStartResponse,
    AgentRunCompleteRequest,
    AgentRunResponse,
    ProposalConfirmResponse,
    ChangeSetResponse,
    ContextInjectionRequest,
    LabRunStatus,
    LabStageStatus,
    LabStage,
    LabArtifact,
    LabFinding,
    LabMessage,
    LabRun,
    LabRunCreate,
    LabRunUpdate,
    LabMessageCreate,
    LabTaskPackPreviewRequest,
    LabTaskPackPreviewResponse,
    LabTaskPackRunRequest,
    LabResultPreviewRequest,
    LabResultConfirmRequest,
    LabResultApplyItem,
    LabResultPreviewResponse,
    LabTaskPackRunResponse,
    ThreadDoc,
    ThreadCreate,
    ThreadUpdate,
    ExportRequest,
    ExportResponse,
    ResultPreviewRequest,
    ResultPreviewResponse,
    FocusedObject,
    ResearchTemplate,
    TaskPackPreviewRequest,
    TaskPackPreviewResponse,
    TaskPackRunRequest,
    TaskPackRunResponse,
    CandidateStatus,
    AtlasUpdateAction,
    AtlasUpdateCandidate,
    AtlasUpdateDoc,
    AtlasCandidateUpdate,
)

@asynccontextmanager
async def app_lifespan(_app):
    ensure_dirs()
    if not get_research_store().read_only:
        APP_SERVICES.projection(get_research_store()).replay(limit=500)
        get_agent_v2_runtime()
        get_campaign_service().mark_incomplete_interrupted()
    try:
        yield
    finally:
        reset_app_services()


app = create_app(lifespan=app_lifespan)
app.include_router(
    create_system_router(
        service_version=SERVICE_VERSION,
        root=ROOT,
        personal_dir=PERSONAL_DIR,
        atlas_cache_dir=WEB_DATA_DIR,
        secret_candidates=SECRET_CANDIDATES,
        research_status=lambda: get_research_store().compatibility_status(),
    )
)
app.include_router(create_draft_router(lambda: ThreadDraftService(get_research_store())))
app.include_router(create_workspace_router(get_workspace_service))
app.include_router(create_atlas_router(get_atlas_service))


def load_thread(thread_id: str) -> ThreadDoc:
    try:
        return get_workspace_service().load_thread(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def write_thread(doc: ThreadDoc, backup: bool = True) -> ThreadDoc:
    return get_workspace_service().write_thread(doc, backup=backup)


def load_project(project_id: str) -> ProjectDoc:
    try:
        return get_workspace_service().load_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def write_project(doc: ProjectDoc) -> ProjectDoc:
    return get_workspace_service().write_project(doc)


def load_object_memory(atlas_id: str, object_type: str, object_id: str) -> ObjectMemory | None:
    return get_atlas_service().load_object_memory(atlas_id, object_type, object_id)


def effective_object_memory(atlas_id: str, object_type: str, object_id: str) -> ObjectMemory | None:
    return get_atlas_service().effective_object_memory(atlas_id, object_type, object_id)


def write_object_memory(atlas_id: str, object_type: str, object_id: str, memory: ObjectMemory) -> ObjectMemory:
    return get_atlas_service().write_object_memory(atlas_id, object_type, object_id, memory)


def load_atlas_updates(atlas_id: str) -> AtlasUpdateDoc:
    return get_atlas_service().load_updates(atlas_id)


def write_atlas_updates(doc: AtlasUpdateDoc) -> AtlasUpdateDoc:
    return get_atlas_service().write_updates(doc)


def load_lab_run(run_id: str) -> LabRun:
    data = get_research_store().get_record("lab_run", run_id)
    if data is None:
        data = read_json(safe_lab_run_path(run_id))
        get_research_store().save_record("lab_run", run_id, data)
    return LabRun.model_validate(data)


def write_lab_run(run: LabRun) -> LabRun:
    get_research_store().ensure_writable()
    run = LabRun.model_validate(run.model_dump(mode="json") if isinstance(run, LabRun) else run)
    run.updated_at = utc_now()
    save_projected_record("lab_run", run.id, run.model_dump(mode="json"), safe_lab_run_path(run.id))
    return run


def lab_run_summary(run: LabRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "thread_id": run.thread_id,
        "title": run.title,
        "goal": run.goal,
        "hypothesis": run.hypothesis,
        "status": run.status,
        "updated_at": run.updated_at,
        "stage_count": len(run.stages),
        "artifact_count": len(run.artifacts),
        "finding_count": len(run.findings),
        "linked_canvas_nodes": run.linked_canvas_nodes,
    }


def append_lab_message(run: LabRun, message: LabMessage) -> LabMessage:
    item = message.model_copy()
    item.id = item.id or slug_id("lab_msg")
    item.created_at = item.created_at or utc_now()
    item.content = safe_message_content(item.content, 3000)
    item.status = safe_run_text(item.status, 32) or "done"
    item.model = safe_run_text(item.model, 120) or None
    run.messages.append(item)
    return item


def atlas_bundle_for_update(atlas_id: str) -> dict[str, Any]:
    try:
        return get_atlas_service().bundle_for_update(atlas_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


def safe_id_or_slug(value: Any, prefix: str) -> str:
    text = safe_run_text(value, 120)
    if text and re.fullmatch(r"[A-Za-z0-9_.:-]+", text):
        return text
    return slug_id(prefix)


def safe_run_text(value: Any, limit: int = 240) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", " ", str(value)).strip()
    return compact[:limit]


def safe_markdown_text(value: Any, limit: int = 3000) -> str:
    if not value:
        return ""
    text = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in {"\n", "\t"} or ord(char) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text[:limit]


def safe_message_content(value: str | None, limit: int = 1200) -> str:
    if not value:
        raise HTTPException(status_code=400, detail="message content is empty")
    text = str(value).replace("\x00", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message content is empty")
    return text[:limit]


def title_from_question(text: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:36] or "新的研究问题"


def should_promote_thread_question(doc: ThreadDoc) -> bool:
    has_user_message = any(message.role == "user" for message in doc.messages)
    default_titles = {"Untitled research thread", "New research thread", "新的研究问题"}
    default_goals = {"", "围绕当前成果目标收集图谱证据，并导出可执行上下文。"}
    return (not has_user_message) and doc.title in default_titles and doc.goal in default_goals


def scrub_refs(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("key", "secret", "token", "password")):
                continue
            cleaned[str(key)[:80]] = scrub_refs(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_refs(item) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return safe_run_text(value, 240) if isinstance(value, str) else value
    return str(value)[:120]


THREAD_CHAT_ACTIONS = {
    "open_atlas": ("去 Atlas 找证据", "打开当前 Atlas，选择 3-5 个能支撑论点的材料。"),
    "add_evidence_hint": ("选择证据", "根据当前问题，在 Atlas 中优先找核心论文、关键关系和反例。"),
    "seed_canvas": ("生成 Canvas 初稿", "把当前问题整理成问题、假设、材料和任务的结构。"),
    "preview_task_pack": ("预览 Task Pack", "根据已选材料和 Canvas 结构生成可复制给 Codex 的上下文包。"),
    "open_templates": ("打开研究动作模板", "选择空白分析、方法演化、关系解释或候选审查。"),
    "paste_codex_return": ("粘贴 Codex 返回", "把外部 Codex 的回答放入预览，确认后再写回。"),
}


def clean_thread_suggestions(raw: Any, doc: ThreadDoc) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        raw = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = safe_run_text(item.get("action") or item.get("id"), 80)
        if action not in THREAD_CHAT_ACTIONS:
            continue
        default_label, default_description = THREAD_CHAT_ACTIONS[action]
        confidence = item.get("confidence", 0.7)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.7
        suggestions.append(
            {
                "id": safe_run_text(item.get("id"), 80) or f"{action}_{len(suggestions) + 1}",
                "label": safe_run_text(item.get("label"), 40) or default_label,
                "description": safe_run_text(item.get("description"), 160) or default_description,
                "action": action,
                "payload": scrub_refs(item.get("payload") or {"thread_id": doc.id, "atlas_id": doc.active_atlas_id}),
                "confidence": confidence,
            }
        )
        if len(suggestions) >= 3:
            break
    return suggestions


def fallback_thread_suggestions(doc: ThreadDoc) -> list[dict[str, Any]]:
    if not doc.context_cards:
        actions = ["open_atlas", "add_evidence_hint", "open_templates"]
    elif not doc.canvas.nodes or len(doc.canvas.nodes) <= 1:
        actions = ["seed_canvas", "preview_task_pack", "open_templates"]
    else:
        actions = ["preview_task_pack", "paste_codex_return", "open_templates"]
    return clean_thread_suggestions([{"action": action} for action in actions], doc)


def thread_chat_steps(active: str = "context", failed: str | None = None) -> list[dict[str, str]]:
    order = [
        ("context", "读取线程上下文"),
        ("atlas_canvas", "整理 Atlas / Canvas"),
        ("reply", "生成回复"),
        ("suggestions", "生成建议动作"),
    ]
    if active == "done":
        return [{"id": step_id, "label": label, "status": "done"} for step_id, label in order]
    active_index = next((index for index, item in enumerate(order) if item[0] == active), len(order) - 1)
    failed_index = next((index for index, item in enumerate(order) if item[0] == failed), None)
    steps = []
    for index, (step_id, label) in enumerate(order):
        if failed_index is not None:
            if index < failed_index:
                status = "done"
            elif index == failed_index:
                status = "failed"
            else:
                status = "pending"
        elif index < active_index:
            status = "done"
        elif index == active_index:
            status = "running"
        else:
            status = "pending"
        steps.append({"id": step_id, "label": label, "status": status})
    return steps


def thread_chat_context_summary(doc: ThreadDoc) -> dict[str, Any]:
    nodes = doc.canvas.nodes or []
    has_question = any(node.type == "question" for node in nodes)
    has_material = any(node.type == "material" for node in nodes)
    has_argument = any(node.type in {"hypothesis", "conclusion"} for node in nodes)
    agent_cards = [card for card in doc.context_cards if card.include_in_agent]
    export_cards = [card for card in doc.context_cards if card.selected_for_export]
    lab_cards = [card for card in doc.context_cards if card.include_in_lab]
    return {
        "project_id": doc.project_id or "unfiled",
        "thread_title": doc.title,
        "thread_goal": safe_run_text(doc.goal, 220),
        "atlas_id": doc.active_atlas_id,
        "context_cards": len(doc.context_cards),
        "agent_context_cards": len(agent_cards),
        "pinned_context_cards": len([card for card in agent_cards if card.pinned]),
        "selected_context_cards": len(export_cards),
        "lab_context_cards": len(lab_cards),
        "canvas_nodes": len(nodes),
        "canvas_ready": has_question and has_material and has_argument,
    }


def agent_context_cards(doc: ThreadDoc, limit: int = 12) -> list[ContextCard]:
    cards = [card for card in doc.context_cards if card.include_in_agent]
    return sorted(
        cards,
        key=lambda card: (
            0 if card.pinned else 1,
            -int(card.priority or 0),
            doc.context_cards.index(card),
        ),
    )[:limit]


def build_agent_context_snapshot(doc: ThreadDoc, attachments: list[dict[str, Any]] | None = None) -> AgentContextSnapshot:
    cards = agent_context_cards(doc, 100)
    selected: list[str] = []
    used_tokens = estimate_agent_tokens({"title": doc.title, "goal": doc.goal, "summary": doc.conversation_summary})
    budget = max(4000, int(os.environ.get("EAI_AGENT_INPUT_BUDGET", "24000")))
    for card in cards:
        estimate = max(card.token_estimate, estimate_agent_tokens({"title": card.title, "summary": card.summary, "note": card.agent_note}))
        if used_tokens + estimate > budget:
            continue
        selected.append(card.id)
        used_tokens += estimate
    clean_attachments = [scrub_refs(item) for item in (attachments or [])[:8] if isinstance(item, dict)]
    used_tokens += estimate_agent_tokens(clean_attachments)
    return AgentContextSnapshot(
        revision=doc.revision,
        context_card_ids=selected,
        pinned_card_ids=[card.id for card in cards if card.pinned and card.id in selected],
        turn_attachments=clean_attachments,
        estimated_tokens=min(used_tokens, budget),
        budget_tokens=budget,
    )


def compact_canvas_for_chat(doc: ThreadDoc) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": node.id,
                "type": node.type,
                "title": node.title,
                "body": safe_run_text(node.body, 220),
                "status": node.status,
                "priority": node.priority,
            }
            for node in doc.canvas.nodes[:20]
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "label": normalize_edge_label(edge.label)}
            for edge in doc.canvas.edges[:30]
        ],
    }


def memory_summary_for_card(card: ContextCard, atlas_id: str) -> dict[str, Any] | None:
    source = card.source_ref or {}
    object_type = ""
    object_id = ""
    if source.get("paper_id"):
        object_type, object_id = "paper", str(source["paper_id"])
    elif source.get("relation_id"):
        object_type, object_id = "relation", str(source["relation_id"])
    elif source.get("path_id"):
        object_type, object_id = "path", str(source["path_id"])
    if not object_type or not object_id:
        return None
    try:
        memory = effective_object_memory(str(source.get("atlas_id") or atlas_id), object_type, object_id)
    except HTTPException:
        return None
    if not memory:
        return None
    return {
        "judgement": safe_run_text(memory.judgement, 220),
        "note": safe_run_text(memory.note, 220),
        "tags": memory.tags[:8],
        "maturity": memory.maturity,
        "star": memory.star,
    }


def build_thread_chat_context(doc: ThreadDoc) -> dict[str, Any]:
    project_context: dict[str, Any] | None = None
    if doc.project_id:
        try:
            project = load_project(doc.project_id)
            project_context = {"id": project.id, "title": project.title, "goal": project.goal}
        except HTTPException:
            project_context = {"id": doc.project_id, "missing": True}

    atlas_context: dict[str, Any] = {"id": doc.active_atlas_id}
    try:
        bundle = atlas_bundle_for_update(doc.active_atlas_id)
        routes = bundle.get("routes", []) or []
        papers = bundle.get("papers", []) or []
        relations = bundle.get("relations", []) or []
        atlas_context.update(
            {
                "title": bundle.get("atlas", {}).get("title_cn") or bundle.get("atlas", {}).get("title") or doc.active_atlas_id,
                "paper_count": len(papers),
                "relation_count": len(relations),
                "routes": [
                    {
                        "id": route.get("id"),
                        "name": route.get("name_cn") or route.get("name") or route.get("id"),
                        "rationale": safe_run_text(route.get("rationale") or route.get("description"), 180),
                    }
                    for route in routes[:8]
                ],
            }
        )
    except HTTPException:
        atlas_context["missing"] = True

    cards = []
    for card in agent_context_cards(doc):
        cards.append(
            {
                "id": card.id,
                "type": card.type,
                "title": card.title,
                "summary": safe_run_text(card.summary, 260),
                "priority": card.priority,
                "pinned": card.pinned,
                "agent_note": safe_run_text(card.agent_note, 220),
                "selected_for_export": card.selected_for_export,
                "include_in_agent": card.include_in_agent,
                "include_in_lab": card.include_in_lab,
                "source_ref": scrub_refs(card.source_ref),
                "memory": memory_summary_for_card(card, doc.active_atlas_id),
            }
        )

    return {
        "thread": {"id": doc.id, "title": doc.title, "goal": doc.goal, "active_surface": doc.active_surface},
        "project": project_context,
        "atlas": atlas_context,
        "context_cards": cards,
        "canvas": compact_canvas_for_chat(doc),
        "recent_messages": [
            {
                "role": message.role,
                "kind": message.kind,
                "content": safe_run_text(message.content, 360),
                "created_at": message.created_at,
            }
            for message in doc.messages[-8:]
            if not (message.kind == "assistant_reply" and message.status in {"pending", "streaming"})
        ],
    }


def build_thread_chat_prompt(doc: ThreadDoc, user_message: str) -> str:
    context = build_thread_chat_context(doc)
    return "\n".join(
        [
            "# EAI-Desktop 线程对话",
            "",
            "你是嵌入 EAI-Desktop 的中文研究工作台协调者，不是泛泛聊天机器人。",
            "你的任务是帮助用户把研究问题推进到：Atlas 选证据、Context Canvas 编排、Task Pack 预览、Codex/API 返回预览确认、对象记忆沉淀。",
            "",
            "## 工作约束",
            "- 只基于下方给出的线程、Atlas、Context Cards、Canvas 和对象记忆回答。",
            "- 严格区分“已知证据”“个人判断”“需要查证”。",
            "- 不要声称读过未提供全文的论文；缺失信息写“需要原文确认”。",
            "- 不要直接写入 Canvas、对象记忆或结果卡；只能建议用户打开预览或确认流程。",
            "- 回复应简洁、可执行，最后最多给 3 个系统动作建议。",
            "",
            "## 当前系统上下文",
            json.dumps(context, ensure_ascii=False, indent=2)[:18000],
            "",
            "## 用户输入",
            user_message.strip(),
            "",
            "## 返回格式",
            "可以直接自然语言回答。若要弹出系统动作，请附带一个 fenced JSON 块，格式如下：",
            "```eai-thread-chat/v1",
            json.dumps(
                {
                    "content": "给用户看的中文回复正文",
                    "suggestions": [
                        {
                            "action": "open_atlas",
                            "label": "去 Atlas 找证据",
                            "description": "为什么此刻建议这么做",
                            "confidence": 0.8,
                            "payload": {},
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "允许的 action: " + ", ".join(THREAD_CHAT_ACTIONS.keys()),
        ]
    )


def parse_thread_chat_payload(raw_text: str, doc: ThreadDoc) -> tuple[str, list[dict[str, Any]]]:
    matches = re.findall(r"```(?:json|eai-thread-chat/v1)?\s*([\s\S]*?)```", raw_text)
    candidates = matches[:]
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            content = str(parsed.get("content") or parsed.get("answer") or parsed.get("message") or "").strip()
            suggestions = clean_thread_suggestions(parsed.get("suggestions"), doc)
            if content:
                return content[:2400], suggestions
    visible = re.sub(r"```(?:json|eai-thread-chat/v1)?\s*[\s\S]*?```", "", raw_text).strip() or raw_text.strip()
    return visible[:2400], []


def append_message(doc: ThreadDoc, message: Message) -> Message:
    item = message.model_copy()
    item.id = item.id or slug_id("msg")
    item.created_at = item.created_at or utc_now()
    item.content = safe_message_content(item.content, 3000)
    if item.refs:
        item.refs = scrub_refs(item.refs) if isinstance(item.refs, dict) else {}
    doc.messages.append(item)
    return item


def find_message(doc: ThreadDoc, message_id: str) -> Message:
    for message in doc.messages:
        if message.id == message_id:
            return message
    raise HTTPException(status_code=404, detail="message not found")


def update_assistant_chat_message(
    doc: ThreadDoc,
    assistant: Message,
    *,
    content: str,
    status: str,
    refs: dict[str, Any],
) -> Message:
    assistant.content = safe_message_content(content, 3000)
    assistant.status = safe_run_text(status, 32) or status
    assistant.refs = scrub_refs(refs)
    assistant.created_at = assistant.created_at or utc_now()
    return assistant


def agent_steps(active: str = "intent", failed: str | None = None) -> list[AgentStep]:
    order = [
        ("intent", "理解研究意图"),
        ("context", "装配线程与 Atlas 上下文"),
        ("tools", "执行白名单工具"),
        ("answer", "生成回答与提案"),
        ("confirm", "等待可视确认"),
    ]
    if active == "done":
        return [AgentStep(id=step_id, label=label, status="done") for step_id, label in order]
    active_index = next((index for index, item in enumerate(order) if item[0] == active), len(order) - 1)
    failed_index = next((index for index, item in enumerate(order) if item[0] == failed), None)
    steps: list[AgentStep] = []
    for index, (step_id, label) in enumerate(order):
        if failed_index is not None:
            status = "done" if index < failed_index else "failed" if index == failed_index else "pending"
        else:
            status = "done" if index < active_index else "running" if index == active_index else "pending"
        steps.append(AgentStep(id=step_id, label=label, status=status))
    return steps


def find_agent_run(doc: ThreadDoc, run_id: str) -> AgentRun:
    for run in doc.agent_runs:
        if run.id == run_id:
            return run
    raise HTTPException(status_code=404, detail="agent run not found")


def find_action_proposal(doc: ThreadDoc, proposal_id: str) -> ActionProposal:
    for proposal in doc.action_proposals:
        if proposal.id == proposal_id:
            return proposal
    raise HTTPException(status_code=404, detail="action proposal not found")


def update_agent_run_in_doc(doc: ThreadDoc, run: AgentRun) -> None:
    doc.agent_runs = [run if item.id == run.id else item for item in doc.agent_runs]


def compact_paper_for_agent(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": safe_run_text(paper.get("id"), 120),
        "title": safe_run_text(paper.get("title"), 220),
        "year": paper.get("year"),
        "venue": safe_run_text(paper.get("venue"), 120),
        "route_id": safe_run_text(paper.get("route_id") or (paper.get("raw_fields") or {}).get("routeName"), 120),
        "summary": safe_run_text(paper.get("summary") or paper.get("abstract") or paper.get("why_included"), 420),
        "why_included": safe_run_text(paper.get("why_included") or paper.get("local_role"), 320),
    }


def atlas_search_terms(text: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    blocked = {"这个", "当前", "论文", "研究", "问题", "帮我", "一下", "哪些", "如何", "为什么", "the", "and", "for"}
    terms: list[str] = []
    for term in raw_terms:
        lowered = term.lower()
        if lowered in blocked or lowered in terms:
            continue
        terms.append(lowered)
        if len(terms) >= 8:
            break
    return terms


def estimate_agent_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    ascii_count = sum(1 for char in text if ord(char) < 128)
    return max(1, (len(text) - ascii_count) // 2 + ascii_count // 4)


def add_agent_citation(
    citations: list[AgentCitation],
    *,
    source_type: str,
    title: str,
    source_ref: dict[str, Any],
    excerpt: str,
    tool_call_id: str,
) -> str:
    key = json.dumps(scrub_refs(source_ref), ensure_ascii=False, sort_keys=True)
    existing = next((item for item in citations if json.dumps(item.source_ref, ensure_ascii=False, sort_keys=True) == key), None)
    if existing:
        return existing.id
    source_id = f"S{len(citations) + 1}"
    citations.append(
        AgentCitation(
            id=source_id,
            source_type=source_type,
            title=safe_run_text(title, 220) or source_type,
            source_ref=scrub_refs(source_ref),
            excerpt=safe_run_text(excerpt, 420),
            tool_call_id=tool_call_id,
        )
    )
    return source_id


def find_bundle_paper(bundle: dict[str, Any], paper_id: str) -> dict[str, Any] | None:
    return next((paper for paper in bundle.get("papers", []) or [] if str(paper.get("id")) == paper_id), None)


def canvas_diagnosis(doc: ThreadDoc) -> dict[str, Any]:
    nodes = doc.canvas.nodes or []
    node_types = {node.type for node in nodes}
    issues = []
    if "question" not in node_types:
        issues.append("缺少明确研究问题")
    if not node_types.intersection({"material", "evidence"}):
        issues.append("缺少证据材料节点")
    if not node_types.intersection({"hypothesis", "conclusion", "decision", "finding"}):
        issues.append("缺少假设或结论节点")
    node_ids = {node.id for node in nodes}
    dangling = [edge.id for edge in doc.canvas.edges if edge.source not in node_ids or edge.target not in node_ids]
    if dangling:
        issues.append(f"存在 {len(dangling)} 条悬空连线")
    return {
        "ready": not issues,
        "issues": issues,
        "node_count": len(nodes),
        "edge_count": len(doc.canvas.edges),
        "missing_types": [
            item for item, present in [
                ("question", "question" in node_types),
                ("evidence", bool(node_types.intersection({"material", "evidence"}))),
                ("hypothesis/decision", bool(node_types.intersection({"hypothesis", "conclusion", "decision", "finding"}))),
            ] if not present
        ],
    }


def execute_agent_read_tool(
    doc: ThreadDoc,
    tool: str,
    arguments: dict[str, Any],
    citations: list[AgentCitation],
    *,
    round_number: int,
) -> tuple[AgentToolCall, dict[str, Any]]:
    started = datetime.now(timezone.utc)
    call_id = slug_id("tool_call")
    call = AgentToolCall(
        id=call_id,
        tool=tool,
        permission="read",
        round=round_number,
        input_summary=safe_run_text(arguments.get("query") or arguments.get("paper_id") or arguments.get("object_id") or doc.active_atlas_id, 180),
        status="running",
    )
    result: dict[str, Any] = {}
    try:
        if tool == "get_thread_state":
            result = thread_chat_context_summary(doc)
            result["conversation_summary"] = safe_run_text(doc.conversation_summary, 1200)
        elif tool == "get_context_index":
            cards = agent_context_cards(doc, 100)
            result = {
                "cards": [
                    {
                        "id": card.id,
                        "type": card.type,
                        "title": card.title,
                        "summary": safe_run_text(card.summary, 360),
                        "priority": card.priority,
                        "pinned": card.pinned,
                        "agent_note": safe_run_text(card.agent_note, 220),
                        "source_ref": scrub_refs(card.source_ref),
                    }
                    for card in cards
                ]
            }
            for card in cards[:12]:
                call.source_ids.append(
                    add_agent_citation(
                        citations,
                        source_type=card.type,
                        title=card.title,
                        source_ref=card.source_ref,
                        excerpt=card.summary,
                        tool_call_id=call_id,
                    )
                )
        elif tool in {"get_atlas_overview", "search_atlas", "get_paper", "get_relation_neighborhood"}:
            bundle = atlas_bundle_for_update(doc.active_atlas_id)
            papers = bundle.get("papers", []) or []
            relations = bundle.get("relations", []) or []
            if tool == "get_atlas_overview":
                routes = bundle.get("routes", []) or []
                result = {
                    "atlas_id": doc.active_atlas_id,
                    "title": bundle.get("atlas", {}).get("title_cn") or bundle.get("atlas", {}).get("title"),
                    "paper_count": len(papers),
                    "relation_count": len(relations),
                    "routes": [
                        {
                            "id": route.get("id"),
                            "name": route.get("name_cn") or route.get("name") or route.get("id"),
                            "rationale": safe_run_text(route.get("rationale") or route.get("description"), 260),
                        }
                        for route in routes[:10]
                    ],
                }
            elif tool == "search_atlas":
                query = safe_run_text(arguments.get("query"), 240)
                terms = atlas_search_terms(query)
                scored = []
                for paper in papers:
                    haystack = " ".join(str(paper.get(key) or "") for key in ["title", "summary", "abstract", "why_included", "local_role", "key_contribution"]).lower()
                    score = sum(1 for term in terms if term in haystack)
                    if score:
                        scored.append((score, paper))
                matches = [paper for _, paper in sorted(scored, key=lambda item: item[0], reverse=True)[:8]]
                if not matches:
                    matches = sorted(papers, key=lambda item: str(item.get("year") or ""), reverse=True)[:6]
                result["papers"] = [compact_paper_for_agent(paper) for paper in matches]
                for paper in matches:
                    call.source_ids.append(
                        add_agent_citation(
                            citations,
                            source_type="paper",
                            title=str(paper.get("title") or paper.get("id")),
                            source_ref={"atlas_id": doc.active_atlas_id, "paper_id": paper.get("id")},
                            excerpt=str(paper.get("summary") or paper.get("why_included") or ""),
                            tool_call_id=call_id,
                        )
                    )
            elif tool == "get_paper":
                paper_id = safe_run_text(arguments.get("paper_id"), 160)
                paper = find_bundle_paper(bundle, paper_id)
                if not paper:
                    raise HTTPException(status_code=404, detail="paper not found")
                result = compact_paper_for_agent(paper)
                call.source_ids.append(
                    add_agent_citation(
                        citations,
                        source_type="paper",
                        title=str(paper.get("title") or paper_id),
                        source_ref={"atlas_id": doc.active_atlas_id, "paper_id": paper_id},
                        excerpt=str(paper.get("summary") or paper.get("why_included") or ""),
                        tool_call_id=call_id,
                    )
                )
            else:
                paper_id = safe_run_text(arguments.get("paper_id"), 160)
                neighborhood = [rel for rel in relations if str(rel.get("source")) == paper_id or str(rel.get("target")) == paper_id][:20]
                result = {"paper_id": paper_id, "relations": [scrub_refs(rel) for rel in neighborhood]}
                for rel in neighborhood[:10]:
                    call.source_ids.append(
                        add_agent_citation(
                            citations,
                            source_type="relation",
                            title=safe_run_text(rel.get("label") or rel.get("type") or rel.get("id"), 180),
                            source_ref={"atlas_id": doc.active_atlas_id, "relation_id": rel.get("id")},
                            excerpt=safe_run_text(rel.get("description") or rel.get("why") or "", 360),
                            tool_call_id=call_id,
                        )
                    )
        elif tool == "get_object_memory":
            object_type = safe_run_text(arguments.get("object_type") or "paper", 40)
            object_id = safe_run_text(arguments.get("object_id"), 160)
            memory = effective_object_memory(doc.active_atlas_id, object_type, object_id)
            result = memory.model_dump(mode="json") if memory else {"missing": True, "object_id": object_id}
            if memory:
                call.source_ids.append(
                    add_agent_citation(
                        citations,
                        source_type="object_memory",
                        title=memory.title_snapshot or object_id,
                        source_ref=memory.object_ref,
                        excerpt=memory.judgement or memory.note,
                        tool_call_id=call_id,
                    )
                )
        elif tool == "get_canvas":
            result = compact_canvas_for_chat(doc)
            for node in doc.canvas.nodes[:20]:
                call.source_ids.append(
                    add_agent_citation(
                        citations,
                        source_type="canvas_node",
                        title=node.title,
                        source_ref={"thread_id": doc.id, "canvas_node_id": node.id},
                        excerpt=node.body,
                        tool_call_id=call_id,
                    )
                )
        elif tool == "diagnose_canvas":
            result = canvas_diagnosis(doc)
        else:
            raise HTTPException(status_code=400, detail="unsupported read tool")
        call.status = "done"
        count = len(result.get("papers", [])) if isinstance(result, dict) and "papers" in result else len(result) if isinstance(result, list) else 1
        call.result_summary = safe_run_text(result.get("summary") if isinstance(result, dict) else "", 220) or f"已返回 {count} 组可核查结果。"
    except HTTPException as exc:
        call.status = "failed"
        call.error = safe_run_text(exc.detail, 260)
        result = {"error": call.error}
    call.duration_ms = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
    return call, result


def deterministic_agent_plan(user_text: str, skills: list[dict[str, Any]], tool_history: list[AgentToolCall]) -> list[dict[str, Any]]:
    used = {call.tool for call in tool_history}
    skill_ids = {item.get("id") for item in skills}
    calls: list[dict[str, Any]] = []
    if "get_thread_state" not in used:
        calls.append({"tool": "get_thread_state", "arguments": {}})
    if "canvas_argument_builder" in skill_ids and "get_canvas" not in used:
        calls.extend([{"tool": "get_canvas", "arguments": {}}, {"tool": "diagnose_canvas", "arguments": {}}])
    if "atlas_evidence_search" in skill_ids and "search_atlas" not in used:
        calls.extend([{"tool": "get_atlas_overview", "arguments": {}}, {"tool": "search_atlas", "arguments": {"query": user_text}}])
    if "paper_card_completion" in skill_ids and "get_context_index" not in used:
        calls.append({"tool": "get_context_index", "arguments": {}})
    return calls[: max(0, 8 - len(tool_history))]


def run_agent_tool_loop(
    doc: ThreadDoc,
    user_text: str,
    model: str | None = None,
) -> tuple[list[AgentToolCall], dict[str, Any], list[AgentCitation], list[dict[str, Any]]]:
    skills = choose_skills(user_text, has_canvas=bool(doc.canvas.nodes), has_context=bool(agent_context_cards(doc, 100)))
    allowed = allowed_tools_for_skills(skills)
    calls: list[AgentToolCall] = []
    citations: list[AgentCitation] = []
    tool_context = empty_agent_tool_context()
    seen: set[str] = set()
    for round_number in range(1, 5):
        try:
            planned = call_openai_agent_plan(doc, user_text, skills, calls, tool_context["observations"], allowed, model)
        except HTTPException:
            planned = deterministic_agent_plan(user_text, skills, calls)
        valid = []
        for item in planned:
            tool = safe_run_text(item.get("tool"), 80)
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            signature = f"{tool}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
            if tool not in allowed or tool not in READ_TOOLS or signature in seen:
                continue
            seen.add(signature)
            valid.append((tool, arguments))
            if len(calls) + len(valid) >= 8:
                break
        if not valid:
            break
        for tool, arguments in valid:
            call, result = execute_agent_read_tool(doc, tool, arguments, citations, round_number=round_number)
            calls.append(call)
            record_agent_tool_result(
                tool_context,
                tool=tool,
                arguments=scrub_refs(arguments),
                result=scrub_refs(result),
                source_ids=list(call.source_ids),
            )
        if len(calls) >= 8:
            break
    return calls, tool_context, citations, skills


def run_agent_read_tools(doc: ThreadDoc, user_text: str) -> tuple[list[AgentToolCall], dict[str, Any]]:
    calls, context, _, _ = run_agent_tool_loop(doc, user_text)
    return calls, context


def clean_next_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    allowed = {
        "open_atlas",
        "inspect_sources",
        "open_settings",
        "inspect_proposal",
        "open_context_editor",
        "open_task_pack_editor",
    }
    actions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = safe_run_text(item.get("action") or item.get("id"), 80)
        if action not in allowed:
            continue
        actions.append(
            {
                "id": safe_run_text(item.get("id"), 80) or f"{action}_{len(actions) + 1}",
                "action": action,
                "label": safe_run_text(item.get("label"), 60) or action,
                "description": safe_run_text(item.get("description"), 180),
            }
        )
        if len(actions) >= 3:
            break
    return actions


def coerce_proposal(raw: Any, doc: ThreadDoc, run_id: str) -> ActionProposal | None:
    if not isinstance(raw, dict):
        return None
    proposal_type = safe_run_text(raw.get("type"), 80)
    if proposal_type not in {"context_injection", "object_memory", "paper_card_update", "atlas_candidate", "task_pack_preview"}:
        return None
    target = scrub_refs(raw.get("target") or {})
    diff_items = []
    raw_diff = raw.get("diff") if isinstance(raw.get("diff"), list) else []
    for item in raw_diff[:12]:
        if not isinstance(item, dict):
            continue
        diff_items.append(
            ProposalDiffItem(
                field=safe_run_text(item.get("field"), 80) or "note",
                before=scrub_refs(item.get("before")),
                after=scrub_refs(item.get("after")),
                reason=safe_run_text(item.get("reason"), 220),
            )
        )
    if not diff_items and proposal_type == "object_memory":
        diff_items.append(
            ProposalDiffItem(
                field="judgement",
                before="",
                after=safe_run_text(raw.get("summary"), 500),
                reason="由主对话智能体生成的对象判断草稿。",
            )
        )
    return ActionProposal(
        id=slug_id("proposal"),
        type=proposal_type,
        target=target,
        summary=safe_run_text(raw.get("summary"), 260) or "智能体生成的可确认修改",
        diff=diff_items,
        risk=safe_run_text(raw.get("risk"), 60) or "low",
        status="pending",
        source_run_id=run_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def parse_agent_payload(raw_text: str, doc: ThreadDoc, run_id: str) -> tuple[str, list[ActionProposal], list[dict[str, Any]]]:
    matches = re.findall(r"```(?:json|eai-agent-run/v1)?\s*([\s\S]*?)```", raw_text)
    candidates = matches[:]
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        answer = safe_markdown_text(parsed.get("answer") or parsed.get("content") or parsed.get("message"), 2600)
        proposals = [item for item in (coerce_proposal(raw, doc, run_id) for raw in (parsed.get("action_proposals") or parsed.get("proposals") or [])) if item]
        next_actions = clean_next_actions(parsed.get("next_actions"))
        if answer:
            return answer, proposals[:5], next_actions
    visible = re.sub(r"```(?:json|eai-agent-run/v1)?\s*[\s\S]*?```", "", raw_text).strip() or raw_text.strip()
    return visible[:2600], [], []


def build_agent_prompt(
    doc: ThreadDoc,
    user_text: str,
    tool_context: dict[str, Any],
    citations: list[AgentCitation] | None = None,
    skills: list[dict[str, Any]] | None = None,
    context_snapshot: AgentContextSnapshot | None = None,
) -> str:
    snapshot = context_snapshot or build_agent_context_snapshot(doc)
    selected_ids = set(snapshot.context_card_ids)
    payload = {
        "thread": {
            "id": doc.id,
            "title": doc.title,
            "goal": doc.goal,
            "active_atlas_id": doc.active_atlas_id,
            "conversation_summary": safe_run_text(doc.conversation_summary, 1800),
            "context_cards": [
                {
                    "id": card.id,
                    "type": card.type,
                    "title": card.title,
                    "summary": safe_run_text(card.summary, 240),
                    "priority": card.priority,
                    "pinned": card.pinned,
                    "agent_note": safe_run_text(card.agent_note, 220),
                    "selected_for_export": card.selected_for_export,
                    "include_in_agent": card.include_in_agent,
                    "include_in_lab": card.include_in_lab,
                    "source_ref": scrub_refs(card.source_ref),
                }
                for card in agent_context_cards(doc, 100)
                if card.id in selected_ids
            ],
        },
        "tool_context": tool_context,
        "skills": skills or [],
        "citations": [item.model_dump(mode="json") for item in (citations or [])],
        "turn_attachments": snapshot.turn_attachments,
        "recent_messages": [
            {"role": msg.role, "kind": msg.kind, "content": safe_run_text(msg.content, 320)}
            for msg in doc.messages[-24:]
            if msg.status != "pending"
        ],
    }
    return "\n".join(
        [
            "# EAI-Desktop Main Agent Runtime",
            "",
            "你是嵌入 EAI-Desktop 的中文研究智能体。你可以读取线程、Atlas 和对象记忆，但不能直接写入长期数据。",
            "你可以调查和判断，但不能直接写入长期数据。需要修改时只在回答中说明建议，系统会在独立通道生成变更集。",
            "",
            "## 权限边界",
            "- read 工具结果已经由服务端提供，不能假装读取了未提供的论文全文。",
            "- 引用证据时必须使用提供的 [S1]、[S2] 来源编号，不得创造不存在的来源编号。",
            "- 临时检索结果不会自动进入长期 Context。",
            "- 回答要区分：已知证据、个人判断、需要查证。",
            "- 如果需要复制粘贴，只提示打开上下文包编辑器，不要把复制粘贴作为主流程。",
            "",
            "## 当前上下文",
            json.dumps(payload, ensure_ascii=False, indent=2)[:20000],
            "",
            "## 用户输入",
            user_text.strip(),
            "",
            "## 返回格式",
            "只返回给用户看的自然中文，不要输出 JSON、工具协议或代码围栏。结构应自然覆盖：当前判断、依据、不确定处、建议下一步。",
        ]
    )


def call_main_agent(prompt: str, model: str | None = None) -> tuple[str, str]:
    return call_openai_text(
        prompt,
        "你是 EAI-Desktop 的中文 Main Agent。只基于服务端提供的上下文和工具结果回答，并把长期写入表达为可确认提案。",
        model,
    )


def call_main_agent_stream(prompt: str, model: str | None = None) -> tuple[Iterator[str], str, str]:
    return call_openai_text_stream(
        prompt,
        "你是 EAI-Desktop 的中文 Main Agent。只输出自然语言回答；不得输出 JSON、工具协议或隐藏提示。",
        model,
    )


def proposal_to_suggestion(proposal: ActionProposal) -> dict[str, Any]:
    return {
        "id": proposal.id or "",
        "action": "inspect_proposal",
        "label": "查看修改提案",
        "description": proposal.summary,
        "payload": {"proposal_id": proposal.id, "type": proposal.type},
        "confidence": 0.8,
    }


def find_changeset(doc: ThreadDoc, changeset_id: str) -> ChangeSet:
    for changeset in doc.changesets:
        if changeset.id == changeset_id:
            return changeset
    raise HTTPException(status_code=404, detail="changeset not found")


def json_pointer_field(path: str) -> str:
    return path.strip("/").split("/")[-1].replace("~1", "/").replace("~0", "~")


def current_change_value(doc: ThreadDoc, operation: ChangeOperation) -> Any:
    target = operation.target or {}
    field = json_pointer_field(operation.path)
    if operation.op == "add" and operation.path.endswith("/-"):
        if operation.target_type == "context" and target.get("card_id"):
            return operation.after if any(item.id == target.get("card_id") for item in doc.context_cards) else None
        if operation.target_type == "canvas" and target.get("node_id"):
            return operation.after if any(item.id == target.get("node_id") for item in doc.canvas.nodes) else None
        if operation.target_type == "canvas" and target.get("edge_id"):
            return operation.after if any(item.id == target.get("edge_id") for item in doc.canvas.edges) else None
        if operation.target_type == "atlas_candidate" and target.get("candidate_id"):
            atlas_doc = load_atlas_updates(safe_run_text(target.get("atlas_id") or doc.active_atlas_id, 80))
            return operation.after if any(item.id == target.get("candidate_id") for item in atlas_doc.candidates) else None
        return None
    if operation.target_type == "context":
        card = next((item for item in doc.context_cards if item.id == target.get("card_id")), None)
        if not card:
            return None
        return card.model_dump(mode="json").get(field)
    if operation.target_type == "canvas":
        collection = doc.canvas.edges if target.get("edge_id") else doc.canvas.nodes
        item_id = target.get("edge_id") or target.get("node_id")
        item = next((entry for entry in collection if entry.id == item_id), None)
        if not item:
            return None
        return item.model_dump(mode="json").get(field)
    if operation.target_type == "object_memory":
        atlas_id = safe_run_text(target.get("atlas_id") or doc.active_atlas_id, 80)
        object_type = safe_run_text(target.get("object_type") or "paper", 40)
        object_id = safe_run_text(target.get("object_id"), 160)
        memory = effective_object_memory(atlas_id, object_type, object_id)
        if memory:
            return memory.model_dump(mode="json").get(field)
        defaults = {"tags": [], "maturity": 0, "star": False}
        return defaults.get(field, "")
    if operation.target_type == "atlas_candidate":
        atlas_id = safe_run_text(target.get("atlas_id") or doc.active_atlas_id, 80)
        candidate_id = safe_run_text(target.get("candidate_id"), 160)
        update_doc = load_atlas_updates(atlas_id)
        candidate = next((item for item in update_doc.candidates if item.id == candidate_id), None)
        return candidate.model_dump(mode="json").get(field) if candidate else None
    return None


def normalize_change_operation(doc: ThreadDoc, raw: dict[str, Any], index: int) -> ChangeOperation | None:
    target_type = safe_run_text(raw.get("target_type"), 40)
    if target_type not in {"context", "object_memory", "atlas_candidate", "canvas"}:
        return None
    target = scrub_refs(raw.get("target") or {})
    path = safe_run_text(raw.get("path"), 160)
    op = safe_run_text(raw.get("op") or "replace", 20)
    if not path.startswith("/") or op not in {"add", "replace", "remove"}:
        return None
    allowed_fields = {
        "context": {"summary", "priority", "pinned", "agent_note", "include_in_agent", "selected_for_export", "cards"},
        "object_memory": {"judgement", "note", "core_innovation", "core_technology", "evidence", "limitations", "reusable_insight", "tags", "maturity", "star"},
        "atlas_candidate": {"title", "authors", "year", "venue", "url", "abstract", "suggested_route_id", "why", "relevance", "confidence", "status", "candidates"},
        "canvas": {"title", "body", "status", "priority", "nodes", "edges", "label"},
    }
    if json_pointer_field(path) not in allowed_fields[target_type]:
        return None
    operation = ChangeOperation(
        id=safe_run_text(raw.get("id"), 100) or slug_id(f"change_{index + 1}"),
        target_type=target_type,
        target=target,
        op=op,
        path=path,
        after=scrub_refs(raw.get("after")),
        reason=safe_run_text(raw.get("reason"), 320),
        risk=safe_run_text(raw.get("risk"), 40) or "low",
        selected=bool(raw.get("selected", True)),
    )
    operation.before = current_change_value(doc, operation)
    return operation


def build_changeset_from_raw(doc: ThreadDoc, run_id: str, raw: dict[str, Any]) -> ChangeSet | None:
    raw_operations = raw.get("operations") if isinstance(raw.get("operations"), list) else []
    operations = [
        operation
        for operation in (normalize_change_operation(doc, item, index) for index, item in enumerate(raw_operations[:24]) if isinstance(item, dict))
        if operation
    ]
    if not operations:
        return None
    now = utc_now()
    return ChangeSet(
        id=slug_id("changeset"),
        thread_id=doc.id,
        source_run_id=run_id,
        base_revision=doc.revision,
        summary=safe_run_text(raw.get("summary"), 300) or "Main Agent 生成的可确认修改",
        risk=safe_run_text(raw.get("risk"), 40) or max((item.risk for item in operations), default="low"),
        operations=operations,
        created_at=now,
        updated_at=now,
    )


def legacy_proposal_to_changeset(doc: ThreadDoc, run_id: str, proposal: ActionProposal) -> ChangeSet | None:
    target_type = {
        "context_injection": "context",
        "object_memory": "object_memory",
        "paper_card_update": "object_memory",
        "atlas_candidate": "atlas_candidate",
    }.get(proposal.type)
    if not target_type:
        return None
    operations = []
    if proposal.type == "context_injection":
        operations.append(
            {
                "target_type": "context",
                "target": proposal.target,
                "op": "add",
                "path": "/cards/-",
                "after": {
                    "type": proposal.target.get("type", "paper"),
                    "title": proposal.target.get("title") or proposal.summary,
                    "summary": next((item.after for item in proposal.diff if item.field in {"summary", "note", "judgement"}), proposal.summary),
                    "source_ref": proposal.target.get("source_ref") or proposal.target,
                },
                "reason": proposal.summary,
            }
        )
    elif proposal.type == "atlas_candidate" and not proposal.diff:
        operations.append({"target_type": target_type, "target": proposal.target, "op": "add", "path": "/candidates/-", "after": proposal.target, "reason": proposal.summary})
    else:
        for item in proposal.diff:
            operations.append(
                {
                    "target_type": target_type,
                    "target": proposal.target,
                    "op": "replace",
                    "path": f"/{item.field}",
                    "after": item.after,
                    "reason": item.reason or proposal.summary,
                }
            )
    return build_changeset_from_raw(doc, run_id, {"summary": proposal.summary, "risk": proposal.risk, "operations": operations})


def parse_changeset_payload(raw_text: str, doc: ThreadDoc, run_id: str) -> list[ChangeSet]:
    matches = re.findall(r"```(?:json|eai-changeset/v1)?\s*([\s\S]*?)```", raw_text or "")
    if (raw_text or "").strip().startswith("{"):
        matches.append(raw_text.strip())
    for candidate in matches:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        raw_sets = payload.get("changesets") if isinstance(payload, dict) else None
        if not isinstance(raw_sets, list):
            raw_sets = [payload] if isinstance(payload, dict) and isinstance(payload.get("operations"), list) else []
        changesets = [item for item in (build_changeset_from_raw(doc, run_id, raw) for raw in raw_sets[:4] if isinstance(raw, dict)) if item]
        if changesets:
            return changesets
    return []


def should_compile_changesets(user_text: str) -> bool:
    return any(word in user_text.lower() for word in ["修改", "更新", "补全", "完善", "加入", "创建", "生成", "组织", "编排", "记录", "写入", "候选", "canvas"])


def compile_agent_changesets(
    doc: ThreadDoc,
    run_id: str,
    user_text: str,
    answer: str,
    tool_context: dict[str, Any],
    model: str | None = None,
) -> list[ChangeSet]:
    if os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"):
        _, proposals, _ = parse_agent_payload(os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"], doc, run_id)
        return [item for item in (legacy_proposal_to_changeset(doc, run_id, proposal) for proposal in proposals) if item]
    if not should_compile_changesets(user_text):
        return []
    prompt = "\n".join(
        [
            "# EAI Main Agent ChangeSet Compiler",
            "只在用户明确要求修改长期数据时生成 changesets；否则返回 {\"changesets\": []}。",
            "允许目标：context、object_memory、atlas_candidate、canvas。不得修改正式 bundle 或 SQLite。",
            "operation 字段：target_type, target, op(add|replace|remove), path, after, reason, risk。before 由服务端补齐。",
            "Canvas 新节点使用 /nodes/-，新连线使用 /edges/-；Context 新卡使用 /cards/-。",
            "用户输入：" + user_text,
            "回答摘要：" + safe_run_text(answer, 1800),
            "可用上下文：" + json.dumps(tool_context, ensure_ascii=False)[:10000],
            "只返回 eai-changeset/v1 JSON 代码块。",
        ]
    )
    try:
        raw, _ = call_openai_text(prompt, "你是严格的变更集编译器。不得输出自然语言解释。", model)
    except HTTPException:
        return []
    return parse_changeset_payload(raw, doc, run_id)


def apply_context_injection(doc: ThreadDoc, proposal: ActionProposal) -> dict[str, Any]:
    target = proposal.target or {}
    source_ref = target.get("source_ref") if isinstance(target.get("source_ref"), dict) else target
    title = safe_run_text(target.get("title") or proposal.summary, 180) or "智能体上下文"
    summary = next((safe_run_text(item.after, 500) for item in proposal.diff if item.field in {"summary", "note", "judgement"}), proposal.summary)
    card = ContextCard(
        id=slug_id("card"),
        type=target.get("type") if target.get("type") in {"paper", "relation", "path", "file"} else "paper",
        title=title,
        source_ref=scrub_refs(source_ref if isinstance(source_ref, dict) else {}),
        summary=summary,
        token_estimate=max(80, len(summary) // 2),
        selected_for_export=True,
        include_in_agent=True,
    )
    doc.context_cards.append(card)
    return {"context_card_id": card.id, "title": card.title}


def apply_object_memory_proposal(proposal: ActionProposal) -> dict[str, Any]:
    target = proposal.target or {}
    atlas_id = safe_run_text(target.get("atlas_id"), 80)
    object_type = safe_run_text(target.get("object_type") or target.get("type"), 40)
    object_id = safe_run_text(target.get("object_id") or target.get("id"), 160)
    if not atlas_id or object_type not in {"paper", "relation", "path", "file"} or not object_id:
        raise HTTPException(status_code=400, detail="object memory proposal target is incomplete")
    memory = effective_object_memory(atlas_id, object_type, object_id) or ObjectMemory(
        object_ref={"atlas_id": atlas_id, "object_type": object_type, "object_id": object_id},
        title_snapshot=safe_run_text(target.get("title"), 220),
    )
    for item in proposal.diff:
        if item.field in {"judgement", "note", "core_innovation", "core_technology", "evidence", "limitations", "reusable_insight"}:
            setattr(memory, item.field, safe_run_text(item.after, 1800))
        elif item.field == "tags":
            if isinstance(item.after, list):
                memory.tags = [safe_run_text(tag, 80) for tag in item.after if safe_run_text(tag, 80)]
            elif isinstance(item.after, str):
                memory.tags = [tag.strip() for tag in item.after.split(",") if tag.strip()]
        elif item.field == "maturity":
            try:
                memory.maturity = max(0, min(5, int(item.after)))
            except (TypeError, ValueError):
                pass
        elif item.field == "star":
            memory.star = bool(item.after)
    saved = write_object_memory(atlas_id, object_type, object_id, memory)
    return {"object_ref": saved.object_ref, "updated_at": saved.updated_at}


def apply_atlas_candidate_proposal(proposal: ActionProposal) -> dict[str, Any]:
    target = proposal.target or {}
    atlas_id = safe_run_text(target.get("atlas_id"), 80)
    if not atlas_id:
        raise HTTPException(status_code=400, detail="atlas candidate proposal target is incomplete")
    values = {item.field: item.after for item in proposal.diff}
    candidate = AtlasUpdateCandidate(
        id=safe_run_text(target.get("object_id") or target.get("id"), 120) or slug_id("cand"),
        title=safe_run_text(values.get("title") or target.get("title") or proposal.summary, 220),
        authors=[safe_run_text(author, 120) for author in values.get("authors", [])] if isinstance(values.get("authors"), list) else [],
        year=values.get("year"),
        venue=safe_run_text(values.get("venue"), 160),
        url=safe_run_text(values.get("url"), 400),
        doi=safe_run_text(values.get("doi"), 160),
        arxiv_id=safe_run_text(values.get("arxiv_id"), 120),
        abstract=safe_run_text(values.get("abstract"), 1800),
        suggested_route_id=safe_run_text(values.get("suggested_route_id"), 160),
        why=safe_run_text(values.get("why") or proposal.summary, 1200),
        relevance=safe_run_text(values.get("relevance"), 1200),
        confidence=max(0, min(1, float(values.get("confidence") or 0.6))),
        source_run_id=proposal.source_run_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    updates = load_atlas_updates(atlas_id)
    updates.candidates.insert(0, candidate)
    write_atlas_updates(updates)
    return {"candidate_id": candidate.id, "title": candidate.title}


@app.post("/api/vnext/threads/{thread_id}/messages", response_model=ThreadDoc)
def add_message(thread_id: str, payload: MessageCreate) -> ThreadDoc:
    doc = load_thread(thread_id)
    append_message(
        doc,
        Message(
            id=slug_id("msg"),
            role=payload.role,
            kind=payload.kind,
            content=payload.content,
            created_at=utc_now(),
            status=safe_run_text(payload.status, 32) or "done",
            surface=payload.surface,
            refs=payload.refs,
            linked_result_id=safe_run_text(payload.linked_result_id, 120) or None,
            linked_tool_run_id=safe_run_text(payload.linked_tool_run_id, 120) or None,
        ),
    )
    return write_thread(doc)


def complete_thread_chat_draft(doc: ThreadDoc, assistant: Message, model: str | None = None) -> tuple[ThreadDoc, Message, bool]:
    if assistant.role != "assistant" or assistant.kind != "assistant_reply":
        raise HTTPException(status_code=400, detail="message is not an assistant chat draft")
    user_message_id = safe_run_text((assistant.refs or {}).get("user_message_id"), 120)
    if not user_message_id:
        raise HTTPException(status_code=400, detail="assistant draft is missing user message reference")
    user_message = find_message(doc, user_message_id)
    context_summary = thread_chat_context_summary(doc)
    base_refs = {
        **(assistant.refs or {}),
        "user_message_id": user_message.id,
        "context_summary": context_summary,
    }
    degraded = False
    try:
        prompt = build_thread_chat_prompt(doc, user_message.content)
        raw_text, used_model = call_openai_thread_chat(prompt, model)
        content, suggestions = parse_thread_chat_payload(raw_text, doc)
        if not suggestions:
            suggestions = fallback_thread_suggestions(doc)[:2]
        update_assistant_chat_message(
            doc,
            assistant,
            content=content or "我已经收到。当前信息还不够完整，建议先从 Atlas 选择证据或补充 Canvas 结构。",
            status="done",
            refs={
                **base_refs,
                "provider": "openai",
                "model": used_model,
                "steps": thread_chat_steps(active="done"),
                "suggestions": suggestions,
            },
        )
    except HTTPException as exc:
        degraded = True
        no_key = exc.status_code in {400, 401, 403}
        update_assistant_chat_message(
            doc,
            assistant,
            content=(
                "我已记录你的问题，但当前没有可用的 API 密钥，所以无法自动回复。"
                "你可以先复制 Task Pack 给 Codex，或打开研究动作模板生成更明确的上下文。"
                if no_key
                else "我已记录你的问题，但这次 API 调用失败。你可以稍后重试，或先复制 Task Pack 给 Codex。"
            ),
            status="done" if no_key else "failed",
            refs={
                **base_refs,
                "degraded": True,
                "error": safe_run_text(exc.detail, 260),
                "steps": thread_chat_steps(failed="reply"),
                "suggestions": clean_thread_suggestions(
                    [{"action": "preview_task_pack"}, {"action": "open_templates"}, {"action": "open_atlas"}],
                    doc,
                ),
            },
        )
    doc.active_surface = "thread"
    return doc, assistant, degraded


@app.post("/api/vnext/threads/{thread_id}/chat/start", response_model=ThreadChatStartResponse)
def start_thread_chat(thread_id: str, payload: ThreadChatRequest) -> ThreadChatStartResponse:
    raise HTTPException(status_code=410, detail="旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口")
    doc = load_thread(thread_id)
    user_text = safe_message_content(payload.message, 2400)
    if should_promote_thread_question(doc):
        doc.title = title_from_question(user_text)
        doc.goal = user_text
    user_message = append_message(
        doc,
        Message(
            role="user",
            kind="text",
            content=user_text,
            status="done",
            surface=payload.surface or doc.active_surface,
            refs={"active_atlas_id": doc.active_atlas_id},
        ),
    )
    assistant = append_message(
        doc,
        Message(
            role="assistant",
            kind="assistant_reply",
            content="正在理解你的研究问题，并整理当前线程上下文...",
            status="pending",
            surface=payload.surface or doc.active_surface,
            refs={
                "user_message_id": user_message.id,
                "steps": thread_chat_steps(active="context"),
                "context_summary": thread_chat_context_summary(doc),
                "suggestions": [],
            },
        ),
    )
    doc.active_surface = "thread"
    updated = write_thread(doc)
    return ThreadChatStartResponse(
        thread=updated,
        user_message_id=user_message.id or "",
        assistant_message_id=assistant.id or "",
    )


@app.post("/api/vnext/threads/{thread_id}/chat/complete", response_model=ThreadChatResponse)
def complete_thread_chat(thread_id: str, payload: ThreadChatCompleteRequest) -> ThreadChatResponse:
    raise HTTPException(status_code=410, detail="旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口")
    doc = load_thread(thread_id)
    assistant = find_message(doc, payload.assistant_message_id)
    doc, assistant, degraded = complete_thread_chat_draft(doc, assistant, payload.model)
    updated = write_thread(doc)
    return ThreadChatResponse(thread=updated, assistant_message=assistant, degraded=degraded)


@app.post("/api/vnext/threads/{thread_id}/chat/{assistant_message_id}/retry", response_model=ThreadChatResponse)
def retry_thread_chat(thread_id: str, assistant_message_id: str, payload: ThreadChatRetryRequest | None = None) -> ThreadChatResponse:
    raise HTTPException(status_code=410, detail="旧 chat 重试接口已停用，请使用 Agent Runtime v2 resume 接口")
    doc = load_thread(thread_id)
    assistant = find_message(doc, assistant_message_id)
    assistant.content = "正在重新生成回复..."
    assistant.status = "pending"
    assistant.refs = scrub_refs({
        **(assistant.refs or {}),
        "steps": thread_chat_steps(active="context"),
        "suggestions": [],
        "error": "",
    })
    doc, assistant, degraded = complete_thread_chat_draft(doc, assistant, payload.model if payload else None)
    updated = write_thread(doc)
    return ThreadChatResponse(thread=updated, assistant_message=assistant, degraded=degraded)


def finish_agent_run(
    doc: ThreadDoc,
    run: AgentRun,
    assistant: Message,
    *,
    answer: str,
    proposals: list[ActionProposal],
    next_actions: list[dict[str, Any]],
    tool_calls: list[AgentToolCall],
    citations: list[AgentCitation],
    skills: list[dict[str, Any]],
    changesets: list[ChangeSet],
    degraded: bool,
    provider: str | None = None,
    model: str | None = None,
    error: str = "",
) -> tuple[ThreadDoc, AgentRun, Message]:
    for proposal in proposals:
        proposal.source_run_id = run.id
        proposal.status = "pending"
        proposal.created_at = proposal.created_at or utc_now()
        proposal.updated_at = utc_now()
    incoming_ids = {proposal.id for proposal in proposals}
    doc.action_proposals = [proposal for proposal in doc.action_proposals if proposal.id not in incoming_ids]
    doc.action_proposals = proposals + doc.action_proposals
    run.tool_calls = tool_calls
    run.skills = skills
    run.citations = citations
    run.changeset_ids = [item.id for item in changesets]
    run.proposals = proposals
    run.next_actions = next_actions
    run.answer = answer
    run.provider = provider or run.provider
    run.model = model or run.model
    run.error = safe_run_text(error, 260)
    run.status = "waiting_confirmation" if proposals or changesets else "done"
    run.steps = agent_steps(active="confirm" if proposals or changesets else "done")
    run.updated_at = utc_now()
    update_agent_run_in_doc(doc, run)
    incoming_changeset_ids = {item.id for item in changesets}
    doc.changesets = changesets + [item for item in doc.changesets if item.id not in incoming_changeset_ids]

    suggestion_items = [proposal_to_suggestion(proposal) for proposal in proposals[:2]] or next_actions[:2]
    if changesets:
        suggestion_items = [
            {
                "id": changesets[0].id,
                "action": "inspect_changeset",
                "label": "查看变更集",
                "description": changesets[0].summary,
                "payload": {"changeset_id": changesets[0].id},
            }
        ]
    update_assistant_chat_message(
        doc,
        assistant,
        content=answer or "我已完成本轮分析，但没有生成新的长期写入提案。",
        status="preview" if proposals or changesets else "done",
        refs={
            **(assistant.refs or {}),
            "agent_run_id": run.id,
            "steps": [step.model_dump(mode="json") for step in run.steps],
            "tool_calls": [call.model_dump(mode="json") for call in tool_calls],
            "action_proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            "changeset_ids": [item.id for item in changesets],
            "changesets": [item.model_dump(mode="json") for item in changesets],
            "citations": [item.model_dump(mode="json") for item in citations],
            "skills": skills,
            "context_snapshot": run.context_snapshot.model_dump(mode="json"),
            "next_actions": next_actions,
            "suggestions": suggestion_items,
            "degraded": degraded,
            "provider": run.provider,
            "model": run.model,
        },
    )
    doc.active_surface = "thread"
    return doc, run, assistant


def complete_agent_run(doc: ThreadDoc, run: AgentRun, model: str | None = None) -> tuple[ThreadDoc, AgentRun, Message, bool]:
    assistant = find_message(doc, run.assistant_message_id)
    user_message = find_message(doc, run.user_message_id)
    run.status = "planning"
    run.steps = agent_steps(active="tools")
    run.updated_at = utc_now()
    tool_calls, tool_context, citations, skills = run_agent_tool_loop(doc, user_message.content, model)
    run.tool_calls = tool_calls
    degraded = False
    proposals: list[ActionProposal] = []
    next_actions: list[dict[str, Any]] = []
    changesets: list[ChangeSet] = []
    try:
        prompt = build_agent_prompt(doc, user_message.content, tool_context, citations, skills, run.context_snapshot)
        raw_text, used_model = call_main_agent(prompt, model)
        answer, proposals, next_actions = parse_agent_payload(raw_text, doc, run.id)
        changesets = compile_agent_changesets(doc, run.id, user_message.content, answer, tool_context, model)
        run.provider = "openai"
        run.model = used_model
    except HTTPException as exc:
        degraded = True
        answer, next_actions = build_fallback_agent_answer(
            tool_context,
            visible_context_count=len(agent_context_cards(doc, 1000)),
        )
        proposals = []
        run.error = safe_run_text(exc.detail, 260)

    doc, run, assistant = finish_agent_run(
        doc,
        run,
        assistant,
        answer=answer,
        proposals=proposals,
        next_actions=next_actions,
        tool_calls=tool_calls,
        citations=citations,
        skills=skills,
        changesets=changesets,
        degraded=degraded,
        provider=run.provider,
        model=run.model,
        error=run.error,
    )
    return doc, run, assistant, degraded


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def emit_agent_event(run: AgentRun, event: str, data: dict[str, Any]) -> str:
    run.event_seq += 1
    payload = {**scrub_refs(data), "seq": run.event_seq}
    buffer = APP_SERVICES.legacy_agent_event_buffers.setdefault(run.id, [])
    buffer.append({"event": event, "data": payload})
    if len(buffer) > 240:
        del buffer[:-240]
    return sse_event(event, payload)


def refresh_conversation_summary(doc: ThreadDoc) -> None:
    stable = [message for message in doc.messages if message.status not in {"pending", "streaming"}]
    if len(stable) <= 24:
        return
    older = stable[:-16]
    fragments = []
    for message in older[-20:]:
        if message.role not in {"user", "assistant"}:
            continue
        speaker = "用户" if message.role == "user" else "Main Agent"
        fragments.append(f"{speaker}: {safe_run_text(message.content, 180)}")
    if fragments:
        doc.conversation_summary = safe_run_text("\n".join(fragments), 3000)


def merge_agent_completion(doc: ThreadDoc, run: AgentRun, assistant: Message) -> ThreadDoc:
    latest = load_thread(doc.id)
    latest.messages = [assistant if item.id == assistant.id else item for item in latest.messages]
    latest.agent_runs = [run if item.id == run.id else item for item in latest.agent_runs]
    incoming_proposals = {item.id for item in doc.action_proposals if item.source_run_id == run.id}
    latest.action_proposals = [item for item in latest.action_proposals if item.id not in incoming_proposals]
    latest.action_proposals = [item for item in doc.action_proposals if item.source_run_id == run.id] + latest.action_proposals
    incoming_changesets = [item for item in doc.changesets if item.source_run_id == run.id]
    incoming_ids = {item.id for item in incoming_changesets}
    latest.changesets = [item for item in latest.changesets if item.id not in incoming_ids]
    for item in incoming_changesets:
        item.base_revision = latest.revision + 1
    latest.changesets = incoming_changesets + latest.changesets
    refresh_conversation_summary(latest)
    return write_thread(latest)


def reset_agent_run_for_stream(doc: ThreadDoc, run: AgentRun) -> Message:
    assistant = find_message(doc, run.assistant_message_id)
    assistant.content = "正在运行主对话智能体..."
    assistant.status = "pending"
    assistant.refs = scrub_refs({
        **(assistant.refs or {}),
        "steps": [step.model_dump(mode="json") for step in agent_steps(active="intent")],
        "tool_calls": [],
        "action_proposals": [],
        "suggestions": [],
        "error": "",
        "streaming": True,
    })
    run.status = "running"
    run.steps = agent_steps(active="intent")
    run.tool_calls = []
    run.proposals = []
    run.next_actions = []
    run.answer = ""
    run.error = ""
    run.updated_at = utc_now()
    update_agent_run_in_doc(doc, run)
    return assistant


def stream_agent_run_events(thread_id: str, run_id: str, model: str | None = None, retry: bool = False) -> Iterator[str]:
    doc = load_thread(thread_id)
    run = find_agent_run(doc, run_id)
    if retry:
        run.attempt += 1
        APP_SERVICES.cancelled_legacy_agent_runs.discard(run.id)
    assistant = reset_agent_run_for_stream(doc, run)
    user_message = find_message(doc, run.user_message_id)
    raw_chunks: list[str] = []
    tool_calls: list[AgentToolCall] = []
    degraded = False
    answer = ""
    proposals: list[ActionProposal] = []
    next_actions: list[dict[str, Any]] = []
    used_provider: str | None = None
    used_model: str | None = None
    error_text = ""

    for step in agent_steps(active="context"):
        yield emit_agent_event(run, "step", step.model_dump(mode="json"))

    try:
        if run.id in APP_SERVICES.cancelled_legacy_agent_runs:
            raise InterruptedError("run cancelled")
        run.status = "planning"
        run.steps = agent_steps(active="tools")
        yield emit_agent_event(run, "step", {"id": "tools", "label": "选择 Skill 与调查工具", "status": "running"})
        tool_calls, tool_context, citations, skills = run_agent_tool_loop(doc, user_message.content, model)
        run.skills = skills
        run.citations = citations
        for skill in skills:
            yield emit_agent_event(run, "skill", skill)
        for call in tool_calls:
            yield emit_agent_event(run, "tool_call", call.model_dump(mode="json"))
            yield emit_agent_event(
                run,
                "observation",
                {"tool_call_id": call.id, "summary": call.result_summary or call.error, "source_ids": call.source_ids},
            )

        if run.id in APP_SERVICES.cancelled_legacy_agent_runs:
            raise InterruptedError("run cancelled")
        run.status = "running"
        run.steps = agent_steps(active="answer")
        yield emit_agent_event(run, "step", {"id": "answer", "label": "综合证据并生成回答", "status": "running"})
        prompt = build_agent_prompt(doc, user_message.content, tool_context, citations, skills, run.context_snapshot)
        chunk_iter, used_model, used_provider = call_main_agent_stream(prompt, model)
        for chunk in chunk_iter:
            if not chunk:
                continue
            if run.id in APP_SERVICES.cancelled_legacy_agent_runs:
                raise InterruptedError("run cancelled")
            raw_chunks.append(chunk)
        raw_text = "".join(raw_chunks)
        answer, proposals, next_actions = parse_agent_payload(raw_text, doc, run.id)
        for chunk in mock_text_chunks(answer, 64):
            yield emit_agent_event(run, "answer_delta", {"text": chunk})
        changesets = compile_agent_changesets(doc, run.id, user_message.content, answer, tool_context, model)
    except InterruptedError:
        answer = "已停止本轮 Main Agent 运行。你的问题和已完成的工具轨迹仍保留在线程中。"
        run.status = "cancelled"
        changesets = []
        citations = locals().get("citations", [])
        skills = locals().get("skills", [])
        yield emit_agent_event(run, "error", {"message": "运行已停止", "recoverable": True, "degraded": False})
        yield emit_agent_event(run, "answer_delta", {"text": answer})
    except HTTPException as exc:
        degraded = True
        error_text = safe_run_text(exc.detail, 260)
        answer, next_actions = build_fallback_agent_answer(
            tool_context if "tool_context" in locals() else empty_agent_tool_context(),
            visible_context_count=len(agent_context_cards(doc, 1000)),
        )
        proposals = []
        changesets = []
        citations = locals().get("citations", [])
        skills = locals().get("skills", [])
        yield emit_agent_event(run, "error", {"message": error_text, "recoverable": True, "degraded": True})
        yield emit_agent_event(run, "answer_delta", {"text": answer})
    except Exception as exc:
        degraded = True
        error_text = safe_run_text(exc, 260)
        answer, next_actions = build_fallback_agent_answer(
            tool_context if "tool_context" in locals() else empty_agent_tool_context(),
            visible_context_count=len(agent_context_cards(doc, 1000)),
        )
        proposals = []
        changesets = []
        citations = locals().get("citations", [])
        skills = locals().get("skills", [])
        yield emit_agent_event(run, "error", {"message": error_text, "recoverable": True, "degraded": True})
        yield emit_agent_event(run, "answer_delta", {"text": answer})

    for proposal in proposals:
        yield emit_agent_event(run, "proposal", proposal.model_dump(mode="json"))
    for changeset in changesets:
        yield emit_agent_event(run, "changeset", changeset.model_dump(mode="json"))

    was_cancelled = run.status == "cancelled"
    doc, run, assistant = finish_agent_run(
        doc,
        run,
        assistant,
        answer=answer,
        proposals=proposals,
        next_actions=next_actions,
        tool_calls=tool_calls,
        citations=citations,
        skills=skills,
        changesets=changesets,
        degraded=degraded,
        provider=used_provider,
        model=used_model,
        error=error_text,
    )
    if was_cancelled:
        run.status = "cancelled"
        assistant.status = "done"
        assistant.refs = {**(assistant.refs or {}), "cancelled": True}
    APP_SERVICES.cancelled_legacy_agent_runs.discard(run.id)
    updated = merge_agent_completion(doc, run, assistant)
    final_run = find_agent_run(updated, run.id)
    final_assistant = find_message(updated, assistant.id or "")
    yield emit_agent_event(
        final_run,
        "done",
        AgentRunResponse(thread=updated, run=final_run, assistant_message=final_assistant, degraded=degraded).model_dump(mode="json"),
    )


@app.post("/api/vnext/threads/{thread_id}/agent-runs/start", response_model=AgentRunStartResponse)
def start_agent_run(thread_id: str, payload: ThreadChatRequest) -> AgentRunStartResponse:
    raise HTTPException(status_code=410, detail="Agent v1 已转为只读历史，请使用 Agent Runtime v2")
    doc = load_thread(thread_id)
    active = next((item for item in doc.agent_runs if item.status in {"pending", "planning", "running"}), None)
    if active:
        raise HTTPException(status_code=409, detail={"message": "当前线程已有正在运行的 Main Agent", "run_id": active.id})
    user_text = safe_message_content(payload.message, 2400)
    attachments = [scrub_refs(item) for item in payload.turn_attachments[:8] if isinstance(item, dict)]
    if should_promote_thread_question(doc):
        doc.title = title_from_question(user_text)
        doc.goal = user_text
    user_message = append_message(
        doc,
        Message(
            role="user",
            kind="text",
            content=user_text,
            status="done",
            surface="thread",
            refs={"active_atlas_id": doc.active_atlas_id, "agent_entry": "main", "turn_attachments": attachments},
        ),
    )
    assistant = append_message(
        doc,
        Message(
            role="assistant",
            kind="assistant_reply",
            content="正在启动主对话智能体，读取线程、Atlas 和对象记忆...",
            status="pending",
            surface="thread",
            refs={
                "user_message_id": user_message.id,
                "steps": [step.model_dump(mode="json") for step in agent_steps(active="intent")],
                "tool_calls": [],
                "action_proposals": [],
                "suggestions": [],
            },
        ),
    )
    run = AgentRun(
        id=slug_id("agent_run"),
        thread_id=doc.id,
        status="pending",
        user_message_id=user_message.id or "",
        assistant_message_id=assistant.id or "",
        context_snapshot=build_agent_context_snapshot(doc, attachments),
        steps=agent_steps(active="intent"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    assistant.refs["agent_run_id"] = run.id
    doc.agent_runs.insert(0, run)
    doc.active_surface = "thread"
    updated = write_thread(doc)
    return AgentRunStartResponse(
        thread=updated,
        run=run,
        user_message_id=user_message.id or "",
        assistant_message_id=assistant.id or "",
    )


@app.post("/api/vnext/threads/{thread_id}/agent-runs/{run_id}/complete", response_model=AgentRunResponse)
def complete_agent_run_endpoint(thread_id: str, run_id: str, payload: AgentRunCompleteRequest | None = None) -> AgentRunResponse:
    raise HTTPException(status_code=410, detail="Agent v1 已转为只读历史，请使用 Agent Runtime v2")
    doc = load_thread(thread_id)
    run = find_agent_run(doc, run_id)
    doc, run, assistant, degraded = complete_agent_run(doc, run, payload.model if payload else None)
    updated = merge_agent_completion(doc, run, assistant)
    return AgentRunResponse(thread=updated, run=find_agent_run(updated, run.id), assistant_message=find_message(updated, assistant.id or ""), degraded=degraded)


@app.post("/api/vnext/threads/{thread_id}/agent-runs/{run_id}/stream")
def stream_agent_run_endpoint(thread_id: str, run_id: str, payload: AgentRunCompleteRequest | None = None) -> StreamingResponse:
    raise HTTPException(status_code=410, detail="Agent v1 已转为只读历史，请使用 Agent Runtime v2")
    return StreamingResponse(
        stream_agent_run_events(thread_id, run_id, payload.model if payload else None, payload.retry if payload else False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/vnext/threads/{thread_id}/agent-runs/{run_id}/retry", response_model=AgentRunResponse)
def retry_agent_run_endpoint(thread_id: str, run_id: str, payload: AgentRunCompleteRequest | None = None) -> AgentRunResponse:
    raise HTTPException(status_code=410, detail="Agent v1 已转为只读历史，请使用 Agent Runtime v2 resume 接口")
    doc = load_thread(thread_id)
    run = find_agent_run(doc, run_id)
    assistant = find_message(doc, run.assistant_message_id)
    assistant.content = "正在重新运行主对话智能体..."
    assistant.status = "pending"
    assistant.refs = scrub_refs({
        **(assistant.refs or {}),
        "steps": [step.model_dump(mode="json") for step in agent_steps(active="intent")],
        "tool_calls": [],
        "action_proposals": [],
        "suggestions": [],
        "error": "",
    })
    run.status = "pending"
    run.steps = agent_steps(active="intent")
    run.tool_calls = []
    run.proposals = []
    run.error = ""
    run.attempt += 1
    run.context_snapshot = build_agent_context_snapshot(doc, (find_message(doc, run.user_message_id).refs or {}).get("turn_attachments") or [])
    run.updated_at = utc_now()
    update_agent_run_in_doc(doc, run)
    doc, run, assistant, degraded = complete_agent_run(doc, run, payload.model if payload else None)
    updated = merge_agent_completion(doc, run, assistant)
    return AgentRunResponse(thread=updated, run=find_agent_run(updated, run.id), assistant_message=find_message(updated, assistant.id or ""), degraded=degraded)


@app.get("/api/vnext/threads/{thread_id}/agent-runs/{run_id}/events")
def replay_agent_run_events(thread_id: str, run_id: str, after_seq: int = 0) -> StreamingResponse:
    doc = load_thread(thread_id)
    run = find_agent_run(doc, run_id)

    def replay() -> Iterator[str]:
        buffered = [item for item in APP_SERVICES.legacy_agent_event_buffers.get(run_id, []) if int(item.get("data", {}).get("seq", 0)) > after_seq]
        if buffered:
            for item in buffered:
                yield sse_event(str(item.get("event") or "step"), item.get("data") or {})
            return
        for step in run.steps:
            yield sse_event("step", {**step.model_dump(mode="json"), "seq": after_seq + 1})
        for call in run.tool_calls:
            yield sse_event("tool_call", {**call.model_dump(mode="json"), "seq": after_seq + 2})
        if run.status in {"done", "waiting_confirmation", "failed", "cancelled", "interrupted"}:
            assistant = find_message(doc, run.assistant_message_id)
            yield sse_event(
                "done",
                {
                    "thread": doc.model_dump(mode="json"),
                    "run": run.model_dump(mode="json"),
                    "assistant_message": assistant.model_dump(mode="json"),
                    "degraded": bool((assistant.refs or {}).get("degraded")),
                    "seq": max(run.event_seq, after_seq + 3),
                },
            )

    return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/vnext/threads/{thread_id}/agent-runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel_agent_run(thread_id: str, run_id: str) -> AgentRunResponse:
    raise HTTPException(status_code=410, detail="Agent v1 已转为只读历史")
    doc = load_thread(thread_id)
    run = find_agent_run(doc, run_id)
    if run.status not in {"pending", "planning", "running"}:
        assistant = find_message(doc, run.assistant_message_id)
        return AgentRunResponse(thread=doc, run=run, assistant_message=assistant, degraded=False)
    APP_SERVICES.cancelled_legacy_agent_runs.add(run_id)
    run.status = "cancelled"
    run.updated_at = utc_now()
    run.error = "用户停止了本轮运行"
    assistant = find_message(doc, run.assistant_message_id)
    assistant.status = "done"
    assistant.content = "正在停止本轮 Main Agent 运行..."
    updated = write_thread(doc)
    return AgentRunResponse(thread=updated, run=find_agent_run(updated, run_id), assistant_message=find_message(updated, assistant.id or ""), degraded=False)


@app.get("/api/vnext/threads/{thread_id}/agent-runs/{run_id}", response_model=AgentRun)
def get_agent_run(thread_id: str, run_id: str) -> AgentRun:
    return find_agent_run(load_thread(thread_id), run_id)


def set_model_field(model: BaseModel, field: str, value: Any) -> None:
    if field not in model.__class__.model_fields:
        raise HTTPException(status_code=400, detail=f"unsupported field: {field}")
    validated = model.__class__.model_validate({**model.model_dump(mode="json"), field: value})
    setattr(model, field, getattr(validated, field))


def apply_change_operation(
    thread_doc: ThreadDoc,
    operation: ChangeOperation,
    memories: dict[tuple[str, str, str], ObjectMemory],
    atlas_docs: dict[str, AtlasUpdateDoc],
    *,
    reverse: bool = False,
) -> dict[str, Any]:
    target = operation.target or {}
    value = operation.before if reverse else operation.after
    op = {"add": "remove", "remove": "add"}.get(operation.op, operation.op) if reverse else operation.op
    field = json_pointer_field(operation.path)
    if operation.target_type == "context":
        if operation.path.endswith("/-"):
            if op == "add":
                raw = value if isinstance(value, dict) else {}
                card = ContextCard(
                    id=safe_run_text(raw.get("id"), 120) or slug_id("card"),
                    type=raw.get("type") if raw.get("type") in {"paper", "relation", "path", "file"} else "paper",
                    title=safe_run_text(raw.get("title"), 220) or "Main Agent 上下文",
                    source_ref=scrub_refs(raw.get("source_ref") or target),
                    summary=safe_run_text(raw.get("summary"), 1800),
                    token_estimate=max(80, estimate_agent_tokens(raw.get("summary") or "")),
                    include_in_agent=True,
                    selected_for_export=bool(raw.get("selected_for_export", False)),
                )
                thread_doc.context_cards.append(card)
                operation.target = {**target, "card_id": card.id}
                return {"context_card_id": card.id}
            card_id = safe_run_text(target.get("card_id") or (operation.after or {}).get("id"), 120)
            thread_doc.context_cards = [item for item in thread_doc.context_cards if item.id != card_id]
            return {"removed_context_card_id": card_id}
        card = next((item for item in thread_doc.context_cards if item.id == target.get("card_id")), None)
        if not card:
            raise HTTPException(status_code=409, detail="context card no longer exists")
        if op == "remove":
            value = False if isinstance(getattr(card, field, None), bool) else ""
        set_model_field(card, field, value)
        return {"context_card_id": card.id, "field": field}
    if operation.target_type == "canvas":
        if operation.path.endswith("/-"):
            if field == "nodes":
                raw = value if isinstance(value, dict) else {}
                if op == "add":
                    node = CanvasNode.model_validate({"id": raw.get("id") or slug_id("node"), **raw})
                    thread_doc.canvas.nodes.append(node)
                    operation.target = {**target, "node_id": node.id}
                    return {"canvas_node_id": node.id}
                node_id = safe_run_text(target.get("node_id") or (operation.after or {}).get("id"), 120)
                thread_doc.canvas.nodes = [item for item in thread_doc.canvas.nodes if item.id != node_id]
                thread_doc.canvas.edges = [edge for edge in thread_doc.canvas.edges if edge.source != node_id and edge.target != node_id]
                return {"removed_canvas_node_id": node_id}
            raw = value if isinstance(value, dict) else {}
            if op == "add":
                edge = CanvasEdge.model_validate({"id": raw.get("id") or slug_id("edge"), **raw})
                node_ids = {item.id for item in thread_doc.canvas.nodes}
                if edge.source not in node_ids or edge.target not in node_ids:
                    raise HTTPException(status_code=409, detail="canvas edge references a missing node")
                thread_doc.canvas.edges.append(edge)
                operation.target = {**target, "edge_id": edge.id}
                return {"canvas_edge_id": edge.id}
            edge_id = safe_run_text(target.get("edge_id") or (operation.after or {}).get("id"), 120)
            thread_doc.canvas.edges = [item for item in thread_doc.canvas.edges if item.id != edge_id]
            return {"removed_canvas_edge_id": edge_id}
        collection = thread_doc.canvas.edges if target.get("edge_id") else thread_doc.canvas.nodes
        item_id = target.get("edge_id") or target.get("node_id")
        item = next((entry for entry in collection if entry.id == item_id), None)
        if not item:
            raise HTTPException(status_code=409, detail="canvas target no longer exists")
        set_model_field(item, field, value)
        return {"canvas_id": item_id, "field": field}
    if operation.target_type == "object_memory":
        atlas_id = safe_run_text(target.get("atlas_id") or thread_doc.active_atlas_id, 80)
        object_type = safe_run_text(target.get("object_type") or "paper", 40)
        object_id = safe_run_text(target.get("object_id"), 160)
        key = (atlas_id, object_type, object_id)
        memory = memories.get(key) or effective_object_memory(atlas_id, object_type, object_id) or ObjectMemory(
            object_ref={"atlas_id": atlas_id, "object_type": object_type, "object_id": object_id},
            title_snapshot=safe_run_text(target.get("title"), 220),
        )
        set_model_field(memory, field, value)
        memories[key] = memory
        return {"object_ref": memory.object_ref, "field": field}
    atlas_id = safe_run_text(target.get("atlas_id") or thread_doc.active_atlas_id, 80)
    atlas_doc = atlas_docs.get(atlas_id) or load_atlas_updates(atlas_id)
    atlas_docs[atlas_id] = atlas_doc
    if operation.path.endswith("/-"):
        if op == "add":
            raw = value if isinstance(value, dict) else {}
            candidate = AtlasUpdateCandidate.model_validate(
                {
                    "id": raw.get("id") or slug_id("candidate"),
                    "title": raw.get("title") or "未命名候选论文",
                    "source_run_id": operation.target.get("source_run_id"),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                    **raw,
                }
            )
            atlas_doc.candidates.append(candidate)
            operation.target = {**target, "candidate_id": candidate.id}
            return {"candidate_id": candidate.id, "atlas_id": atlas_id}
        candidate_id = safe_run_text(target.get("candidate_id") or (operation.after or {}).get("id"), 160)
        atlas_doc.candidates = [item for item in atlas_doc.candidates if item.id != candidate_id]
        return {"removed_candidate_id": candidate_id, "atlas_id": atlas_id}
    candidate = next((item for item in atlas_doc.candidates if item.id == target.get("candidate_id")), None)
    if not candidate:
        raise HTTPException(status_code=409, detail="atlas candidate no longer exists")
    set_model_field(candidate, field, value)
    candidate.updated_at = utc_now()
    return {"candidate_id": candidate.id, "field": field, "atlas_id": atlas_id}


def changeset_conflicts(doc: ThreadDoc, changeset: ChangeSet, operations: list[ChangeOperation], *, reverse: bool = False) -> list[dict[str, Any]]:
    conflicts = []
    for operation in operations:
        current = current_change_value(doc, operation)
        expected = operation.after if reverse else operation.before
        if current != expected:
            conflicts.append({"operation_id": operation.id, "path": operation.path, "expected": expected, "current": current})
    return conflicts


def transaction_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def restore_transaction_snapshot(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        _mirror_restored_record(path, None)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".restore-{uuid.uuid4().hex[:8]}")
    tmp.write_bytes(snapshot)
    os.replace(tmp, path)
    _mirror_restored_record(path, json.loads(snapshot.decode("utf-8")))


def _mirror_restored_record(path: Path, payload: dict[str, Any] | None) -> None:
    kind = ""
    record_id = path.stem
    if path.parent == THREADS_DIR:
        kind = "thread"
    elif path.parent == PROJECTS_DIR:
        kind = "project"
    elif path.parent == LAB_RUNS_DIR:
        kind = "lab_run"
    elif path.parent == ATLAS_UPDATES_DIR:
        kind = "atlas_update"
    elif OBJECTS_DIR in path.parents:
        kind = "object_memory"
        if payload:
            ref = payload.get("object_ref") or {}
            record_id = f"{ref.get('atlas_id')}:{ref.get('object_type')}:{ref.get('object_id')}"
    if not kind:
        return
    if payload is None:
        get_research_store().delete_record(kind, record_id)
    else:
        get_research_store().save_record(kind, record_id, payload)


def commit_changeset(
    doc: ThreadDoc,
    changeset: ChangeSet,
    operations: list[ChangeOperation],
    *,
    reverse: bool = False,
) -> tuple[ThreadDoc, dict[str, Any]]:
    thread_copy = ThreadDoc.model_validate(doc.model_dump(mode="json"))
    memories: dict[tuple[str, str, str], ObjectMemory] = {}
    atlas_docs: dict[str, AtlasUpdateDoc] = {}
    applied = []
    for operation in operations:
        applied.append(apply_change_operation(thread_copy, operation, memories, atlas_docs, reverse=reverse))

    touched_paths = {safe_thread_path(doc.id)}
    for atlas_id, object_type, object_id in memories:
        touched_paths.add(safe_object_path(atlas_id, object_type, object_id))
    for atlas_id in atlas_docs:
        touched_paths.add(safe_atlas_update_path(atlas_id))
    snapshots = {path: transaction_snapshot(path) for path in touched_paths}
    transaction_id = slug_id("transaction")
    journal_path = PERSONAL_DIR / "transactions" / f"{transaction_id}.json"
    atomic_write_json(
        journal_path,
        {"id": transaction_id, "changeset_id": changeset.id, "thread_id": doc.id, "status": "committing", "targets": [path.name for path in touched_paths], "created_at": utc_now()},
    )
    try:
        for (atlas_id, object_type, object_id), memory in memories.items():
            write_object_memory(atlas_id, object_type, object_id, memory)
        for atlas_doc in atlas_docs.values():
            write_atlas_updates(atlas_doc)
        changeset_in_copy = find_changeset(thread_copy, changeset.id)
        operation_map = {item.id: item for item in operations}
        changeset_in_copy.operations = [operation_map.get(item.id, item) for item in changeset_in_copy.operations]
        changeset_in_copy.status = "undone" if reverse else "applied"
        changeset_in_copy.updated_at = utc_now()
        if reverse:
            changeset_in_copy.undone_at = changeset_in_copy.updated_at
        else:
            changeset_in_copy.applied_at = changeset_in_copy.updated_at
        append_message(
            thread_copy,
            Message(
                role="tool",
                kind="state",
                content=("已撤销变更集：" if reverse else "已应用变更集：") + changeset.summary,
                status="done",
                surface="thread",
                refs={"changeset_id": changeset.id, "operation_ids": [item.id for item in operations], "undo": reverse},
            ),
        )
        updated = write_thread(thread_copy)
        atomic_write_json(journal_path, {"id": transaction_id, "changeset_id": changeset.id, "status": "committed", "updated_at": utc_now()})
        return updated, {"transaction_id": transaction_id, "operations": applied}
    except Exception:
        for path, snapshot in snapshots.items():
            restore_transaction_snapshot(path, snapshot)
        atomic_write_json(journal_path, {"id": transaction_id, "changeset_id": changeset.id, "status": "rolled_back", "updated_at": utc_now()})
        raise


@app.get("/api/vnext/threads/{thread_id}/changesets/{changeset_id}", response_model=ChangeSet)
def get_changeset(thread_id: str, changeset_id: str) -> ChangeSet:
    return find_changeset(load_thread(thread_id), changeset_id)


@app.post("/api/vnext/threads/{thread_id}/changesets/{changeset_id}/confirm", response_model=ChangeSetResponse)
def confirm_changeset(thread_id: str, changeset_id: str, payload: ChangeSetConfirmRequest) -> ChangeSetResponse:
    doc = load_thread(thread_id)
    changeset = find_changeset(doc, changeset_id)
    if changeset.status not in {"pending", "conflicted"}:
        raise HTTPException(status_code=400, detail="changeset is not pending")
    if payload.expected_revision is not None and payload.expected_revision != doc.revision:
        raise HTTPException(status_code=409, detail={"message": "线程版本已变化", "current_revision": doc.revision})
    selected_ids = set(payload.selected_operation_ids or [item.id for item in changeset.operations if item.selected])
    for item in changeset.operations:
        item.selected = item.id in selected_ids
        if item.id in payload.edited_values:
            item.after = scrub_refs(payload.edited_values[item.id])
    operations = [ChangeOperation.model_validate(item.model_dump(mode="json")) for item in changeset.operations if item.id in selected_ids]
    conflicts = changeset_conflicts(doc, changeset, operations)
    if conflicts:
        changeset.status = "conflicted"
        changeset.conflicts = conflicts
        changeset.updated_at = utc_now()
        write_thread(doc)
        raise HTTPException(status_code=409, detail={"message": "变更目标已发生变化", "conflicts": conflicts})
    updated, applied = commit_changeset(doc, changeset, operations)
    return ChangeSetResponse(thread=updated, changeset=find_changeset(updated, changeset_id), applied=applied)


@app.post("/api/vnext/threads/{thread_id}/changesets/{changeset_id}/reject", response_model=ChangeSetResponse)
def reject_changeset(thread_id: str, changeset_id: str) -> ChangeSetResponse:
    doc = load_thread(thread_id)
    changeset = find_changeset(doc, changeset_id)
    if changeset.status not in {"pending", "conflicted"}:
        raise HTTPException(status_code=400, detail="changeset is not pending")
    changeset.status = "rejected"
    changeset.updated_at = utc_now()
    append_message(doc, Message(role="tool", kind="state", content=f"已驳回变更集：{changeset.summary}", surface="thread", refs={"changeset_id": changeset.id}))
    updated = write_thread(doc)
    return ChangeSetResponse(thread=updated, changeset=find_changeset(updated, changeset_id), applied={})


@app.post("/api/vnext/threads/{thread_id}/changesets/{changeset_id}/undo", response_model=ChangeSetResponse)
def undo_changeset(thread_id: str, changeset_id: str) -> ChangeSetResponse:
    doc = load_thread(thread_id)
    changeset = find_changeset(doc, changeset_id)
    if changeset.status != "applied":
        raise HTTPException(status_code=400, detail="only an applied changeset can be undone")
    operations = [item for item in changeset.operations if item.selected]
    conflicts = changeset_conflicts(doc, changeset, operations, reverse=True)
    if conflicts:
        raise HTTPException(status_code=409, detail={"message": "当前值已变化，无法安全撤销", "conflicts": conflicts})
    updated, applied = commit_changeset(doc, changeset, list(reversed(operations)), reverse=True)
    return ChangeSetResponse(thread=updated, changeset=find_changeset(updated, changeset_id), applied=applied)


@app.post("/api/vnext/threads/{thread_id}/action-proposals/{proposal_id}/confirm", response_model=ProposalConfirmResponse)
def confirm_action_proposal(thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
    doc = load_thread(thread_id)
    proposal = find_action_proposal(doc, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="proposal is not pending")
    if proposal.type == "context_injection":
        applied = apply_context_injection(doc, proposal)
    elif proposal.type in {"object_memory", "paper_card_update"}:
        applied = apply_object_memory_proposal(proposal)
    elif proposal.type == "atlas_candidate":
        applied = apply_atlas_candidate_proposal(proposal)
    elif proposal.type == "task_pack_preview":
        applied = {"preview": True}
    else:
        raise HTTPException(status_code=400, detail="unsupported proposal type")
    proposal.status = "confirmed"
    proposal.updated_at = utc_now()
    append_message(
        doc,
        Message(
            role="tool",
            kind="state",
            content=f"已确认智能体提案：{proposal.summary}",
            status="done",
            surface="thread",
            refs={"proposal_id": proposal.id, "proposal_type": proposal.type, "applied": applied},
        ),
    )
    for run in doc.agent_runs:
        if run.id == proposal.source_run_id:
            run.proposals = [proposal if item.id == proposal.id else item for item in run.proposals]
            run.updated_at = utc_now()
            if not any(item.status == "pending" for item in run.proposals):
                run.status = "done"
    updated = write_thread(doc)
    return ProposalConfirmResponse(thread=updated, proposal=proposal, applied=applied)


@app.post("/api/vnext/threads/{thread_id}/action-proposals/{proposal_id}/reject", response_model=ProposalConfirmResponse)
def reject_action_proposal(thread_id: str, proposal_id: str) -> ProposalConfirmResponse:
    doc = load_thread(thread_id)
    proposal = find_action_proposal(doc, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail="proposal is not pending")
    proposal.status = "rejected"
    proposal.updated_at = utc_now()
    append_message(
        doc,
        Message(
            role="tool",
            kind="state",
            content=f"已驳回智能体提案：{proposal.summary}",
            status="done",
            surface="thread",
            refs={"proposal_id": proposal.id, "proposal_type": proposal.type},
        ),
    )
    for run in doc.agent_runs:
        if run.id == proposal.source_run_id:
            run.proposals = [proposal if item.id == proposal.id else item for item in run.proposals]
            run.updated_at = utc_now()
            if not any(item.status == "pending" for item in run.proposals):
                run.status = "done"
    updated = write_thread(doc)
    return ProposalConfirmResponse(thread=updated, proposal=proposal, applied={})


@app.post("/api/vnext/threads/{thread_id}/context-injections", response_model=ThreadDoc)
def add_context_injection(thread_id: str, payload: ContextInjectionRequest) -> ThreadDoc:
    doc = load_thread(thread_id)
    source = scrub_refs(payload.source or {})
    card_type = source.get("type") if isinstance(source, dict) and source.get("type") in {"paper", "relation", "path", "file"} else "paper"
    title = safe_run_text(payload.title or (source.get("title") if isinstance(source, dict) else "") or "注入的上下文", 180)
    summary = safe_run_text(payload.summary or (source.get("summary") if isinstance(source, dict) else "") or "", 900)
    card = ContextCard(
        id=slug_id("card"),
        type=card_type,
        title=title,
        source_ref=source if isinstance(source, dict) else {},
        summary=summary,
        token_estimate=max(80, len(summary) // 2),
        selected_for_export=True,
        include_in_agent=True,
    )
    doc.context_cards.append(card)
    append_message(
        doc,
        Message(
            role="tool",
            kind="state",
            content=f"已把上下文送入主对话：{title}",
            status="done",
            surface="thread",
            refs={"context_card_id": card.id, "target": payload.target},
        ),
    )
    return write_thread(doc)


@app.post("/api/vnext/threads/{thread_id}/chat", response_model=ThreadChatResponse)
def chat_with_thread(thread_id: str, payload: ThreadChatRequest) -> ThreadChatResponse:
    raise HTTPException(status_code=410, detail="旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口")
    doc = load_thread(thread_id)
    user_text = safe_message_content(payload.message, 2400)
    if should_promote_thread_question(doc):
        doc.title = title_from_question(user_text)
        doc.goal = user_text
    append_message(
        doc,
        Message(
            role="user",
            kind="text",
            content=user_text,
            status="done",
            surface=payload.surface or doc.active_surface,
            refs={"active_atlas_id": doc.active_atlas_id},
        ),
    )

    degraded = False
    try:
        prompt = build_thread_chat_prompt(doc, user_text)
        raw_text, used_model = call_openai_thread_chat(prompt, payload.model)
        content, suggestions = parse_thread_chat_payload(raw_text, doc)
        if not suggestions:
            suggestions = fallback_thread_suggestions(doc)[:2]
        assistant = Message(
            role="assistant",
            kind="assistant_reply",
            content=content or "我已经收到。当前信息还不够完整，建议先从 Atlas 选择证据或补充 Canvas 结构。",
            status="done",
            surface=payload.surface or doc.active_surface,
            refs={
                "provider": "openai",
                "model": used_model,
                "suggestions": suggestions,
            },
        )
    except HTTPException as exc:
        degraded = True
        no_key = exc.status_code in {400, 401, 403}
        assistant = Message(
            role="assistant",
            kind="assistant_reply",
            content=(
                "我已记录你的问题，但当前没有可用的 API 密钥，所以无法自动回复。"
                "你可以先复制 Task Pack 给 Codex，或打开研究动作模板生成更明确的上下文。"
                if no_key
                else (
                    "我已记录你的问题，但这次 API 调用失败。"
                    "你可以稍后重试，或先复制 Task Pack 给 Codex。"
                )
            ),
            status="done",
            surface=payload.surface or doc.active_surface,
            refs={
                "degraded": True,
                "reason": safe_run_text(exc.detail, 180),
                "suggestions": clean_thread_suggestions(
                    [{"action": "preview_task_pack"}, {"action": "open_templates"}, {"action": "open_atlas"}],
                    doc,
                ),
            },
        )

    assistant_item = append_message(doc, assistant)
    doc.active_surface = "thread"
    updated = write_thread(doc)
    return ThreadChatResponse(thread=updated, assistant_message=assistant_item, degraded=degraded)


@app.put("/api/vnext/threads/{thread_id}/messages/{message_id}", response_model=ThreadDoc)
def update_message(thread_id: str, message_id: str, payload: MessageUpdate) -> ThreadDoc:
    doc = load_thread(thread_id)
    for message in doc.messages:
        if message.id == message_id:
            if payload.content is not None:
                message.content = safe_message_content(payload.content)
            if payload.status is not None:
                message.status = safe_run_text(payload.status, 32) or message.status
            if payload.refs is not None:
                message.refs = payload.refs
            if payload.linked_result_id is not None:
                message.linked_result_id = safe_run_text(payload.linked_result_id, 120) or None
            if payload.linked_tool_run_id is not None:
                message.linked_tool_run_id = safe_run_text(payload.linked_tool_run_id, 120) or None
            return write_thread(doc)
    raise HTTPException(status_code=404, detail="message not found")


@app.post("/api/vnext/threads/{thread_id}/tool-runs", response_model=ThreadDoc)
def add_tool_run(thread_id: str, payload: ToolRunCreate) -> ThreadDoc:
    doc = load_thread(thread_id)
    run = ToolRun(
        id=slug_id("tool"),
        tool=safe_run_text(payload.tool, 80) or "tool_run",
        status=safe_run_text(payload.status, 32) or "done",
        summary=safe_run_text(payload.summary),
        created_at=utc_now(),
        template_id=safe_run_text(payload.template_id, 80) or None,
        mode=payload.mode,
        provider=safe_run_text(payload.provider, 80) or None,
        model=safe_run_text(payload.model, 120) or None,
        token_estimate=payload.token_estimate,
        input_summary=safe_run_text(payload.input_summary),
    )
    doc.tool_runs.insert(0, run)
    append_message(
        doc,
        Message(
            role="tool",
            kind="tool_run",
            content=f"已记录工具运行：{run.summary or run.tool}",
            status=run.status,
            surface="tools",
            refs={"template_id": run.template_id, "mode": run.mode, "token_estimate": run.token_estimate},
            linked_tool_run_id=run.id,
        ),
    )
    return write_thread(doc)


def card_object_ref(card: ContextCard) -> tuple[str, str, str] | None:
    atlas_id = card.source_ref.get("atlas_id")
    if not atlas_id:
        return None
    if card.type == "paper" and card.source_ref.get("paper_id"):
        return atlas_id, "paper", str(card.source_ref["paper_id"])
    if card.type == "relation" and card.source_ref.get("relation_id"):
        return atlas_id, "relation", str(card.source_ref["relation_id"])
    if card.type == "path":
        object_id = card.source_ref.get("path_id")
        if not object_id and card.source_ref.get("paper_ids"):
            object_id = "path_" + "_".join(str(i) for i in card.source_ref["paper_ids"])
        if object_id:
            return atlas_id, "path", str(object_id)
    return None


RESEARCH_TEMPLATES: dict[str, ResearchTemplate] = {
    "atlas_gap": ResearchTemplate(
        id="atlas_gap",
        title="研究空白分析",
        short_title="空白分析",
        description="基于当前 Atlas 对象、论证结构和选中材料，找出尚未被充分解释的问题、证据缺口和可切入方向。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["关键空白", "证据边界", "可执行切入点", "下一步任务"],
    ),
    "method_evolution": ResearchTemplate(
        id="method_evolution",
        title="方法演化梳理",
        short_title="方法演化",
        description="沿年份、路线和关系解释方法如何演化，区分技术继承、替代、组合与断裂。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["演化阶段", "方法差异", "关键转折", "可写作结构"],
    ),
    "relation_explain": ResearchTemplate(
        id="relation_explain",
        title="关系/路径解释",
        short_title="关系解释",
        description="解释论文、关系或路径之间为什么相连，以及这些连接对当前研究问题意味着什么。",
        recommended_for=["relation", "path", "paper"],
        output_focus=["关系类型", "连接理由", "论证价值", "疑点与反例"],
    ),
    "candidate_audit": ResearchTemplate(
        id="candidate_audit",
        title="候选论文/关系审查",
        short_title="候选审查",
        description="审查候选论文或关系是否值得纳入当前上下文，给出保留、降级、暂缓或剔除建议。",
        recommended_for=["paper", "relation", "path", "canvas"],
        output_focus=["纳入判断", "证据质量", "风险", "后续核查任务"],
    ),
}


TEMPLATE_INSTRUCTIONS: dict[str, str] = {
    "atlas_gap": (
        "请做研究空白分析：先说明当前材料覆盖了什么，再指出仍未被充分回答的问题。"
        "区分事实证据、个人判断和你的推测；最后给出 3-5 个可以立刻推进的下一步任务。"
    ),
    "method_evolution": (
        "请梳理方法演化：按时间、路线或关系链拆成阶段，说明每一阶段解决了什么、遗留了什么、与下一阶段如何相连。"
        "输出要能直接转化为综述段落结构。"
    ),
    "relation_explain": (
        "请解释关系或路径：说明连接的依据、可能的关系类型、对当前研究问题的论证价值，以及需要警惕的过度解释。"
    ),
    "candidate_audit": (
        "请审查候选对象：判断每篇论文或每条关系是否值得纳入当前上下文，给出保留/降级/暂缓/剔除建议，"
        "并列出需要人工核查的证据。"
    ),
}


def card_type_label(card_type: str | None) -> str:
    return {
        "paper": "论文",
        "relation": "关系",
        "path": "路径",
        "file": "文件",
        "canvas_node": "Canvas 节点",
    }.get(card_type or "", card_type or "对象")


def render_memory(memory: ObjectMemory | None) -> str:
    if not memory:
        return ""
    parts = []
    if memory.star:
        parts.append("- personal_star: true")
    if memory.maturity:
        parts.append(f"- maturity: {memory.maturity}/5")
    if memory.tags:
        parts.append(f"- tags: {', '.join(memory.tags)}")
    if memory.judgement:
        parts.append(f"- personal_judgement: {memory.judgement}")
    if memory.note:
        parts.append(f"- personal_note: {memory.note}")
    if memory.core_innovation:
        parts.append(f"- core_innovation: {memory.core_innovation}")
    if memory.core_technology:
        parts.append(f"- core_technology: {memory.core_technology}")
    if memory.evidence:
        parts.append(f"- evidence: {memory.evidence}")
    if memory.limitations:
        parts.append(f"- limitations: {memory.limitations}")
    if memory.reusable_insight:
        parts.append(f"- reusable_insight: {memory.reusable_insight}")
    if memory.reading_questions:
        parts.append(f"- reading_questions: {', '.join(memory.reading_questions)}")
    return "\n".join(parts)


NODE_LABELS = {
    "question": "问题",
    "hypothesis": "假设",
    "conclusion": "结论",
    "material": "材料",
    "task": "任务",
}

EDGE_LABELS = {
    "supports": "支持",
    "challenges": "质疑",
    "leads_to": "推出",
    "requires": "需要",
}


def render_card(card: ContextCard, idx: int) -> str:
    source = json.dumps(card.source_ref, ensure_ascii=False)
    memory = None
    ref = card_object_ref(card)
    if ref:
        memory = effective_object_memory(*ref)
    memory_text = render_memory(memory)
    return (
        f"### {idx}. [{card.type}] {card.title}\n"
        f"- 来源引用: `{source}`\n"
        f"- 估算 token: {card.token_estimate}\n"
        f"- 摘要: {card.summary or '(无)'}\n"
        + (f"{memory_text}\n" if memory_text else "")
    )


def normalize_edge_label(label: str) -> str:
    return label if label in EDGE_LABELS else "supports"


def canvas_children(node_id: str, edges: list[CanvasEdge], nodes_by_id: dict[str, CanvasNode]) -> list[tuple[str, CanvasNode]]:
    children = []
    for edge in edges:
        if edge.source == node_id and edge.target in nodes_by_id:
            children.append((normalize_edge_label(edge.label), nodes_by_id[edge.target]))
    return children


def render_canvas(doc: ThreadDoc) -> str:
    nodes = doc.canvas.nodes
    edges = doc.canvas.edges
    if not nodes:
        return "(暂无 Canvas 节点)"
    nodes_by_id = {node.id: node for node in nodes}
    referenced = {edge.target for edge in edges}
    roots = [node for node in nodes if node.type == "question"] or [node for node in nodes if node.id not in referenced] or nodes
    lines = []
    seen: set[str] = set()

    def walk(node: CanvasNode, depth: int = 0) -> None:
        if node.id in seen:
            return
        seen.add(node.id)
        indent = "  " * depth
        meta = []
        if node.status:
            meta.append(f"状态: {node.status}")
        if node.priority is not None:
            meta.append(f"优先级: {node.priority}")
        suffix = f" ({'; '.join(meta)})" if meta else ""
        lines.append(f"{indent}- {NODE_LABELS.get(node.type, node.type)}: {node.title}{suffix}")
        if node.body:
            lines.append(f"{indent}  - 说明: {node.body}")
        for label, child in canvas_children(node.id, edges, nodes_by_id):
            lines.append(f"{indent}  - {EDGE_LABELS.get(label, label)} -> {NODE_LABELS.get(child.type, child.type)}: {child.title}")
            walk(child, depth + 2)

    for root in roots:
        walk(root)
    remaining = [node for node in nodes if node.id not in seen]
    if remaining:
        lines.append("\n未连接节点:")
        for node in remaining:
            lines.append(f"- {NODE_LABELS.get(node.type, node.type)}: {node.title}" + (f" — {node.body}" if node.body else ""))
    return "\n".join(lines)


def selected_context_cards(doc: ThreadDoc, selected_only: bool) -> list[ContextCard]:
    return [
        card
        for card in doc.context_cards
        if card.selected_for_export or not selected_only
    ]


def focused_object_ref(focused: FocusedObject | None, fallback_atlas_id: str) -> tuple[str, str, str] | None:
    if not focused or not focused.type or not focused.id:
        return None
    if focused.type not in {"paper", "relation", "path", "file"}:
        return None
    atlas_id = str(focused.source_ref.get("atlas_id") or fallback_atlas_id)
    return atlas_id, str(focused.type), str(focused.id)


def render_focused_object(focused: FocusedObject | None, doc: ThreadDoc) -> str:
    if not focused or not (focused.title or focused.id):
        return "(当前没有聚焦对象，默认使用 Canvas 和已选上下文。)"
    source = json.dumps(focused.source_ref, ensure_ascii=False)
    memory_text = ""
    ref = focused_object_ref(focused, doc.active_atlas_id)
    if ref:
        memory_text = render_memory(effective_object_memory(*ref))
    lines = [
        f"- 类型: {card_type_label(focused.type)}",
        f"- 标题: {focused.title or focused.id}",
        f"- 对象 ID: {focused.id or '(无)'}",
    ]
    if focused.summary:
        lines.append(f"- 摘要: {focused.summary}")
    if focused.source_ref:
        lines.append(f"- 来源引用: `{source}`")
    if memory_text:
        lines.append("### 聚焦对象记忆")
        lines.append(memory_text)
    return "\n".join(lines)


def render_project_context(doc: ThreadDoc) -> str:
    if not doc.project_id:
        return "- 成果项目: 未归档成果"
    try:
        project = load_project(doc.project_id)
    except HTTPException:
        return f"- 成果项目: {doc.project_id}"
    lines = [f"- 成果项目: {project.title}"]
    if project.goal:
        lines.append(f"- 项目目标: {project.goal}")
    return "\n".join(lines)


def build_task_pack(doc: ThreadDoc, payload: TaskPackPreviewRequest) -> TaskPackPreviewResponse:
    template = RESEARCH_TEMPLATES.get(payload.template_id)
    if not template:
        raise HTTPException(status_code=400, detail="unknown research template")
    cards = selected_context_cards(doc, payload.selected_only)
    warnings: list[str] = []
    if not cards:
        warnings.append("当前没有选中的 Context card，Task Pack 将主要依赖 Canvas 和聚焦对象。")
    if not doc.canvas.nodes:
        warnings.append("当前 Context Canvas 为空，建议先至少保留一个问题节点。")

    instruction = payload.instruction_override or TEMPLATE_INSTRUCTIONS[template.id]
    markdown = "\n".join(
        [
            f"# EAI Task Pack：{template.title}",
            "",
            "## 当前研究问题",
            f"- 线程: {doc.title}",
            f"- 目标: {doc.goal or '(未填写目标)'}",
            render_project_context(doc),
            f"- 当前 Atlas: {doc.active_atlas_id}",
            "",
            "## 聚焦对象",
            render_focused_object(payload.focused_object, doc),
            "",
            "## Context Canvas 论证结构",
            render_canvas(doc),
            "",
            "## 选中上下文材料",
            "\n".join(render_card(card, i + 1) for i, card in enumerate(cards))
            or "(暂无选中的上下文卡片)",
            "",
            "## 本次研究动作",
            f"- 模板: {template.title}",
            f"- 输出重点: {'、'.join(template.output_focus)}",
            instruction,
            "",
            "## 输出要求",
            "- 使用中文输出。",
            "- 明确区分已有证据、个人判断和推测。",
            "- 不要假装读过未提供的论文全文；缺失信息请标注“未知”。",
            "- 请给出可沉淀到 Context Canvas 的结论和下一步任务。",
            "",
            "## 返回格式",
            "如适合结构化返回，请包含一个 eai-result/v1 JSON 代码块：",
            "```json",
            json.dumps(
                {
                    "schema_version": "eai-result/v1",
                    "summary": "...",
                    "findings": [
                        {"title": "...", "body": "..."}
                    ],
                    "candidate_papers": [],
                    "candidate_relations": [],
                    "next_tasks": [
                        {"title": "...", "body": "..."}
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )
    return TaskPackPreviewResponse(
        template_id=template.id,
        title=template.title,
        markdown=markdown,
        token_estimate=max(120, len(markdown) // 3),
        included_cards=len(cards),
        warnings=warnings,
    )


def render_lab_source_context(run: LabRun, doc: ThreadDoc | None) -> str:
    lines = []
    if doc:
        lines.extend(
            [
                "## 当前研究问题",
                f"- 线程: {doc.title}",
                f"- 目标: {doc.goal or '(未填写)'}",
                render_project_context(doc),
                f"- 当前 Atlas: {doc.active_atlas_id}",
                "",
                "## Context Canvas 结构",
                render_canvas(doc),
                "",
                "## 选中材料与对象记忆",
                "\n".join(render_card(card, index + 1) for index, card in enumerate(selected_context_cards(doc, True)))
                or "(暂无选中材料)",
            ]
        )
    if run.source_refs:
        lines.extend(
            [
                "",
                "## 实验来源引用",
                json.dumps(scrub_refs(run.source_refs), ensure_ascii=False, indent=2),
            ]
        )
    return "\n".join(lines)


def parse_lab_payload(raw_text: str) -> dict[str, Any] | None:
    matches = re.findall(r"```(?:json|eai-lab-run/v1)?\s*([\s\S]*?)```", raw_text)
    candidates = matches[:]
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def coerce_lab_stage(raw: Any) -> LabStage | None:
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        return None
    title = safe_run_text(raw.get("title") or raw.get("name"), 160)
    if not title:
        return None
    status = str(raw.get("status") or "planned")
    if status not in {"planned", "running", "blocked", "failed", "completed"}:
        status = "planned"
    return LabStage(
        id=safe_id_or_slug(raw.get("id"), "stage"),
        title=title,
        status=status,  # type: ignore[arg-type]
        summary=str(raw.get("summary") or raw.get("description") or "")[:1200],
        command=str(raw.get("command") or raw.get("cmd") or "")[:1200],
        notes=str(raw.get("notes") or raw.get("risk") or "")[:1200],
        updated_at=utc_now(),
    )


def coerce_lab_artifact(raw: Any, stage_by_title: dict[str, str]) -> LabArtifact | None:
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        return None
    title = safe_run_text(raw.get("title") or raw.get("name"), 180)
    if not title:
        return None
    stage_title = safe_run_text(raw.get("stage_title") or raw.get("stage"), 180)
    return LabArtifact(
        id=safe_id_or_slug(raw.get("id"), "artifact"),
        type=safe_run_text(raw.get("type"), 40) or "note",
        title=title,
        summary=str(raw.get("summary") or raw.get("description") or "")[:1600],
        uri=safe_run_text(raw.get("uri") or raw.get("path") or raw.get("url"), 500),
        content_preview=str(raw.get("content_preview") or raw.get("snippet") or "")[:2400],
        stage_id=stage_by_title.get(stage_title),
        created_at=utc_now(),
    )


def coerce_lab_finding(raw: Any) -> LabFinding | None:
    title, body = text_from_item(raw)
    title = title[:160].strip()
    if not title:
        return None
    confidence = raw.get("confidence") if isinstance(raw, dict) else ""
    return LabFinding(
        id=safe_id_or_slug(raw.get("id") if isinstance(raw, dict) else None, "finding"),
        title=title,
        body=body[:1600],
        confidence=safe_run_text(confidence, 80),
        source_stage_id=safe_run_text(raw.get("stage_id"), 120) if isinstance(raw, dict) else None,
        created_at=utc_now(),
    )


def default_lab_stages() -> list[LabStage]:
    now = utc_now()
    return [
        LabStage(id=slug_id("stage"), title="\u521d\u59cb\u5b9e\u73b0", summary="\u786e\u8ba4\u5b9e\u9a8c\u5165\u53e3\u3001\u6570\u636e\u3001\u4f9d\u8d56\u548c\u6700\u5c0f\u53ef\u8fd0\u884c\u8def\u5f84\u3002", updated_at=now),
        LabStage(id=slug_id("stage"), title="\u57fa\u7ebf\u590d\u73b0", summary="\u590d\u73b0\u6216\u767b\u8bb0\u5f53\u524d\u53ef\u6bd4\u8f83\u57fa\u7ebf\uff0c\u660e\u786e\u6307\u6807\u4e0e\u8bc4\u4f30\u811a\u672c\u3002", updated_at=now),
        LabStage(id=slug_id("stage"), title="\u521b\u65b0\u5b9e\u9a8c", summary="\u9a8c\u8bc1\u5f53\u524d\u5047\u8bbe\u5e26\u6765\u7684\u5173\u952e\u53d8\u5316\uff0c\u5e76\u8bb0\u5f55\u5bf9\u7167\u8bbe\u7f6e\u3002", updated_at=now),
        LabStage(id=slug_id("stage"), title="\u6d88\u878d\u5b9e\u9a8c", summary="\u62c6\u5206\u5173\u952e\u6a21\u5757\u6216\u53d8\u91cf\uff0c\u786e\u8ba4\u8d21\u732e\u6765\u6e90\u3002", updated_at=now),
        LabStage(id=slug_id("stage"), title="\u7ed3\u679c\u5206\u6790", summary="\u6574\u7406\u6307\u6807\u3001\u5931\u8d25\u539f\u56e0\u3001\u5c40\u9650\u548c\u4e0b\u4e00\u6b65\u3002", updated_at=now),
    ]


def build_lab_result_preview(run: LabRun, raw_text: str) -> LabResultPreviewResponse:
    raw = raw_text.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="empty lab result text")
    parsed = parse_lab_payload(raw)
    summary = parsed.get("summary") if parsed else None
    stage_items = [stage for stage in (coerce_lab_stage(item) for item in ((parsed or {}).get("stages") or [])) if stage]
    stage_by_title = {stage.title: stage.id or "" for stage in stage_items}
    artifact_items = [
        artifact
        for artifact in (coerce_lab_artifact(item, stage_by_title) for item in ((parsed or {}).get("artifacts") or []))
        if artifact
    ]
    finding_items = [
        finding
        for finding in (coerce_lab_finding(item) for item in ((parsed or {}).get("findings") or []))
        if finding
    ]
    next_tasks: list[CanvasNode] = []
    canvas_nodes: list[CanvasNode] = []
    canvas_edges: list[CanvasEdge] = []
    source_id = run.linked_canvas_nodes[0] if run.linked_canvas_nodes else ""
    for idx, finding in enumerate(finding_items):
        node = CanvasNode(
            id=slug_id("conclusion"),
            type="conclusion",
            title=finding.title,
            body=finding.body,
            x=520,
            y=110 + idx * 118,
        )
        canvas_nodes.append(node)
        if source_id:
            canvas_edges.append(CanvasEdge(id=slug_id("edge"), source=source_id, target=node.id, label="leads_to"))
    for idx, artifact in enumerate(artifact_items[:8]):
        node = CanvasNode(
            id=slug_id("material"),
            type="material",
            title=artifact.title,
            body=artifact.summary or artifact.content_preview,
            x=768,
            y=110 + idx * 118,
        )
        canvas_nodes.append(node)
        if source_id:
            canvas_edges.append(CanvasEdge(id=slug_id("edge"), source=source_id, target=node.id, label="supports"))
    for idx, item in enumerate((parsed or {}).get("next_tasks") or []):
        title, body = text_from_item(item)
        task = CanvasNode(
            id=slug_id("task"),
            type="task",
            title=title[:120] or "新的实验任务",
            body=body,
            x=1006,
            y=110 + idx * 118,
            status="todo",
            priority=1,
        )
        next_tasks.append(task)
        canvas_nodes.append(task)
        if source_id:
            canvas_edges.append(CanvasEdge(id=slug_id("edge"), source=source_id, target=task.id, label="requires"))
    return LabResultPreviewResponse(
        result_card_preview=ResultCard(
            id=slug_id("result"),
            title=summary or f"实验结果：{run.title}",
            raw_text=raw,
            parsed_json=parsed,
            created_at=utc_now(),
        ),
        stages=stage_items,
        artifacts=artifact_items,
        findings=finding_items,
        next_tasks=next_tasks,
        canvas_nodes=canvas_nodes,
        canvas_edges=canvas_edges,
        apply_items=[
            *[
                LabResultApplyItem(id=f"stage:{index}", kind="stage", title=item.title, summary=item.summary)
                for index, item in enumerate(stage_items)
            ],
            *[
                LabResultApplyItem(id=f"artifact:{index}", kind="artifact", title=item.title, summary=item.summary or item.content_preview)
                for index, item in enumerate(artifact_items)
            ],
            *[
                LabResultApplyItem(id=f"finding:{index}", kind="finding", title=item.title, summary=item.body)
                for index, item in enumerate(finding_items)
            ],
            *[
                LabResultApplyItem(id=f"task:{index}", kind="task", title=item.title, summary=item.body)
                for index, item in enumerate(next_tasks)
            ],
        ],
    )


def build_lab_task_pack(run: LabRun, payload: LabTaskPackPreviewRequest) -> LabTaskPackPreviewResponse:
    doc = None
    warnings: list[str] = []
    if run.thread_id:
        try:
            doc = load_thread(run.thread_id)
        except HTTPException:
            warnings.append("\u5173\u8054\u7ebf\u7a0b\u4e0d\u5b58\u5728\uff0cTask Pack \u5c06\u53ea\u4f7f\u7528\u5b9e\u9a8c\u8fd0\u884c\u81ea\u8eab\u4fe1\u606f\u3002")
    if not run.hypothesis:
        warnings.append("\u5b9e\u9a8c\u8fd0\u884c\u7f3a\u5c11\u660e\u786e\u5047\u8bbe\uff0c\u5efa\u8bae\u5148\u8865\u5145 hypothesis\u3002")
    instruction = payload.instruction_override or (
        "\u8bf7\u628a\u5f53\u524d\u7814\u7a76\u5047\u8bbe\u8f6c\u6210\u53ef\u6267\u884c\u5b9e\u9a8c\u8ba1\u5212\u3002\u4e0d\u8981\u6267\u884c\u547d\u4ee4\uff1b"
        "\u53ea\u7ed9\u51fa\u8ba1\u5212\u3001\u547d\u4ee4\u5efa\u8bae\u3001\u9700\u8981\u8bb0\u5f55\u7684\u65e5\u5fd7/\u4ea7\u7269\u3001\u98ce\u9669\u548c\u4e0b\u4e00\u6b65\u3002"
    )
    stage_lines = [
        f"- {stage.title}: {stage.status} | {stage.summary or stage.notes or '\u5f85\u7ec6\u5316'}"
        + (f" | command={stage.command}" if stage.command else "")
        for stage in run.stages
    ]
    artifact_lines = [
        f"- {artifact.title} [{artifact.type}] {artifact.summary or artifact.uri or artifact.content_preview}"
        for artifact in run.artifacts
    ]
    finding_lines = [f"- {finding.title}: {finding.body}" for finding in run.findings]
    markdown = "\n".join(
        [
            f"# EAI \u5b9e\u9a8c Task Pack\uff1a{run.title}",
            "",
            "\u4f60\u662f\u4e25\u8c28\u7684\u4e2d\u6587\u7814\u7a76\u5b9e\u9a8c\u534f\u4f5c\u8005\u3002\u4e0d\u8981\u6267\u884c\u672c\u5730\u547d\u4ee4\uff1b\u53ea\u89c4\u5212\u3001\u5ba1\u67e5\u3001\u89e3\u91ca\u548c\u603b\u7ed3\u5b9e\u9a8c\u3002",
            "",
            "## \u5b9e\u9a8c\u8fd0\u884c",
            f"- run_id: {run.id}",
            f"- status: {run.status}",
            f"- goal: {run.goal or '(\u672a\u586b\u5199)'}",
            f"- hypothesis: {run.hypothesis or '(\u672a\u586b\u5199)'}",
            "",
            render_lab_source_context(run, doc),
            "",
            "## \u5f53\u524d\u9636\u6bb5\u6811",
            "\n".join(stage_lines) or "(\u6682\u65e0\u9636\u6bb5)",
            "",
            "## \u5df2\u767b\u8bb0\u4ea7\u7269 / \u65e5\u5fd7\u6458\u8981",
            "\n".join(artifact_lines) or "(\u6682\u65e0\u4ea7\u7269)",
            "",
            "## \u5df2\u6709\u53d1\u73b0",
            "\n".join(finding_lines) or "(\u6682\u65e0\u53d1\u73b0)",
            "",
            "## \u672c\u6b21\u4efb\u52a1",
            instruction,
            "",
            "## \u8f93\u51fa\u8981\u6c42",
            "- \u4f7f\u7528\u4e2d\u6587\u3002",
            "- \u533a\u5206\u5df2\u89c2\u5bdf\u5230\u7684\u7ed3\u679c\u3001\u5efa\u8bae\u6267\u884c\u7684\u6b65\u9aa4\u548c\u4f60\u7684\u63a8\u6d4b\u3002",
            "- \u4e0d\u7f16\u9020\u5b9e\u9a8c\u7ed3\u679c\uff1b\u7f3a\u5931\u4fe1\u606f\u6807\u8bb0\u4e3a\u201c\u672a\u63d0\u4f9b\u201d\u3002",
            "- \u4ea7\u7269\u53ea\u7ed9 metadata\u3001\u6458\u8981\u3001\u8def\u5f84\u6216\u7c98\u8d34\u7247\u6bb5\u5efa\u8bae\uff0c\u4e0d\u8981\u6c42\u4e0a\u4f20\u5927\u6587\u4ef6\u3002",
            "",
            "```json",
            json.dumps(
                {
                    "schema_version": "eai-lab-run/v1",
                    "summary": "...",
                    "stages": [{"title": "\u57fa\u7ebf\u590d\u73b0", "status": "planned", "summary": "...", "command": "...", "notes": "..."}],
                    "artifacts": [{"type": "log", "title": "...", "summary": "...", "uri": "", "content_preview": "...", "stage_title": "\u57fa\u7ebf\u590d\u73b0"}],
                    "findings": [{"title": "...", "body": "...", "confidence": "preliminary"}],
                    "next_tasks": [{"title": "...", "body": "..."}],
                    "risks": ["..."],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )
    return LabTaskPackPreviewResponse(
        run_id=run.id,
        title=f"\u5b9e\u9a8c\u8fd0\u884c\uff1a{run.title}",
        markdown=markdown,
        token_estimate=max(160, len(markdown) // 3),
        warnings=warnings,
    )


def load_secret_data() -> tuple[dict[str, Any] | None, Path | None]:
    return read_secret_data(SECRET_CANDIDATES)


def openai_config(data: dict[str, Any] | None, requested_model: str | None = None) -> dict[str, str] | None:
    env_openrouter = os.environ.get("OPENROUTER_API_KEY")
    if env_openrouter:
        return {
            "api_key": env_openrouter,
            "model": requested_model or os.environ.get("OPENROUTER_MODEL", "openrouter/auto"),
            "base_url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "api_format": "chat",
            "provider": "openrouter",
        }
    if not data:
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key:
            return {
                "api_key": env_key,
                "model": requested_model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "api_format": os.environ.get("OPENAI_API_FORMAT", "responses"),
                "provider": "openai",
            }
        return None
    raw = data.get("openrouter") or data.get("OpenRouter") or data.get("OPENROUTER_API_KEY")
    if isinstance(raw, str):
        return {
            "api_key": raw,
            "model": requested_model or str(data.get("openrouter_model") or "openrouter/auto"),
            "base_url": str(data.get("openrouter_base_url") or "https://openrouter.ai/api/v1"),
            "api_format": "chat",
            "provider": "openrouter",
        }
    if isinstance(raw, dict):
        key = raw.get("api_key") or raw.get("key") or raw.get("OPENROUTER_API_KEY")
        if key:
            return {
                "api_key": str(key),
                "model": requested_model or str(raw.get("model") or data.get("openrouter_model") or "openrouter/auto"),
                "base_url": str(raw.get("base_url") or "https://openrouter.ai/api/v1"),
                "api_format": str(raw.get("api_format") or "chat"),
                "provider": "openrouter",
                "mock_response": str(raw.get("mock_response") or "") if raw.get("mock_response") else "",
            }
    raw = data.get("openai") or data.get("OpenAI") or data.get("OPENAI_API_KEY")
    if isinstance(raw, str):
        return {
            "api_key": raw,
            "model": requested_model or str(data.get("openai_model") or "gpt-4.1-mini"),
            "base_url": str(data.get("openai_base_url") or "https://api.openai.com/v1"),
            "api_format": str(data.get("openai_api_format") or "responses"),
            "provider": "openai",
        }
    if isinstance(raw, dict):
        key = raw.get("api_key") or raw.get("key") or raw.get("OPENAI_API_KEY")
        if key:
            return {
                "api_key": str(key),
                "model": requested_model or str(raw.get("model") or data.get("openai_model") or "gpt-4.1-mini"),
                "base_url": str(raw.get("base_url") or "https://api.openai.com/v1"),
                "api_format": str(raw.get("api_format") or data.get("openai_api_format") or "responses"),
                "provider": "openai",
                "mock_response": str(raw.get("mock_response") or "") if raw.get("mock_response") else "",
            }
    return None


def extract_openai_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list):
        texts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                texts.append(message["content"])
            elif isinstance(choice.get("text"), str):
                texts.append(choice["text"])
        if texts:
            return "\n".join(texts).strip()
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    texts: list[str] = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return "\n".join(texts).strip()


def agent_tool_schema(tool: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if tool == "search_atlas":
        properties = {"query": {"type": "string", "description": "用于检索当前 Atlas 的研究问题或关键词"}}
        required = ["query"]
    elif tool in {"get_paper", "get_relation_neighborhood"}:
        properties = {"paper_id": {"type": "string"}}
        required = ["paper_id"]
    elif tool == "get_object_memory":
        properties = {
            "object_type": {"type": "string", "enum": ["paper", "relation", "path", "file"]},
            "object_id": {"type": "string"},
        }
        required = ["object_id"]
    return {
        "type": "function",
        "function": {
            "name": tool,
            "description": AGENT_TOOL_REGISTRY[tool]["label"],
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        },
    }


def parse_agent_plan_content(content: str) -> list[dict[str, Any]]:
    match = re.search(r"```(?:json|eai-agent-plan/v1)?\s*([\s\S]*?)```", content or "")
    candidates = [match.group(1)] if match else []
    if (content or "").strip().startswith("{"):
        candidates.append(content.strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        raw_calls = payload.get("tool_calls") if isinstance(payload, dict) else None
        if not isinstance(raw_calls, list):
            continue
        return [
            {
                "tool": safe_run_text(item.get("tool") or item.get("name"), 80),
                "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            }
            for item in raw_calls
            if isinstance(item, dict)
        ]
    return []


def call_openai_agent_plan(
    doc: ThreadDoc,
    user_text: str,
    skills: list[dict[str, Any]],
    tool_calls: list[AgentToolCall],
    observations: list[dict[str, Any]],
    allowed_tools: set[str],
    model: str | None = None,
) -> list[dict[str, Any]]:
    mock_plan = os.environ.get("EAI_VNEXT_MOCK_AGENT_PLAN")
    if mock_plan:
        return parse_agent_plan_content(mock_plan)
    if os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"):
        raise HTTPException(status_code=400, detail="mock final response uses local agent planning")
    secret_data, _ = load_secret_data()
    config = openai_config(secret_data, model)
    if not config:
        raise HTTPException(status_code=400, detail="未配置模型通道")
    tools = [agent_tool_schema(tool) for tool in sorted(allowed_tools.intersection(READ_TOOLS))]
    if not tools:
        return []
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 EAI-Desktop Main Agent 的调查规划器。只选择完成当前问题必要的只读工具。"
                    "每轮避免重复调用已有工具；如果信息足够则不要调用工具。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": user_text,
                        "thread": {"title": doc.title, "goal": doc.goal, "atlas_id": doc.active_atlas_id},
                        "skills": skills,
                        "previous_tools": [item.model_dump(mode="json") for item in tool_calls],
                        "observations": observations[-6:],
                    },
                    ensure_ascii=False,
                )[:14000],
            },
        ],
        "tools": tools,
        "tool_choice": "auto",
    }
    request = urllib.request.Request(
        f"{(config.get('base_url') or 'https://api.openai.com/v1').rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"Agent 规划调用失败: {detail[:260]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent 规划调用失败: {exc}") from exc
    choices = body.get("choices") or []
    message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
    planned = []
    for item in message.get("tool_calls", []) or []:
        function = item.get("function", {}) if isinstance(item, dict) else {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        planned.append({"tool": safe_run_text(function.get("name"), 80), "arguments": arguments if isinstance(arguments, dict) else {}})
    if planned:
        return planned
    return parse_agent_plan_content(str(message.get("content") or ""))


def mock_text_chunks(text: str, size: int = 48) -> Iterator[str]:
    for index in range(0, len(text), size):
        yield text[index:index + size]


def extract_chat_delta(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list):
        return ""
    texts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            texts.append(delta["content"])
        elif isinstance(message, dict) and isinstance(message.get("content"), str):
            texts.append(message["content"])
        elif isinstance(choice.get("text"), str):
            texts.append(choice["text"])
    return "".join(texts)


def iter_chat_completion_stream(endpoint: str, config: dict[str, str], request_payload: dict[str, Any]) -> Iterator[str]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = extract_chat_delta(parsed)
                if delta:
                    yield delta
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"OpenAI-compatible API 流式调用失败: {detail[:500]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI-compatible API 流式调用失败: {exc}") from exc


def call_openai_text_stream(user_content: str, system_content: str, model: str | None = None) -> tuple[Iterator[str], str, str]:
    env_mock = os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE")
    if env_mock:
        return mock_text_chunks(env_mock), model or "mock-model", "mock"
    secret_data, _ = load_secret_data()
    config = openai_config(secret_data, model)
    if not config:
        raise HTTPException(status_code=400, detail="未配置 OpenAI-compatible / OpenRouter 密钥，无法发送 API。")
    mock = config.get("mock_response")
    if mock:
        return mock_text_chunks(mock), config["model"], config.get("provider") or "openai"
    base_url = config.get("base_url") or "https://api.openai.com/v1"
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    request_payload = {
        "model": config["model"],
        "stream": True,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
    }
    return iter_chat_completion_stream(endpoint, config, request_payload), config["model"], config.get("provider") or "openai"


def call_openai_text(user_content: str, system_content: str, model: str | None = None) -> tuple[str, str]:
    env_mock = os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE")
    if env_mock:
        return env_mock, model or "mock-model"
    secret_data, _ = load_secret_data()
    config = openai_config(secret_data, model)
    if not config:
        raise HTTPException(status_code=400, detail="未配置 OpenAI-compatible 密钥，无法发送 API。")
    mock = config.get("mock_response")
    if mock:
        return mock, config["model"]
    base_url = config.get("base_url") or "https://api.openai.com/v1"
    if config.get("api_format") == "chat":
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        request_payload = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        }
    else:
        endpoint = f"{base_url.rstrip('/')}/responses"
        request_payload = {
            "model": config["model"],
            "input": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"OpenAI-compatible API 调用失败: {detail[:500]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI-compatible API 调用失败: {exc}") from exc
    text = extract_openai_text(body)
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI-compatible API 返回为空。")
    return text, config["model"]


def call_openai_task_pack(markdown: str, model: str | None = None) -> tuple[str, str]:
    return call_openai_text(
        markdown,
        "你是严谨的中文学术研究助手。只基于用户提供的 Task Pack 回答，不编造论文细节。",
        model,
    )


def call_openai_thread_chat(prompt: str, model: str | None = None) -> tuple[str, str]:
    return call_openai_text(
        prompt,
        (
            "你是 EAI-Desktop 里的中文研究工作台协调者。"
            "你要帮助用户在 Atlas、Context Canvas、Task Pack、Codex 返回预览之间推进研究，"
            "只基于给定上下文回答，不编造论文全文、实验细节或不存在的证据。"
        ),
        model,
    )


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


def text_from_item(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        title = str(item.get("title") or item.get("summary") or item.get("task") or item.get("content") or "未命名")
        body = str(item.get("body") or item.get("detail") or item.get("rationale") or item.get("note") or "")
        return title, body
    return str(item), ""


@app.get("/api/vnext/research-templates", response_model=list[ResearchTemplate])
def list_research_templates() -> list[ResearchTemplate]:
    return list(RESEARCH_TEMPLATES.values())


@app.post("/api/vnext/threads/{thread_id}/task-pack/preview", response_model=TaskPackPreviewResponse)
def preview_task_pack(thread_id: str, payload: TaskPackPreviewRequest) -> TaskPackPreviewResponse:
    doc = load_thread(thread_id)
    return build_task_pack(doc, payload)


@app.post("/api/vnext/threads/{thread_id}/task-pack/run", response_model=TaskPackRunResponse)
def run_task_pack(thread_id: str, payload: TaskPackRunRequest) -> TaskPackRunResponse:
    doc = load_thread(thread_id)
    preview = build_task_pack(doc, payload)
    provider = payload.provider or "openai"
    if provider != "openai":
        raise HTTPException(status_code=400, detail="第一版仅支持 OpenAI provider。")
    raw_text, used_model = call_openai_task_pack(preview.markdown, payload.model)
    result_preview = preview_result(thread_id, ResultPreviewRequest(raw_text=raw_text))
    tool_run = ToolRun(
        id=slug_id("tool"),
        tool="research_template_run",
        status="preview",
        summary=f"{preview.title} API 返回已生成预览",
        created_at=utc_now(),
        template_id=preview.template_id,
        mode="api",
        provider=provider,
        model=used_model,
        token_estimate=preview.token_estimate,
        input_summary=f"{preview.included_cards} 张上下文卡",
    )
    doc.tool_runs.insert(0, tool_run)
    append_message(
        doc,
        Message(
            role="tool",
            kind="task_pack",
            content=f"已通过 API 发送「{preview.title}」，返回预览待确认。",
            status="preview",
            surface="thread",
            refs={
                "template_id": preview.template_id,
                "provider": provider,
                "model": used_model,
                "included_cards": preview.included_cards,
                "token_estimate": preview.token_estimate,
            },
            linked_tool_run_id=tool_run.id,
        ),
    )
    write_thread(doc)
    return TaskPackRunResponse(
        result_card_preview=result_preview.result_card_preview,
        canvas_nodes=result_preview.canvas_nodes,
        canvas_edges=result_preview.canvas_edges,
        tool_run=tool_run,
    )


@app.post("/api/vnext/threads/{thread_id}/results/preview", response_model=ResultPreviewResponse)
def preview_result(thread_id: str, payload: ResultPreviewRequest) -> ResultPreviewResponse:
    doc = load_thread(thread_id)
    raw = payload.raw_text.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="empty result text")
    parsed = parse_result_payload(raw)
    summary = parsed.get("summary") if parsed else None
    preview = ResultCard(
        id=slug_id("result"),
        title=summary or "Codex 返回",
        raw_text=raw,
        parsed_json=parsed,
        created_at=utc_now(),
    )
    nodes: list[CanvasNode] = []
    edges: list[CanvasEdge] = []
    question = next((node for node in doc.canvas.nodes if node.type == "question"), None)

    if parsed:
        conclusion_ids: list[str] = []
        for idx, item in enumerate(parsed.get("findings") or []):
            title, body = text_from_item(item)
            node = CanvasNode(
                id=slug_id("conclusion"),
                type="conclusion",
                title=title[:120] or "未命名结论",
                body=body,
                x=520,
                y=110 + idx * 118,
            )
            nodes.append(node)
            conclusion_ids.append(node.id)
            if question:
                edges.append(CanvasEdge(id=slug_id("edge"), source=question.id, target=node.id, label="leads_to"))
        for idx, item in enumerate(parsed.get("next_tasks") or []):
            title, body = text_from_item(item)
            node = CanvasNode(
                id=slug_id("task"),
                type="task",
                title=title[:120] or "未命名任务",
                body=body,
                x=760,
                y=110 + idx * 118,
                status="todo",
                priority=1,
            )
            nodes.append(node)
            source = conclusion_ids[0] if conclusion_ids else question.id if question else ""
            if source:
                edges.append(CanvasEdge(id=slug_id("edge"), source=source, target=node.id, label="requires"))

    return ResultPreviewResponse(
        result_card_preview=preview,
        canvas_nodes=nodes,
        canvas_edges=edges,
    )


@app.post("/api/vnext/threads/{thread_id}/export", response_model=ExportResponse)
def export_thread(thread_id: str, payload: ExportRequest) -> ExportResponse:
    doc = load_thread(thread_id)
    cards = [
        c
        for c in doc.context_cards
        if (c.selected_for_export or not payload.selected_only)
    ]
    token_estimate = sum(c.token_estimate for c in cards)
    markdown = "\n".join(
        [
            f"# EAI Task Pack：{doc.title}",
            "",
            "## 任务目标",
            doc.goal or "(未填写目标)",
            "",
            "## 当前思维板",
            render_canvas(doc),
            "",
            "## 选中上下文",
            "\n".join(render_card(card, i + 1) for i, card in enumerate(cards))
            or "(暂无选中的上下文卡片)",
            "",
            "## 输出要求",
            payload.instruction,
            "",
            "## 返回格式",
            "如果适合结构化返回，请包含一个 JSON 代码块:",
            "```json",
            json.dumps(
                {
                    "schema_version": "eai-result/v1",
                    "summary": "...",
                    "findings": [],
                    "candidate_papers": [],
                    "candidate_relations": [],
                    "next_tasks": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )
    tool_run = ToolRun(
        id=slug_id("tool"),
        tool="export_task_pack",
        status="done",
        summary=f"复制通用 Task Pack：{len(cards)} 张上下文卡片",
        created_at=utc_now(),
        mode="copy",
        token_estimate=token_estimate,
        input_summary=f"{len(cards)} 张上下文卡片",
    )
    doc.tool_runs.insert(0, tool_run)
    append_message(
        doc,
        Message(
            role="tool",
            kind="task_pack",
            content=f"已复制通用 Task Pack：{len(cards)} 张上下文卡片，约 {token_estimate} tokens。",
            status="done",
            surface="thread",
            refs={
                "selected_only": payload.selected_only,
                "exported_cards": len(cards),
                "token_estimate": token_estimate,
            },
            linked_tool_run_id=tool_run.id,
        ),
    )
    write_thread(doc)
    return ExportResponse(
        markdown=markdown,
        exported_cards=len(cards),
        token_estimate=token_estimate,
    )


@app.post("/api/vnext/threads/{thread_id}/results", response_model=ThreadDoc)
def add_result(thread_id: str, payload: ResultCard) -> ThreadDoc:
    doc = load_thread(thread_id)
    result = payload.model_copy()
    result.id = result.id or slug_id("result")
    result.created_at = result.created_at or utc_now()
    doc.result_cards.insert(0, result)
    tool_run = ToolRun(
        id=slug_id("tool"),
        tool="paste_codex_result",
        status="done",
        summary=result.title,
        created_at=utc_now(),
    )
    doc.tool_runs.insert(0, tool_run)
    parsed = result.parsed_json or {}
    append_message(
        doc,
        Message(
            role="assistant",
            kind="result",
            content=f"已确认写入 Codex 返回：{result.title}",
            status="done",
            surface="thread",
            refs={
                "findings": len(parsed.get("findings") or []) if isinstance(parsed, dict) else 0,
                "next_tasks": len(parsed.get("next_tasks") or []) if isinstance(parsed, dict) else 0,
            },
            linked_result_id=result.id,
            linked_tool_run_id=tool_run.id,
        ),
    )
    return write_thread(doc)


def v2_profile_model(profile: str, overrides: dict[str, str] | None = None) -> str | None:
    override = (overrides or {}).get(profile)
    if override:
        return safe_run_text(override, 160)
    value = os.environ.get(f"EAI_AGENT_MODEL_{profile.upper()}")
    return safe_run_text(value, 160) if value else None


def v2_route_model(prompt: str) -> str | None:
    mock = os.environ.get("EAI_V2_MOCK_ROUTE")
    if mock:
        return mock
    text, _ = call_openai_text(
        prompt,
        "你是 EAI Desktop 服务路由器。普通交流必须选择 conversation；只返回符合 schema 的 JSON。",
        v2_profile_model("router"),
    )
    return text


def v2_stream_model(
    prompt: str,
    profile: str,
    overrides: dict[str, str],
    cancelled: Callable[[], bool],
) -> tuple[Iterator[str], str, str]:
    selected_model = v2_profile_model(profile, overrides)
    env_mock = os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE")
    if env_mock:
        return mock_text_chunks(env_mock), "mock", selected_model or "mock-model"
    secret_data, _ = load_secret_data()
    config = openai_config(secret_data, selected_model)
    if not config:
        raise HTTPException(status_code=400, detail="未配置模型通道")
    mock = config.get("mock_response")
    if mock:
        return mock_text_chunks(str(mock)), config.get("provider") or "openai", config["model"]
    iterator = request_text_stream(
        config,
        prompt,
        "你是 EAI Desktop 自适应研究总管。只输出用户可读的自然语言，不输出内部协议。",
        cancelled=cancelled,
    )
    return iterator, config.get("provider") or "openai", config["model"]


def v2_plan_model(prompt: str, overrides: dict[str, str]) -> str | None:
    mock = os.environ.get("EAI_V2_MOCK_OPERATION_PLAN")
    if mock:
        return mock
    text, _ = call_openai_text(
        prompt,
        "你是 EAI Desktop 操作规划器。只输出最小、可确认、符合能力 schema 的 JSON。",
        v2_profile_model("planner", overrides),
    )
    return text


def v2_tool_model(prompt: str, tools: list[dict[str, Any]], overrides: dict[str, str]) -> str | dict[str, Any] | None:
    mock = os.environ.get("EAI_V2_MOCK_TOOL_DECISION")
    if mock:
        return mock
    secret_data, _ = load_secret_data()
    config = openai_config(secret_data, v2_profile_model("planner", overrides))
    if not config:
        return None
    if config.get("mock_response"):
        return str(config["mock_response"])
    return request_tool_decision(config, prompt, tools)


def campaign_plan_model(prompt: str) -> str | None:
    mock = os.environ.get("EAI_CAMPAIGN_MOCK_BRANCH_PLAN")
    if mock:
        return mock
    text, _ = call_openai_text(
        prompt,
        "你是 EAI Desktop Campaign 实验规划器。只返回 JSON：title、plan、code。代码必须是单文件 Python，输出 EAI_METRIC JSON 行，不得访问宿主机或密钥。",
        v2_profile_model("coder"),
    )
    return text


def v2_load_full_context(thread_id: str, request: AgentV2TurnRequest) -> dict[str, Any]:
    doc = load_thread(thread_id)
    research_store = get_research_store()
    project = None
    if doc.project_id:
        try:
            project = load_project(doc.project_id).model_dump(mode="json")
        except HTTPException:
            project = {"id": doc.project_id, "missing": True}
    campaign_summaries = []
    runtime = APP_SERVICES.agent_runtime_if_created
    if runtime:
        for snapshot in runtime.store.list_campaign_checkpoints(thread_id)[:6]:
            campaign = snapshot.get("campaign") if isinstance(snapshot, dict) else {}
            if isinstance(campaign, dict):
                campaign_summaries.append({
                    "id": campaign.get("id"), "title": campaign.get("title"),
                    "status": campaign.get("status"), "current_stage_id": campaign.get("current_stage_id"),
                    "objective": safe_run_text(campaign.get("objective"), 500),
                })
    thread_state = {
            "id": doc.id,
            "title": doc.title,
            "goal": doc.goal,
            "revision": doc.revision,
            "active_atlas_id": doc.active_atlas_id,
            "active_surface": doc.active_surface,
            "conversation_summary": safe_run_text(doc.conversation_summary, 2400),
        }
    context_cards = [
        {
            "id": card.id,
            "type": card.type,
            "title": card.title,
            "summary": safe_run_text(card.summary, 600),
            "source_ref": scrub_refs(card.source_ref),
            "pinned": card.pinned,
            "priority": card.priority,
            "agent_note": safe_run_text(card.agent_note, 500),
        }
        for card in agent_context_cards(doc, 24)
    ]
    canvas = compact_canvas_for_chat(doc)
    memories = [
        item for item in research_store.list_records("object_memory")
        if (item.get("object_ref") or {}).get("atlas_id") in {None, doc.active_atlas_id}
    ][:24]
    research_state = build_research_state(
        thread=thread_state,
        project=project,
        context_cards=context_cards,
        canvas=canvas,
        lab_runs=[],
        long_term_memories=memories,
    )
    research_state["normalized_graph"] = research_store.get_research_state(
        thread_id=doc.id, project_id=doc.project_id
    )
    return {
        "thread": thread_state,
        "project": project,
        "context_cards": context_cards,
        "canvas": canvas,
        "campaign_summaries": campaign_summaries,
        "long_term_memories": memories,
        "research_state": research_state,
        "recent_messages": [
            {"role": message.role, "content": safe_run_text(message.content, 800), "created_at": message.created_at}
            for message in doc.messages[-16:]
            if message.status not in {"pending", "streaming"} and message.role in {"user", "assistant"}
        ],
        "turn_attachments": [scrub_refs(item) for item in request.turn_attachments[:8]],
    }


def v2_load_context(thread_id: str, request: AgentV2TurnRequest) -> dict[str, Any]:
    doc = load_thread(thread_id)
    project = None
    if doc.project_id:
        try:
            raw_project = load_project(doc.project_id)
            project = {
                "id": raw_project.id, "title": raw_project.title,
                "goal": safe_run_text(raw_project.goal, 1000), "status": raw_project.status,
            }
        except HTTPException:
            project = {"id": doc.project_id, "missing": True}
    campaign_summaries = []
    runtime = APP_SERVICES.agent_runtime_if_created
    if runtime:
        for snapshot in runtime.store.list_campaign_checkpoints(thread_id)[:4]:
            campaign = snapshot.get("campaign") if isinstance(snapshot, dict) else {}
            if isinstance(campaign, dict):
                campaign_summaries.append({
                    "id": campaign.get("id"), "title": campaign.get("title"),
                    "status": campaign.get("status"), "current_stage_id": campaign.get("current_stage_id"),
                })
    return {
        "thread": {
            "id": doc.id, "title": doc.title, "goal": safe_run_text(doc.goal, 1200),
            "revision": doc.revision, "active_atlas_id": doc.active_atlas_id,
            "active_surface": doc.active_surface,
            "conversation_summary": safe_run_text(doc.conversation_summary, 1800),
        },
        "project": project,
        "campaign_summaries": campaign_summaries,
        "recent_messages": [
            {"role": message.role, "content": safe_run_text(message.content, 800), "created_at": message.created_at}
            for message in doc.messages[-8:]
            if message.status not in {"pending", "streaming"} and message.role in {"user", "assistant"}
        ],
        "turn_attachments": [scrub_refs(item) for item in request.turn_attachments[:8]],
    }


def v2_research_search(
    query: str,
    atlas_id: str,
    task_id: str | None,
    attachments: list[dict[str, Any]],
    limit: int,
) -> list[Any]:
    _, sources = search_for_agent(
        get_research_store(), query=query, atlas_id=atlas_id, task_id=task_id,
        attachments=attachments, limit=limit,
    )
    return sources


def v2_research_execute(
    capability_id: str,
    arguments: dict[str, Any],
    thread_id: str,
    task_id: str,
) -> dict[str, Any]:
    store = get_research_store()
    thread = load_thread(thread_id)
    if capability_id == "knowledge.search":
        bundle = store.search(
            safe_message_content(arguments.get("query") or "", 1200),
            atlas_ids=[thread.active_atlas_id] if thread.active_atlas_id else [],
            limit=max(1, min(int(arguments.get("limit") or 18), 30)),
        )
        sources = evidence_bundle_to_sources(bundle, task_id)
        return {
            "summary": f"在研究知识库中找到 {len(bundle.works)} 篇论文、{len(bundle.claims)} 条论断和 {len(bundle.evidence)} 个证据片段。",
            "works": [
                {"id": item.id, "title": item.title, "year": item.year, "evidence_status": item.evidence_status,
                 "atlas_placements": item.atlas_placements[:4]}
                for item in bundle.works
            ],
            "claims": [item.model_dump(mode="json") for item in bundle.claims[:20]],
            "evidence": [item.model_dump(mode="json") for item in bundle.evidence[:20]],
            "graph_paths": bundle.graph_paths[:20], "conflicts": bundle.conflicts[:10], "missing": bundle.missing,
            "sources": [item.model_dump(mode="json") for item in sources],
        }
    if capability_id == "knowledge.resolve_work":
        work = store.resolve_work(str(arguments.get("identifier") or ""), arguments.get("scheme"))
        return {"summary": "已解析论文身份。" if work else "没有找到可靠的论文身份。", "work": work.model_dump(mode="json") if work else None}
    if capability_id == "knowledge.work_profile":
        work = store.get_work(str(arguments.get("work_id") or ""))
        if not work:
            raise ValueError("论文不存在")
        claims, evidence = store.claims_for_work(work.id, verified_only=False)
        return {
            "summary": f"已读取《{work.title}》的证据档案：{len(claims)} 条论断，{len(evidence)} 个可定位证据。",
            "work": work.model_dump(mode="json"),
            "claims": [item.model_dump(mode="json") for item in claims[:30]],
            "evidence": [item.model_dump(mode="json") for item in evidence[:30]],
        }
    if capability_id == "knowledge.claim_evidence":
        claim, evidence = store.get_claim_evidence(str(arguments.get("claim_id") or ""))
        if not claim:
            raise ValueError("论断不存在")
        return {
            "summary": f"论断包含 {len(evidence)} 个可定位证据。",
            "claim": claim.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
    if capability_id == "knowledge.compare_works":
        profiles = []
        for work_id in list(arguments.get("work_ids") or [])[:8]:
            work = store.get_work(str(work_id))
            if not work:
                continue
            claims, evidence = store.claims_for_work(work.id)
            profiles.append({
                "work": work.model_dump(mode="json"),
                "claims": [item.model_dump(mode="json") for item in claims[:12]],
                "evidence": [item.model_dump(mode="json") for item in evidence[:16]],
            })
        return {"summary": f"已装配 {len(profiles)} 篇论文的可比证据。", "question": arguments.get("question") or "", "profiles": profiles}
    if capability_id == "knowledge.traverse_graph":
        value = store.graph_neighborhood(
            str(arguments.get("entity_id") or ""), depth=int(arguments.get("depth") or 1),
            limit=int(arguments.get("limit") or 40),
        )
        return {"summary": f"研究图邻域包含 {len(value.get('works') or [])} 篇论文和 {len(value.get('relations') or [])} 条关系。", **value}
    if capability_id == "knowledge.research_state":
        value = store.get_research_state(thread_id=thread_id, project_id=thread.project_id)
        return {"summary": f"研究状态包含 {len(value.get('entities') or [])} 个对象。", **value}
    if capability_id == "knowledge.inspect_gaps":
        work_ids = list(arguments.get("work_ids") or [])[:20]
        works = [store.get_work(str(item)) for item in work_ids] if work_ids else []
        gaps = []
        for work in [item for item in works if item]:
            status = work.evidence_status
            if not status.get("full_text") or not status.get("verified_claims"):
                gaps.append({"work_id": work.id, "title": work.title, "missing_full_text": not status.get("full_text"), "verified_claims": status.get("verified_claims", 0)})
        knowledge = store.status()
        return {"summary": f"发现 {len(gaps)} 个论文级证据缺口。", "gaps": gaps, "coverage": knowledge.coverage}
    raise ValueError(f"Research Store 能力未实现：{capability_id}")


def v2_campaign_execute(capability_id: str, arguments: dict[str, Any], thread_id: str) -> dict[str, Any]:
    service = get_campaign_service()
    if capability_id == "campaign.inspect":
        snapshot = service.get(str(arguments.get("campaign_id") or ""))
        if not snapshot or snapshot.campaign.thread_id != thread_id:
            raise ValueError("Campaign 不存在或不属于当前线程")
        return {
            "summary": f"Campaign《{snapshot.campaign.title}》当前处于 {snapshot.campaign.status}，包含 {len(snapshot.branches)} 个分支。",
            "campaign": snapshot.campaign.model_dump(mode="json"),
            "branches": [
                {"id": item.id, "stage_id": item.stage_id, "parent_id": item.parent_id, "origin": item.origin,
                 "status": item.status, "title": item.title, "analysis": safe_run_text(item.analysis, 1200), "is_best": item.is_best}
                for item in snapshot.branches
            ],
            "metrics": [item.model_dump(mode="json") for item in snapshot.metrics],
            "manuscripts": [{"id": item.id, "version": item.version, "status": item.status, "warnings": item.warnings} for item in snapshot.manuscripts],
            "reviews": [{"id": item.id, "role": item.role, "score": item.score, "decision": item.decision, "summary": item.summary} for item in snapshot.reviews],
        }
    if capability_id == "campaign.compare":
        result = service.compare_branches(
            str(arguments.get("campaign_id") or ""),
            BranchCompareRequest(branch_ids=list(arguments.get("branch_ids") or [])),
        )
        return {"summary": "已生成 Campaign 分支比较产物。", **result}
    if capability_id == "campaign.prepare_idea":
        preview = service.preview_ideas(
            thread_id, node_id=arguments.get("node_id"),
            objective=safe_message_content(arguments.get("objective") or "", 2000),
            count=max(1, min(int(arguments.get("count") or 3), 3)),
        )
        return {"summary": f"已准备 {len(preview.ideas)} 个带证据边界的研究想法。", **preview.model_dump(mode="json")}
    raise ValueError(f"Campaign 能力未实现：{capability_id}")


def v2_persist_assistant(thread_id: str, message_id: str, content: str, status: str, refs: dict[str, Any]) -> dict[str, Any]:
    with APP_SERVICES.agent_thread_lock:
        doc = load_thread(thread_id)
        assistant = find_message(doc, message_id)
        assistant.content = safe_markdown_text(content, 12000) or "本轮任务已完成。"
        assistant.status = safe_run_text(status, 32) or "done"
        assistant.refs = scrub_refs({**(assistant.refs or {}), **refs})
        updated = write_thread(doc)
    return updated.model_dump(mode="json")


def v2_current_operation_value(doc: ThreadDoc, operation: Any) -> Any:
    arguments = operation.arguments or {}
    capability_id = operation.capability
    if capability_id == "context.add":
        card = next((item for item in doc.context_cards if item.id == arguments.get("id")), None)
        return card.model_dump(mode="json") if card else None
    if capability_id == "context.remove":
        card = next((item for item in doc.context_cards if item.id == arguments.get("card_id")), None)
        return card.model_dump(mode="json") if card else None
    if capability_id == "thread.update":
        fields = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
        allowed = {"title", "goal", "status", "active_atlas_id", "project_id"}
        return {key: getattr(doc, key) for key in fields if key in allowed}
    if capability_id == "project.update":
        project = load_project(safe_run_text(arguments.get("project_id"), 160))
        fields = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
        allowed = {"title", "goal", "status", "default_atlas_id"}
        return {key: getattr(project, key) for key in fields if key in allowed}
    if capability_id == "memory.update":
        atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
        object_type = safe_run_text(arguments.get("object_type") or "paper", 40)
        object_id = safe_run_text(arguments.get("object_id"), 180)
        memory = effective_object_memory(atlas_id, object_type, object_id)
        return memory.model_dump(mode="json") if memory else None
    if capability_id == "memory.promote":
        draft = get_agent_v2_runtime().store.get_memory_draft(safe_run_text(arguments.get("draft_id"), 180))
        return draft.model_dump(mode="json") if draft else None
    if capability_id == "canvas.apply":
        return doc.canvas.model_dump(mode="json")
    if capability_id == "atlas_candidate.upsert":
        atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
        candidate_id = safe_run_text(arguments.get("candidate_id"), 160)
        atlas_doc = load_atlas_updates(atlas_id)
        candidate = next((item for item in atlas_doc.candidates if item.id == candidate_id), None)
        return candidate.model_dump(mode="json") if candidate else None
    return None


def v2_prepare_operation_batch(batch: AgentV2OperationBatch) -> AgentV2OperationBatch:
    doc = load_thread(batch.thread_id)
    batch.base_revision = doc.revision
    for operation in batch.operations:
        if operation.capability == "context.add" and not operation.arguments.get("id"):
            operation.arguments["id"] = slug_id("card")
        operation.before = v2_current_operation_value(doc, operation)
        operation.after = scrub_refs(operation.arguments)
    batch.updated_at = utc_now()
    return batch


def v2_canvas_apply(canvas: CanvasState, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    applied = []
    for raw in (arguments.get("operations") or [])[:40]:
        if not isinstance(raw, dict):
            continue
        action = safe_run_text(raw.get("action"), 40)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        if action == "add_node":
            node = CanvasNode.model_validate({"id": data.get("id") or slug_id("node"), **data})
            canvas.nodes.append(node)
            applied.append({"action": action, "id": node.id})
        elif action == "update_node":
            node = next((item for item in canvas.nodes if item.id == data.get("id")), None)
            if not node:
                raise HTTPException(status_code=409, detail=f"Canvas node missing: {data.get('id')}")
            for field in [
                "type", "title", "body", "status", "priority", "card_id", "entity_id",
                "campaign_id", "source_refs", "verification_status",
            ]:
                if field in data:
                    set_model_field(node, field, data[field])
            applied.append({"action": action, "id": node.id})
        elif action == "remove_node":
            node_id = safe_run_text(data.get("id"), 160)
            canvas.nodes = [item for item in canvas.nodes if item.id != node_id]
            canvas.edges = [item for item in canvas.edges if item.source != node_id and item.target != node_id]
            applied.append({"action": action, "id": node_id})
        elif action == "add_edge":
            edge = CanvasEdge.model_validate({"id": data.get("id") or slug_id("edge"), **data})
            node_ids = {item.id for item in canvas.nodes}
            if edge.source not in node_ids or edge.target not in node_ids:
                raise HTTPException(status_code=409, detail="Canvas edge references a missing node")
            canvas.edges.append(edge)
            applied.append({"action": action, "id": edge.id})
        elif action == "update_edge":
            edge = next((item for item in canvas.edges if item.id == data.get("id")), None)
            if not edge:
                raise HTTPException(status_code=409, detail=f"Canvas edge missing: {data.get('id')}")
            for field in ["source", "target", "label"]:
                if field in data:
                    set_model_field(edge, field, data[field])
            applied.append({"action": action, "id": edge.id})
        elif action == "remove_edge":
            edge_id = safe_run_text(data.get("id"), 160)
            canvas.edges = [item for item in canvas.edges if item.id != edge_id]
            applied.append({"action": action, "id": edge_id})
    return applied


def v2_apply_operation_batch(batch: AgentV2OperationBatch, resolution: AgentV2ApprovalResolveRequest) -> dict[str, Any]:
    with APP_SERVICES.agent_thread_lock:
        doc = load_thread(batch.thread_id)
        if doc.revision != batch.base_revision:
            raise HTTPException(status_code=409, detail={"message": "线程在确认前发生变化", "expected_revision": batch.base_revision, "current_revision": doc.revision})
        conflicts = []
        for operation in batch.operations:
            current = v2_current_operation_value(doc, operation)
            if current != operation.before:
                conflicts.append({"operation_id": operation.id, "capability": operation.capability, "expected": operation.before, "current": current})
        if conflicts:
            raise HTTPException(status_code=409, detail={"message": "操作目标发生变化", "conflicts": conflicts})

        thread_copy = ThreadDoc.model_validate(doc.model_dump(mode="json"))
        project_docs: dict[str, ProjectDoc] = {}
        memories: dict[tuple[str, str, str], ObjectMemory] = {}
        atlas_docs: dict[str, AtlasUpdateDoc] = {}
        memory_promotions: list[str] = []
        touched_paths: set[Path] = {safe_thread_path(doc.id)}
        applied: list[dict[str, Any]] = []

        for operation in batch.operations:
            arguments = operation.arguments or {}
            capability_id = operation.capability
            if capability_id == "context.add":
                card = ContextCard(
                    id=safe_run_text(arguments.get("id"), 160) or slug_id("card"),
                    type=arguments.get("type") if arguments.get("type") in {"paper", "relation", "path", "file"} else "paper",
                    title=safe_run_text(arguments.get("title"), 240) or "Agent 资料",
                    source_ref=scrub_refs(arguments.get("source_ref") or {}),
                    summary=safe_markdown_text(arguments.get("summary"), 3000),
                    token_estimate=estimate_agent_tokens(arguments.get("summary") or ""),
                    include_in_agent=True,
                    selected_for_export=False,
                )
                thread_copy.context_cards.append(card)
                applied.append({"capability": capability_id, "card_id": card.id})
            elif capability_id == "context.remove":
                card_id = safe_run_text(arguments.get("card_id"), 160)
                thread_copy.context_cards = [item for item in thread_copy.context_cards if item.id != card_id]
                applied.append({"capability": capability_id, "card_id": card_id})
            elif capability_id == "thread.update":
                changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                for field in ["title", "goal", "status", "active_atlas_id", "project_id"]:
                    if field in changes:
                        set_model_field(thread_copy, field, changes[field])
                applied.append({"capability": capability_id, "fields": list(changes)})
            elif capability_id == "project.update":
                project_id = safe_run_text(arguments.get("project_id"), 160)
                project = ProjectDoc.model_validate(load_project(project_id).model_dump(mode="json"))
                changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                for field in ["title", "goal", "status", "default_atlas_id"]:
                    if field in changes:
                        set_model_field(project, field, changes[field])
                project_docs[project_id] = project
                touched_paths.add(safe_project_path(project_id))
                applied.append({"capability": capability_id, "project_id": project_id})
            elif capability_id == "memory.update":
                atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
                object_type = safe_run_text(arguments.get("object_type") or "paper", 40)
                object_id = safe_run_text(arguments.get("object_id"), 180)
                memory = effective_object_memory(atlas_id, object_type, object_id) or ObjectMemory(
                    object_ref={"atlas_id": atlas_id, "object_type": object_type, "object_id": object_id},
                    title_snapshot=safe_run_text(arguments.get("title"), 240),
                )
                memory = ObjectMemory.model_validate(memory.model_dump(mode="json"))
                changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                for field in ["judgement", "note", "core_innovation", "core_technology", "evidence", "limitations", "reusable_insight", "tags", "maturity", "star", "reading_status", "reading_questions"]:
                    if field in changes:
                        set_model_field(memory, field, changes[field])
                memories[(atlas_id, object_type, object_id)] = memory
                touched_paths.add(safe_object_path(atlas_id, object_type, object_id))
                applied.append({"capability": capability_id, "object_id": object_id})
            elif capability_id == "memory.promote":
                draft_id = safe_run_text(arguments.get("draft_id"), 180)
                if not get_agent_v2_runtime().store.get_memory_draft(draft_id):
                    raise HTTPException(status_code=409, detail="长期记忆草稿不存在")
                memory_promotions.append(draft_id)
                applied.append({"capability": capability_id, "draft_id": draft_id})
            elif capability_id == "canvas.apply":
                applied.extend(v2_canvas_apply(thread_copy.canvas, arguments))
            elif capability_id == "atlas_candidate.upsert":
                atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
                atlas_doc = atlas_docs.get(atlas_id) or AtlasUpdateDoc.model_validate(load_atlas_updates(atlas_id).model_dump(mode="json"))
                candidate_id = safe_run_text(arguments.get("candidate_id"), 160)
                candidate = next((item for item in atlas_doc.candidates if item.id == candidate_id), None)
                changes = arguments.get("changes") if isinstance(arguments.get("changes"), dict) else arguments
                if candidate:
                    for field in AtlasCandidateUpdate.model_fields:
                        if field in changes:
                            set_model_field(candidate, field, changes[field])
                    candidate.updated_at = utc_now()
                else:
                    candidate = AtlasUpdateCandidate.model_validate({
                        "id": candidate_id or slug_id("candidate"), "title": changes.get("title") or "未命名候选论文",
                        "created_at": utc_now(), "updated_at": utc_now(), **changes,
                    })
                    atlas_doc.candidates.append(candidate)
                atlas_docs[atlas_id] = atlas_doc
                touched_paths.add(safe_atlas_update_path(atlas_id))
                applied.append({"capability": capability_id, "candidate_id": candidate.id})
            else:
                raise HTTPException(status_code=400, detail=f"unsupported Agent v2 capability: {capability_id}")

        snapshots = {path: transaction_snapshot(path) for path in touched_paths}
        journal_id = slug_id("agent_v2_transaction")
        journal_path = PERSONAL_DIR / "transactions" / f"{journal_id}.json"
        atomic_write_json(journal_path, {"id": journal_id, "batch_id": batch.id, "task_id": batch.task_id, "status": "committing", "targets": [str(path) for path in touched_paths], "created_at": utc_now()})
        try:
            promoted_drafts: list[str] = []
            for draft_id in memory_promotions:
                get_agent_v2_runtime().store.promote_memory(draft_id, utc_now())
                promoted_drafts.append(draft_id)
            for project in project_docs.values():
                write_project(project)
            for (atlas_id, object_type, object_id), memory in memories.items():
                write_object_memory(atlas_id, object_type, object_id, memory)
            for atlas_doc in atlas_docs.values():
                write_atlas_updates(atlas_doc)
            append_message(thread_copy, Message(role="tool", kind="state", content=f"已应用 Agent v2 操作：{batch.summary}", surface="thread", refs={"agent_v2_task_id": batch.task_id, "operation_batch_id": batch.id}))
            updated = write_thread(thread_copy)
            for operation in batch.operations:
                operation.after = v2_current_operation_value(updated, operation)
            batch.status = "applied"
            batch.applied_at = utc_now()
            batch.updated_at = batch.applied_at
            batch.receipt = {"transaction_id": journal_id, "operations": applied}
            atomic_write_json(journal_path, {"id": journal_id, "batch_id": batch.id, "status": "committed", "updated_at": utc_now()})
            get_agent_v2_runtime().store.save_operation_batch(batch)
            return {"status": "applied", "operation_batch_id": batch.id, "transaction_id": journal_id, "operations": applied, "thread": updated.model_dump(mode="json")}
        except Exception:
            for draft_id in locals().get("promoted_drafts", []):
                get_agent_v2_runtime().store.revert_promoted_memory(draft_id)
            for path, snapshot in snapshots.items():
                restore_transaction_snapshot(path, snapshot)
            atomic_write_json(journal_path, {"id": journal_id, "batch_id": batch.id, "status": "rolled_back", "updated_at": utc_now()})
            raise


def v2_undo_operation_batch(batch: AgentV2OperationBatch) -> dict[str, Any]:
    with APP_SERVICES.agent_thread_lock:
        if batch.status != "applied":
            raise HTTPException(status_code=409, detail="只有已应用的操作批次可以撤销")
        doc = load_thread(batch.thread_id)
        conflicts = []
        for operation in batch.operations:
            current = v2_current_operation_value(doc, operation)
            if current != operation.after:
                conflicts.append(
                    {
                        "operation_id": operation.id,
                        "capability": operation.capability,
                        "expected": operation.after,
                        "current": current,
                    }
                )
        if conflicts:
            batch.conflicts = conflicts
            batch.updated_at = utc_now()
            get_agent_v2_runtime().store.save_operation_batch(batch)
            raise HTTPException(status_code=409, detail={"message": "目标在应用后发生变化，无法安全撤销", "conflicts": conflicts})

        thread_copy = ThreadDoc.model_validate(doc.model_dump(mode="json"))
        project_docs: dict[str, ProjectDoc] = {}
        memories: dict[tuple[str, str, str], ObjectMemory | None] = {}
        atlas_docs: dict[str, AtlasUpdateDoc] = {}
        promoted_drafts: list[str] = []
        touched_paths: set[Path] = {safe_thread_path(doc.id)}

        for operation in reversed(batch.operations):
            arguments = operation.arguments or {}
            capability_id = operation.capability
            if capability_id == "context.add":
                card_id = safe_run_text(arguments.get("id"), 160)
                thread_copy.context_cards = [item for item in thread_copy.context_cards if item.id != card_id]
            elif capability_id == "context.remove":
                if operation.before:
                    restored = ContextCard.model_validate(operation.before)
                    thread_copy.context_cards = [item for item in thread_copy.context_cards if item.id != restored.id]
                    thread_copy.context_cards.append(restored)
            elif capability_id == "thread.update":
                for field, value in (operation.before or {}).items():
                    set_model_field(thread_copy, field, value)
            elif capability_id == "project.update":
                project_id = safe_run_text(arguments.get("project_id"), 160)
                project = project_docs.get(project_id) or ProjectDoc.model_validate(load_project(project_id).model_dump(mode="json"))
                for field, value in (operation.before or {}).items():
                    set_model_field(project, field, value)
                project_docs[project_id] = project
                touched_paths.add(safe_project_path(project_id))
            elif capability_id == "memory.update":
                atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
                object_type = safe_run_text(arguments.get("object_type") or "paper", 40)
                object_id = safe_run_text(arguments.get("object_id"), 180)
                memories[(atlas_id, object_type, object_id)] = ObjectMemory.model_validate(operation.before) if operation.before else None
                touched_paths.add(safe_object_path(atlas_id, object_type, object_id))
            elif capability_id == "memory.promote":
                promoted_drafts.append(safe_run_text(arguments.get("draft_id"), 180))
            elif capability_id == "canvas.apply":
                thread_copy.canvas = CanvasState.model_validate(operation.before or {})
            elif capability_id == "atlas_candidate.upsert":
                atlas_id = safe_run_text(arguments.get("atlas_id") or doc.active_atlas_id, 80)
                atlas_doc = atlas_docs.get(atlas_id) or AtlasUpdateDoc.model_validate(load_atlas_updates(atlas_id).model_dump(mode="json"))
                candidate_id = safe_run_text(arguments.get("candidate_id") or (operation.after or {}).get("id"), 160)
                atlas_doc.candidates = [item for item in atlas_doc.candidates if item.id != candidate_id]
                if operation.before:
                    atlas_doc.candidates.append(AtlasUpdateCandidate.model_validate(operation.before))
                atlas_docs[atlas_id] = atlas_doc
                touched_paths.add(safe_atlas_update_path(atlas_id))
            else:
                raise HTTPException(status_code=400, detail=f"unsupported Agent v2 undo capability: {capability_id}")

        snapshots = {path: transaction_snapshot(path) for path in touched_paths}
        journal_id = slug_id("agent_v2_undo")
        journal_path = PERSONAL_DIR / "transactions" / f"{journal_id}.json"
        atomic_write_json(
            journal_path,
            {"id": journal_id, "batch_id": batch.id, "status": "undoing", "targets": [str(path) for path in touched_paths], "created_at": utc_now()},
        )
        reverted_promotions: list[str] = []
        try:
            for draft_id in promoted_drafts:
                get_agent_v2_runtime().store.revert_promoted_memory(draft_id)
                reverted_promotions.append(draft_id)
            for project in project_docs.values():
                write_project(project)
            for (atlas_id, object_type, object_id), memory in memories.items():
                path = safe_object_path(atlas_id, object_type, object_id)
                if memory is None:
                    path.unlink(missing_ok=True)
                else:
                    write_object_memory(atlas_id, object_type, object_id, memory)
            for atlas_doc in atlas_docs.values():
                write_atlas_updates(atlas_doc)
            append_message(
                thread_copy,
                Message(
                    role="tool",
                    kind="state",
                    content=f"已撤销 Agent v2 操作：{batch.summary}",
                    surface="thread",
                    refs={"agent_v2_task_id": batch.task_id, "operation_batch_id": batch.id, "undone": True},
                ),
            )
            batch.status = "undone"
            batch.undone_at = utc_now()
            batch.updated_at = batch.undone_at
            batch.receipt = {**batch.receipt, "undo_transaction_id": journal_id}
            task = get_agent_v2_runtime().store.get_task(batch.task_id)
            if task:
                try:
                    assistant = find_message(thread_copy, task.assistant_message_id)
                    existing_batches = assistant.refs.get("agent_v2_operation_batches") if isinstance(assistant.refs, dict) else []
                    assistant.refs = scrub_refs(
                        {
                            **(assistant.refs or {}),
                            "agent_v2_operation_batches": [
                                batch.model_dump(mode="json") if item.get("id") == batch.id else item
                                for item in (existing_batches or [])
                            ] or [batch.model_dump(mode="json")],
                        }
                    )
                except HTTPException:
                    pass
            updated = write_thread(thread_copy)
            atomic_write_json(journal_path, {"id": journal_id, "batch_id": batch.id, "status": "undone", "updated_at": utc_now()})
            get_agent_v2_runtime().store.save_operation_batch(batch)
            return {"status": "undone", "operation_batch": batch.model_dump(mode="json"), "thread": updated.model_dump(mode="json")}
        except Exception:
            for path, snapshot in snapshots.items():
                restore_transaction_snapshot(path, snapshot)
            for draft_id in reverted_promotions:
                try:
                    get_agent_v2_runtime().store.promote_memory(draft_id, utc_now())
                except Exception:
                    pass
            atomic_write_json(journal_path, {"id": journal_id, "batch_id": batch.id, "status": "rollback", "updated_at": utc_now()})
            raise


def get_agent_v2_runtime() -> AgentRuntimeV2:
    research_store = get_research_store()

    def create_runtime() -> AgentRuntimeV2:
        store = RuntimeStore(
            RUNTIME_V2_DIR,
            read_only=research_store.read_only,
            database_schema=research_store.schema_version,
            supported_schema=research_store.compatibility_status()["supported_schema_version"],
        )
        return AgentRuntimeV2(
            store,
            RuntimeDependencies(
                load_context=v2_load_context,
                persist_assistant=v2_persist_assistant,
                atlas_loader=atlas_bundle_for_update,
                route_model=v2_route_model,
                stream_model=v2_stream_model,
                plan_model=v2_plan_model,
                tool_model=v2_tool_model,
                research_search=v2_research_search,
                research_execute=v2_research_execute,
                campaign_execute=v2_campaign_execute,
                load_full_context=v2_load_full_context,
                prepare_operation_batch=v2_prepare_operation_batch,
                apply_operation_batch=v2_apply_operation_batch,
            ),
        )

    return APP_SERVICES.agent_runtime(RUNTIME_V2_DIR, research_store, create_runtime)


def get_campaign_service() -> CampaignService:
    runtime = get_agent_v2_runtime()

    def create_campaign() -> CampaignService:
        return CampaignService(
            research_store=get_research_store(), runtime_store=runtime.store,
            load_thread=load_thread, prepare_operation_batch=v2_prepare_operation_batch,
            apply_operation_batch=v2_apply_operation_batch,
            planner=lambda prompt: campaign_plan_model(prompt) or "",
            read_only=runtime.store.read_only,
        )

    return APP_SERVICES.campaign(runtime, create_campaign)


def get_agent_api_service() -> AgentApiService:
    runtime = get_agent_v2_runtime()
    return APP_SERVICES.agent_api(
        runtime,
        lambda: AgentApiService(
            runtime,
            get_workspace_service(),
            get_research_store(),
            get_campaign_service=get_campaign_service,
            undo_operation_batch=v2_undo_operation_batch,
            thread_lock=APP_SERVICES.agent_thread_lock,
        ),
    )


app.include_router(create_research_router(get_research_store, get_knowledge_enrichment, get_page_preview_service))
app.include_router(create_campaign_router(get_campaign_service))
app.include_router(create_agent_v2_router(get_agent_api_service))
