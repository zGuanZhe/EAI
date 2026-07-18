from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..agent_v2.models import ApprovalRequest, ApprovalResolveRequest, Operation, OperationBatch
from ..agent_v2.sandbox import SandboxUnavailable, docker_available, docker_gpu_available, run_docker_command, stop_docker_container
from ..agent_v2.store import RuntimeStore
from ..research.store import ResearchStore
from .adapters import EAIEvidenceBackend, EAIExecutionBackend, EAIJournalSink, EAIModelBackend, write_branch_workspace
from .migration import migrate_legacy_labs
from .models import (
    BranchCompareRequest,
    BranchPromoteRequest,
    CampaignArtifactRef,
    CampaignBudget,
    CampaignCheckpoint,
    CampaignSnapshot,
    CampaignStage,
    CampaignWorkspaceSeed,
    ExecutionSession,
    ExperimentBranch,
    ManuscriptGenerateRequest,
    IdeaPreviewResponse,
    MetricObservation,
    ReleaseExportRequest,
    ReviewStartRequest,
    ResearchCampaign,
    ResearchIdea,
    RevisionApplyRequest,
)
from .publication import PublicationService
from .runtime import CampaignRuntimeManager, hidden_process_flags


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


STAGE_BLUEPRINTS = [
    ("evidence_preparation", "证据准备", ["核验论文身份与全文证据", "记录缺失证据与实验边界"], 0),
    ("initial_implementation", "初始实现", ["产生独立草案", "找到可运行的最小实现"], 3),
    ("baseline_tuning", "基线调优", ["保持研究问题不变", "建立稳定且可比较的基线"], 3),
    ("creative_research", "创新研究", ["探索能检验核心假设的改进", "记录成功与失败模式"], 3),
    ("ablation", "消融", ["拆分关键组件", "验证指标变化的来源"], 3),
    ("writeup", "写作", ["整理证据、实验与披露"], 0),
    ("review", "审稿", ["检查论断、图表和局限"], 0),
    ("release", "发布", ["核验引用、披露与可复现研究包"], 0),
]


class CampaignService:
    def __init__(
        self,
        *,
        research_store: ResearchStore,
        runtime_store: RuntimeStore,
        load_thread: Callable[[str], Any],
        prepare_operation_batch: Callable[[OperationBatch], OperationBatch],
        apply_operation_batch: Callable[[OperationBatch, ApprovalResolveRequest], dict[str, Any]],
        planner: Callable[[str], str] | None = None,
        read_only: bool = False,
    ):
        self.research_store = research_store
        self.runtime_store = runtime_store
        self.load_thread = load_thread
        self.prepare_operation_batch = prepare_operation_batch
        self.apply_operation_batch = apply_operation_batch
        self.model = EAIModelBackend(planner)
        self.evidence = EAIEvidenceBackend(research_store.search)
        self.execution = EAIExecutionBackend()
        self.publication = PublicationService(planner)
        self.read_only = read_only
        self.runtime = CampaignRuntimeManager(runtime_store.runtime_dir, read_only=read_only)
        self._lock = threading.RLock()
        self._workers: dict[str, threading.Thread] = {}
        personal_dir = getattr(self.research_store, "personal_dir", None)
        self.legacy_migration = (
            migrate_legacy_labs(
                personal_dir=Path(personal_dir),
                already_imported=lambda campaign_id: self.research_store.get_record("research_campaign", campaign_id),
                save_snapshot=self._save_imported_snapshot,
                now=utc_now(),
            )
            if personal_dir and not read_only else {"found": 0, "imported": 0, "unchanged": 0, "failed": 0}
        )

    def ensure_writable(self) -> None:
        self.research_store.ensure_writable()

    def _save_imported_snapshot(self, snapshot: CampaignSnapshot) -> None:
        self.research_store.save_record(
            "research_campaign", snapshot.campaign.id, snapshot.campaign.model_dump(mode="json")
        )
        self._checkpoint(snapshot)

    def _save_campaign(self, campaign: ResearchCampaign) -> ResearchCampaign:
        campaign.updated_at = utc_now()
        campaign.revision += 1
        self.research_store.save_record("research_campaign", campaign.id, campaign.model_dump(mode="json"))
        return campaign

    def _checkpoint(self, snapshot: CampaignSnapshot) -> CampaignSnapshot:
        snapshot.last_seq = max(
            [event.seq for event in self.runtime_store.list_campaign_events(snapshot.campaign.id)] or [snapshot.last_seq]
        )
        self.runtime_store.save_campaign_checkpoint(
            snapshot.campaign.id, snapshot.campaign.thread_id, snapshot.campaign.status,
            snapshot.model_dump(mode="json"), snapshot.campaign.updated_at,
        )
        return snapshot

    def _emit(self, campaign_id: str, kind: str, payload: dict[str, Any]):
        return self.runtime_store.append_campaign_event(campaign_id, kind, payload, utc_now())

    def list_for_thread(self, thread_id: str) -> list[CampaignSnapshot]:
        records = [item for item in self.research_store.list_records("research_campaign") if item.get("thread_id") == thread_id]
        snapshots = []
        for record in records:
            snapshot = self.get(record["id"])
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def runtime_status(self) -> dict[str, Any]:
        return {**self.runtime.status(), "legacy_migration": self.legacy_migration}

    def install_runtime(self, profile: str) -> dict[str, Any]:
        return self.runtime.install(profile)

    def cancel_runtime_install(self) -> dict[str, Any]:
        return self.runtime.cancel_install()

    def runtime_events(self, after_seq: int):
        return self.runtime.events(after_seq)

    def mark_incomplete_interrupted(self) -> int:
        changed = 0
        records = self.research_store.list_records("research_campaign")
        for record in records:
            snapshot = self.get(str(record.get("id") or ""))
            if not snapshot or snapshot.campaign.status != "running":
                continue
            snapshot.campaign.status = "interrupted"
            for branch in snapshot.branches:
                if branch.status == "running":
                    branch.status = "proposed"
                    branch.error_summary = "应用重启中断了该分支；原容器不会在宿主机继续运行。"
                    branch.updated_at = utc_now()
            self._save_campaign(snapshot.campaign)
            self._emit(snapshot.campaign.id, "error", {"message": "应用重启中断了 Campaign，可继续运行。", "recoverable": True})
            self._checkpoint(snapshot)
            changed += 1
        return changed

    def get(self, campaign_id: str) -> CampaignSnapshot | None:
        campaign_payload = self.research_store.get_record("research_campaign", campaign_id)
        if not campaign_payload:
            return None
        checkpoint = self.runtime_store.get_campaign_checkpoint(campaign_id)
        if checkpoint:
            snapshot = CampaignSnapshot.model_validate(checkpoint)
            snapshot.campaign = ResearchCampaign.model_validate(campaign_payload)
            snapshot.last_seq = max([event.seq for event in self.runtime_store.list_campaign_events(campaign_id)] or [0])
            return snapshot
        return CampaignSnapshot(campaign=ResearchCampaign.model_validate(campaign_payload))

    def preview_ideas(self, thread_id: str, *, node_id: str | None, objective: str, count: int) -> IdeaPreviewResponse:
        thread = self.load_thread(thread_id)
        node = next((item for item in thread.canvas.nodes if item.id == node_id), None) if node_id else None
        query = (objective or (node.title if node else "") or thread.goal or thread.title).strip()
        bundle = self.evidence.investigate(query, thread.active_atlas_id, limit=12)
        evidence_by_work = {item.work_id for item in bundle.evidence if item.work_id and item.evidence_level == "full_text"}
        sources = [
            {
                "id": work.id, "title": work.title, "year": work.year,
                "evidence_level": "full_text" if work.id in evidence_by_work else "curated_summary",
                "atlas_placements": work.atlas_placements,
            }
            for work in bundle.works
        ]
        source_ids = [item["id"] for item in sources[:8]]
        related = "、".join(item["title"] for item in sources[:3]) or "当前工作区尚无可核验论文"
        ideas = []
        frames = [
            ("可证伪基线", "先建立可复现基线，再验证核心变量是否带来稳定变化"),
            ("边界条件", "系统改变数据、任务或训练条件，定位假设成立与失效的边界"),
            ("机制消融", "拆分关键组件并比较指标与失败模式，验证贡献来源"),
            ("跨路线迁移", "将相邻研究路线的方法迁移到当前问题并检查泛化"),
            ("反例驱动", "主动构造反例，检验当前解释是否过度概括"),
        ]
        for index, (label, hypothesis_frame) in enumerate(frames[:count]):
            title = f"{query[:44]}：{label}"
            ideas.append(ResearchIdea(
                id=new_id("idea"), title=title,
                short_hypothesis=f"围绕“{query}”，{hypothesis_frame}。",
                novelty_summary=f"以 {related} 为已知边界，候选创新点需在原文与实验中继续核验。",
                related_work_source_ids=source_ids,
                experiments=["建立固定数据与指标的最小基线", f"执行{label}分支并记录随机种子", "比较成功、失败和资源开销"],
                metrics=["primary_metric", "stability", "runtime"],
                resource_estimate="默认 2 CPU / 4 GB；单命令最长 60 分钟",
                risks=["当前来源可能只有策展摘要", "实验代码需要逐次审批", "指标不可跨设置直接比较"],
                evidence_note="包含可定位全文证据" if evidence_by_work else "当前仅有策展或元数据证据；执行前应补充原文",
            ))
        warnings = list(bundle.missing)
        if not evidence_by_work:
            warnings.append("未找到可定位的全文证据，候选想法仅用于规划，不能据此声称新颖性。")
        return IdeaPreviewResponse(objective=query, ideas=ideas, sources=sources, warnings=list(dict.fromkeys(warnings)))

    def create(
        self, thread_id: str, idea: ResearchIdea, source_node_ids: list[str],
        budget: CampaignBudget, workspace_seed: CampaignWorkspaceSeed | None = None,
    ) -> CampaignSnapshot:
        thread = self.load_thread(thread_id)
        now = utc_now()
        idea.status = "selected"
        campaign_id = new_id("campaign")
        workspace_seed = self._materialize_workspace_seed(campaign_id, workspace_seed or CampaignWorkspaceSeed(), budget)
        stages = [
            CampaignStage(id=f"stage_{campaign_id}_{index + 1}", kind=kind, title=title, sequence=index + 1, goals=goals, max_branches=max_branches)
            for index, (kind, title, goals, max_branches) in enumerate(STAGE_BLUEPRINTS)
        ]
        campaign = ResearchCampaign(
            id=campaign_id, thread_id=thread_id, project_id=thread.project_id,
            source_node_ids=source_node_ids, title=idea.title, objective=idea.title,
            hypothesis=idea.short_hypothesis, status="ready", selected_idea=idea,
            stages=stages, budget=budget, source_ids=idea.related_work_source_ids,
            workspace_seed=workspace_seed,
            created_at=now, updated_at=now,
        )
        self._save_campaign(campaign)
        snapshot = self._checkpoint(CampaignSnapshot(campaign=campaign))
        self._emit(campaign.id, "campaign_status", {"status": "ready", "label": "Campaign 已创建"})
        return self._checkpoint(snapshot)

    def _materialize_workspace_seed(
        self, campaign_id: str, seed: CampaignWorkspaceSeed, budget: CampaignBudget
    ) -> CampaignWorkspaceSeed:
        if seed.kind != "local_snapshot":
            return seed.model_copy(update={"source_path": ""})
        source = Path(seed.source_path).expanduser().resolve()
        if not source.exists() or source.is_symlink():
            raise ValueError("本地实验起点不存在或是符号链接")
        target = self.runtime_store.workspaces_dir / campaign_id / "seed"
        target.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        files = [source] if source.is_file() else sorted(item for item in source.rglob("*") if item.is_file())
        excluded = {".git", "node_modules", "dist", "target", "__pycache__", ".venv", "venv"}
        for path in files:
            relative = Path(path.name) if source.is_file() else path.relative_to(source)
            if any(part in excluded for part in relative.parts) or path.is_symlink():
                continue
            size = path.stat().st_size
            total += size
            if total > budget.max_storage_mb * 1024 * 1024:
                shutil.rmtree(target, ignore_errors=True)
                raise ValueError("本地实验快照超过 Campaign 存储预算")
            digest.update(str(relative).replace("\\", "/").encode("utf-8"))
            digest.update(path.read_bytes())
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        return CampaignWorkspaceSeed(
            kind="local_snapshot", title=seed.title or source.name,
            source_path="seed", snapshot_hash=digest.hexdigest(), readonly=True, imported_at=utc_now(),
        )

    def _propose_branch(self, snapshot: CampaignSnapshot, stage: CampaignStage, *, parent: ExperimentBranch | None, index: int, origin: str | None = None) -> ExperimentBranch:
        now = utc_now()
        proposal = self.model.propose_branch(
            hypothesis=snapshot.campaign.hypothesis, stage_title=stage.title, index=index, parent=parent
        )
        branch = ExperimentBranch(
            id=new_id("branch"), campaign_id=snapshot.campaign.id, stage_id=stage.id,
            parent_id=parent.id if parent else None,
            origin=origin or ("draft" if not parent else "improve"), title=proposal["title"],
            plan_summary=proposal["plan"], overall_plan=snapshot.campaign.objective,
            code=proposal["code"], debug_depth=(parent.debug_depth + 1 if origin == "debug" and parent else 0),
            created_at=now, updated_at=now,
        )
        snapshot.branches.append(branch)
        stage.branch_ids.append(branch.id)
        if parent:
            parent.child_ids.append(branch.id)
            parent.updated_at = now
        self._emit(snapshot.campaign.id, "branch_proposed", branch.model_dump(mode="json"))
        return branch

    def start(self, campaign_id: str) -> CampaignSnapshot:
        with self._lock:
            snapshot = self._required(campaign_id)
            if snapshot.campaign.status not in {"ready", "paused", "interrupted"}:
                raise ValueError("Campaign 当前状态不能启动")
            stage = snapshot.campaign.stages[0] if not snapshot.campaign.current_stage_id else self._stage(snapshot, snapshot.campaign.current_stage_id)
            if stage.kind == "evidence_preparation":
                stage.status = "completed"
                stage.completion_reason = "已记录 Idea 使用的 Research Store 来源与证据缺口"
                self._emit(campaign_id, "stage_completed", stage.model_dump(mode="json"))
                stage = snapshot.campaign.stages[1]
            snapshot.campaign.current_stage_id = stage.id
            snapshot.campaign.status = "running"
            stage.status = "running"
            self._save_campaign(snapshot.campaign)
            self._emit(campaign_id, "campaign_status", {"status": "running", "label": "Campaign 已启动"})
            self._emit(campaign_id, "stage_started", stage.model_dump(mode="json"))
            if not stage.branch_ids:
                for index in range(min(stage.max_branches, snapshot.campaign.budget.max_branches)):
                    self._propose_branch(snapshot, stage, parent=None, index=index)
                self._save_campaign(snapshot.campaign)
            return self._checkpoint(snapshot)

    def pause(self, campaign_id: str) -> CampaignSnapshot:
        snapshot = self._required(campaign_id)
        snapshot.campaign.status = "paused"
        self._save_campaign(snapshot.campaign)
        self._emit(campaign_id, "campaign_status", {"status": "paused", "label": "Campaign 已暂停"})
        return self._checkpoint(snapshot)

    def resume(self, campaign_id: str) -> CampaignSnapshot:
        return self.start(campaign_id)

    def cancel(self, campaign_id: str) -> CampaignSnapshot:
        snapshot = self._required(campaign_id)
        for branch in snapshot.branches:
            if branch.status == "running":
                stop_docker_container(f"eai-campaign-{branch.id}"[:63])
                branch.status = "cancelled"
                branch.updated_at = utc_now()
        snapshot.campaign.status = "cancelled"
        self._save_campaign(snapshot.campaign)
        self._emit(campaign_id, "campaign_status", {"status": "cancelled", "label": "Campaign 已取消"})
        self._emit(campaign_id, "done", {"status": "cancelled"})
        return self._checkpoint(snapshot)

    def prepare_execution(self, campaign_id: str, branch_id: str) -> dict[str, Any]:
        with self._lock:
            snapshot = self._required(campaign_id)
            branch = self._branch(snapshot, branch_id)
            if branch.status not in {"proposed", "failed"}:
                raise ValueError("该分支当前不能准备执行")
            workspace = self.runtime_store.workspaces_dir / campaign_id / branch.id
            write_branch_workspace(workspace, branch)
            runtime = self.runtime.status()
            profile = snapshot.campaign.budget.execution_profile
            image = runtime.get(profile, {})
            runner_payload = {
                "campaign_id": campaign_id, "branch_id": branch.id,
                "journal_node_id": branch.journal_node_id or branch.id,
                "objective": snapshot.campaign.objective, "plan": branch.plan_summary,
                "command": branch.command or "python runfile.py",
            }
            (workspace / "session.json").write_text(json.dumps(runner_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            spec = self.execution.prepare(
                branch, profile=profile, timeout=snapshot.campaign.budget.max_runtime_seconds,
                cpus=snapshot.campaign.budget.cpu_count, memory_mb=snapshot.campaign.budget.memory_mb,
            )
            spec.image = str(image.get("image") or self.runtime.image(profile))
            spec.command = "python /opt/ai-scientist-v2/eai_campaign_runner.py < session.json"
            spec.network = False
            if not docker_available() or not image.get("installed"):
                artifact = CampaignArtifactRef(
                    id=new_id("artifact"), branch_id=branch.id, kind="command_preview",
                    title=f"会话预览：{branch.title}", summary=branch.command,
                    path=str(workspace / "runfile.py"), created_at=utc_now(),
                )
                snapshot.artifacts.append(artifact)
                branch.artifact_ids.append(artifact.id)
                self._emit(campaign_id, "artifact_ready", artifact.model_dump(mode="json"))
                self._checkpoint(snapshot)
                return {
                    "approval": None, "branch": branch.model_dump(mode="json"),
                    "command_preview": spec.model_dump(mode="json"), "docker_available": docker_available(),
                    "runtime_required": not image.get("installed"),
                }
            session = ExecutionSession(
                id=new_id("session"), campaign_id=campaign_id, branch_id=branch.id,
                image=spec.image, image_digest=str(image.get("digest") or ""), command=branch.command,
                readonly_inputs=["campaign-seed"] if snapshot.campaign.workspace_seed.kind == "local_snapshot" else [],
                network_domains=list(snapshot.campaign.budget.network_domains),
                cpu_count=snapshot.campaign.budget.cpu_count,
                memory_mb=snapshot.campaign.budget.memory_mb,
                timeout_seconds=snapshot.campaign.budget.max_runtime_seconds,
                created_at=utc_now(),
            )
            snapshot.sessions.append(session)
            approval = ApprovalRequest(
                id=new_id("approval"), task_id=f"campaign:{campaign_id}", kind="execution_session",
                level="command", title="确认分支执行会话", summary=branch.plan_summary,
                payload={
                    "campaign_id": campaign_id, "branch_id": branch.id,
                    "session": session.model_dump(mode="json"), "command": spec.model_dump(mode="json"),
                },
                created_at=utc_now(),
            )
            self.runtime_store.save_approval(approval)
            branch.approval_id = approval.id
            branch.session_id = session.id
            branch.status = "waiting_approval"
            branch.updated_at = utc_now()
            snapshot.campaign.status = "waiting_approval"
            self._save_campaign(snapshot.campaign)
            self._emit(campaign_id, "approval_required", approval.model_dump(mode="json"))
            self._checkpoint(snapshot)
            return {
                "approval": approval.model_dump(mode="json"), "branch": branch.model_dump(mode="json"),
                "session": session.model_dump(mode="json"), "docker_available": True,
            }

    def promote(self, campaign_id: str, branch_id: str, request: BranchPromoteRequest) -> dict[str, Any]:
        with self._lock:
            snapshot = self._required(campaign_id)
            branch = self._branch(snapshot, branch_id)
            if branch.status != "succeeded":
                raise ValueError("只有成功分支可以晋升")
            operations = []
            source_id = snapshot.campaign.source_node_ids[0] if snapshot.campaign.source_node_ids else None
            campaign_node_id = f"campaign_ref_{campaign_id}"
            operations.append({"action": "add_node", "data": {
                "id": campaign_node_id, "type": "campaign_ref", "title": snapshot.campaign.title,
                "body": f"{self._stage(snapshot, branch.stage_id).title} · {branch.title}", "campaign_id": campaign_id,
                "entity_id": f"research_campaign:{campaign_id}", "status": "active",
            }})
            if source_id:
                operations.append({"action": "add_edge", "data": {"id": new_id("edge"), "source": source_id, "target": campaign_node_id, "label": "leads_to"}})
            if request.include_finding:
                finding_id = new_id("finding")
                operations.append({"action": "add_node", "data": {
                    "id": finding_id, "type": "finding", "title": f"实验发现：{branch.title}",
                    "body": branch.analysis or "分支执行成功，指标与产物可在 Campaign 中检查。",
                    "campaign_id": campaign_id, "verification_status": "observed",
                }})
                operations.append({"action": "add_edge", "data": {"id": new_id("edge"), "source": campaign_node_id, "target": finding_id, "label": "supports"}})
            if request.include_task:
                task_id = new_id("task")
                operations.append({"action": "add_node", "data": {
                    "id": task_id, "type": "task", "title": "复核并扩展最佳实验分支",
                    "body": "检查随机种子、数据边界与失败分支后决定是否推进下一阶段。",
                    "campaign_id": campaign_id, "status": "todo", "priority": 1,
                }})
                operations.append({"action": "add_edge", "data": {"id": new_id("edge"), "source": campaign_node_id, "target": task_id, "label": "requires"}})
            batch = OperationBatch(
                id=new_id("operation_batch"), task_id=f"campaign:{campaign_id}", thread_id=snapshot.campaign.thread_id,
                summary=f"晋升 Campaign 分支：{branch.title}",
                operations=[Operation(
                    id=new_id("operation"), capability="canvas.apply", arguments={"operations": operations},
                    summary="把分支发现和下一步写入 Canvas", risk="medium", approval="confirm",
                )], created_at=utc_now(), updated_at=utc_now(),
            )
            batch = self.prepare_operation_batch(batch)
            self.runtime_store.save_operation_batch(batch)
            approval = ApprovalRequest(
                id=new_id("approval"), task_id=f"campaign:{campaign_id}", kind="operation_batch",
                level="confirm", title="确认晋升实验分支", summary=batch.summary,
                payload={"campaign_id": campaign_id, "branch_id": branch_id, "operation_batch": batch.model_dump(mode="json")},
                created_at=utc_now(),
            )
            self.runtime_store.save_approval(approval)
            branch.operation_batch_id = batch.id
            branch.approval_id = approval.id
            self._emit(campaign_id, "approval_required", approval.model_dump(mode="json"))
            self._checkpoint(snapshot)
            return {"approval": approval.model_dump(mode="json"), "operation_batch": batch.model_dump(mode="json")}

    def discard(self, campaign_id: str, branch_id: str) -> CampaignSnapshot:
        snapshot = self._required(campaign_id)
        branch = self._branch(snapshot, branch_id)
        branch.status = "discarded"
        branch.updated_at = utc_now()
        return self._checkpoint(snapshot)

    def advance(self, campaign_id: str, branch_id: str | None) -> CampaignSnapshot:
        with self._lock:
            snapshot = self._required(campaign_id)
            current = self._stage(snapshot, snapshot.campaign.current_stage_id)
            selected = self._branch(snapshot, branch_id) if branch_id else self._best_branch(snapshot, current)
            if not selected or selected.status not in {"succeeded", "promoted"}:
                raise ValueError("需要先选择一个成功分支")
            current.status = "completed"
            current.best_branch_id = selected.id
            current.completion_reason = "用户确认最佳分支并推进"
            selected.is_best = True
            self._emit(campaign_id, "stage_completed", current.model_dump(mode="json"))
            next_stage = next((item for item in snapshot.campaign.stages if item.sequence == current.sequence + 1), None)
            if not next_stage:
                snapshot.campaign.status = "completed"
                self._emit(campaign_id, "done", {"status": "completed"})
            else:
                next_stage.status = "running"
                snapshot.campaign.current_stage_id = next_stage.id
                snapshot.campaign.status = "running"
                self._emit(campaign_id, "stage_started", next_stage.model_dump(mode="json"))
                if next_stage.kind not in {"writeup", "review", "release"}:
                    self._propose_branch(snapshot, next_stage, parent=selected, index=0)
            self._save_campaign(snapshot.campaign)
            return self._checkpoint(snapshot)

    def resolve_approval(self, approval_id: str, resolution: ApprovalResolveRequest) -> dict[str, Any]:
        approval = self.runtime_store.get_approval(approval_id)
        if not approval or not approval.task_id.startswith("campaign:"):
            raise KeyError("Campaign approval not found")
        if approval.status != "pending":
            raise ValueError("Campaign approval is not pending")
        campaign_id = approval.payload.get("campaign_id")
        snapshot = self._required(campaign_id)
        branch = self._branch(snapshot, approval.payload.get("branch_id"))
        approval.status = "approved" if resolution.decision == "approve" else "rejected"
        approval.resolved_at = utc_now()
        self.runtime_store.save_approval(approval)
        if resolution.decision != "approve":
            branch.status = "proposed" if approval.kind in {"sandbox_command", "execution_session"} else branch.status
            if branch.session_id:
                session = next((item for item in snapshot.sessions if item.id == branch.session_id), None)
                if session:
                    session.status = "cancelled"
                    session.completed_at = utc_now()
            branch.updated_at = utc_now()
            snapshot.campaign.status = "running"
            self._save_campaign(snapshot.campaign)
            self._checkpoint(snapshot)
            return {"approval": approval.model_dump(mode="json"), "campaign": snapshot.model_dump(mode="json")}
        if approval.kind == "operation_batch":
            batch = OperationBatch.model_validate(approval.payload["operation_batch"])
            result = self.apply_operation_batch(batch, resolution)
            branch.status = "promoted"
            branch.updated_at = utc_now()
            if not any(item.id == branch.id for item in snapshot.campaign.promoted_branches):
                snapshot.campaign.promoted_branches.append(branch)
            self._save_campaign(snapshot.campaign)
            self._emit(campaign_id, "campaign_status", {"status": snapshot.campaign.status, "label": "分支已晋升到 Canvas", "branch_id": branch.id})
            self._checkpoint(snapshot)
            return {"approval": approval.model_dump(mode="json"), "result": result, "campaign": snapshot.model_dump(mode="json")}
        branch.status = "running"
        branch.updated_at = utc_now()
        if branch.session_id:
            session = next((item for item in snapshot.sessions if item.id == branch.session_id), None)
            if session:
                session.status = "running"
                session.started_at = utc_now()
        snapshot.campaign.status = "running"
        self._save_campaign(snapshot.campaign)
        self._emit(campaign_id, "branch_started", branch.model_dump(mode="json"))
        if branch.session_id:
            self._emit(campaign_id, "session_started", {"session_id": branch.session_id, "branch_id": branch.id})
        self._checkpoint(snapshot)
        worker = threading.Thread(target=self._execute_branch, args=(campaign_id, branch.id, approval.payload["command"]), daemon=True)
        self._workers[branch.id] = worker
        worker.start()
        return {"approval": approval.model_dump(mode="json"), "campaign": snapshot.model_dump(mode="json")}

    def _execute_branch(self, campaign_id: str, branch_id: str, command_payload: dict[str, Any]) -> None:
        from ..agent_v2.models import SandboxCommand

        workspace = self.runtime_store.workspaces_dir / campaign_id / branch_id
        try:
            initial = self._required(campaign_id)
            seed_path = self.runtime_store.workspaces_dir / campaign_id / "seed"
            readonly_inputs = [seed_path] if initial.campaign.workspace_seed.kind == "local_snapshot" and seed_path.exists() else []
            output = run_docker_command(SandboxCommand.model_validate(command_payload), workspace, readonly_inputs)
        except (SandboxUnavailable, Exception) as exc:
            output = {"exit_code": -1, "stdout": "", "stderr": str(exc), "command": command_payload.get("command")}
        with self._lock:
            snapshot = self._required(campaign_id)
            branch = self._branch(snapshot, branch_id)
            if branch.status == "cancelled" or snapshot.campaign.status == "cancelled":
                return
            now = utc_now()
            artifact_path = workspace / "execution.json"
            artifact_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            runner_events = []
            for line in str(output.get("stdout") or "").splitlines():
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and parsed.get("kind"):
                    runner_events.append(parsed)
                    if parsed["kind"] in {"command_started", "file_changed", "checkpoint_saved"}:
                        self._emit(campaign_id, parsed["kind"], parsed.get("payload") or {})
            command_result = next((item.get("payload") for item in reversed(runner_events) if item.get("kind") == "command_completed"), None)
            if command_result:
                output = {**output, **command_result}
            artifact = CampaignArtifactRef(
                id=new_id("artifact"), branch_id=branch.id, kind="command_output",
                title=f"执行输出：{branch.title}", summary=(output.get("stdout") or output.get("stderr") or "")[-800:],
                path=str(artifact_path), created_at=now,
                content_hash=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            )
            snapshot.artifacts.append(artifact)
            branch.artifact_ids.append(artifact.id)
            EAIJournalSink(lambda kind, payload: self._emit(campaign_id, kind, payload)).artifact(artifact)
            journal_path = workspace / "journal.json"
            if journal_path.exists():
                checkpoint = CampaignCheckpoint(
                    id=new_id("checkpoint"), campaign_id=campaign_id, branch_id=branch.id,
                    journal_step=max(0, len(runner_events) - 1), journal_path=str(journal_path),
                    git_commit=self._git_checkpoint(workspace, branch), created_at=now,
                )
                snapshot.checkpoints.append(checkpoint)
                branch.checkpoint_id = checkpoint.id
                branch.git_commit = checkpoint.git_commit
                self._emit(campaign_id, "checkpoint_saved", checkpoint.model_dump(mode="json"))
            if output.get("exit_code") == 0:
                metrics = self.execution.parse_metrics(output.get("stdout") or "", branch.id, now)
                snapshot.metrics.extend(metrics)
                branch.metric_ids.extend(item.id for item in metrics)
                branch.status = "succeeded"
                branch.analysis = "分支执行完成。" + (f"记录 {len(metrics)} 项指标。" if metrics else "未解析到结构化指标。")
                for metric in metrics:
                    self._emit(campaign_id, "metric_observed", metric.model_dump(mode="json"))
                self._emit(campaign_id, "branch_completed", branch.model_dump(mode="json"))
                self._select_best(snapshot, self._stage(snapshot, branch.stage_id))
            else:
                branch.status = "failed"
                branch.error_summary = (output.get("stderr") or "实验命令失败")[-2000:]
                self._emit(campaign_id, "branch_failed", branch.model_dump(mode="json"))
                if branch.debug_depth < snapshot.campaign.budget.max_debug_depth and len(snapshot.branches) < snapshot.campaign.budget.max_branches:
                    self._propose_branch(snapshot, self._stage(snapshot, branch.stage_id), parent=branch, index=len(branch.child_ids), origin="debug")
            branch.updated_at = now
            if branch.session_id:
                session = next((item for item in snapshot.sessions if item.id == branch.session_id), None)
                if session:
                    session.status = "completed" if branch.status == "succeeded" else "failed"
                    session.completed_at = now
            snapshot.campaign.status = "running"
            self._save_campaign(snapshot.campaign)
            self._checkpoint(snapshot)

    @staticmethod
    def _git_checkpoint(workspace: Path, branch: ExperimentBranch) -> str:
        if not shutil.which("git"):
            return ""
        flags = hidden_process_flags()
        try:
            def run(*args: str):
                return subprocess.run(
                    ["git", *args], cwd=workspace, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=10, check=False,
                    creationflags=flags,
                )
            if not (workspace / ".git").exists():
                run("init", "--quiet")
                run("config", "user.name", "EAI Campaign")
                run("config", "user.email", "campaign@eai.local")
            run("add", "runfile.py", "journal.json", "execution.json")
            run("commit", "--quiet", "-m", f"Campaign branch {branch.id}")
            result = run("rev-parse", "HEAD")
            return result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _select_best(self, snapshot: CampaignSnapshot, stage: CampaignStage) -> ExperimentBranch | None:
        candidates = [self._branch(snapshot, item) for item in stage.branch_ids]
        candidates = [item for item in candidates if item.status in {"succeeded", "promoted"}]
        if not candidates:
            return None
        metric_map = {metric.id: metric for metric in snapshot.metrics}
        protocols: dict[str, int] = {}
        for branch in candidates:
            for metric_id in branch.metric_ids:
                if metric_id in metric_map:
                    key = metric_map[metric_id].protocol_key
                    protocols[key] = protocols.get(key, 0) + 1
        primary_protocol = max(protocols, key=protocols.get) if protocols else ""
        def score(branch: ExperimentBranch) -> float:
            comparable = [metric_map[item] for item in branch.metric_ids if item in metric_map and (not primary_protocol or metric_map[item].protocol_key == primary_protocol)]
            if not comparable:
                return 0.0
            values = [(-item.value if item.direction == "minimize" else item.value) for item in comparable]
            stability_penalty = (max(values) - min(values)) * 0.1 if len(values) > 1 else 0
            return sum(values) / len(values) - stability_penalty - branch.debug_depth * 0.001
        selected = max(candidates, key=lambda item: (score(item), -item.debug_depth, item.created_at))
        for item in candidates:
            item.is_best = item.id == selected.id
            item.score = score(item)
            item.score_reason = f"按兼容评价协议 {primary_protocol or '无结构化指标'} 比较，并计入稳定性与调试深度"
        if stage.best_branch_id != selected.id:
            stage.best_branch_id = selected.id
            self._emit(snapshot.campaign.id, "best_branch_changed", {"stage_id": stage.id, "branch_id": selected.id, "score": score(selected)})
        return selected

    def _best_branch(self, snapshot: CampaignSnapshot, stage: CampaignStage) -> ExperimentBranch | None:
        if stage.best_branch_id:
            return self._branch(snapshot, stage.best_branch_id)
        return self._select_best(snapshot, stage)

    def compare_branches(self, campaign_id: str, request: BranchCompareRequest) -> dict[str, Any]:
        with self._lock:
            snapshot = self._required(campaign_id)
            branches = [self._branch(snapshot, branch_id) for branch_id in request.branch_ids]
            metric_map = {item.id: item for item in snapshot.metrics}
            protocols = {
                metric_map[metric_id].protocol_key
                for branch in branches for metric_id in branch.metric_ids if metric_id in metric_map
            }
            if len(protocols) > 1:
                raise ValueError("所选分支使用不同评价协议，不能直接比较")
            rows = []
            for branch in branches:
                rows.append({
                    "branch_id": branch.id, "title": branch.title, "status": branch.status,
                    "score": branch.score, "score_reason": branch.score_reason,
                    "metrics": [metric_map[item].model_dump(mode="json") for item in branch.metric_ids if item in metric_map],
                    "analysis": branch.analysis, "git_commit": branch.git_commit,
                })
            workspace = self.runtime_store.workspaces_dir / campaign_id / "comparisons"
            workspace.mkdir(parents=True, exist_ok=True)
            path = workspace / f"comparison-{uuid.uuid4().hex[:10]}.json"
            payload = {"campaign_id": campaign_id, "protocol": next(iter(protocols), "none"), "branches": rows}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact = CampaignArtifactRef(
                id=new_id("artifact"), kind="branch_comparison", title=f"{len(branches)} 个分支的指标比较",
                summary=f"评价协议：{payload['protocol']}", path=str(path),
                content_hash=hashlib.sha256(path.read_bytes()).hexdigest(), created_at=utc_now(),
            )
            snapshot.artifacts.append(artifact)
            self._emit(campaign_id, "artifact_ready", artifact.model_dump(mode="json"))
            self._checkpoint(snapshot)
            return {"comparison": payload, "artifact": artifact.model_dump(mode="json")}

    def _publication_sources(self, campaign: ResearchCampaign) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for source_id in campaign.source_ids:
            work = self.research_store.get_work(source_id)
            if not work:
                continue
            claims, evidence = self.research_store.claims_for_work(work.id, verified_only=True)
            evidence_ids = [item.id for item in evidence]
            sources.append({
                "id": work.id, "title": work.title, "year": work.year,
                "evidence_level": "full_text" if evidence_ids else "metadata",
                "evidence_ids": evidence_ids,
                "claim_ids": [item.id for item in claims],
            })
        return sources

    def generate_manuscript(self, campaign_id: str, request: ManuscriptGenerateRequest) -> CampaignSnapshot:
        with self._lock:
            snapshot = self._required(campaign_id)
            if not any(branch.status in {"succeeded", "promoted"} for branch in snapshot.branches):
                raise ValueError("至少需要一个成功实验分支才能生成论文")
            now = utc_now()
            workspace = self.runtime_store.workspaces_dir / campaign_id / "manuscript"
            manuscript = self.publication.generate(
                campaign=snapshot.campaign, branches=snapshot.branches, metrics=snapshot.metrics,
                sources=self._publication_sources(snapshot.campaign), workspace=workspace, now=now,
            )
            runtime = self.runtime.status()
            profile = snapshot.campaign.budget.execution_profile
            image = runtime.get(profile, {})
            if image.get("installed"):
                spec = self.execution.prepare(
                    ExperimentBranch(
                        id="writeup", campaign_id=campaign_id, stage_id="writeup", title="论文编译",
                        command="pdflatex -interaction=nonstopmode manuscript.tex",
                        created_at=now, updated_at=now,
                    ), profile=profile, timeout=300,
                    cpus=snapshot.campaign.budget.cpu_count, memory_mb=snapshot.campaign.budget.memory_mb,
                )
                spec.image = image["image"]
                spec.command = "pdflatex -interaction=nonstopmode manuscript.tex"
                compiled = run_docker_command(spec, workspace)
                pdf_path = workspace / "manuscript.pdf"
                if compiled.get("exit_code") == 0 and pdf_path.exists():
                    manuscript.pdf_path = str(pdf_path)
                else:
                    manuscript.warnings.append("LaTeX 编译失败；已保留可编辑源文件")
            else:
                manuscript.warnings.append("Campaign Runtime 未安装；尚未编译 PDF")
            for item in snapshot.manuscripts:
                if item.status == "current":
                    item.status = "draft"
            manuscript.status = "current"
            snapshot.manuscripts.append(manuscript)
            snapshot.campaign.current_manuscript_id = manuscript.id
            writeup = next((item for item in snapshot.campaign.stages if item.kind == "writeup"), None)
            if writeup:
                writeup.status = "completed"
                writeup.completion_reason = "已生成带引用绑定与披露的论文候选稿"
            review_stage = next((item for item in snapshot.campaign.stages if item.kind == "review"), None)
            if review_stage:
                review_stage.status = "running"
                snapshot.campaign.current_stage_id = review_stage.id
            self.research_store.save_record("campaign_manuscript", manuscript.id, manuscript.model_dump(mode="json"))
            self._save_campaign(snapshot.campaign)
            self._emit(campaign_id, "manuscript_ready", {"manuscript_id": manuscript.id, "warnings": manuscript.warnings})
            return self._checkpoint(snapshot)

    def start_review(self, campaign_id: str, request: ReviewStartRequest) -> CampaignSnapshot:
        with self._lock:
            snapshot = self._required(campaign_id)
            manuscript_id = request.manuscript_id or snapshot.campaign.current_manuscript_id
            manuscript = next((item for item in snapshot.manuscripts if item.id == manuscript_id), None)
            if not manuscript:
                raise KeyError("Campaign manuscript not found")
            now = utc_now()
            reviews = self.publication.reviews(manuscript, campaign_id, now)
            snapshot.reviews = [item for item in snapshot.reviews if item.manuscript_id != manuscript.id] + reviews
            for review in reviews:
                self.research_store.save_record("campaign_review", review.id, review.model_dump(mode="json"))
                self._emit(campaign_id, "review_completed", review.model_dump(mode="json"))
            rounds = [item for item in snapshot.revisions if item.campaign_id == campaign_id]
            if len(rounds) < 2:
                candidate, revision = self.publication.revise(manuscript, reviews, len(rounds) + 1, now)
                workspace = self.runtime_store.workspaces_dir / campaign_id / "manuscript" / f"v{candidate.version}"
                workspace.mkdir(parents=True, exist_ok=True)
                candidate.latex_path = str(workspace / "manuscript.tex")
                candidate.bibtex_path = str(workspace / "references.bib")
                Path(candidate.latex_path).write_text(self.publication.render_latex(candidate), encoding="utf-8")
                source_payload = self._publication_sources(snapshot.campaign)
                Path(candidate.bibtex_path).write_text(self.publication.render_bibtex(candidate, source_payload), encoding="utf-8")
                snapshot.manuscripts.append(candidate)
                snapshot.revisions.append(revision)
                self.research_store.save_record("campaign_manuscript", candidate.id, candidate.model_dump(mode="json"))
                self.research_store.save_record("campaign_revision", revision.id, revision.model_dump(mode="json"))
                self._emit(campaign_id, "revision_ready", revision.model_dump(mode="json"))
            review_stage = next((item for item in snapshot.campaign.stages if item.kind == "review"), None)
            if review_stage:
                review_stage.status = "completed"
                review_stage.completion_reason = "三角色审稿与 meta-review 已完成"
            release_stage = next((item for item in snapshot.campaign.stages if item.kind == "release"), None)
            if release_stage:
                release_stage.status = "running"
                snapshot.campaign.current_stage_id = release_stage.id
            return self._checkpoint(snapshot)

    def apply_revision(self, campaign_id: str, request: RevisionApplyRequest) -> CampaignSnapshot:
        with self._lock:
            snapshot = self._required(campaign_id)
            revision = next((item for item in snapshot.revisions if item.id == request.revision_id), None)
            if not revision:
                raise KeyError("Campaign revision not found")
            if revision.status != "candidate":
                raise ValueError("该修订候选已经处理")
            revision.status = "applied" if request.apply else "rejected"
            if request.apply:
                for item in snapshot.manuscripts:
                    item.status = "current" if item.id == revision.candidate_manuscript_id else ("draft" if item.status == "current" else item.status)
                snapshot.campaign.current_manuscript_id = revision.candidate_manuscript_id
                self._save_campaign(snapshot.campaign)
            self.research_store.save_record("campaign_revision", revision.id, revision.model_dump(mode="json"))
            return self._checkpoint(snapshot)

    def export_release(self, campaign_id: str, request: ReleaseExportRequest) -> dict[str, Any]:
        with self._lock:
            snapshot = self._required(campaign_id)
            manuscript_id = request.manuscript_id or snapshot.campaign.current_manuscript_id
            manuscript = next((item for item in snapshot.manuscripts if item.id == manuscript_id), None)
            if not manuscript:
                raise KeyError("Campaign manuscript not found")
            workspace = self.runtime_store.workspaces_dir / campaign_id / "release-package"
            archive = self.publication.export(
                campaign=snapshot.campaign, manuscript=manuscript, artifacts=snapshot.artifacts,
                workspace=workspace, allow_warnings=request.allow_draft_warnings,
            )
            artifact = CampaignArtifactRef(
                id=new_id("artifact"), kind="draft_release" if manuscript.warnings else "release_package",
                title="研究发布包" if not manuscript.warnings else "带警告的研究草稿包",
                summary="包含论文源文件、引用、披露和研究 manifest。", path=str(archive),
                media_type="application/zip", content_hash=hashlib.sha256(archive.read_bytes()).hexdigest(), created_at=utc_now(),
            )
            snapshot.artifacts.append(artifact)
            release_stage = next((item for item in snapshot.campaign.stages if item.kind == "release"), None)
            if release_stage and not manuscript.warnings:
                release_stage.status = "completed"
                release_stage.completion_reason = "引用、披露和研究包校验通过"
                manuscript.status = "released"
                snapshot.campaign.status = "completed"
            self._emit(campaign_id, "release_ready", artifact.model_dump(mode="json"))
            self._checkpoint(snapshot)
            return {"artifact": artifact.model_dump(mode="json"), "path": str(archive), "draft": bool(manuscript.warnings)}

    def safe_artifact_path(self, campaign_id: str, raw_path: str) -> Path:
        root = (self.runtime_store.workspaces_dir / campaign_id).resolve()
        path = Path(raw_path).resolve()
        if path != root and root not in path.parents:
            raise ValueError("Artifact path is outside the Campaign workspace")
        if not path.is_file():
            raise FileNotFoundError("Artifact file not found")
        return path

    def event_stream(self, campaign_id: str, after_seq: int) -> Iterator[str]:
        cursor = max(0, after_seq)
        idle = 0
        while idle < 300:
            events = self.runtime_store.list_campaign_events(campaign_id, cursor)
            if events:
                idle = 0
                for event in events:
                    cursor = event.seq
                    yield f"id: {event.seq}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n"
            else:
                idle += 1
                snapshot = self.get(campaign_id)
                if not snapshot or snapshot.campaign.status in {"completed", "failed", "cancelled"}:
                    break
                if snapshot.campaign.status == "waiting_approval":
                    break
                if idle % 15 == 0:
                    yield ": keep-alive\n\n"
                time.sleep(0.2)

    def _required(self, campaign_id: str) -> CampaignSnapshot:
        snapshot = self.get(campaign_id)
        if not snapshot:
            raise KeyError("Campaign not found")
        return snapshot

    @staticmethod
    def _stage(snapshot: CampaignSnapshot, stage_id: str | None) -> CampaignStage:
        stage = next((item for item in snapshot.campaign.stages if item.id == stage_id), None)
        if not stage:
            raise KeyError("Campaign stage not found")
        return stage

    @staticmethod
    def _branch(snapshot: CampaignSnapshot, branch_id: str | None) -> ExperimentBranch:
        branch = next((item for item in snapshot.branches if item.id == branch_id), None)
        if not branch:
            raise KeyError("Campaign branch not found")
        return branch
