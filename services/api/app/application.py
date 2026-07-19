from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import HTTPException

from .agent_v2.models import (
    AgentTurnRequest as AgentV2TurnRequest,
    ApprovalResolveRequest as AgentV2ApprovalResolveRequest,
    OperationBatch as AgentV2OperationBatch,
)
from .agent_v2.runtime import AgentRuntimeV2, RuntimeDependencies
from .agent_v2.store import SUPPORTED_RUNTIME_SCHEMA, RuntimeStore
from .campaign.router import create_campaign_router
from .campaign.service import CampaignService
from .core.config import ApplicationConfig
from .composition import ApplicationAssembly, create_application
from .factory import create_app
from .routers.system import create_system_router
from .routers.drafts import create_draft_router
from .routers.atlas import create_atlas_router
from .routers.agent_v2 import create_agent_v2_router
from .routers.agent_v3 import create_agent_v3_router
from .routers.legacy import create_legacy_router
from .routers.change_review import create_change_review_router
from .routers.thread_content import create_thread_content_router
from .routers.task_pack import create_task_pack_router
from .routers.workspace import create_workspace_router
from .services.drafts import ThreadDraftService
from .services.container import AppServices
from .services.atlas import AtlasService
from .services.agent_api import AgentApiService
from .services.agent_v3 import AgentV3Service
from .services.agent_domain import AgentDomainService
from .services.agent_operations import AgentOperationError, AgentOperationService
from .services.legacy_read import LegacyReadService
from .services.change_review import ChangeReviewService
from .services.thread_content import ThreadContentService
from .services.task_pack import TaskPackService
from .services.provider import ProviderAdapter
from .services.workspace import WorkspaceService
from .research.enrichment import KnowledgeEnrichmentService
from .research.page_preview import PagePreviewService
from .research.router import create_research_router
from .research.store import ResearchStore
from .schemas.models import AtlasUpdateDoc, ObjectMemory, ProjectDoc, ThreadDoc

SERVICE_VERSION = "1.0.1"
DEFAULT_ROOT = Path(__file__).resolve().parents[3]
CONFIG = ApplicationConfig.load(DEFAULT_ROOT, service_version=SERVICE_VERSION)
RUNTIME_PATHS = CONFIG.paths
ROOT = RUNTIME_PATHS.root
WEB_DATA_DIR = RUNTIME_PATHS.atlas_cache_dir
PERSONAL_DIR = RUNTIME_PATHS.personal_dir
THREADS_DIR = RUNTIME_PATHS.threads_dir
BACKUPS_DIR = RUNTIME_PATHS.backups_dir
PROJECTS_DIR = RUNTIME_PATHS.projects_dir
OBJECTS_DIR = RUNTIME_PATHS.objects_dir
ATLAS_UPDATES_DIR = RUNTIME_PATHS.atlas_updates_dir
LAB_RUNS_DIR = RUNTIME_PATHS.lab_runs_dir
RUNTIME_V2_DIR = CONFIG.runtime_dir
APP_SERVICES = AppServices()

SECRET_CANDIDATES = CONFIG.secret_candidates


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
    runtime_schema = RuntimeStore._read_runtime_schema(RUNTIME_V2_DIR / "runtime.db")
    runtime_newer = runtime_schema > SUPPORTED_RUNTIME_SCHEMA
    return APP_SERVICES.research_store(
        expected_dir,
        WEB_DATA_DIR,
        PERSONAL_DIR,
        force_read_only=runtime_newer,
        read_only_reason="runtime_schema_newer_than_app" if runtime_newer else "",
    )


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
    store = get_research_store()
    signature = (id(store), PROJECTS_DIR.resolve(), THREADS_DIR.resolve(), BACKUPS_DIR.resolve())
    return APP_SERVICES.domain_service(
        "workspace", signature,
        lambda: WorkspaceService(
            store, projects_dir=PROJECTS_DIR, threads_dir=THREADS_DIR, backups_dir=BACKUPS_DIR,
        ),
    )


def get_atlas_service() -> AtlasService:
    store = get_research_store()
    signature = (id(store), WEB_DATA_DIR.resolve(), PERSONAL_DIR.resolve(), OBJECTS_DIR.resolve(), ATLAS_UPDATES_DIR.resolve())
    return APP_SERVICES.domain_service(
        "atlas", signature,
        lambda: AtlasService(
            store, atlas_cache_dir=WEB_DATA_DIR, personal_dir=PERSONAL_DIR,
            objects_dir=OBJECTS_DIR, atlas_updates_dir=ATLAS_UPDATES_DIR,
            paper_model=get_provider_adapter().run_task_pack,
        ),
    )


def get_provider_adapter() -> ProviderAdapter:
    signature = tuple(path.resolve() if path else None for path in SECRET_CANDIDATES)
    return APP_SERVICES.domain_service("provider", signature, lambda: ProviderAdapter(SECRET_CANDIDATES))


def get_thread_content_service() -> ThreadContentService:
    workspace = get_workspace_service()
    return APP_SERVICES.domain_service("thread_content", (id(workspace),), lambda: ThreadContentService(workspace))


def get_change_review_service() -> ChangeReviewService:
    store, workspace, atlas = get_research_store(), get_workspace_service(), get_atlas_service()
    return APP_SERVICES.domain_service(
        "change_review", (id(store), id(workspace), id(atlas)),
        lambda: ChangeReviewService(store, workspace, atlas),
    )


def get_task_pack_service() -> TaskPackService:
    workspace, atlas, provider = get_workspace_service(), get_atlas_service(), get_provider_adapter()
    return APP_SERVICES.domain_service(
        "task_pack", (id(workspace), id(atlas), id(provider)),
        lambda: TaskPackService(workspace, atlas, provider),
    )


def get_agent_domain_service() -> AgentDomainService:
    store, workspace, atlas = get_research_store(), get_workspace_service(), get_atlas_service()
    return APP_SERVICES.domain_service(
        "agent_domain", (id(store), id(workspace), id(atlas)),
        lambda: AgentDomainService(
            store, workspace, atlas,
            runtime_if_created=lambda: APP_SERVICES.agent_runtime_if_created,
            get_campaign=get_campaign_service,
            thread_lock=APP_SERVICES.agent_thread_lock,
        ),
    )


def get_agent_operation_service() -> AgentOperationService:
    store, workspace, atlas = get_research_store(), get_workspace_service(), get_atlas_service()
    return APP_SERVICES.domain_service(
        "agent_operations", (id(store), id(workspace), id(atlas)),
        lambda: AgentOperationService(
            store, workspace, atlas,
            get_runtime=get_agent_v2_runtime,
            thread_lock=APP_SERVICES.agent_thread_lock,
        ),
    )


@asynccontextmanager
async def app_lifespan(_app):
    runtime_newer = RuntimeStore._read_runtime_schema(RUNTIME_V2_DIR / "runtime.db") > SUPPORTED_RUNTIME_SCHEMA
    if not runtime_newer:
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
app.include_router(create_thread_content_router(get_thread_content_service))
app.include_router(create_change_review_router(get_change_review_service))
app.include_router(create_task_pack_router(get_task_pack_service))


def get_legacy_read_service() -> LegacyReadService:
    workspace = get_workspace_service()
    return APP_SERVICES.domain_service("legacy_read", (id(workspace),), lambda: LegacyReadService(workspace.load_thread))


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


def atlas_bundle_for_update(atlas_id: str) -> dict[str, Any]:
    try:
        return get_atlas_service().bundle_for_update(atlas_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


def safe_markdown_text(value: Any, limit: int = 3000) -> str:
    if not value:
        return ""
    text = str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in {"\n", "\t"} or ord(char) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text[:limit]


def v2_route_model(prompt: str) -> str | None:
    return get_provider_adapter().route_model(prompt)


def v2_stream_model(
    prompt: str,
    profile: str,
    overrides: dict[str, str],
    cancelled: Callable[[], bool],
) -> tuple[Iterator[str], str, str]:
    return get_provider_adapter().stream_model(prompt, profile, overrides, cancelled)


def v2_plan_model(prompt: str, overrides: dict[str, str]) -> str | None:
    return get_provider_adapter().plan_model(prompt, overrides)


def v2_tool_model(prompt: str, tools: list[dict[str, Any]], overrides: dict[str, str]) -> str | dict[str, Any] | None:
    return get_provider_adapter().tool_model(prompt, tools, overrides)


def campaign_plan_model(prompt: str) -> str | None:
    return get_provider_adapter().campaign_plan_model(prompt)


def v2_load_full_context(thread_id: str, request: AgentV2TurnRequest) -> dict[str, Any]:
    return get_agent_domain_service().load_full_context(thread_id, request)


def v2_load_context(thread_id: str, request: AgentV2TurnRequest) -> dict[str, Any]:
    return get_agent_domain_service().load_context(thread_id, request)


def v2_research_search(
    query: str,
    atlas_id: str,
    task_id: str | None,
    attachments: list[dict[str, Any]],
    limit: int,
) -> list[Any]:
    return get_agent_domain_service().research_search(query, atlas_id, task_id, attachments, limit)


def v2_research_execute(
    capability_id: str,
    arguments: dict[str, Any],
    thread_id: str,
    task_id: str,
) -> dict[str, Any]:
    return get_agent_domain_service().research_execute(capability_id, arguments, thread_id, task_id)


def v2_campaign_execute(capability_id: str, arguments: dict[str, Any], thread_id: str) -> dict[str, Any]:
    return get_agent_domain_service().campaign_execute(capability_id, arguments, thread_id)


def v2_persist_assistant(thread_id: str, message_id: str, content: str, status: str, refs: dict[str, Any]) -> dict[str, Any]:
    return get_agent_domain_service().persist_assistant(thread_id, message_id, content, status, refs)


def v2_prepare_operation_batch(batch: AgentV2OperationBatch) -> AgentV2OperationBatch:
    return get_agent_operation_service().prepare(batch)


def v2_apply_operation_batch(batch: AgentV2OperationBatch, resolution: AgentV2ApprovalResolveRequest) -> dict[str, Any]:
    try:
        return get_agent_operation_service().apply(batch, resolution)
    except AgentOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def v2_undo_operation_batch(batch: AgentV2OperationBatch) -> dict[str, Any]:
    try:
        return get_agent_operation_service().undo(batch)
    except AgentOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def get_agent_v2_runtime() -> AgentRuntimeV2:
    research_store = get_research_store()

    def create_runtime() -> AgentRuntimeV2:
        runtime_schema = RuntimeStore._read_runtime_schema(RUNTIME_V2_DIR / "runtime.db")
        if 0 < runtime_schema < SUPPORTED_RUNTIME_SCHEMA and not research_store.read_only:
            research_store.backup_database(f"runtime-v{runtime_schema}-to-v{SUPPORTED_RUNTIME_SCHEMA}")
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


def get_agent_v3_service() -> AgentV3Service:
    runtime = get_agent_v2_runtime()
    return APP_SERVICES.agent_v3(
        runtime,
        lambda: AgentV3Service(
            get_agent_api_service(), load_full_context=get_agent_domain_service().load_full_context,
        ),
    )


app.include_router(create_research_router(get_research_store, get_knowledge_enrichment, get_page_preview_service))
app.include_router(create_campaign_router(get_campaign_service))
app.include_router(create_agent_v2_router(get_agent_api_service))
app.include_router(create_agent_v3_router(get_agent_v3_service))
app.include_router(create_legacy_router(get_legacy_read_service))
