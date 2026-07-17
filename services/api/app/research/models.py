from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


DataLayer = Literal["curated", "derived", "personal"]
VerificationStatus = Literal["verified", "unverified", "unsupported", "rejected"]


class ProvenanceRef(BaseModel):
    id: str
    origin_kind: str
    origin_ref: str
    content_hash: str = ""
    extractor: str = ""
    created_at: str


class ResearchWork(BaseModel):
    id: str
    title: str
    year: int | None = None
    venue: str = ""
    canonical_key: str
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    resolved_fields: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    atlas_placements: list[dict[str, Any]] = Field(default_factory=list)
    evidence_status: dict[str, Any] = Field(default_factory=dict)
    revision: int = 0


class ClaimRecord(BaseModel):
    id: str
    subject_id: str
    predicate: str
    text: str
    object_entity_id: str | None = None
    object_value: Any = None
    polarity: Literal["supports", "opposes", "neutral"] = "neutral"
    modality: Literal["observed", "reported", "inferred", "proposed"] = "reported"
    layer: DataLayer = "derived"
    status: VerificationStatus = "unverified"
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_id: str = ""
    created_at: str


class EvidenceSpan(BaseModel):
    id: str
    document_id: str
    chunk_id: str = ""
    work_id: str | None = None
    section: str = ""
    page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quote: str
    locator: dict[str, Any] = Field(default_factory=dict)
    evidence_level: str = "full_text"
    content_hash: str


class KnowledgeSearchRequest(BaseModel):
    query: str
    thread_id: str | None = None
    atlas_ids: list[str] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    include_graph: bool = True
    include_claims: bool = True
    limit: int = Field(default=12, ge=1, le=50)


class EvidenceBundle(BaseModel):
    query: str
    works: list[ResearchWork] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    graph_paths: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)


class SyncRequest(BaseModel):
    scope: Literal["metadata", "hot_fulltext", "reindex", "all"] = "metadata"
    atlas_ids: list[str] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)


class SyncJob(BaseModel):
    id: str
    scope: str
    status: Literal["pending", "running", "paused", "done", "failed", "cancelled"] = "pending"
    atlas_ids: list[str] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)
    progress: int = 0
    total: int = 0
    summary: str = ""
    error: str = ""
    created_at: str
    updated_at: str


class KnowledgeStatus(BaseModel):
    ready: bool
    schema_version: int
    database_path: str
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, float] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
    embedding: dict[str, Any] = Field(default_factory=dict)
    active_jobs: list[SyncJob] = Field(default_factory=list)
    migration: dict[str, Any] = Field(default_factory=dict)


class IdentityResolutionRequest(BaseModel):
    decision: Literal["merge", "keep_separate", "reject"]
    target_work_id: str | None = None


class ClaimResolutionRequest(BaseModel):
    note: str = ""


class GraphNeighborhoodRequest(BaseModel):
    entity_id: str
    depth: int = Field(default=1, ge=1, le=2)
    predicates: list[str] = Field(default_factory=list)
    limit: int = Field(default=40, ge=1, le=200)
