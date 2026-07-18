from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..agent_v2.models import AgentTask, SourcePolicy


InteractionLane = Literal["ask", "research"]
ResearchDepth = Literal["standard", "deep"]
ResearchDeliverable = Literal["answer", "research_note", "comparison", "literature_map", "evidence_audit"]


class FocusRef(BaseModel):
    type: Literal["paper", "work", "document", "context-card", "canvas-node", "campaign", "thread", "project"]
    id: str = Field(min_length=1, max_length=240)
    title: str = Field(default="", max_length=500)


class AskTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    surface: str | None = Field(default=None, max_length=80)
    focus_ref: FocusRef | None = None
    source_policy: SourcePolicy = "local_and_external"
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class ResearchTaskCreate(BaseModel):
    objective: str = Field(min_length=1, max_length=8000)
    deliverable: ResearchDeliverable = "research_note"
    depth: ResearchDepth = "standard"
    source_policy: SourcePolicy = "local_and_external"
    surface: str | None = Field(default=None, max_length=80)
    focus_ref: FocusRef | None = None
    context_refs: list[FocusRef] = Field(default_factory=list, max_length=12)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class ContextManifestItem(BaseModel):
    kind: str
    id: str
    title: str = ""
    priority: int = 0
    revision: int | None = None
    source_layer: Literal["turn", "focus", "thread", "project", "memory", "atlas", "external"]
    trust: Literal["system", "user", "curated", "untrusted"]
    evidence_eligible: bool = False
    content_hash: str


class ContextManifest(BaseModel):
    id: str
    task_id: str
    thread_id: str
    interaction_lane: InteractionLane
    source_policy: SourcePolicy
    surface: str = "thread"
    items: list[ContextManifestItem] = Field(default_factory=list)
    omitted: list[dict[str, str]] = Field(default_factory=list)
    content_hash: str
    created_at: str


class RetrievalDecision(BaseModel):
    information_request: bool
    search_required: bool
    source_policy: SourcePolicy
    scopes: list[str] = Field(default_factory=list)
    reason: str
    unavailable_scopes: list[str] = Field(default_factory=list)


class AgentV3TurnResponse(BaseModel):
    thread: dict[str, Any]
    task: AgentTask
    user_message_id: str
    assistant_message_id: str
    retrieval: RetrievalDecision
    context_manifest: ContextManifest


class ResearchTaskView(BaseModel):
    task: AgentTask
    context_manifest: ContextManifest | None = None
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)


class UiCommand(BaseModel):
    id: str
    task_id: str
    action: Literal["open_surface", "open_object", "focus_object", "open_inspector"]
    target: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending", "applied", "dismissed"] = "pending"
    created_at: str
    resolved_at: str | None = None
