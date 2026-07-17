from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..legacy.models import ActionProposal, AgentRun, ChangeSet

__all__ = [
    "CardType",
    "NodeType",
    "SurfaceType",
    "ContextCard",
    "CanvasNode",
    "CanvasEdge",
    "CanvasState",
    "ProjectDoc",
    "ProjectCreate",
    "ProjectUpdate",
    "ObjectMemory",
    "PaperChatRequest",
    "ResultCard",
    "Message",
    "ToolRun",
    "ToolRunCreate",
    "MessageCreate",
    "ThreadChatRequest",
    "ThreadChatResponse",
    "ThreadChatStartResponse",
    "ThreadChatCompleteRequest",
    "ThreadChatRetryRequest",
    "MessageUpdate",
    "AgentRunStartResponse",
    "AgentRunCompleteRequest",
    "AgentRunResponse",
    "ProposalConfirmResponse",
    "ChangeSetResponse",
    "ContextInjectionRequest",
    "LabRunStatus",
    "LabStageStatus",
    "LabStage",
    "LabArtifact",
    "LabFinding",
    "LabMessage",
    "LabRun",
    "LabRunCreate",
    "LabRunUpdate",
    "LabMessageCreate",
    "LabTaskPackPreviewRequest",
    "LabTaskPackPreviewResponse",
    "LabTaskPackRunRequest",
    "LabResultPreviewRequest",
    "LabResultPreviewResponse",
    "LabTaskPackRunResponse",
    "ThreadDoc",
    "ThreadCreate",
    "ThreadUpdate",
    "ExportRequest",
    "ExportResponse",
    "ResultPreviewRequest",
    "ResultPreviewResponse",
    "FocusedObject",
    "ResearchTemplate",
    "TaskPackPreviewRequest",
    "TaskPackPreviewResponse",
    "TaskPackRunRequest",
    "TaskPackRunResponse",
    "CandidateStatus",
    "AtlasUpdateAction",
    "AtlasUpdateCandidate",
    "AtlasUpdateRun",
    "AtlasUpdateDoc",
    "AtlasUpdateTaskPackRequest",
    "AtlasUpdateTaskPackResponse",
    "AtlasUpdateResultPreviewRequest",
    "AtlasUpdateResultPreviewResponse",
    "AtlasCandidateUpdate",
]

CardType = Literal["paper", "relation", "path", "file"]
NodeType = Literal[
    "question", "hypothesis", "conclusion", "task", "material",
    "evidence", "decision", "finding", "campaign_ref",
]
SurfaceType = Literal["thread", "canvas", "atlas"]


class ContextCard(BaseModel):
    id: str
    type: CardType
    title: str
    source_ref: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    token_estimate: int = 0
    selected_for_export: bool = True
    include_in_agent: bool = True
    include_in_lab: bool = False
    priority: int = Field(default=1, ge=0, le=3)
    pinned: bool = False
    agent_note: str = ""


class CanvasNode(BaseModel):
    id: str
    type: NodeType
    title: str
    body: str = ""
    x: float = 0
    y: float = 0
    card_id: str | None = None
    status: str | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    entity_id: str | None = None
    campaign_id: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: str | None = None


class CanvasEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str = "supports"


class CanvasState(BaseModel):
    nodes: list[CanvasNode] = Field(default_factory=list)
    edges: list[CanvasEdge] = Field(default_factory=list)
    campaign_ids: list[str] = Field(default_factory=list)


class ProjectDoc(BaseModel):
    id: str
    title: str
    goal: str = ""
    status: str = "active"
    default_atlas_id: str = "G"
    created_at: str
    updated_at: str


class ProjectCreate(BaseModel):
    title: str = "Untitled outcome"
    goal: str = ""
    default_atlas_id: str = "G"


class ProjectUpdate(BaseModel):
    title: str | None = None
    goal: str | None = None
    status: str | None = None
    default_atlas_id: str | None = None


class ObjectMemory(BaseModel):
    object_ref: dict[str, Any] = Field(default_factory=dict)
    title_snapshot: str = ""
    star: bool = False
    maturity: int = Field(default=0, ge=0, le=5)
    tags: list[str] = Field(default_factory=list)
    judgement: str = ""
    note: str = ""
    core_innovation: str = ""
    core_technology: str = ""
    evidence: str = ""
    limitations: str = ""
    reusable_insight: str = ""
    reading_status: str = "unread"
    reading_questions: list[str] = Field(default_factory=list)
    paper_chat: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None


class PaperChatRequest(BaseModel):
    message: str
    paper_context: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None


class ResultCard(BaseModel):
    id: str | None = None
    title: str = "Codex result"
    raw_text: str
    parsed_json: dict[str, Any] | None = None
    created_at: str | None = None


class Message(BaseModel):
    id: str | None = None
    role: Literal["user", "assistant", "system", "tool"] = "system"
    kind: Literal["text", "assistant_reply", "task_pack", "result", "tool_run", "state"] = "text"
    content: str = ""
    created_at: str | None = None
    status: str = "done"
    surface: SurfaceType | Literal["home", "tools"] | None = None
    refs: dict[str, Any] = Field(default_factory=dict)
    linked_result_id: str | None = None
    linked_tool_run_id: str | None = None


class ToolRun(BaseModel):
    id: str | None = None
    tool: str
    status: str = "done"
    summary: str = ""
    created_at: str | None = None
    template_id: str | None = None
    mode: Literal["copy", "api"] | None = None
    provider: str | None = None
    model: str | None = None
    token_estimate: int | None = None
    input_summary: str | None = None


class ToolRunCreate(BaseModel):
    tool: str
    status: str = "done"
    summary: str = ""
    template_id: str | None = None
    mode: Literal["copy", "api"] | None = None
    provider: str | None = None
    model: str | None = None
    token_estimate: int | None = None
    input_summary: str | None = None


class MessageCreate(BaseModel):
    role: Literal["user", "assistant", "system", "tool"] = "user"
    kind: Literal["text", "assistant_reply", "task_pack", "result", "tool_run", "state"] = "text"
    content: str
    status: str = "done"
    surface: SurfaceType | Literal["home", "tools"] | None = None
    refs: dict[str, Any] = Field(default_factory=dict)
    linked_result_id: str | None = None
    linked_tool_run_id: str | None = None


class ThreadChatRequest(BaseModel):
    message: str
    surface: SurfaceType | Literal["home", "tools"] | None = None
    model: str | None = None
    turn_attachments: list[dict[str, Any]] = Field(default_factory=list)


class ThreadChatResponse(BaseModel):
    thread: ThreadDoc
    assistant_message: Message
    degraded: bool = False


class ThreadChatStartResponse(BaseModel):
    thread: ThreadDoc
    user_message_id: str
    assistant_message_id: str


class ThreadChatCompleteRequest(BaseModel):
    assistant_message_id: str
    model: str | None = None


class ThreadChatRetryRequest(BaseModel):
    surface: SurfaceType | Literal["home", "tools"] | None = None
    model: str | None = None


class MessageUpdate(BaseModel):
    content: str | None = None
    status: str | None = None
    refs: dict[str, Any] | None = None
    linked_result_id: str | None = None
    linked_tool_run_id: str | None = None


class AgentRunStartResponse(BaseModel):
    thread: ThreadDoc
    run: AgentRun
    user_message_id: str
    assistant_message_id: str


class AgentRunCompleteRequest(BaseModel):
    model: str | None = None
    retry: bool = False


class AgentRunResponse(BaseModel):
    thread: ThreadDoc
    run: AgentRun
    assistant_message: Message
    degraded: bool = False


class ProposalConfirmResponse(BaseModel):
    thread: ThreadDoc
    proposal: ActionProposal
    applied: dict[str, Any] = Field(default_factory=dict)


class ChangeSetResponse(BaseModel):
    thread: ThreadDoc
    changeset: ChangeSet
    applied: dict[str, Any] = Field(default_factory=dict)


class ContextInjectionRequest(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    target: Literal["main_chat", "context"] = "main_chat"
    title: str = ""
    summary: str = ""


LabRunStatus = Literal["planned", "running", "blocked", "completed", "archived"]
LabStageStatus = Literal["planned", "running", "blocked", "failed", "completed"]


class LabStage(BaseModel):
    id: str | None = None
    title: str
    status: LabStageStatus = "planned"
    summary: str = ""
    command: str = ""
    notes: str = ""
    updated_at: str | None = None


class LabArtifact(BaseModel):
    id: str | None = None
    type: str = "note"
    title: str
    summary: str = ""
    uri: str = ""
    content_preview: str = ""
    stage_id: str | None = None
    created_at: str | None = None


class LabFinding(BaseModel):
    id: str | None = None
    title: str
    body: str = ""
    confidence: str = ""
    source_stage_id: str | None = None
    created_at: str | None = None


class LabMessage(BaseModel):
    id: str | None = None
    role: Literal["user", "assistant", "system", "tool"] = "user"
    content: str
    created_at: str | None = None
    status: str = "done"
    model: str | None = None


class LabRun(BaseModel):
    id: str
    project_id: str | None = None
    thread_id: str | None = None
    title: str
    goal: str = ""
    hypothesis: str = ""
    status: LabRunStatus = "planned"
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    stages: list[LabStage] = Field(default_factory=list)
    artifacts: list[LabArtifact] = Field(default_factory=list)
    findings: list[LabFinding] = Field(default_factory=list)
    messages: list[LabMessage] = Field(default_factory=list)
    linked_canvas_nodes: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class LabRunCreate(BaseModel):
    thread_id: str | None = None
    project_id: str | None = None
    title: str = "新的实验运行"
    goal: str = ""
    hypothesis: str = ""
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    linked_canvas_nodes: list[str] = Field(default_factory=list)


class LabRunUpdate(BaseModel):
    project_id: str | None = None
    thread_id: str | None = None
    title: str | None = None
    goal: str | None = None
    hypothesis: str | None = None
    status: LabRunStatus | None = None
    source_refs: list[dict[str, Any]] | None = None
    stages: list[LabStage] | None = None
    artifacts: list[LabArtifact] | None = None
    findings: list[LabFinding] | None = None
    messages: list[LabMessage] | None = None
    linked_canvas_nodes: list[str] | None = None


class LabMessageCreate(BaseModel):
    role: Literal["user", "assistant", "system", "tool"] = "user"
    content: str
    status: str = "done"
    model: str | None = None


class LabTaskPackPreviewRequest(BaseModel):
    instruction_override: str | None = None


class LabTaskPackPreviewResponse(BaseModel):
    run_id: str
    title: str
    markdown: str
    token_estimate: int
    warnings: list[str] = Field(default_factory=list)


class LabTaskPackRunRequest(LabTaskPackPreviewRequest):
    provider: str | None = None
    model: str | None = None


class LabResultPreviewRequest(BaseModel):
    raw_text: str


class LabResultConfirmRequest(LabResultPreviewRequest):
    selected_item_ids: list[str] | None = None


class LabResultApplyItem(BaseModel):
    id: str
    kind: Literal["stage", "artifact", "finding", "task"]
    title: str
    summary: str = ""
    selected_by_default: bool = True


class LabResultPreviewResponse(BaseModel):
    result_card_preview: ResultCard
    stages: list[LabStage] = Field(default_factory=list)
    artifacts: list[LabArtifact] = Field(default_factory=list)
    findings: list[LabFinding] = Field(default_factory=list)
    next_tasks: list[CanvasNode] = Field(default_factory=list)
    canvas_nodes: list[CanvasNode] = Field(default_factory=list)
    canvas_edges: list[CanvasEdge] = Field(default_factory=list)
    apply_items: list[LabResultApplyItem] = Field(default_factory=list)


class LabTaskPackRunResponse(LabResultPreviewResponse):
    message: LabMessage


class ThreadDoc(BaseModel):
    id: str
    project_id: str | None = None
    title: str
    goal: str = ""
    status: str = "active"
    created_at: str
    updated_at: str
    revision: int = 0
    conversation_summary: str = ""
    active_surface: SurfaceType = "atlas"
    active_atlas_id: str = "G"
    context_cards: list[ContextCard] = Field(default_factory=list)
    canvas: CanvasState = Field(default_factory=CanvasState)
    messages: list[Message] = Field(default_factory=list)
    result_cards: list[ResultCard] = Field(default_factory=list)
    tool_runs: list[ToolRun] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    action_proposals: list[ActionProposal] = Field(default_factory=list)
    changesets: list[ChangeSet] = Field(default_factory=list)


class ThreadCreate(BaseModel):
    title: str = "Untitled research thread"
    goal: str = ""
    active_atlas_id: str = "G"
    project_id: str | None = None


class ThreadUpdate(BaseModel):
    project_id: str | None = None
    title: str | None = None
    goal: str | None = None
    status: str | None = None
    active_surface: SurfaceType | None = None
    active_atlas_id: str | None = None
    context_cards: list[ContextCard] | None = None
    canvas: CanvasState | None = None
    messages: list[Message] | None = None
    result_cards: list[ResultCard] | None = None
    tool_runs: list[ToolRun] | None = None
    agent_runs: list[AgentRun] | None = None
    action_proposals: list[ActionProposal] | None = None
    changesets: list[ChangeSet] | None = None
    expected_revision: int | None = None


class ExportRequest(BaseModel):
    selected_only: bool = True
    instruction: str = (
        "请基于选中的研究上下文给出精确、可执行的回答。"
        "如果产生结构化结果，请返回一个简洁的 eai-result/v1 JSON 代码块。"
    )


class ExportResponse(BaseModel):
    markdown: str
    exported_cards: int
    token_estimate: int


class ResultPreviewRequest(BaseModel):
    raw_text: str


class ResultPreviewResponse(BaseModel):
    result_card_preview: ResultCard
    canvas_nodes: list[CanvasNode] = Field(default_factory=list)
    canvas_edges: list[CanvasEdge] = Field(default_factory=list)


class FocusedObject(BaseModel):
    type: CardType | Literal["canvas_node"] | None = None
    id: str | None = None
    title: str = ""
    summary: str = ""
    source_ref: dict[str, Any] = Field(default_factory=dict)


class ResearchTemplate(BaseModel):
    id: str
    title: str
    short_title: str
    description: str
    recommended_for: list[str] = Field(default_factory=list)
    output_focus: list[str] = Field(default_factory=list)


class TaskPackPreviewRequest(BaseModel):
    template_id: str
    focused_object: FocusedObject | None = None
    selected_only: bool = True
    instruction_override: str | None = None


class TaskPackPreviewResponse(BaseModel):
    template_id: str
    title: str
    markdown: str
    token_estimate: int
    included_cards: int
    warnings: list[str] = Field(default_factory=list)


class TaskPackRunRequest(TaskPackPreviewRequest):
    provider: str | None = None
    model: str | None = None


class TaskPackRunResponse(ResultPreviewResponse):
    tool_run: ToolRun


CandidateStatus = Literal["pending", "applied", "deferred", "rejected"]
AtlasUpdateAction = Literal["recent", "all", "complete_cards", "evidence"]


class AtlasUpdateCandidate(BaseModel):
    id: str | None = None
    status: CandidateStatus = "pending"
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | str | None = None
    venue: str = ""
    url: str = ""
    doi: str = ""
    arxiv_id: str = ""
    abstract: str = ""
    suggested_route_id: str = ""
    why: str = ""
    relevance: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_run_id: str | None = None
    duplicate_of: str | None = None
    judgement: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class AtlasUpdateRun(BaseModel):
    id: str | None = None
    action: AtlasUpdateAction = "recent"
    title: str = ""
    status: str = "preview"
    created_at: str | None = None
    token_estimate: int = 0
    candidate_count: int = 0
    summary: str = ""


class AtlasUpdateDoc(BaseModel):
    atlas_id: str
    runs: list[AtlasUpdateRun] = Field(default_factory=list)
    candidates: list[AtlasUpdateCandidate] = Field(default_factory=list)
    updated_at: str | None = None


class AtlasUpdateTaskPackRequest(BaseModel):
    action: AtlasUpdateAction = "recent"
    route_id: str | None = None
    instruction_override: str | None = None


class AtlasUpdateTaskPackResponse(BaseModel):
    run: AtlasUpdateRun
    markdown: str
    token_estimate: int
    warnings: list[str] = Field(default_factory=list)


class AtlasUpdateResultPreviewRequest(BaseModel):
    raw_text: str
    action: AtlasUpdateAction = "recent"


class AtlasUpdateResultPreviewResponse(BaseModel):
    run: AtlasUpdateRun
    candidates: list[AtlasUpdateCandidate]
    duplicate_count: int = 0
    applied_count: int = 0


class AtlasCandidateUpdate(BaseModel):
    status: CandidateStatus | None = None
    title: str | None = None
    authors: list[str] | None = None
    year: int | str | None = None
    venue: str | None = None
    url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    suggested_route_id: str | None = None
    why: str | None = None
    relevance: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    judgement: str | None = None
    tags: list[str] | None = None
