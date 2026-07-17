from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CampaignStatus = Literal[
    "planned", "ready", "running", "waiting_approval", "paused",
    "completed", "failed", "cancelled", "interrupted", "archived",
]
StageKind = Literal[
    "evidence_preparation", "initial_implementation", "baseline_tuning",
    "creative_research", "ablation", "writeup", "review", "release",
    "legacy_import",
]
BranchOrigin = Literal["draft", "improve", "debug", "manual"]
BranchStatus = Literal[
    "proposed", "waiting_approval", "running", "succeeded", "failed",
    "discarded", "promoted", "cancelled", "interrupted",
]


class CampaignWorkspaceSeed(BaseModel):
    kind: Literal["blank", "local_snapshot", "legacy_lab"] = "blank"
    title: str = "空白实验工作区"
    source_path: str = ""
    snapshot_hash: str = ""
    readonly: bool = True
    imported_at: str | None = None


class CampaignBudget(BaseModel):
    max_branches: int = Field(default=12, ge=1, le=64)
    max_debug_depth: int = Field(default=3, ge=0, le=8)
    max_runtime_seconds: int = Field(default=1800, ge=30, le=14400)
    max_total_runtime_seconds: int = Field(default=14400, ge=60, le=172800)
    max_model_calls: int = Field(default=60, ge=1, le=500)
    max_sessions: int = Field(default=12, ge=1, le=64)
    max_storage_mb: int = Field(default=10240, ge=512, le=102400)
    cpu_count: float = Field(default=2, gt=0, le=8)
    memory_mb: int = Field(default=4096, ge=512, le=16384)
    execution_profile: Literal["cpu", "cuda"] = "cpu"
    network_domains: list[str] = Field(default_factory=list)


class ResearchIdea(BaseModel):
    id: str
    title: str
    short_hypothesis: str
    novelty_summary: str = ""
    related_work_source_ids: list[str] = Field(default_factory=list)
    experiments: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    resource_estimate: str = ""
    risks: list[str] = Field(default_factory=list)
    evidence_note: str = ""
    status: Literal["draft", "selected", "rejected"] = "draft"


class MetricObservation(BaseModel):
    id: str
    branch_id: str
    name: str
    value: float
    direction: Literal["maximize", "minimize"] = "maximize"
    dataset: str = "default"
    split: str = "validation"
    seed: int | None = None
    unit: str = ""
    source_artifact_id: str | None = None
    created_at: str

    @property
    def protocol_key(self) -> str:
        return f"{self.name}:{self.dataset}:{self.split}:{self.direction}"


class CampaignArtifactRef(BaseModel):
    id: str
    branch_id: str = ""
    kind: str
    title: str
    summary: str = ""
    path: str = ""
    content_hash: str = ""
    media_type: str = "application/json"
    created_at: str


class ExecutionSession(BaseModel):
    id: str
    campaign_id: str
    branch_id: str
    status: Literal["pending", "approved", "running", "completed", "failed", "cancelled", "expired"] = "pending"
    image: str
    image_digest: str = ""
    command: str = "python runfile.py"
    readonly_inputs: list[str] = Field(default_factory=list)
    network_domains: list[str] = Field(default_factory=list)
    cpu_count: float = 2
    memory_mb: int = 4096
    timeout_seconds: int = 1800
    max_commands: int = 24
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str


class CampaignCheckpoint(BaseModel):
    id: str
    campaign_id: str
    branch_id: str | None = None
    journal_step: int = 0
    journal_path: str = ""
    git_commit: str = ""
    status: str = "saved"
    created_at: str


class ExperimentBranch(BaseModel):
    id: str
    campaign_id: str
    stage_id: str
    parent_id: str | None = None
    journal_node_id: str = ""
    origin: BranchOrigin = "draft"
    status: BranchStatus = "proposed"
    title: str
    plan_summary: str = ""
    overall_plan: str = ""
    code: str = ""
    command: str = "python runfile.py"
    git_commit: str = ""
    parent_git_commit: str = ""
    code_diff: str = ""
    analysis: str = ""
    error_summary: str = ""
    debug_depth: int = 0
    metric_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    checkpoint_id: str | None = None
    approval_id: str | None = None
    operation_batch_id: str | None = None
    score: float | None = None
    score_reason: str = ""
    is_best: bool = False
    created_at: str
    updated_at: str


class CampaignStage(BaseModel):
    id: str
    kind: StageKind
    title: str
    sequence: int
    goals: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "blocked"] = "pending"
    max_branches: int = Field(default=3, ge=0, le=32)
    branch_ids: list[str] = Field(default_factory=list)
    best_branch_id: str | None = None
    completion_reason: str = ""


class CitationBinding(BaseModel):
    id: str
    manuscript_id: str
    citation_key: str
    source_id: str
    evidence_span_ids: list[str] = Field(default_factory=list)
    evidence_level: str = "metadata"
    claim_text: str = ""
    status: Literal["verified", "limited", "unsupported", "invalid"] = "limited"


class ManuscriptSection(BaseModel):
    id: str
    title: str
    order: int
    content: str = ""
    claim_binding_ids: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class Manuscript(BaseModel):
    id: str
    campaign_id: str
    version: int = 1
    status: Literal["draft", "candidate", "current", "released"] = "draft"
    title: str
    language: str = "en"
    sections: list[ManuscriptSection] = Field(default_factory=list)
    citation_bindings: list[CitationBinding] = Field(default_factory=list)
    latex_path: str = ""
    pdf_path: str = ""
    bibtex_path: str = ""
    chinese_summary: str = ""
    disclosure: str = ""
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class PeerReview(BaseModel):
    id: str
    campaign_id: str
    manuscript_id: str
    role: Literal["methods", "evidence", "presentation", "meta"]
    summary: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    score: float = 0
    decision: Literal["accept", "revise", "reject"] = "revise"
    created_at: str


class RevisionRound(BaseModel):
    id: str
    campaign_id: str
    source_manuscript_id: str
    candidate_manuscript_id: str
    round_number: int = Field(ge=1, le=2)
    summary: str
    section_diffs: dict[str, str] = Field(default_factory=dict)
    status: Literal["candidate", "applied", "rejected"] = "candidate"
    created_at: str


class ResearchCampaign(BaseModel):
    id: str
    thread_id: str
    project_id: str | None = None
    source_kind: Literal["native", "legacy_lab"] = "native"
    source_ref: str = ""
    source_hash: str = ""
    source_node_ids: list[str] = Field(default_factory=list)
    workspace_seed: CampaignWorkspaceSeed = Field(default_factory=CampaignWorkspaceSeed)
    title: str
    objective: str
    hypothesis: str
    status: CampaignStatus = "planned"
    current_stage_id: str | None = None
    selected_idea: ResearchIdea
    stages: list[CampaignStage] = Field(default_factory=list)
    promoted_branches: list[ExperimentBranch] = Field(default_factory=list)
    budget: CampaignBudget = Field(default_factory=CampaignBudget)
    source_ids: list[str] = Field(default_factory=list)
    current_manuscript_id: str | None = None
    revision: int = 0
    disclosure_required: bool = True
    created_at: str
    updated_at: str


class IdeaPreviewRequest(BaseModel):
    node_id: str | None = None
    objective: str = ""
    count: int = Field(default=3, ge=1, le=5)


class IdeaPreviewResponse(BaseModel):
    objective: str
    ideas: list[ResearchIdea]
    sources: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CampaignCreateRequest(BaseModel):
    idea: ResearchIdea
    source_node_ids: list[str] = Field(default_factory=list)
    budget: CampaignBudget = Field(default_factory=CampaignBudget)
    workspace_seed: CampaignWorkspaceSeed = Field(default_factory=CampaignWorkspaceSeed)


class CampaignEvent(BaseModel):
    campaign_id: str
    seq: int
    kind: Literal[
        "campaign_status", "stage_started", "branch_proposed", "approval_required",
        "session_started", "command_started", "file_changed", "checkpoint_saved",
        "branch_started", "metric_observed", "branch_completed", "branch_failed",
        "best_branch_changed", "artifact_ready", "stage_completed", "manuscript_ready",
        "review_completed", "revision_ready", "release_ready", "done", "error",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CampaignSnapshot(BaseModel):
    campaign: ResearchCampaign
    branches: list[ExperimentBranch] = Field(default_factory=list)
    metrics: list[MetricObservation] = Field(default_factory=list)
    artifacts: list[CampaignArtifactRef] = Field(default_factory=list)
    sessions: list[ExecutionSession] = Field(default_factory=list)
    checkpoints: list[CampaignCheckpoint] = Field(default_factory=list)
    manuscripts: list[Manuscript] = Field(default_factory=list)
    reviews: list[PeerReview] = Field(default_factory=list)
    revisions: list[RevisionRound] = Field(default_factory=list)
    last_seq: int = 0


class StageAdvanceRequest(BaseModel):
    branch_id: str | None = None


class BranchPromoteRequest(BaseModel):
    include_finding: bool = True
    include_task: bool = True


class BranchCompareRequest(BaseModel):
    branch_ids: list[str] = Field(min_length=2, max_length=6)


class ManuscriptGenerateRequest(BaseModel):
    draft: bool = True


class ReviewStartRequest(BaseModel):
    manuscript_id: str | None = None


class RevisionApplyRequest(BaseModel):
    revision_id: str
    apply: bool = True


class ReleaseExportRequest(BaseModel):
    manuscript_id: str | None = None
    allow_draft_warnings: bool = False
