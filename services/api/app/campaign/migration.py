from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .models import (
    CampaignArtifactRef,
    CampaignBudget,
    CampaignSnapshot,
    CampaignStage,
    CampaignWorkspaceSeed,
    ExperimentBranch,
    ResearchCampaign,
    ResearchIdea,
)


def legacy_lab_snapshot(payload: dict[str, Any], source_path: Path, now: str) -> CampaignSnapshot:
    raw = source_path.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    legacy_id = str(payload.get("id") or source_path.stem)
    campaign_id = f"campaign_legacy_{legacy_id}"
    idea = ResearchIdea(
        id=f"idea_legacy_{legacy_id}", title=str(payload.get("title") or "历史实验"),
        short_hypothesis=str(payload.get("hypothesis") or payload.get("goal") or "历史实验记录"),
        novelty_summary="由旧研究室记录迁移；不代表 AI Scientist 自动生成。", status="selected",
    )
    stage = CampaignStage(
        id=f"stage_{campaign_id}_legacy", kind="legacy_import", title="旧研究室归档",
        sequence=1, goals=["保留旧实验阶段、产物、发现和对话"], status="completed",
        max_branches=max(1, len(payload.get("stages") or [])), completion_reason="由 LabRun 自动归档迁移",
    )
    branches: list[ExperimentBranch] = []
    status_map = {"completed": "succeeded", "failed": "failed", "blocked": "failed"}
    for index, item in enumerate(payload.get("stages") or []):
        branch_id = f"branch_legacy_{legacy_id}_{index + 1}"
        branch = ExperimentBranch(
            id=branch_id, campaign_id=campaign_id, stage_id=stage.id, origin="manual",
            status=status_map.get(str(item.get("status") or ""), "succeeded"),
            title=str(item.get("title") or f"历史阶段 {index + 1}"),
            plan_summary=str(item.get("summary") or ""), command=str(item.get("command") or ""),
            analysis=str(item.get("notes") or ""), journal_node_id=branch_id,
            created_at=str(item.get("updated_at") or payload.get("created_at") or now),
            updated_at=str(item.get("updated_at") or payload.get("updated_at") or now),
        )
        branches.append(branch)
        stage.branch_ids.append(branch.id)
    artifacts: list[CampaignArtifactRef] = []
    for index, item in enumerate(payload.get("artifacts") or []):
        artifacts.append(CampaignArtifactRef(
            id=f"artifact_legacy_{legacy_id}_{index + 1}", kind=str(item.get("type") or "legacy_artifact"),
            title=str(item.get("title") or "历史产物"), summary=str(item.get("summary") or item.get("content_preview") or ""),
            path=str(item.get("uri") or ""), created_at=str(item.get("created_at") or now),
        ))
    findings = payload.get("findings") or []
    if findings:
        artifacts.append(CampaignArtifactRef(
            id=f"artifact_legacy_{legacy_id}_findings", kind="legacy_findings", title="历史研究发现",
            summary="\n\n".join(f"{item.get('title', '发现')}：{item.get('body', '')}" for item in findings), created_at=now,
        ))
    messages = payload.get("messages") or []
    if messages:
        artifacts.append(CampaignArtifactRef(
            id=f"artifact_legacy_{legacy_id}_transcript", kind="legacy_transcript", title="旧研究室只读对话",
            summary="\n\n".join(f"[{item.get('role', 'user')}] {item.get('content', '')}" for item in messages), created_at=now,
        ))
    campaign = ResearchCampaign(
        id=campaign_id, thread_id=str(payload.get("thread_id") or "legacy-unfiled"),
        project_id=payload.get("project_id"), source_kind="legacy_lab", source_ref=legacy_id,
        source_hash=source_hash, source_node_ids=list(payload.get("linked_canvas_nodes") or []),
        workspace_seed=CampaignWorkspaceSeed(
            kind="legacy_lab", title="旧研究室快照", source_path=str(source_path),
            snapshot_hash=source_hash, imported_at=now,
        ),
        title=str(payload.get("title") or "历史实验"), objective=str(payload.get("goal") or payload.get("title") or "历史实验"),
        hypothesis=idea.short_hypothesis, status="archived", current_stage_id=stage.id,
        selected_idea=idea, stages=[stage], budget=CampaignBudget(max_branches=max(1, len(branches) or 1)),
        created_at=str(payload.get("created_at") or now), updated_at=str(payload.get("updated_at") or now),
    )
    return CampaignSnapshot(campaign=campaign, branches=branches, artifacts=artifacts)


def migrate_legacy_labs(*, personal_dir: Path, already_imported, save_snapshot, now: str) -> dict[str, int]:
    source_dir = personal_dir / "lab_runs"
    result = {"found": 0, "imported": 0, "unchanged": 0, "failed": 0}
    if not source_dir.exists():
        return result
    backup_dir = personal_dir / "migration-backups" / "legacy-lab"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.glob("*.json")):
        result["found"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = legacy_lab_snapshot(payload, path, now)
            existing = already_imported(snapshot.campaign.id)
            if existing and existing.get("source_hash") == snapshot.campaign.source_hash:
                result["unchanged"] += 1
                continue
            backup = backup_dir / path.name
            if not backup.exists():
                shutil.copy2(path, backup)
            save_snapshot(snapshot)
            result["imported"] += 1
        except (OSError, ValueError, json.JSONDecodeError):
            result["failed"] += 1
    return result
