from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentRunStatus = Literal[
    "pending",
    "planning",
    "running",
    "waiting_confirmation",
    "done",
    "failed",
    "cancelled",
    "interrupted",
]
ToolPermission = Literal["read", "preview", "write"]
ProposalStatus = Literal["pending", "confirmed", "rejected"]
ChangeSetStatus = Literal["pending", "applied", "rejected", "conflicted", "undone"]


class AgentStep(BaseModel):
    id: str
    label: str
    status: str = "pending"


class AgentCitation(BaseModel):
    id: str
    source_type: str
    title: str
    source_ref: dict[str, Any] = Field(default_factory=dict)
    excerpt: str = ""
    tool_call_id: str | None = None


class AgentContextSnapshot(BaseModel):
    revision: int = 0
    context_card_ids: list[str] = Field(default_factory=list)
    pinned_card_ids: list[str] = Field(default_factory=list)
    turn_attachments: list[dict[str, Any]] = Field(default_factory=list)
    estimated_tokens: int = 0
    budget_tokens: int = 24000


class AgentToolCall(BaseModel):
    id: str | None = None
    tool: str
    permission: ToolPermission = "read"
    round: int = 1
    input_summary: str = ""
    status: str = "pending"
    result_summary: str = ""
    source_ids: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    error: str = ""


class ProposalDiffItem(BaseModel):
    field: str
    before: Any = None
    after: Any = None
    reason: str = ""


class ActionProposal(BaseModel):
    id: str | None = None
    type: Literal["context_injection", "object_memory", "paper_card_update", "atlas_candidate", "task_pack_preview"]
    target: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    diff: list[ProposalDiffItem] = Field(default_factory=list)
    risk: str = "low"
    status: ProposalStatus = "pending"
    source_run_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ChangeOperation(BaseModel):
    id: str
    target_type: Literal["context", "object_memory", "atlas_candidate", "canvas"]
    target: dict[str, Any] = Field(default_factory=dict)
    op: Literal["add", "replace", "remove"] = "replace"
    path: str
    before: Any = None
    after: Any = None
    reason: str = ""
    risk: str = "low"
    selected: bool = True


class ChangeSet(BaseModel):
    id: str
    thread_id: str
    source_run_id: str | None = None
    base_revision: int = 0
    summary: str
    risk: str = "low"
    status: ChangeSetStatus = "pending"
    operations: list[ChangeOperation] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str
    applied_at: str | None = None
    undone_at: str | None = None


class AgentRun(BaseModel):
    id: str
    thread_id: str
    status: AgentRunStatus = "pending"
    user_message_id: str
    assistant_message_id: str
    attempt: int = 1
    event_seq: int = 0
    steps: list[AgentStep] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    proposals: list[ActionProposal] = Field(default_factory=list)
    changeset_ids: list[str] = Field(default_factory=list)
    citations: list[AgentCitation] = Field(default_factory=list)
    context_snapshot: AgentContextSnapshot = Field(default_factory=AgentContextSnapshot)
    answer: str = ""
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    error: str = ""
    created_at: str
    updated_at: str


class ChangeSetConfirmRequest(BaseModel):
    selected_operation_ids: list[str] | None = None
    edited_values: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = None
