from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ..agent_v2.models import SandboxCommand
from .models import CampaignArtifactRef, ExperimentBranch, MetricObservation


UPSTREAM_COMMIT = "96bd51617cfdbb494a9fc283af00fe090edfae48"


def journal_node_to_branch(
    node: dict[str, Any], *, campaign_id: str, stage_id: str, now: str
) -> ExperimentBranch:
    parent_id = node.get("parent_id")
    is_buggy = bool(node.get("is_buggy") or node.get("exc_type"))
    origin = "draft" if not parent_id else "debug" if is_buggy else "improve"
    return ExperimentBranch(
        id=str(node.get("id")), campaign_id=campaign_id, stage_id=stage_id,
        parent_id=str(parent_id) if parent_id else None, origin=origin,
        status="failed" if is_buggy else "succeeded",
        title=str(node.get("plan") or f"{origin} branch")[:180],
        plan_summary=str(node.get("plan") or ""), overall_plan=str(node.get("overall_plan") or ""),
        code=str(node.get("code") or ""), analysis=str(node.get("analysis") or ""),
        error_summary=str(node.get("exc_info") or node.get("exc_type") or ""),
        debug_depth=int(node.get("debug_depth") or 0), created_at=now, updated_at=now,
    )


class EAIModelBackend:
    """Provider adapter with a deterministic fallback for offline Campaign planning."""

    def __init__(self, planner: Callable[[str], str] | None = None):
        self.planner = planner

    def propose_branch(self, *, hypothesis: str, stage_title: str, index: int, parent: ExperimentBranch | None) -> dict[str, str]:
        prompt = (
            f"Research hypothesis: {hypothesis}\nStage: {stage_title}\n"
            f"Parent analysis: {parent.analysis if parent else 'none'}\n"
            "Return a concise experiment plan and executable Python code."
        )
        if self.planner:
            try:
                raw = self.planner(prompt)
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("plan") and parsed.get("code"):
                    return {"title": str(parsed.get("title") or parsed["plan"]), "plan": str(parsed["plan"]), "code": str(parsed["code"])}
            except Exception:
                pass
        parent_note = f"基于父分支 {parent.title} 继续改进。" if parent else "建立可复现的最小实验基线。"
        plan = f"{stage_title}方案 {index + 1}：{parent_note}围绕“{hypothesis}”登记指标、随机种子和失败原因。"
        code = (
            "import json\n"
            f"branch_index = {index}\n"
            "metric = 0.5 + branch_index * 0.05\n"
            "print('EAI_METRIC:' + json.dumps({'name':'campaign_score','value':metric,'direction':'maximize'}))\n"
        )
        return {"title": f"{stage_title}方案 {index + 1}", "plan": plan, "code": code}


class EAIEvidenceBackend:
    def __init__(self, search: Callable[..., Any]):
        self.search = search

    def investigate(self, query: str, atlas_id: str, limit: int = 12):
        return self.search(query, atlas_ids=[atlas_id] if atlas_id else [], limit=limit)


class EAIExecutionBackend:
    """Builds approved Docker commands. It never executes on the host."""

    def prepare(self, branch: ExperimentBranch, *, profile: str, timeout: int, cpus: float, memory_mb: int) -> SandboxCommand:
        image = "python:3.11-slim"
        return SandboxCommand(
            command=branch.command or "python runfile.py", image=image, network=False,
            timeout_seconds=timeout, cpus=cpus, memory_mb=memory_mb,
            container_name=f"eai-campaign-{branch.id}"[:63], gpus="all" if profile == "cuda" else None,
        )

    @staticmethod
    def parse_metrics(stdout: str, branch_id: str, now: str) -> list[MetricObservation]:
        metrics: list[MetricObservation] = []
        for index, match in enumerate(re.finditer(r"^EAI_METRIC:(\{.*\})$", stdout or "", re.MULTILINE)):
            try:
                item = json.loads(match.group(1))
                metrics.append(MetricObservation(
                    id=f"metric_{branch_id}_{index}", branch_id=branch_id,
                    name=str(item.get("name") or "metric"), value=float(item["value"]),
                    direction=item.get("direction") if item.get("direction") in {"maximize", "minimize"} else "maximize",
                    dataset=str(item.get("dataset") or "default"), split=str(item.get("split") or "validation"),
                    unit=str(item.get("unit") or ""), created_at=now,
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return metrics


class EAIJournalSink:
    def __init__(self, emit: Callable[[str, dict[str, Any]], Any]):
        self.emit = emit

    def branch(self, branch: ExperimentBranch) -> None:
        self.emit("branch_proposed", branch.model_dump(mode="json"))

    def artifact(self, artifact: CampaignArtifactRef) -> None:
        self.emit("artifact_ready", artifact.model_dump(mode="json"))


def write_branch_workspace(workspace: Path, branch: ExperimentBranch) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "runfile.py"
    target.write_text(branch.code, encoding="utf-8")
    return target
