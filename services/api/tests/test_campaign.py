from __future__ import annotations

import tempfile
import time
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent_v2.models import ApprovalResolveRequest
from app.agent_v2.store import RuntimeStore
from app.campaign.adapters import journal_node_to_branch
from app.campaign.models import (
    BranchCompareRequest,
    BranchPromoteRequest,
    CampaignBudget,
    CampaignWorkspaceSeed,
    ManuscriptGenerateRequest,
    ReleaseExportRequest,
    ReviewStartRequest,
    RevisionApplyRequest,
)
from app.campaign.service import CampaignService
from app.campaign.migration import migrate_legacy_labs
from app.schemas.models import CanvasNode, CanvasState


class FakeResearchStore:
    def __init__(self):
        self.records = {}

    def save_record(self, kind, record_id, payload):
        self.records[(kind, record_id)] = payload
        return payload

    def get_record(self, kind, record_id):
        return self.records.get((kind, record_id))

    def list_records(self, kind):
        return [payload for (record_kind, _), payload in self.records.items() if record_kind == kind]

    def search(self, query, **_kwargs):
        work = SimpleNamespace(
            id="work_1", title="Verified research work", year=2025,
            atlas_placements=[{"atlas_id": "I", "route_id": "route-a"}],
        )
        evidence = SimpleNamespace(work_id="work_1", evidence_level="full_text")
        return SimpleNamespace(works=[work], evidence=[evidence], missing=[])

    def get_work(self, work_id):
        if work_id != "work_1":
            return None
        return SimpleNamespace(id="work_1", title="Verified research work", year=2025)

    def claims_for_work(self, _work_id, verified_only=True):
        return [SimpleNamespace(id="claim_1")], [SimpleNamespace(id="span_1")]


class CampaignServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = RuntimeStore(Path(self.temp.name) / "runtime")
        self.research = FakeResearchStore()
        self.thread = SimpleNamespace(
            id="thread-1", project_id="project-1", active_atlas_id="I",
            title="Research thread", goal="Test a grounded hypothesis",
            canvas=CanvasState(nodes=[CanvasNode(id="hyp-1", type="hypothesis", title="Grounded hypothesis")]),
        )
        self.applied_batches = []
        self.service = CampaignService(
            research_store=self.research,
            runtime_store=self.runtime,
            load_thread=lambda _thread_id: self.thread,
            prepare_operation_batch=lambda batch: batch,
            apply_operation_batch=self._apply_batch,
            planner=None,
        )
        self.service.runtime.status = lambda: {
            "docker_available": True, "cuda_available": False, "profiles": ["cpu"],
            "cpu": {"installed": True, "image": "eai-test-runtime", "digest": "sha256:test"},
            "install": {"status": "ready"},
        }

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def _apply_batch(self, batch, _resolution):
        self.applied_batches.append(batch)
        return {"status": "applied", "thread": {"id": "thread-1"}}

    def create_started_campaign(self):
        preview = self.service.preview_ideas("thread-1", node_id="hyp-1", objective="", count=3)
        snapshot = self.service.create("thread-1", preview.ideas[0], ["hyp-1"], CampaignBudget(max_branches=8))
        return self.service.start(snapshot.campaign.id)

    def test_journal_node_mapping_preserves_lineage(self):
        branch = journal_node_to_branch({
            "id": "node-2", "parent_id": "node-1", "plan": "debug plan", "code": "print(1)",
            "is_buggy": True, "exc_type": "ValueError", "debug_depth": 2,
        }, campaign_id="campaign-1", stage_id="stage-1", now="2026-07-17T00:00:00Z")
        self.assertEqual(branch.origin, "debug")
        self.assertEqual(branch.parent_id, "node-1")
        self.assertEqual(branch.status, "failed")
        self.assertEqual(branch.debug_depth, 2)

    def test_ideation_uses_real_source_levels_and_start_creates_three_drafts(self):
        preview = self.service.preview_ideas("thread-1", node_id="hyp-1", objective="", count=3)
        self.assertEqual(len(preview.ideas), 3)
        self.assertEqual(preview.sources[0]["evidence_level"], "full_text")
        snapshot = self.service.create("thread-1", preview.ideas[0], ["hyp-1"], CampaignBudget())
        self.assertEqual(snapshot.campaign.status, "ready")
        started = self.service.start(snapshot.campaign.id)
        self.assertEqual(len(started.branches), 3)
        self.assertTrue(all(branch.origin == "draft" and branch.status == "proposed" for branch in started.branches))

    def test_execution_requires_approval_and_records_metric(self):
        snapshot = self.create_started_campaign()
        branch = snapshot.branches[0]
        prepared = self.service.prepare_execution(snapshot.campaign.id, branch.id)
        self.assertEqual(prepared["branch"]["status"], "waiting_approval")
        self.assertEqual(prepared["approval"]["kind"], "execution_session")
        self.assertEqual(prepared["session"]["network_domains"], [])
        self.assertNotIn("api_key", json.dumps(prepared, ensure_ascii=False).lower())
        self.assertFalse((self.runtime.workspaces_dir / snapshot.campaign.id / branch.id / "execution.json").exists())
        with patch("app.campaign.service.run_docker_command", return_value={
            "exit_code": 0,
            "stdout": 'EAI_METRIC:{"name":"accuracy","value":0.82,"direction":"maximize"}\n',
            "stderr": "",
            "command": "python runfile.py",
        }):
            self.service.resolve_approval(prepared["approval"]["id"], ApprovalResolveRequest(decision="approve"))
            worker = self.service._workers[branch.id]
            worker.join(timeout=5)
        completed = self.service.get(snapshot.campaign.id)
        completed_branch = next(item for item in completed.branches if item.id == branch.id)
        self.assertEqual(completed_branch.status, "succeeded")
        self.assertEqual(completed.metrics[0].value, 0.82)
        self.assertEqual(completed.campaign.stages[1].best_branch_id, branch.id)

    def test_docker_unavailable_returns_preview_without_approval(self):
        snapshot = self.create_started_campaign()
        branch = snapshot.branches[0]
        with patch("app.campaign.service.docker_available", return_value=False):
            prepared = self.service.prepare_execution(snapshot.campaign.id, branch.id)
        self.assertIsNone(prepared["approval"])
        self.assertEqual(prepared["command_preview"]["network"], False)
        self.assertEqual(self.service.get(snapshot.campaign.id).branches[0].status, "proposed")

    def test_running_campaign_is_interrupted_on_restart(self):
        snapshot = self.create_started_campaign()
        self.assertEqual(snapshot.campaign.status, "running")
        self.assertEqual(self.service.mark_incomplete_interrupted(), 1)
        interrupted = self.service.get(snapshot.campaign.id)
        self.assertEqual(interrupted.campaign.status, "interrupted")
        events = self.runtime.list_campaign_events(snapshot.campaign.id)
        self.assertTrue(any(event.kind == "error" and event.payload.get("recoverable") for event in events))

    def test_failed_execution_proposes_debug_child(self):
        snapshot = self.create_started_campaign()
        branch = snapshot.branches[0]
        prepared = self.service.prepare_execution(snapshot.campaign.id, branch.id)
        with patch("app.campaign.service.run_docker_command", return_value={
            "exit_code": 1, "stdout": "", "stderr": "boom", "command": "python runfile.py",
        }):
            self.service.resolve_approval(prepared["approval"]["id"], ApprovalResolveRequest(decision="approve"))
            self.service._workers[branch.id].join(timeout=5)
        failed = self.service.get(snapshot.campaign.id)
        debug = next(item for item in failed.branches if item.parent_id == branch.id)
        self.assertEqual(debug.origin, "debug")
        self.assertEqual(debug.debug_depth, 1)

    def test_promote_is_an_operation_batch_and_advances_stage(self):
        snapshot = self.create_started_campaign()
        branch = snapshot.branches[0]
        prepared = self.service.prepare_execution(snapshot.campaign.id, branch.id)
        with patch("app.campaign.service.run_docker_command", return_value={
            "exit_code": 0, "stdout": 'EAI_METRIC:{"name":"score","value":1.0}\n', "stderr": "", "command": "python runfile.py",
        }):
            self.service.resolve_approval(prepared["approval"]["id"], ApprovalResolveRequest(decision="approve"))
            self.service._workers[branch.id].join(timeout=5)
        promotion = self.service.promote(snapshot.campaign.id, branch.id, BranchPromoteRequest())
        self.assertEqual(promotion["operation_batch"]["status"], "pending")
        self.assertFalse(self.applied_batches)
        self.service.resolve_approval(promotion["approval"]["id"], ApprovalResolveRequest(decision="approve"))
        self.assertEqual(len(self.applied_batches), 1)
        promoted = self.service.get(snapshot.campaign.id)
        self.assertEqual(next(item for item in promoted.branches if item.id == branch.id).status, "promoted")
        advanced = self.service.advance(snapshot.campaign.id, branch.id)
        self.assertEqual(advanced.campaign.stages[1].status, "completed")
        self.assertEqual(advanced.campaign.stages[2].status, "running")
        self.assertTrue(any(item.stage_id == advanced.campaign.stages[2].id for item in advanced.branches))

    def test_compare_rejects_incompatible_metric_protocols(self):
        snapshot = self.create_started_campaign()
        first, second = snapshot.branches[:2]
        first.status = second.status = "succeeded"
        now = "2026-07-17T00:00:00Z"
        from app.campaign.models import MetricObservation
        snapshot.metrics = [
            MetricObservation(id="m1", branch_id=first.id, name="accuracy", value=.8, dataset="a", created_at=now),
            MetricObservation(id="m2", branch_id=second.id, name="loss", value=.2, dataset="b", direction="minimize", created_at=now),
        ]
        first.metric_ids = ["m1"]
        second.metric_ids = ["m2"]
        self.service._checkpoint(snapshot)
        with self.assertRaisesRegex(ValueError, "不同评价协议"):
            self.service.compare_branches(snapshot.campaign.id, BranchCompareRequest(branch_ids=[first.id, second.id]))

    def test_local_workspace_seed_is_snapshotted_without_host_path(self):
        source = Path(self.temp.name) / "source-project"
        source.mkdir()
        (source / "train.py").write_text("print('ok')", encoding="utf-8")
        (source / ".git").mkdir()
        (source / ".git" / "config").write_text("secret", encoding="utf-8")
        preview = self.service.preview_ideas("thread-1", node_id="hyp-1", objective="", count=1)
        snapshot = self.service.create(
            "thread-1", preview.ideas[0], ["hyp-1"], CampaignBudget(max_storage_mb=512),
            CampaignWorkspaceSeed(kind="local_snapshot", title="seed", source_path=str(source)),
        )
        seed = snapshot.campaign.workspace_seed
        self.assertEqual(seed.source_path, "seed")
        self.assertNotIn(str(source), json.dumps(snapshot.model_dump(mode="json")))
        target = self.runtime.workspaces_dir / snapshot.campaign.id / "seed"
        self.assertTrue((target / "train.py").exists())
        self.assertFalse((target / ".git").exists())

    def test_manuscript_review_revision_and_release_are_evidence_bound(self):
        snapshot = self.create_started_campaign()
        branch = snapshot.branches[0]
        branch.status = "succeeded"
        branch.analysis = "The approved experiment completed."
        branch.is_best = True
        self.service._checkpoint(snapshot)
        self.service.runtime.status = lambda: {"docker_available": True, "profiles": ["cpu"], "cpu": {"installed": False}}
        generated = self.service.generate_manuscript(snapshot.campaign.id, ManuscriptGenerateRequest())
        manuscript = generated.manuscripts[-1]
        self.assertIn("machine-generated", manuscript.disclosure)
        self.assertEqual(manuscript.citation_bindings[0].status, "verified")
        reviewed = self.service.start_review(snapshot.campaign.id, ReviewStartRequest(manuscript_id=manuscript.id))
        self.assertEqual(len([item for item in reviewed.reviews if item.manuscript_id == manuscript.id]), 4)
        revision = reviewed.revisions[-1]
        applied = self.service.apply_revision(snapshot.campaign.id, RevisionApplyRequest(revision_id=revision.id, apply=True))
        self.assertEqual(applied.campaign.current_manuscript_id, revision.candidate_manuscript_id)
        for item in applied.manuscripts:
            item.warnings = []
        self.service._checkpoint(applied)
        released = self.service.export_release(snapshot.campaign.id, ReleaseExportRequest(allow_draft_warnings=False))
        self.assertTrue(Path(released["path"]).exists())


class LegacyLabMigrationTest(unittest.TestCase):
    def test_legacy_lab_is_archived_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            personal = Path(directory)
            source = personal / "lab_runs"
            source.mkdir()
            fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "legacy" / "lab-run.json"
            payload = json.loads(fixture.read_text(encoding="utf-8"))
            source_path = source / "lab-legacy-fixture.json"
            source_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            records = {}
            snapshots = []
            def save(snapshot):
                snapshots.append(snapshot)
                records[snapshot.campaign.id] = snapshot.campaign.model_dump(mode="json")
            first = migrate_legacy_labs(
                personal_dir=personal, already_imported=records.get, save_snapshot=save,
                now="2026-07-17T00:00:00Z",
            )
            second = migrate_legacy_labs(
                personal_dir=personal, already_imported=records.get, save_snapshot=save,
                now="2026-07-17T00:00:00Z",
            )
            self.assertEqual(first["imported"], 1)
            self.assertEqual(second["unchanged"], 1)
            self.assertEqual(snapshots[0].campaign.status, "archived")
            self.assertEqual(snapshots[0].branches[0].origin, "manual")
            self.assertTrue(any(item.kind == "legacy_transcript" for item in snapshots[0].artifacts))
            self.assertEqual(snapshots[0].campaign.source_ref, "lab-legacy-fixture")
            self.assertEqual(snapshots[0].campaign.source_node_ids, ["node-legacy-hypothesis"])
            self.assertTrue((personal / "migration-backups" / "legacy-lab" / source_path.name).exists())


if __name__ == "__main__":
    unittest.main()
