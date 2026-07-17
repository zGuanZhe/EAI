from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ServiceType = Literal[
    "conversation",
    "evidence_research",
    "document_reading",
    "synthesis",
    "workspace_operation",
    "sandbox_execution",
    "research_campaign",
]
IntentOverride = Literal["auto", "chat", "local", "deep_research", "execute"]
SourcePolicy = Literal["none", "local_only", "local_and_external"]
TaskStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "done",
    "failed",
    "cancelled",
    "interrupted",
]
AttemptStatus = Literal["running", "waiting_approval", "done", "failed", "cancelled", "interrupted"]
EvidenceLevel = Literal[
    "system_truth",
    "user_knowledge",
    "curated_summary",
    "metadata",
    "abstract",
    "full_text",
    "web_content",
]
RiskLevel = Literal["low", "medium", "high", "critical"]
ApprovalLevel = Literal["auto", "confirm", "command", "strong_confirm", "deny"]


class ServiceDecision(BaseModel):
    service: ServiceType = "conversation"
    objective: str = ""
    depth: Literal["quick", "standard", "deep"] = "quick"
    source_policy: SourcePolicy = "none"
    requested_outputs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    requires_clarification: bool = False
    clarification_question: str = ""
    reason: str = ""


class SourceRecord(BaseModel):
    id: str
    task_id: str | None = None
    source_kind: Literal[
        "workspace",
        "atlas",
        "local_document",
        "openalex",
        "arxiv",
        "crossref",
        "semantic_scholar",
        "web",
    ]
    evidence_level: EvidenceLevel
    title: str
    locator: dict[str, Any] = Field(default_factory=dict)
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    excerpt: str = ""
    canonical_key: str = ""
    content_hash: str = ""
    retrieved_at: str
    confidence: float = Field(default=0.7, ge=0, le=1)
    access: Literal["local", "open", "metadata_only", "unknown"] = "unknown"
    provider: str = ""


class EvidenceChunk(BaseModel):
    id: str
    source_id: str
    task_id: str | None = None
    text: str
    section: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    score: float = 0
    evidence_level: EvidenceLevel


class AgentArtifact(BaseModel):
    id: str
    task_id: str
    kind: Literal[
        "evidence_set",
        "research_note",
        "document_analysis",
        "canvas_draft",
        "operation_preview",
        "command_preview",
        "command_output",
        "memory_draft",
        "research_campaign",
    ]
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    created_at: str


class MemoryDraft(BaseModel):
    id: str
    task_id: str
    thread_id: str
    scope: Literal["thread", "project", "global"] = "thread"
    kind: Literal["preference", "judgement", "conclusion"]
    title: str
    content: str
    rationale: str = ""
    status: Literal["draft", "promoted", "rejected"] = "draft"
    created_at: str
    resolved_at: str | None = None


class TaskBudget(BaseModel):
    max_rounds: int = Field(default=4, ge=1, le=12)
    max_tool_calls: int = Field(default=8, ge=1, le=32)
    max_specialists: int = Field(default=4, ge=1, le=12)
    max_source_queries: int = Field(default=8, ge=1, le=24)
    max_sources: int = Field(default=24, ge=1, le=80)
    max_external_queries: int = Field(default=3, ge=0, le=12)
    max_fulltext_imports: int = Field(default=2, ge=0, le=8)
    max_parallel_reads: int = Field(default=3, ge=1, le=4)
    max_runtime_seconds: int = Field(default=120, ge=30, le=3600)


class BudgetUsage(BaseModel):
    rounds: int = 0
    tool_calls: int = 0
    source_queries: int = 0
    external_queries: int = 0
    fulltext_imports: int = 0
    sources: int = 0
    elapsed_seconds: float = 0


class AgentAttempt(BaseModel):
    id: str
    task_id: str
    number: int = 1
    status: AttemptStatus = "running"
    objective: str = ""
    budget: TaskBudget = Field(default_factory=TaskBudget)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    stop_reason: str = ""
    created_at: str
    updated_at: str


class ToolCall(BaseModel):
    id: str
    task_id: str
    attempt_id: str
    round: int = 1
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    input_summary: str = ""
    status: Literal["pending", "running", "done", "failed", "cancelled", "preview"] = "pending"
    source_ids: list[str] = Field(default_factory=list)
    observation_id: str | None = None
    error_code: str = ""
    duration_ms: int = 0
    created_at: str
    completed_at: str | None = None


class Observation(BaseModel):
    id: str
    task_id: str
    attempt_id: str
    tool_call_id: str
    capability: str
    status: Literal["ok", "partial", "failed", "preview"] = "ok"
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class ToolCallRequest(BaseModel):
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ToolDecision(BaseModel):
    action: Literal["call_tools", "answer", "clarify"] = "answer"
    calls: list[ToolCallRequest] = Field(default_factory=list)
    reason: str = ""
    clarification_question: str = ""


class AnswerClaim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    kind: Literal["fact", "inference", "recommendation", "status"] = "fact"


class AnswerDraft(BaseModel):
    answer: str
    claims: list[AnswerClaim] = Field(default_factory=list)


class CitationBinding(BaseModel):
    id: str
    source_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    claim_text: str = ""
    evidence_level: EvidenceLevel
    locator: dict[str, Any] = Field(default_factory=dict)
    status: Literal["verified", "limited", "unsupported"] = "verified"


class AgentTask(BaseModel):
    id: str
    thread_id: str
    turn_id: str
    assistant_message_id: str
    parent_task_id: str | None = None
    attempt: int = 1
    runtime_version: str = "2.1"
    active_attempt_id: str = ""
    service: ServiceType = "conversation"
    status: TaskStatus = "pending"
    objective: str = ""
    decision: ServiceDecision = Field(default_factory=ServiceDecision)
    source_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    child_task_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    stop_reason: str = ""
    input_payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: str
    updated_at: str


class AgentEvent(BaseModel):
    task_id: str
    seq: int
    kind: Literal[
        "service_selected",
        "objective_confirmed",
        "status",
        "specialist_started",
        "source_found",
        "artifact_ready",
        "approval_required",
        "capability_started",
        "capability_completed",
        "steer_applied",
        "evidence_gap",
        "answer_ready",
        "answer_delta",
        "operation_applied",
        "done",
        "error",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CapabilitySpec(BaseModel):
    id: str
    label: str
    description: str
    services: list[ServiceType]
    permission: Literal["read", "temporary", "write", "execute"]
    risk: RiskLevel = "low"
    approval: ApprovalLevel = "auto"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)
    idempotent: bool = True
    reversible: bool = False
    timeout_seconds: int = 30
    budget_cost: int = Field(default=1, ge=0, le=10)
    available: bool = True
    unavailable_reason: str = ""


class Operation(BaseModel):
    id: str
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    before: Any = None
    after: Any = None
    risk: RiskLevel = "medium"
    approval: ApprovalLevel = "confirm"
    selected: bool = True


class OperationBatch(BaseModel):
    id: str
    task_id: str
    thread_id: str
    base_revision: int = 0
    summary: str
    status: Literal["pending", "applied", "rejected", "conflicted", "failed", "undone"] = "pending"
    operations: list[Operation] = Field(default_factory=list)
    created_at: str
    updated_at: str
    applied_at: str | None = None
    undone_at: str | None = None
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    receipt: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    id: str
    task_id: str
    kind: Literal["operation_batch", "sandbox_command", "execution_session", "strong_action"]
    level: ApprovalLevel
    title: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    created_at: str
    resolved_at: str | None = None


class ApprovalResolveRequest(BaseModel):
    decision: Literal["approve", "reject"]
    selected_operation_ids: list[str] | None = None
    edited_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AgentTurnRequest(BaseModel):
    message: str
    surface: str | None = None
    intent_override: IntentOverride = "auto"
    source_policy: SourcePolicy | None = None
    turn_attachments: list[dict[str, Any]] = Field(default_factory=list)
    model_overrides: dict[str, str] = Field(default_factory=dict)


class AgentSteerRequest(BaseModel):
    message: str


class AgentTurnResponse(BaseModel):
    thread: dict[str, Any]
    task: AgentTask
    user_message_id: str
    assistant_message_id: str


class SourceSearchRequest(BaseModel):
    query: str
    thread_id: str | None = None
    source_policy: SourcePolicy = "local_and_external"
    providers: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=80)


class SourceSearchResponse(BaseModel):
    query: str
    sources: list[SourceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DocumentRecord(BaseModel):
    id: str
    title: str
    file_name: str
    media_type: str
    path: str
    content_hash: str
    page_count: int = 0
    chunk_count: int = 0
    created_at: str


class SandboxCommand(BaseModel):
    command: str
    image: str = "python:3.13-slim"
    network: bool = False
    timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    cpus: float = Field(default=2, gt=0, le=8)
    memory_mb: int = Field(default=4096, ge=256, le=16384)
    input_document_ids: list[str] = Field(default_factory=list)
    container_name: str | None = None
    gpus: str | None = None
