from __future__ import annotations

import json
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import application as main
from app.agent_v2.documents import import_document, import_open_source
from app.agent_v2.capabilities import capability, validate_capability_arguments
from app.agent_v2.context_broker import build_context_seed
from app.agent_v2.evidence import assess_evidence
from app.agent_v2.models import AgentTurnRequest, ApprovalResolveRequest, DocumentRecord, Operation, OperationBatch, SandboxCommand, ServiceDecision, SourceRecord
from app.agent_v2.provider import request_tool_decision, safe_provider_error
from app.agent_v2.routing import fallback_service_decision, route_turn
from app.agent_v2.sandbox import SandboxUnavailable, docker_arguments, run_docker_command
from app.agent_v2.sources import SourceService
from app.agent_v2.store import RuntimeStore


class AgentRuntimeV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original = {
            "PERSONAL_DIR": main.PERSONAL_DIR,
            "THREADS_DIR": main.THREADS_DIR,
            "BACKUPS_DIR": main.BACKUPS_DIR,
            "PROJECTS_DIR": main.PROJECTS_DIR,
            "OBJECTS_DIR": main.OBJECTS_DIR,
            "ATLAS_UPDATES_DIR": main.ATLAS_UPDATES_DIR,
            "LAB_RUNS_DIR": main.LAB_RUNS_DIR,
            "RUNTIME_V2_DIR": main.RUNTIME_V2_DIR,
            "SECRET_CANDIDATES": main.SECRET_CANDIDATES,
        }
        if main.AGENT_V2_RUNTIME is not None:
            main.AGENT_V2_RUNTIME.close()
            main.AGENT_V2_RUNTIME = None
        personal = root / "personal"
        main.PERSONAL_DIR = personal
        main.THREADS_DIR = personal / "threads"
        main.BACKUPS_DIR = personal / "backups"
        main.PROJECTS_DIR = personal / "projects"
        main.OBJECTS_DIR = personal / "objects"
        main.ATLAS_UPDATES_DIR = personal / "atlas_updates"
        main.LAB_RUNS_DIR = personal / "lab_runs"
        main.RUNTIME_V2_DIR = root / "runtime"
        main.SECRET_CANDIDATES = [root / "missing-secrets.json"]
        for name in ["OPENAI_API_KEY", "EAI_VNEXT_MOCK_OPENAI_RESPONSE", "EAI_V2_MOCK_ROUTE", "EAI_V2_MOCK_OPERATION_PLAN", "EAI_V2_MOCK_TOOL_DECISION"]:
            os.environ.pop(name, None)
        main.ensure_dirs()
        self.client = TestClient(main.app)

    def tearDown(self):
        if main.AGENT_V2_RUNTIME is not None:
            main.AGENT_V2_RUNTIME.close()
            main.AGENT_V2_RUNTIME = None
        for name, value in self.original.items():
            setattr(main, name, value)
        for name in ["OPENAI_API_KEY", "EAI_VNEXT_MOCK_OPENAI_RESPONSE", "EAI_V2_MOCK_ROUTE", "EAI_V2_MOCK_OPERATION_PLAN", "EAI_V2_MOCK_TOOL_DECISION"]:
            os.environ.pop(name, None)
        self.temp.cleanup()

    def create_thread(self, title: str = "Agent v2 test") -> dict:
        response = self.client.post("/api/vnext/threads", json={"title": title, "active_atlas_id": "I"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_task(self, task_id: str, statuses: set[str] | None = None, timeout: float = 8) -> dict:
        expected = statuses or {"done", "failed", "waiting_approval"}
        deadline = time.time() + timeout
        latest = None
        while time.time() < deadline:
            response = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}")
            self.assertEqual(response.status_code, 200, response.text)
            latest = response.json()["task"]
            if latest["status"] in expected:
                return latest
            time.sleep(0.04)
        self.fail(f"task did not reach {expected}: {latest}")

    def test_greeting_uses_conversation_without_sources_or_skills(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "你好", "intent_override": "auto"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        task = self.wait_task(started.json()["task"]["id"], {"done"})
        self.assertEqual(task["service"], "conversation")
        self.assertEqual(task["source_ids"], [])
        self.assertEqual(task["child_task_ids"], [])
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        assistant = saved["messages"][-1]
        self.assertIn("你好", assistant["content"])
        self.assertEqual(assistant["refs"]["citations"], [])
        self.assertNotIn("skills", assistant["refs"])
        events = main.get_agent_v2_runtime().store.list_events(task["id"])
        self.assertNotIn("specialist_started", [event.kind for event in events])
        self.assertNotIn("source_found", [event.kind for event in events])

    def test_sse_contract_preserves_event_names_sequence_and_reconnect(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "SSE contract response."
        thread = self.create_thread("SSE contract")
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "你好", "intent_override": "auto"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        task_id = started.json()["task"]["id"]
        self.wait_task(task_id, {"done"})

        streamed = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}/events")
        self.assertEqual(streamed.status_code, 200, streamed.text)
        self.assertTrue(streamed.headers["content-type"].startswith("text/event-stream"))
        events = []
        for block in streamed.text.strip().split("\n\n"):
            lines = block.splitlines()
            kind = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("event:"))
            payload = json.loads(next(line.split(":", 1)[1].strip() for line in lines if line.startswith("data:")))
            self.assertEqual(kind, payload["kind"])
            events.append(payload)
        sequences = [event["seq"] for event in events]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertIn("answer_ready", [event["kind"] for event in events])
        self.assertEqual(events[-1]["kind"], "done")

        cursor = sequences[len(sequences) // 2]
        replayed = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}/events?after_seq={cursor}")
        replayed_events = [
            json.loads(next(line.split(":", 1)[1].strip() for line in block.splitlines() if line.startswith("data:")))
            for block in replayed.text.strip().split("\n\n")
            if block.strip()
        ]
        self.assertEqual([event["seq"] for event in replayed_events], [seq for seq in sequences if seq > cursor])

    def test_structured_provider_payload_never_flashes_protocol_json(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = '```json\n{"answer":"Structured natural answer.","tool_calls":[{"name":"forbidden"}]}\n```'
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "hello", "intent_override": "chat"},
        ).json()
        task = self.wait_task(started["task"]["id"], {"done"})
        events = main.get_agent_v2_runtime().store.list_events(task["id"])
        streamed = "".join(event.payload.get("text", "") for event in events if event.kind == "answer_delta")
        self.assertEqual(streamed, "Structured natural answer.")
        self.assertNotIn("```", streamed)
        self.assertNotIn("tool_calls", streamed)
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual(saved["messages"][-1]["content"], "Structured natural answer.")

    def test_context_seed_excludes_full_workspace_until_requested(self):
        seed = build_context_seed(
            {
                "thread": {"id": "thread-1", "title": "Minimal", "goal": "Test", "active_atlas_id": "I"},
                "recent_messages": [{"role": "user", "content": str(index)} for index in range(12)],
                "campaign_summaries": [{"id": "campaign-1", "status": "draft"}],
                "canvas": {"nodes": [{"id": "secret-node"}]},
                "context_cards": [{"id": "long-term-card"}],
                "object_memories": [{"id": "memory-1"}],
                "lab_runs": [{"id": "legacy-lab"}],
            },
            AgentTurnRequest(message="你好", intent_override="chat"),
        )
        self.assertEqual(len(seed["recent_messages"]), 8)
        self.assertNotIn("canvas", seed)
        self.assertNotIn("context_cards", seed)
        self.assertNotIn("object_memories", seed)
        self.assertNotIn("lab_runs", seed)

    def test_tool_planner_changes_action_after_observation(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        prompts: list[dict] = []
        original = runtime.dependencies.tool_model

        def adaptive(prompt, _tools, _overrides):
            payload = json.loads(prompt)
            prompts.append(payload)
            if not payload["context"]["observations"]:
                return {
                    "action": "call_tools",
                    "calls": [{"capability": "knowledge.search", "arguments": {"query": "VLA", "limit": 5}, "rationale": "先看本地证据"}],
                    "reason": "需要证据",
                }
            return {"action": "answer", "calls": [], "reason": "观察结果已足够"}

        runtime.dependencies.tool_model = adaptive
        try:
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "检索 VLA 研究路线", "intent_override": "local"},
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        finally:
            runtime.dependencies.tool_model = original
        audit = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}/audit").json()
        self.assertEqual([item["capability"] for item in audit["tool_calls"]], ["knowledge.search"])
        self.assertGreaterEqual(len(prompts), 2)
        self.assertTrue(prompts[1]["context"]["observations"])

    def test_round_budget_stops_repetitive_tool_planner(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        original = runtime.dependencies.tool_model
        runtime.dependencies.tool_model = lambda *_args: {
            "action": "call_tools",
            "calls": [{"capability": "knowledge.search", "arguments": {"query": "budget probe", "limit": 2}}],
            "reason": "repeat",
        }
        try:
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "检索预算测试", "intent_override": "local"},
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        finally:
            runtime.dependencies.tool_model = original
        self.assertEqual(task["budget_usage"]["rounds"], 4)
        self.assertLessEqual(task["budget_usage"]["tool_calls"], 4)
        self.assertEqual(task["stop_reason"], "round_budget")

    def test_provider_parses_tool_calls_and_sanitizes_errors(self):
        real_client = httpx.Client
        config = {"base_url": "https://provider.invalid/v1", "api_key": "top-secret", "model": "test-model"}

        def execute(body, api_format):
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body, request=request))
            with patch("app.agent_v2.provider.httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
                return request_tool_decision(
                    {**config, "api_format": api_format},
                    "prompt",
                    [{"name": "knowledge.search", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}],
                )

        chat = execute({"choices": [{"message": {"tool_calls": [{"function": {"name": "knowledge__search", "arguments": "{\"query\":\"VLA\"}"}}]}}]}, "chat")
        responses = execute({"output": [{"type": "function_call", "name": "campaign__inspect", "arguments": "{\"campaign_id\":\"c1\"}"}]}, "responses")
        self.assertEqual(chat["calls"][0]["capability"], "knowledge.search")
        self.assertEqual(chat["calls"][0]["arguments"]["query"], "VLA")
        self.assertEqual(responses["calls"][0]["capability"], "campaign.inspect")
        cleaned = safe_provider_error(RuntimeError("Authorization: Bearer top-secret https://provider.invalid raw-body"))
        self.assertNotIn("top-secret", cleaned)
        self.assertNotIn("provider.invalid", cleaned)

    def test_local_research_uses_real_atlas_sources_and_evidence_levels(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "当前证据显示不同路线存在明显分歧 [S1]。"
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "检索在线强化学习 VLA 的关键证据", "intent_override": "local"},
        ).json()
        task = self.wait_task(started["task"]["id"], {"done"})
        self.assertEqual(task["service"], "evidence_research")
        self.assertGreater(len(task["source_ids"]), 0)
        response = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        self.assertTrue(response["tool_calls"])
        self.assertTrue(response["observations"])
        self.assertTrue(all(item["attempt_id"] == task["active_attempt_id"] for item in response["tool_calls"]))
        self.assertTrue(all(source["source_kind"] in {"atlas", "local_document"} for source in response["sources"]))
        self.assertTrue(all(source["evidence_level"] in {"curated_summary", "full_text"} for source in response["sources"]))
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        citations = saved["messages"][-1]["refs"]["citations"]
        self.assertTrue(citations)
        self.assertTrue(all(citation["source_ref"].get("source_id") for citation in citations))

    def test_atlas_only_constraint_skips_documents_and_network(self):
        decision = route_turn(AgentTurnRequest(message="只基于 Atlas 检索 VLA 论文"))
        self.assertEqual(decision.source_policy, "local_only")
        self.assertIn("atlas_only", decision.requested_outputs)
        latest = route_turn(AgentTurnRequest(message="这个方向的最新进展是什么"))
        self.assertEqual(latest.source_policy, "local_and_external")

        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_documents", side_effect=AssertionError("documents must not run")), patch.object(runtime.sources, "search_external", side_effect=AssertionError("network must not run")):
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "只基于 Atlas 检索 VLA 论文"},
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        self.assertTrue(task["source_ids"])
        sources = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()["sources"]
        self.assertTrue(all(source["source_kind"] == "atlas" for source in sources))

    def test_atlas_card_cannot_support_method_or_result_claims(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "不应出现的无原文方法结论。"
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_documents", side_effect=AssertionError("atlas-only must not search documents")), patch.object(runtime.sources, "search_external", side_effect=AssertionError("atlas-only must not use network")):
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "只基于 Atlas 分析 VLA 论文的方法、实验结果和局限"},
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        assistant = saved["messages"][-1]
        self.assertIn("Atlas 卡片是策展导航，不是论文本体", assistant["content"])
        self.assertNotIn("不应出现的无原文方法结论", assistant["content"])
        assessment = assistant["refs"]["evidence_assessment"]
        self.assertEqual(assessment["requirement"], "full_text")
        self.assertFalse(assessment["sufficient"])
        self.assertEqual(assessment["source_policy"], "local_only")
        self.assertEqual([item["action"] for item in assistant["refs"]["next_actions"]], ["inspect_sources"])
        artifact = next(item for item in self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()["artifacts"] if item["kind"] == "evidence_set")
        self.assertFalse(artifact["payload"]["evidence_assessment"]["sufficient"])

    def test_detailed_research_searches_and_imports_open_original(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "已基于开放全文核验方法与实验边界 [S3]。"
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        now = main.utc_now()
        title = "Deep Reinforcement Learning for Robotics: Real-World Successes"
        open_source = SourceRecord(
            id="source-open-original", source_kind="arxiv", evidence_level="abstract", title=title,
            locator={"url": "https://arxiv.org/abs/1234.5678", "pdf_url": "https://arxiv.org/pdf/1234.5678", "arxiv_id": "1234.5678"},
            abstract="Abstract evidence.", excerpt="Abstract evidence.", canonical_key="arxiv:1234.5678",
            content_hash="abstract-hash", retrieved_at=now, provider="arxiv", access="open",
        )
        full_source = SourceRecord(
            id="source-imported-original", source_kind="local_document", evidence_level="full_text", title=title,
            locator={"document_id": "document-open-original", "chunk_id": "chunk-method", "section": "Methods", "page": 3},
            excerpt="The method and experiment setup are described here.", canonical_key="document:open-original",
            content_hash="full-hash", retrieved_at=now, provider="local_fts", access="local",
        )
        document = DocumentRecord(
            id="document-open-original", title=title, file_name="paper.pdf", media_type="application/pdf",
            path="paper.pdf", content_hash="pdf-hash", page_count=12, chunk_count=8, created_at=now,
        )
        def search_documents(*_args):
            return [full_source]

        with patch.object(runtime.sources, "search_external", return_value=([open_source], [])) as external_search, patch.object(runtime.sources, "search_documents", side_effect=search_documents), patch("app.agent_v2.dispatcher.import_open_source", return_value=document) as import_original:
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={
                    "message": "分析这篇论文的方法、实验结果和局限",
                    "intent_override": "deep_research",
                    "turn_attachments": [{
                        "type": "paper", "title": title,
                        "source_ref": {"atlas_id": "I", "paper_id": "deep_reinforcement_learning_for_robotics_real_world_successes"},
                    }],
                },
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        self.assertGreaterEqual(external_search.call_count, 2)
        import_original.assert_called_once()
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        assessment = saved["messages"][-1]["refs"]["evidence_assessment"]
        self.assertTrue(assessment["requires_full_text"])
        self.assertTrue(assessment["sufficient"])
        self.assertGreaterEqual(assessment["counts"]["full_text"], 1)
        self.assertEqual(assessment["full_text_attempts"][0]["status"], "imported")
        self.assertIn("已基于开放全文", saved["messages"][-1]["content"])
        self.assertIn("source-imported-original", task["source_ids"])

    def test_discovery_query_can_use_curated_metadata(self):
        source = SourceRecord(
            id="source-curated", source_kind="atlas", evidence_level="curated_summary", title="Curated Paper",
            locator={"atlas_id": "I", "paper_id": "curated-paper"}, excerpt="Curated navigation summary.",
            canonical_key="title:curatedpaper", content_hash="curated", retrieved_at=main.utc_now(), provider="atlas", access="local",
        )
        request = AgentTurnRequest(message="检索这个方向有哪些代表论文")
        decision = ServiceDecision(service="evidence_research", objective=request.message, source_policy="local_only")
        assessment = assess_evidence(request, decision, [source], target_sources=[source])
        self.assertEqual(assessment.requirement, "discovery")
        self.assertTrue(assessment.sufficient)
        self.assertFalse(assessment.requires_original)

    def test_generic_local_research_uses_explicit_atlas_attachment(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "The attached Atlas paper is available as evidence [S1]."
        thread = self.create_thread()
        paper_id = "deep_reinforcement_learning_for_robotics_real_world_successes"
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={
                "message": "请基于当前材料说明下一步。",
                "intent_override": "local",
                "turn_attachments": [
                    {
                        "type": "paper",
                        "title": "Deep Reinforcement Learning for Robotics: Real-World Successes",
                        "source_ref": {"atlas_id": "I", "paper_id": paper_id},
                    }
                ],
            },
        ).json()
        self.wait_task(started["task"]["id"], {"done"})
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        citations = saved["messages"][-1]["refs"]["citations"]
        self.assertTrue(citations)
        self.assertEqual(citations[0]["source_type"], "atlas")
        self.assertEqual(citations[0]["source_ref"]["paper_id"], paper_id)

    def test_completed_task_retry_reuses_messages_and_increments_attempt(self):
        os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = "Retryable response."
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "解释这个问题", "intent_override": "chat"},
        ).json()
        task_id = started["task"]["id"]
        first = self.wait_task(task_id, {"done"})
        before = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        first_done_seq = [event.seq for event in main.get_agent_v2_runtime().store.list_events(task_id) if event.kind == "done"][-1]
        self.assertEqual(before["messages"][-1]["refs"]["agent_v2"]["last_seq"], first_done_seq)
        retried = self.client.post(f"/api/vnext/agent-v2/tasks/{task_id}/resume", json={})
        self.assertEqual(retried.status_code, 200, retried.text)
        second = self.wait_task(task_id, {"done"})
        after = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual(second["attempt"], first["attempt"] + 1)
        self.assertEqual(len(after["messages"]), len(before["messages"]))
        self.assertEqual(after["messages"][-1]["id"], before["messages"][-1]["id"])
        self.assertEqual(after["messages"][-1]["content"], "Retryable response.")
        second_done_seq = [event.seq for event in main.get_agent_v2_runtime().store.list_events(task_id) if event.kind == "done"][-1]
        self.assertGreater(second_done_seq, first_done_seq)
        self.assertEqual(after["messages"][-1]["refs"]["agent_v2"]["last_seq"], second_done_seq)

    def test_cancelled_research_cannot_be_revived_by_late_specialist_result(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()

        original_execute = runtime.dispatcher.execute

        def slow_execute(**kwargs):
            time.sleep(0.3)
            return original_execute(**kwargs)

        with patch.object(runtime.dispatcher, "execute", side_effect=slow_execute):
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "检索本地论文", "intent_override": "local"},
            ).json()
            task_id = started["task"]["id"]
            deadline = time.time() + 2
            view = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}").json()
            while time.time() < deadline and not view["tool_calls"]:
                time.sleep(0.01)
                view = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}").json()
            self.assertTrue(view["tool_calls"])
            cancelled = self.client.post(f"/api/vnext/agent-v2/tasks/{task_id}/cancel", json={})
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            time.sleep(0.45)
        final = runtime.store.get_task(task_id)
        self.assertEqual(final.status, "cancelled")
        self.assertEqual(final.source_ids, [])
        self.assertFalse(any(event.kind == "done" and event.payload.get("status") == "done" for event in runtime.store.list_events(task_id)))

    def test_operation_waits_for_approval_then_writes_context(self):
        thread = self.create_thread()
        attachment = {
            "type": "paper",
            "title": "Approval Paper",
            "summary": "Only apply after confirmation.",
            "source_ref": {"atlas_id": "I", "paper_id": "approval-paper"},
        }
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "把这篇论文保存为长期资料", "turn_attachments": [attachment]},
        ).json()
        task = self.wait_task(started["task"]["id"], {"waiting_approval"})
        before = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual(before["context_cards"], [])
        task_view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        approval = task_view["approvals"][0]
        self.assertEqual(approval["status"], "pending")
        resolved = self.client.post(
            f"/api/vnext/agent-v2/approvals/{approval['id']}/resolve",
            json={"decision": "approve"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.wait_task(task["id"], {"done"})
        after = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual([card["title"] for card in after["context_cards"]], ["Approval Paper"])
        self.assertTrue(any("已应用 Agent v2 操作" in message["content"] for message in after["messages"]))
        task_view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        batch = task_view["operation_batches"][0]
        self.assertEqual(batch["status"], "applied")
        undone = self.client.post(f"/api/vnext/agent-v2/operation-batches/{batch['id']}/undo", json={})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["operation_batch"]["status"], "undone")
        self.assertEqual(self.client.get(f"/api/vnext/threads/{thread['id']}").json()["context_cards"], [])

    def test_approval_with_empty_selection_writes_nothing(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={
                "message": "把附件加入长期资料",
                "turn_attachments": [
                    {
                        "type": "paper",
                        "title": "Do not apply",
                        "source_ref": {"atlas_id": "I", "paper_id": "not-applied"},
                    }
                ],
            },
        ).json()
        task = self.wait_task(started["task"]["id"], {"waiting_approval"})
        approval = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()["approvals"][0]
        resolved = self.client.post(
            f"/api/vnext/agent-v2/approvals/{approval['id']}/resolve",
            json={"decision": "approve", "selected_operation_ids": []},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.wait_task(task["id"], {"done"})
        saved = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual(saved["context_cards"], [])

    def test_approval_cannot_replace_operation_target(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={
                "message": "把附件加入长期资料",
                "turn_attachments": [{"type": "paper", "title": "Immutable target", "source_ref": {"id": "paper-original"}}],
            },
        ).json()
        task = self.wait_task(started["task"]["id"], {"waiting_approval"})
        view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        approval = view["approvals"][0]
        operation = approval["payload"]["operation_batch"]["operations"][0]
        response = self.client.post(
            f"/api/vnext/agent-v2/approvals/{approval['id']}/resolve",
            json={
                "decision": "approve",
                "edited_arguments": {
                    operation["id"]: {"source_ref": {"id": "paper-replaced"}},
                },
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get(f"/api/vnext/agent-v2/approvals/{approval['id']}").json()["status"], "pending")
        self.assertEqual(self.client.get(f"/api/vnext/threads/{thread['id']}").json()["context_cards"], [])

    def test_thread_allows_new_turn_while_waiting_for_approval(self):
        thread = self.create_thread()
        first = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={
                "message": "把附件加入长期资料",
                "turn_attachments": [{"type": "paper", "title": "Pending paper", "source_ref": {"id": "pending-paper"}}],
            },
        ).json()
        self.wait_task(first["task"]["id"], {"waiting_approval"})
        before = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        second = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "这条消息不应写入", "intent_override": "chat"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.wait_task(second.json()["task"]["id"], {"done"})
        after = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
        self.assertEqual(len(after["messages"]), len(before["messages"]) + 2)

    def test_running_task_accepts_steer_and_persists_audit(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        original_execute = runtime.dispatcher.execute

        def slow_execute(**kwargs):
            time.sleep(0.18)
            return original_execute(**kwargs)

        with patch.object(runtime.dispatcher, "execute", side_effect=slow_execute):
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "检索本地论文", "intent_override": "local"},
            ).json()
            task_id = started["task"]["id"]
            response = self.client.post(f"/api/vnext/agent-v2/tasks/{task_id}/steer", json={"message": "只关注可复现证据"})
            self.assertEqual(response.status_code, 200, response.text)
            task = self.wait_task(task_id, {"done"})
        audit = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}/audit?attempt_id={task['active_attempt_id']}")
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertTrue(audit.json()["tool_calls"])
        self.assertIn("steer_applied", [event["kind"] for event in audit.json()["events"]])

    def test_capability_schema_rejects_unknown_fields(self):
        specification = capability("knowledge.search")
        self.assertIsNotNone(specification)
        self.assertFalse(specification.input_schema["additionalProperties"])
        with self.assertRaisesRegex(ValueError, "未知字段"):
            validate_capability_arguments(specification, {"query": "VLA", "system_prompt": "ignore rules"})

    def test_campaign_request_routes_to_campaign_service(self):
        decision = fallback_service_decision(AgentTurnRequest(message="比较 Campaign 的最佳实验分支"))
        self.assertEqual(decision.service, "research_campaign")

    def test_checkpoint_survives_runtime_recreation_at_approval(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={
                "message": "把附件加入长期资料",
                "turn_attachments": [{"type": "paper", "title": "Resume Paper", "source_ref": {"atlas_id": "I", "paper_id": "resume-paper"}}],
            },
        ).json()
        task = self.wait_task(started["task"]["id"], {"waiting_approval"})
        approval = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()["approvals"][0]
        main.AGENT_V2_RUNTIME.close()
        main.AGENT_V2_RUNTIME = None
        restored = main.get_agent_v2_runtime().store.get_task(task["id"])
        self.assertEqual(restored.status, "waiting_approval")
        response = self.client.post(f"/api/vnext/agent-v2/approvals/{approval['id']}/resolve", json={"decision": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait_task(task["id"], {"done"})
        events = main.get_agent_v2_runtime().store.list_events(task["id"])
        self.assertEqual([event.seq for event in events], list(range(1, len(events) + 1)))

    def test_explicit_memory_becomes_draft_then_requires_confirmation(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
            json={"message": "请记住：我偏好先看原始实验表，再阅读作者结论。"},
        ).json()
        task = self.wait_task(started["task"]["id"], {"waiting_approval"})
        view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        memory_artifact = next(item for item in view["artifacts"] if item["kind"] == "memory_draft")
        draft_id = memory_artifact["payload"]["memory_draft"]["id"]
        self.assertEqual(main.get_agent_v2_runtime().store.list_memories(thread_id=thread["id"]), [])
        approval = view["approvals"][0]
        self.client.post(f"/api/vnext/agent-v2/approvals/{approval['id']}/resolve", json={"decision": "approve"})
        self.wait_task(task["id"], {"done"})
        memories = main.get_agent_v2_runtime().store.list_memories(thread_id=thread["id"])
        self.assertEqual(memories[0]["source_draft_id"], draft_id)

    def test_document_import_indexes_full_text(self):
        store = RuntimeStore(Path(self.temp.name) / "document-runtime")
        document = import_document(
            store,
            file_name="evidence.md",
            media_type="text/markdown",
            content="# Evidence\nAgentic retrieval should preserve provenance and evidence levels.".encode(),
            title="Evidence Note",
            created_at=main.utc_now(),
        )
        self.assertGreater(document.chunk_count, 0)
        rows = store.search_documents("provenance", 5)
        self.assertEqual(rows[0]["document_id"], document.id)
        store.close()

    def test_source_dedup_prefers_full_text(self):
        store = RuntimeStore(Path(self.temp.name) / "source-runtime")
        service = SourceService(store, lambda _: {"papers": []})
        now = main.utc_now()
        abstract = SourceRecord(
            id="source-a", source_kind="openalex", evidence_level="abstract", title="Same Paper", canonical_key="doi:10.1/test",
            abstract="abstract", content_hash="a", retrieved_at=now, provider="openalex",
        )
        full = SourceRecord(
            id="source-b", source_kind="local_document", evidence_level="full_text", title="Same Paper", canonical_key="doi:10.1/test",
            excerpt="full text", content_hash="b", retrieved_at=now, provider="local_fts", access="local",
        )
        result = service.deduplicate([abstract, full])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].evidence_level, "full_text")
        store.close()

    def test_reused_source_keeps_each_task_audit_link(self):
        store = RuntimeStore(Path(self.temp.name) / "source-links-runtime")
        now = main.utc_now()
        source = SourceRecord(
            id="source-shared",
            task_id="task-one",
            source_kind="atlas",
            evidence_level="curated_summary",
            title="Shared Atlas Paper",
            canonical_key="title:sharedatlaspaper",
            content_hash="shared",
            retrieved_at=now,
            provider="atlas",
        )
        store.save_source(source)
        source.task_id = "task-two"
        store.save_source(source)
        self.assertEqual([item.id for item in store.list_sources("task-one")], ["source-shared"])
        self.assertEqual([item.id for item in store.list_sources("task-two")], ["source-shared"])
        self.assertEqual(store.list_sources("task-one")[0].task_id, "task-one")
        store.close()

    def test_external_search_keeps_successes_when_one_provider_fails(self):
        store = RuntimeStore(Path(self.temp.name) / "partial-source-runtime")
        service = SourceService(store, lambda _: {"papers": []})
        now = main.utc_now()

        def record(identifier: str, provider: str) -> SourceRecord:
            return SourceRecord(
                id=identifier,
                task_id="task-partial",
                source_kind=provider,
                evidence_level="metadata",
                title=identifier,
                canonical_key=f"title:{identifier}",
                content_hash=identifier,
                retrieved_at=now,
                provider=provider,
            )

        service.search_openalex = lambda *_args: [record("source-openalex", "openalex")]
        service.search_crossref = lambda *_args: [record("source-crossref", "crossref")]
        service.search_arxiv = lambda *_args: (_ for _ in ()).throw(httpx.ReadTimeout("arXiv timeout"))
        sources, warnings = service.search_external("adaptive agents", "task-partial")
        self.assertEqual({item.id for item in sources}, {"source-openalex", "source-crossref"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("arxiv", warnings[0])
        service.search_openalex = lambda *_args: (_ for _ in ()).throw(AssertionError("cache miss"))
        service.search_crossref = service.search_openalex
        service.search_arxiv = service.search_openalex
        cached, cached_warnings = service.search_external("adaptive agents", "task-cached")
        self.assertEqual({item.id for item in cached}, {"source-openalex", "source-crossref"})
        self.assertEqual(cached_warnings, warnings)
        self.assertEqual({item.id for item in store.list_sources("task-cached")}, {"source-openalex", "source-crossref"})
        store.close()

    def test_open_pdf_import_enforces_trusted_host_and_indexes_pdf(self):
        store = RuntimeStore(Path(self.temp.name) / "open-pdf-runtime")
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        payload = io.BytesIO()
        writer.write(payload)
        source = SourceRecord(
            id="source-open-pdf",
            source_kind="arxiv",
            evidence_level="abstract",
            title="Open PDF",
            locator={"pdf_url": "https://arxiv.org/pdf/1234.5678"},
            canonical_key="arxiv:1234.5678",
            content_hash="open-pdf",
            retrieved_at=main.utc_now(),
            provider="arxiv",
            access="open",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=payload.getvalue(), request=request)

        document = import_open_source(
            store,
            source,
            created_at=main.utc_now(),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )
        self.assertEqual(document.page_count, 1)
        source.locator["pdf_url"] = "https://example.com/paper.pdf"
        with self.assertRaisesRegex(ValueError, "可信下载列表"):
            import_open_source(store, source, created_at=main.utc_now())
        store.close()

    def test_operation_batch_conflict_causes_zero_writes(self):
        thread = self.create_thread("Conflict target")
        now = main.utc_now()
        batch = OperationBatch(
            id="batch-conflict",
            task_id="task-conflict",
            thread_id=thread["id"],
            summary="Update title",
            operations=[Operation(id="operation-title", capability="thread.update", arguments={"changes": {"title": "Agent title"}})],
            created_at=now,
            updated_at=now,
        )
        prepared = main.v2_prepare_operation_batch(batch)
        concurrent = main.load_thread(thread["id"])
        concurrent.goal = "Concurrent user goal"
        main.write_thread(concurrent)
        with self.assertRaises(HTTPException) as caught:
            main.v2_apply_operation_batch(prepared, ApprovalResolveRequest(decision="approve"))
        self.assertEqual(caught.exception.status_code, 409)
        saved = main.load_thread(thread["id"])
        self.assertEqual(saved.title, "Conflict target")
        self.assertEqual(saved.goal, "Concurrent user goal")

    def test_operation_batch_undo_refuses_changed_target(self):
        thread = self.create_thread("Undo conflict")
        now = main.utc_now()
        batch = OperationBatch(
            id="batch-undo-conflict",
            task_id="task-undo-conflict",
            thread_id=thread["id"],
            summary="Add context",
            operations=[
                Operation(
                    id="operation-context",
                    capability="context.add",
                    arguments={"title": "Undo target", "source_ref": {"type": "note", "id": "undo-target"}},
                )
            ],
            created_at=now,
            updated_at=now,
        )
        prepared = main.v2_prepare_operation_batch(batch)
        applied = main.v2_apply_operation_batch(prepared, ApprovalResolveRequest(decision="approve"))
        saved = main.load_thread(thread["id"])
        saved.context_cards[0].summary = "Changed after apply"
        main.write_thread(saved)
        response = self.client.post(f"/api/vnext/agent-v2/operation-batches/{applied['operation_batch_id']}/undo", json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(main.load_thread(thread["id"]).context_cards[0].summary, "Changed after apply")

    def test_operation_batch_rolls_back_project_when_later_write_fails(self):
        project = self.client.post(
            "/api/vnext/projects",
            json={"title": "Original project", "goal": "Rollback test", "default_atlas_id": "I"},
        ).json()
        thread = self.client.post(
            "/api/vnext/threads",
            json={"title": "Rollback thread", "project_id": project["id"], "active_atlas_id": "I"},
        ).json()
        now = main.utc_now()
        batch = OperationBatch(
            id="batch-rollback",
            task_id="task-rollback",
            thread_id=thread["id"],
            summary="Project and context update",
            operations=[
                Operation(id="operation-project", capability="project.update", arguments={"project_id": project["id"], "changes": {"title": "Changed project"}}),
                Operation(
                    id="operation-context",
                    capability="context.add",
                    arguments={"title": "Should roll back", "source_ref": {"type": "note", "id": "rollback"}},
                ),
            ],
            created_at=now,
            updated_at=now,
        )
        prepared = main.v2_prepare_operation_batch(batch)
        with patch.object(main, "write_thread", side_effect=RuntimeError("simulated write failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated write failure"):
                main.v2_apply_operation_batch(prepared, ApprovalResolveRequest(decision="approve"))
        self.assertEqual(main.load_project(project["id"]).title, "Original project")
        self.assertEqual(main.load_thread(thread["id"]).context_cards, [])

    def test_docker_unavailable_never_executes_on_host(self):
        workspace = Path(self.temp.name) / "docker-unavailable"
        with patch("app.agent_v2.sandbox.docker_available", return_value=False), patch("app.agent_v2.sandbox.subprocess.run") as host_run:
            with self.assertRaises(SandboxUnavailable):
                run_docker_command(SandboxCommand(command="echo forbidden"), workspace)
        host_run.assert_not_called()

    def test_docker_unavailable_creates_non_executable_command_preview(self):
        thread = self.create_thread()
        with patch("app.agent_v2.capabilities.shutil.which", return_value=None), patch("app.agent_v2.sandbox.subprocess.run") as host_run:
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                json={"message": "执行命令: python -V", "intent_override": "execute"},
            ).json()
            task = self.wait_task(started["task"]["id"], {"done"})
        host_run.assert_not_called()
        view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        preview = next(item for item in view["artifacts"] if item["kind"] == "command_preview")
        self.assertFalse(preview["payload"]["available"])
        self.assertFalse(preview["payload"]["executed"])
        self.assertEqual(preview["payload"]["command"]["command"], "python -V")
        self.assertEqual(view["approvals"], [])

    def test_docker_arguments_enforce_default_isolation(self):
        workspace = Path(self.temp.name) / "sandbox"
        args = docker_arguments(SandboxCommand(command="python -V"), workspace)
        self.assertIn("--cpus", args)
        self.assertIn("--memory", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[-3:], ["sh", "-lc", "python -V"])
        self.assertNotIn("powershell", args)

    def test_service_routing_eval_reaches_95_percent(self):
        cases: list[tuple[str, str]] = []
        greetings = ["你好", "您好", "hello", "hi", "在吗", "谢谢", "早上好", "晚上好", "hey", "你好呀"]
        evidence = ["检索最新论文", "搜索相关证据", "这个方向有哪些文献", "查一下 arXiv 进展", "研究这个问题", "找 DOI", "论文证据是什么", "最新进展如何", "检索 VLA 论文", "搜索研究工作"]
        reading = ["阅读这篇论文", "分析这个 PDF", "这份文档第几节说明方法", "阅读全文", "总结这篇论文", "检查 PDF 实验", "读一下这份文档", "论文全文有什么局限", "分析附件文档", "从 PDF 找结论"]
        synthesis = ["比较两条研究路线", "形成证据链", "综合这些论文", "构建 Canvas 论证", "形成假设", "比较方法差异", "整理研究路线", "论证这个结论", "综合不同证据", "Canvas 结构是否完整"]
        operations = ["修改线程目标", "更新论文卡", "创建实验", "保存这条判断", "加入上下文", "重命名项目", "记住这个偏好", "写入 Canvas", "删除候选", "更新对象记忆"]
        execution = ["运行代码", "执行命令 python -V", "复现实验", "跑一下脚本", "run command pytest", "用 docker 执行", "执行命令", "运行这个程序", "跑一下测试", "执行 bash 命令"]
        for _ in range(2):
            cases += [(text, "conversation") for text in greetings]
            cases += [(text, "evidence_research") for text in evidence]
            cases += [(text, "document_reading") for text in reading]
            cases += [(text, "synthesis") for text in synthesis]
            cases += [(text, "workspace_operation") for text in operations]
        cases += [(text, "sandbox_execution") for text in execution]
        self.assertEqual(len(cases), 110)
        mismatches = [
            {"text": text, "expected": expected, "actual": fallback_service_decision(AgentTurnRequest(message=text)).service}
            for text, expected in cases
            if fallback_service_decision(AgentTurnRequest(message=text)).service != expected
        ]
        correct = len(cases) - len(mismatches)
        self.assertGreaterEqual(correct / len(cases), 0.95, json.dumps({"correct": correct, "total": len(cases), "mismatches": mismatches}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
