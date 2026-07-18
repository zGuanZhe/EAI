from __future__ import annotations

import os
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import application as main
from app.agent_v3.context import build_context_manifest
from app.agent_v3.models import FocusRef
from app.agent_v3.routing import is_information_request, retrieval_decision, route_interaction
from app.agent_v3.web import MAX_WEB_BYTES, WebSearchError, WebSearchProvider, WebSearchSettings, validate_public_url


INFORMATION_CASES = [
    f"{prefix}{topic}{suffix}"
    for prefix in ["解释", "比较", "查找", "分析", "What is ", "Find evidence about ", "How does ", "Compare "]
    for topic in [
        "强化学习", "视觉检索", "具身智能", "多模态模型", "机器人规划",
        "证据核验", "robot learning", "retrieval systems", "foundation models", "scientific agents",
    ]
    for suffix in ["？", "的最新进展", "有哪些证据"]
]
NON_INFORMATION_CASES = [
    "你好", "谢谢", "请改写这段话", "翻译成英文", "头脑风暴三个标题", "rewrite this paragraph",
    "translate this sentence", "brainstorm three names",
]


class AgentV3Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original = {
            "PERSONAL_DIR": main.PERSONAL_DIR, "THREADS_DIR": main.THREADS_DIR,
            "BACKUPS_DIR": main.BACKUPS_DIR, "PROJECTS_DIR": main.PROJECTS_DIR,
            "OBJECTS_DIR": main.OBJECTS_DIR, "ATLAS_UPDATES_DIR": main.ATLAS_UPDATES_DIR,
            "LAB_RUNS_DIR": main.LAB_RUNS_DIR, "RUNTIME_V2_DIR": main.RUNTIME_V2_DIR,
            "SECRET_CANDIDATES": main.SECRET_CANDIDATES,
        }
        main.reset_app_services()
        personal = root / "personal"
        main.PERSONAL_DIR = personal
        main.THREADS_DIR = personal / "threads"
        main.BACKUPS_DIR = personal / "backups"
        main.PROJECTS_DIR = personal / "projects"
        main.OBJECTS_DIR = personal / "objects"
        main.ATLAS_UPDATES_DIR = personal / "atlas_updates"
        main.LAB_RUNS_DIR = personal / "lab_runs"
        main.RUNTIME_V2_DIR = root / "runtime"
        main.SECRET_CANDIDATES = [root / "missing.json"]
        for name in ["EAI_WEB_SEARCH_PROVIDER", "EAI_WEB_SEARCH_BASE_URL", "EAI_WEB_SEARCH_API_KEY"]:
            os.environ.pop(name, None)
        main.ensure_dirs()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.reset_app_services()
        for name, value in self.original.items():
            setattr(main, name, value)
        self.temp.cleanup()

    def create_thread(self):
        response = self.client.post("/api/vnext/threads", json={"title": "Agent v3", "active_atlas_id": "I"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait(self, task_id: str, timeout: float = 8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}").json()["task"]
            if task["status"] in {"done", "failed", "cancelled", "waiting_approval", "paused"}:
                return task
            time.sleep(0.04)
        self.fail("Agent v3 task did not finish")

    @staticmethod
    def parse_sse(text: str):
        events = []
        for block in text.split("\n\n"):
            kind = next((line[6:].strip() for line in block.splitlines() if line.startswith("event:")), "")
            data = next((line[5:].strip() for line in block.splitlines() if line.startswith("data:")), "")
            if kind and data:
                events.append((kind, json.loads(data)))
        return events

    def test_information_queries_default_to_real_bounded_search(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_external", return_value=([], ["offline fixture"])):
            response = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent/asks",
                json={"message": "解释视觉强化学习的关键证据"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["retrieval"]["search_required"])
            task = self.wait(body["task"]["id"])
        view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        capabilities = {item["capability"] for item in view["tool_calls"]}
        self.assertIn("knowledge.search", capabilities)
        self.assertIn("sources.search_external", capabilities)
        self.assertEqual(task["interaction_lane"], "ask")

    def test_non_information_ask_has_no_search(self):
        thread = self.create_thread()
        response = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent/asks", json={"message": "你好"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = self.wait(response.json()["task"]["id"])
        view = self.client.get(f"/api/vnext/agent-v2/tasks/{task['id']}").json()
        self.assertFalse(response.json()["retrieval"]["search_required"])
        self.assertEqual(view["tool_calls"], [])

    def test_attached_paper_save_routes_to_operation_approval_before_search(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_external") as academic, patch.object(
            runtime.sources.web, "search"
        ) as web:
            response = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent/asks",
                json={
                    "message": "把这篇论文保存为长期资料",
                    "attachments": [{"type": "work", "id": "work-save-1", "title": "Paper to save"}],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            task = self.wait(response.json()["task"]["id"])
        self.assertEqual(task["service"], "workspace_operation")
        self.assertEqual(task["status"], "waiting_approval")
        self.assertFalse(response.json()["retrieval"]["search_required"])
        academic.assert_not_called()
        web.assert_not_called()

    def test_negated_save_request_never_routes_to_workspace_operation(self):
        decision = route_interaction("不要保存，只解释如何保存")
        self.assertNotEqual(decision.kind, "workspace_operation")
        thread = self.create_thread()
        response = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent/asks",
            json={
                "message": "不要保存，只解释如何保存",
                "attachments": [{"type": "work", "id": "work-read-1", "title": "Paper to explain"}],
                "source_policy": "none",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = self.wait(response.json()["task"]["id"])
        self.assertNotEqual(task["service"], "workspace_operation")
        self.assertNotEqual(task["status"], "waiting_approval")

    def test_information_route_still_selects_bounded_search(self):
        self.assertEqual(route_interaction("比较这些方法的最新证据").kind, "information")
        decision = retrieval_decision(
            "比较这些方法的最新证据",
            "local_and_external",
            web_available=True,
            interaction="information",
        )
        self.assertTrue(decision.search_required)
        self.assertEqual(decision.scopes, ["thread", "atlas", "local_documents", "academic", "web"])

    def test_unknown_source_policy_returns_422_without_network(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_external") as academic, patch.object(
            runtime.sources.web, "search"
        ) as web:
            response = self.client.post(
                f"/api/vnext/threads/{thread['id']}/agent/asks",
                json={"message": "Find current evidence?", "source_policy": "surprise_network"},
            )
        self.assertEqual(response.status_code, 422, response.text)
        academic.assert_not_called()
        web.assert_not_called()

    def test_research_task_does_not_block_daily_ask(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        os.environ["EAI_V2_MOCK_CAPABILITY_DELAY_MS"] = "500"
        try:
            with patch.object(runtime.sources, "search_external", return_value=([], [])):
                research = self.client.post(
                    f"/api/vnext/threads/{thread['id']}/research-tasks",
                    json={"objective": "检索多模态智能体的证据"},
                )
                self.assertEqual(research.status_code, 200, research.text)
                ask = self.client.post(
                    f"/api/vnext/threads/{thread['id']}/agent/asks", json={"message": "你好"}
                )
                self.assertEqual(ask.status_code, 200, ask.text)
                self.wait(ask.json()["task"]["id"])
                self.client.post(f"/api/vnext/research-tasks/{research.json()['task']['id']}/cancel")
        finally:
            os.environ.pop("EAI_V2_MOCK_CAPABILITY_DELAY_MS", None)

    def test_concurrent_research_creation_allows_exactly_one_active_task(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        os.environ["EAI_V2_MOCK_CAPABILITY_DELAY_MS"] = "900"
        try:
            with patch.object(runtime.sources, "search_external", return_value=([], [])):
                def create():
                    return self.client.post(
                        f"/api/vnext/threads/{thread['id']}/research-tasks",
                        json={"objective": "Compare current robot learning evidence?"},
                    )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _index: create(), range(2)))
                self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
                active = [
                    item for item in self.client.get(
                        f"/api/vnext/threads/{thread['id']}/research-tasks"
                    ).json() if item["status"] in {"pending", "running", "paused", "waiting_approval"}
                ]
                self.assertEqual(len(active), 1)
                self.client.post(f"/api/vnext/research-tasks/{active[0]['id']}/cancel")
        finally:
            os.environ.pop("EAI_V2_MOCK_CAPABILITY_DELAY_MS", None)

    def test_research_pause_resume_preserves_message_and_monotonic_events(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        os.environ["EAI_V2_MOCK_CAPABILITY_DELAY_MS"] = "700"
        try:
            with patch.object(runtime.sources, "search_external", return_value=([], [])):
                started = self.client.post(
                    f"/api/vnext/threads/{thread['id']}/research-tasks",
                    json={"objective": "Analyze current retrieval evidence?"},
                ).json()
                task_id = started["task"]["id"]
                paused = self.client.post(f"/api/vnext/research-tasks/{task_id}/pause")
                self.assertEqual(paused.status_code, 200, paused.text)
                self.assertEqual(paused.json()["task"]["status"], "paused")
                current_thread = self.client.get(f"/api/vnext/threads/{thread['id']}").json()
                assistant = next(item for item in current_thread["messages"] if item["id"] == started["assistant_message_id"])
                self.assertEqual(assistant["status"], "pending")
                current_task = self.client.get(f"/api/vnext/agent-v2/tasks/{task_id}").json()["task"]
                self.assertEqual(current_task["status"], "paused", current_task)
                resumed = self.client.post(f"/api/vnext/research-tasks/{task_id}/resume")
                self.assertEqual(resumed.status_code, 200, resumed.text)
                final = self.wait(task_id)
                self.assertEqual(final["status"], "done")
                events = runtime.store.list_events(task_id)
                self.assertEqual([item.seq for item in events], list(range(1, len(events) + 1)))
                kinds = [item.kind for item in events]
                self.assertEqual(kinds.count("paused"), 1)
                self.assertEqual(kinds.count("resumed"), 1)
                self.assertLess(kinds.index("resumed"), kinds.index("done"))
        finally:
            os.environ.pop("EAI_V2_MOCK_CAPABILITY_DELAY_MS", None)

    def test_research_sse_replay_is_monotonic_and_terminal_once(self):
        thread = self.create_thread()
        runtime = main.get_agent_v2_runtime()
        with patch.object(runtime.sources, "search_external", return_value=([], [])):
            started = self.client.post(
                f"/api/vnext/threads/{thread['id']}/research-tasks",
                json={"objective": "Compare evidence retrieval methods?"},
            ).json()
            task_id = started["task"]["id"]
            self.assertEqual(self.wait(task_id)["status"], "done")
        response = self.client.get(f"/api/vnext/research-tasks/{task_id}/events")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = self.parse_sse(response.text)
        sequences = [item[1]["seq"] for item in events]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertEqual([kind for kind, _ in events].count("done"), 1)
        self.assertEqual(events[-1][0], "done")
        after_seq = sequences[len(sequences) // 2]
        replay = self.parse_sse(self.client.get(
            f"/api/vnext/research-tasks/{task_id}/events?after_seq={after_seq}"
        ).text)
        self.assertTrue(replay)
        self.assertTrue(all(item[1]["seq"] > after_seq for item in replay))
        self.assertEqual(replay[-1][0], "done")

    def test_ui_command_and_connector_check_are_receipted_without_secrets(self):
        thread = self.create_thread()
        started = self.client.post(
            f"/api/vnext/threads/{thread['id']}/agent/asks", json={"message": "hello"}
        ).json()
        task_id = started["task"]["id"]
        self.wait(task_id)
        command = {
            "id": "ui_command_test",
            "task_id": task_id,
            "action": "open_object",
            "target": {"type": "paper", "id": "paper-1", "title": "Paper"},
            "status": "pending",
            "created_at": main.utc_now(),
            "resolved_at": None,
        }
        runtime = main.get_agent_v2_runtime()
        runtime.store.save_ui_command(command)
        listed = self.client.get(f"/api/vnext/agent/tasks/{task_id}/ui-commands")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()[0]["target"]["id"], "paper-1")
        resolved = self.client.post(
            "/api/vnext/agent/ui-commands/ui_command_test/resolve?status=applied"
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["status"], "applied")
        with patch.object(runtime.sources.web, "check", return_value={
            "id": "web", "provider": "searxng", "available": True, "health": "healthy",
        }):
            checked = self.client.post("/api/vnext/search-connectors/web/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertNotIn("key", checked.text.lower())

    def test_context_manifest_preserves_minimum_context_but_excludes_local_evidence(self):
        manifest = build_context_manifest(
            task_id="task", thread_id="thread", interaction_lane="ask", source_policy="external_only",
            surface="paper", focus_ref=FocusRef(type="paper", id="paper-1", title="Local paper"),
            raw={"thread": {"id": "thread", "title": "Question", "revision": 3},
                 "project": {"id": "project", "title": "Goal"},
                 "context_cards": [{"id": "card", "title": "Secret local card"}]},
            attachments=[{"id": "document-1", "type": "document", "title": "Local PDF"}], context_refs=[],
        )
        self.assertEqual({item.kind for item in manifest.items}, {"thread", "project"})
        self.assertTrue(all(not item.evidence_eligible for item in manifest.items))
        self.assertTrue(manifest.omitted)

    def test_atlas_only_manifest_excludes_personal_materials(self):
        manifest = build_context_manifest(
            task_id="task", thread_id="thread", interaction_lane="ask", source_policy="atlas_only",
            surface="paper", focus_ref=FocusRef(type="work", id="work-1", title="Atlas work"),
            raw={
                "thread": {"id": "thread", "title": "Question", "revision": 1},
                "project": {"id": "project", "title": "Goal"},
                "context_cards": [{"id": "card", "title": "Personal card"}],
                "long_term_memories": [{"id": "memory", "title": "Personal memory"}],
            },
            attachments=[
                {"id": "document-1", "type": "document", "title": "Local PDF"},
                {"id": "paper-1", "type": "paper", "title": "Atlas paper", "source_ref": {
                    "atlas_id": "I", "paper_id": "paper-1",
                }},
            ],
            context_refs=[FocusRef(type="document", id="document-2", title="Private note")],
        )
        visible = {(item.kind, item.id) for item in manifest.items}
        self.assertIn(("paper", "paper-1"), visible)
        self.assertIn(("work", "work-1"), visible)
        self.assertNotIn(("document", "document-1"), visible)
        self.assertNotIn(("document", "document-2"), visible)
        self.assertTrue(all(item.kind not in {"memory", "context-card"} for item in manifest.items))

    def test_web_security_rejects_private_targets(self):
        private_resolver = lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))]
        with self.assertRaises(WebSearchError):
            validate_public_url("https://example.test/private", private_resolver)
        provider = WebSearchProvider(WebSearchSettings(), resolver=private_resolver)
        self.assertFalse(provider.available)
        self.assertFalse(provider.status()["available"])

    def test_web_provider_normalizes_success_and_fails_closed(self):
        def resolver(host, *_args, **_kwargs):
            address = host if host in {"127.0.0.1", "::1"} else "93.184.216.34"
            return [(None, None, None, None, (address, 443))]

        def search_handler(request):
            self.assertEqual(request.url.path, "/search")
            return httpx.Response(200, json={"results": [
                {"title": "  Result title  ", "url": "https://example.org/paper", "content": " useful   excerpt "},
                {"title": "Private", "url": "https://127.0.0.1/private", "content": "blocked"},
            ]})

        provider = WebSearchProvider(
            WebSearchSettings(provider="searxng", base_url="https://search.example"),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(search_handler)),
            resolver=resolver,
        )
        self.assertEqual(provider.search("test", 5), [{
            "title": "Result title", "url": "https://example.org/paper", "excerpt": "useful excerpt",
        }])
        self.assertEqual(provider.check()["health"], "healthy")

        redirect_provider = WebSearchProvider(
            WebSearchSettings(provider="searxng", base_url="https://search.example"),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
            )),
            resolver=resolver,
        )
        with self.assertRaises(WebSearchError):
            redirect_provider.read("https://example.org/start")

        oversized_provider = WebSearchProvider(
            WebSearchSettings(provider="searxng", base_url="https://search.example"),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, headers={
                    "content-type": "text/plain", "content-length": str(MAX_WEB_BYTES + 1),
                })
            )),
            resolver=resolver,
        )
        with self.assertRaisesRegex(WebSearchError, "exceeds 2 MB"):
            oversized_provider.read("https://example.org/large")

        def timeout_handler(request):
            raise httpx.ReadTimeout("timed out", request=request)

        timeout_provider = WebSearchProvider(
            WebSearchSettings(provider="searxng", base_url="https://search.example"),
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(timeout_handler)),
            resolver=resolver,
        )
        with self.assertRaisesRegex(WebSearchError, "web read request failed"):
            timeout_provider.read("https://example.org/slow")

    def test_retrieval_eval_has_more_than_one_hundred_cases(self):
        cases = INFORMATION_CASES + NON_INFORMATION_CASES
        self.assertGreaterEqual(len(cases), 240)
        self.assertTrue(all(is_information_request(item) for item in INFORMATION_CASES))
        self.assertTrue(all(not is_information_request(item) for item in NON_INFORMATION_CASES))
        for item in INFORMATION_CASES:
            decision = retrieval_decision(item, "local_and_external", web_available=False)
            self.assertTrue(decision.search_required)
            self.assertIn("web", decision.unavailable_scopes)


if __name__ == "__main__":
    unittest.main()
