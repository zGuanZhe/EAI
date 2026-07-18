from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from .agent_v2.models import ApprovalResolveRequest, OperationBatch
from .agent_v2.runtime import AgentRuntimeV2, RuntimeDependencies
from .agent_v2.store import RuntimeStore
from .campaign.router import create_campaign_router
from .campaign.service import CampaignService
from .core.config import ApplicationConfig
from .factory import create_app
from .research.enrichment import KnowledgeEnrichmentService
from .research.page_preview import PagePreviewService
from .research.router import create_research_router
from .research.store import ResearchStore
from .routers.agent_v2 import create_agent_v2_router
from .routers.atlas import create_atlas_router
from .routers.change_review import create_change_review_router
from .routers.drafts import create_draft_router
from .routers.legacy import create_legacy_router
from .routers.system import create_system_router
from .routers.task_pack import create_task_pack_router
from .routers.thread_content import create_thread_content_router
from .routers.workspace import create_workspace_router
from .services.agent_api import AgentApiService
from .services.agent_domain import AgentDomainService
from .services.agent_operations import AgentOperationError, AgentOperationService
from .services.atlas import AtlasService
from .services.change_review import ChangeReviewService
from .services.container import AppServices
from .services.drafts import ThreadDraftService
from .services.legacy_read import LegacyReadService
from .services.provider import ProviderAdapter
from .services.task_pack import TaskPackService
from .services.thread_content import ThreadContentService
from .services.workspace import WorkspaceService


class ApplicationAssembly:
    def __init__(self, config: ApplicationConfig) -> None:
        self.config = config
        self.services = AppServices()
        self.app = self._create_app()

    @property
    def paths(self):
        return self.config.paths

    def ensure_dirs(self) -> None:
        for directory in (
            self.paths.threads_dir, self.paths.backups_dir, self.paths.projects_dir,
            self.paths.objects_dir, self.paths.atlas_updates_dir, self.paths.lab_runs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def research_store(self) -> ResearchStore:
        return self.services.research_store(
            (self.paths.personal_dir.parent / "research").resolve(),
            self.paths.atlas_cache_dir,
            self.paths.personal_dir,
        )

    def workspace(self) -> WorkspaceService:
        store = self.research_store()
        signature = (id(store), self.paths.projects_dir, self.paths.threads_dir, self.paths.backups_dir)
        return self.services.domain_service("workspace", signature, lambda: WorkspaceService(
            store,
            projects_dir=self.paths.projects_dir,
            threads_dir=self.paths.threads_dir,
            backups_dir=self.paths.backups_dir,
        ))

    def provider(self) -> ProviderAdapter:
        signature = tuple(path.resolve() if path else None for path in self.config.secret_candidates)
        return self.services.domain_service("provider", signature, lambda: ProviderAdapter(self.config.secret_candidates))

    def atlas(self) -> AtlasService:
        store = self.research_store()
        signature = (id(store), self.paths.atlas_cache_dir, self.paths.personal_dir, self.paths.objects_dir, self.paths.atlas_updates_dir)
        return self.services.domain_service("atlas", signature, lambda: AtlasService(
            store,
            atlas_cache_dir=self.paths.atlas_cache_dir,
            personal_dir=self.paths.personal_dir,
            objects_dir=self.paths.objects_dir,
            atlas_updates_dir=self.paths.atlas_updates_dir,
            paper_model=self.provider().run_task_pack,
        ))

    def thread_content(self) -> ThreadContentService:
        workspace = self.workspace()
        return self.services.domain_service("thread_content", (id(workspace),), lambda: ThreadContentService(workspace))

    def change_review(self) -> ChangeReviewService:
        store, workspace, atlas = self.research_store(), self.workspace(), self.atlas()
        return self.services.domain_service(
            "change_review", (id(store), id(workspace), id(atlas)),
            lambda: ChangeReviewService(store, workspace, atlas),
        )

    def task_pack(self) -> TaskPackService:
        workspace, atlas, provider = self.workspace(), self.atlas(), self.provider()
        return self.services.domain_service(
            "task_pack", (id(workspace), id(atlas), id(provider)),
            lambda: TaskPackService(workspace, atlas, provider),
        )

    def agent_domain(self) -> AgentDomainService:
        store, workspace, atlas = self.research_store(), self.workspace(), self.atlas()
        return self.services.domain_service(
            "agent_domain", (id(store), id(workspace), id(atlas)),
            lambda: AgentDomainService(
                store, workspace, atlas,
                runtime_if_created=lambda: self.services.agent_runtime_if_created,
                get_campaign=self.campaign,
                thread_lock=self.services.agent_thread_lock,
            ),
        )

    def agent_operations(self) -> AgentOperationService:
        store, workspace, atlas = self.research_store(), self.workspace(), self.atlas()
        return self.services.domain_service(
            "agent_operations", (id(store), id(workspace), id(atlas)),
            lambda: AgentOperationService(
                store, workspace, atlas,
                get_runtime=self.agent_runtime,
                thread_lock=self.services.agent_thread_lock,
            ),
        )

    def _apply_batch(self, batch: OperationBatch, resolution: ApprovalResolveRequest) -> dict[str, Any]:
        try:
            return self.agent_operations().apply(batch, resolution)
        except AgentOperationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    def _undo_batch(self, batch: OperationBatch) -> dict[str, Any]:
        try:
            return self.agent_operations().undo(batch)
        except AgentOperationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    def agent_runtime(self) -> AgentRuntimeV2:
        store = self.research_store()

        def factory() -> AgentRuntimeV2:
            runtime_store = RuntimeStore(
                self.config.runtime_dir,
                read_only=store.read_only,
                database_schema=store.schema_version,
                supported_schema=store.compatibility_status()["supported_schema_version"],
            )
            domain, provider, operations = self.agent_domain(), self.provider(), self.agent_operations()
            return AgentRuntimeV2(runtime_store, RuntimeDependencies(
                load_context=domain.load_context,
                persist_assistant=domain.persist_assistant,
                atlas_loader=self.atlas().bundle_for_update,
                route_model=provider.route_model,
                stream_model=provider.stream_model,
                plan_model=provider.plan_model,
                tool_model=provider.tool_model,
                research_search=domain.research_search,
                research_execute=domain.research_execute,
                campaign_execute=domain.campaign_execute,
                load_full_context=domain.load_full_context,
                prepare_operation_batch=operations.prepare,
                apply_operation_batch=self._apply_batch,
            ))

        return self.services.agent_runtime(self.config.runtime_dir, store, factory)

    def campaign(self) -> CampaignService:
        runtime = self.agent_runtime()
        return self.services.campaign(runtime, lambda: CampaignService(
            research_store=self.research_store(),
            runtime_store=runtime.store,
            load_thread=self.workspace().load_thread,
            prepare_operation_batch=self.agent_operations().prepare,
            apply_operation_batch=self._apply_batch,
            planner=self.provider().campaign_plan_model,
            read_only=runtime.store.read_only,
        ))

    def agent_api(self) -> AgentApiService:
        runtime = self.agent_runtime()
        return self.services.agent_api(runtime, lambda: AgentApiService(
            runtime,
            self.workspace(),
            self.research_store(),
            get_campaign_service=self.campaign,
            undo_operation_batch=self._undo_batch,
            thread_lock=self.services.agent_thread_lock,
        ))

    def enrichment(self) -> KnowledgeEnrichmentService:
        store = self.research_store()
        return self.services.enrichment(
            store,
            start_initial_sync=(
                os.environ.get("EAI_DESKTOP_MODE") == "1"
                and os.environ.get("EAI_DISABLE_AUTO_KNOWLEDGE_SYNC") != "1"
            ),
        )

    def page_preview(self) -> PagePreviewService:
        return self.services.page_preview(self.research_store())

    def legacy_read(self) -> LegacyReadService:
        workspace = self.workspace()
        return self.services.domain_service("legacy_read", (id(workspace),), lambda: LegacyReadService(workspace.load_thread))

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(_app):
            self.ensure_dirs()
            if not self.research_store().read_only:
                self.services.projection(self.research_store()).replay(limit=500)
                self.agent_runtime()
                self.campaign().mark_incomplete_interrupted()
            try:
                yield
            finally:
                self.services.close()

        app = create_app(lifespan=lifespan)
        app.state.eai = self
        app.include_router(create_system_router(
            service_version=self.config.service_version,
            root=self.paths.root,
            personal_dir=self.paths.personal_dir,
            atlas_cache_dir=self.paths.atlas_cache_dir,
            secret_candidates=list(self.config.secret_candidates),
            research_status=lambda: self.research_store().compatibility_status(),
        ))
        app.include_router(create_draft_router(lambda: ThreadDraftService(self.research_store())))
        app.include_router(create_workspace_router(self.workspace))
        app.include_router(create_atlas_router(self.atlas))
        app.include_router(create_thread_content_router(self.thread_content))
        app.include_router(create_change_review_router(self.change_review))
        app.include_router(create_task_pack_router(self.task_pack))
        app.include_router(create_research_router(self.research_store, self.enrichment, self.page_preview))
        app.include_router(create_campaign_router(self.campaign))
        app.include_router(create_agent_v2_router(self.agent_api))
        app.include_router(create_legacy_router(self.legacy_read))
        return app


def create_application(config: ApplicationConfig) -> FastAPI:
    return ApplicationAssembly(config).app
