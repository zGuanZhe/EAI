from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ..agent_v2.runtime import AgentRuntimeV2
from .agent_api import AgentApiService
from ..campaign.service import CampaignService
from ..research.enrichment import KnowledgeEnrichmentService
from ..research.page_preview import PagePreviewService
from ..research.store import ResearchStore
from .projection import ProjectionService


class AppServices:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.agent_thread_lock = threading.RLock()
        self._research_signature: tuple[Path, Path, Path] | None = None
        self._runtime_signature: tuple[Path, bool, int] | None = None
        self._research_store: ResearchStore | None = None
        self._enrichment: KnowledgeEnrichmentService | None = None
        self._page_preview: PagePreviewService | None = None
        self._agent_runtime: AgentRuntimeV2 | None = None
        self._agent_api: AgentApiService | None = None
        self._campaign: CampaignService | None = None

    @property
    def agent_runtime_if_created(self) -> AgentRuntimeV2 | None:
        return self._agent_runtime

    def research_store(self, research_dir: Path, atlas_dir: Path, personal_dir: Path) -> ResearchStore:
        signature = (research_dir.resolve(), atlas_dir.resolve(), personal_dir.resolve())
        with self._lock:
            if self._research_signature != signature:
                self._close_locked()
                self._research_signature = signature
            if self._research_store is None:
                self._research_store = ResearchStore(*signature)
            return self._research_store

    def enrichment(
        self,
        store: ResearchStore,
        *,
        start_initial_sync: bool,
    ) -> KnowledgeEnrichmentService:
        with self._lock:
            if self._enrichment is None or self._enrichment.store is not store:
                self._enrichment = KnowledgeEnrichmentService(store)
                if start_initial_sync:
                    self._enrichment.ensure_initial_metadata_sync()
            return self._enrichment

    def projection(self, store: ResearchStore) -> ProjectionService:
        return ProjectionService(store, store.personal_dir)

    def page_preview(self, store: ResearchStore) -> PagePreviewService:
        with self._lock:
            if self._page_preview is None or self._page_preview.store is not store:
                if self._page_preview is not None:
                    self._page_preview.close()
                self._page_preview = PagePreviewService(store)
            return self._page_preview

    def agent_runtime(
        self,
        runtime_dir: Path,
        research_store: ResearchStore,
        factory: Callable[[], AgentRuntimeV2],
    ) -> AgentRuntimeV2:
        signature = (runtime_dir.resolve(), research_store.read_only, research_store.schema_version)
        with self._lock:
            if self._runtime_signature != signature:
                self._close_runtime_locked()
                self._runtime_signature = signature
            if self._agent_runtime is None:
                self._agent_runtime = factory()
            return self._agent_runtime

    def campaign(
        self,
        runtime: AgentRuntimeV2,
        factory: Callable[[], CampaignService],
    ) -> CampaignService:
        with self._lock:
            if self._campaign is None or self._campaign.runtime_store is not runtime.store:
                self._campaign = factory()
            return self._campaign

    def agent_api(
        self,
        runtime: AgentRuntimeV2,
        factory: Callable[[], AgentApiService],
    ) -> AgentApiService:
        with self._lock:
            if self._agent_api is None or self._agent_api.runtime is not runtime:
                self._agent_api = factory()
            return self._agent_api

    def reset_runtime(self) -> None:
        with self._lock:
            self._close_runtime_locked()
            self._runtime_signature = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._research_signature = None

    def _close_runtime_locked(self) -> None:
        self._agent_api = None
        self._campaign = None
        if self._agent_runtime is not None:
            self._agent_runtime.close()
            self._agent_runtime = None

    def _close_locked(self) -> None:
        self._close_runtime_locked()
        self._enrichment = None
        if self._page_preview is not None:
            self._page_preview.close()
            self._page_preview = None
        if self._research_store is not None:
            self._research_store.close()
            self._research_store = None
