from __future__ import annotations

import tempfile
import unittest
import os
import json
import hashlib
import shutil
import sqlite3
from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import application as main


class VNextServiceTest(unittest.TestCase):
    def parse_sse_events(self, text: str) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        for block in text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            event = "message"
            data_lines = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
            if data_lines:
                events.append((event, json.loads("\n".join(data_lines))))
        return events

    def test_openapi_contract_matches_reviewed_vnext_baseline(self):
        schema = json.loads(json.dumps(main.app.openapi()))
        schema.get("info", {}).pop("version", None)
        serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(len(schema["paths"]), 102)
        self.assertEqual(sum(len(operations) for operations in schema["paths"].values()), 109)
        self.assertEqual(
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "91501d8801616771ea4cc14a29e02f7c8f9a67cd2e8de8deb6ef651b7a510aec",
        )

    def test_system_info_exposes_runtime_boundaries_without_secrets(self):
        client = TestClient(main.app)
        response = client.get("/api/vnext/system/info")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(Path(body["app_root"]).is_absolute())
        self.assertEqual(Path(body["app_root"]).resolve(), main.ROOT.resolve())
        self.assertIn("dev-personal", body["personal_dir"])
        self.assertIn("atlas-cache", body["atlas_cache_dir"])
        self.assertIn("service_version", body)
        serialized = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("api_key", serialized.lower())
        self.assertNotIn("sk-", serialized)

    def test_desktop_session_token_protects_local_api(self):
        original = os.environ.get("EAI_DESKTOP_SESSION_TOKEN")
        os.environ["EAI_DESKTOP_SESSION_TOKEN"] = "test-desktop-session"
        try:
            client = TestClient(main.app)
            unauthorized = client.get("/api/vnext/health")
            self.assertEqual(unauthorized.status_code, 401)
            authorized = client.get(
                "/api/vnext/health",
                headers={"X-EAI-Session": "test-desktop-session"},
            )
            self.assertEqual(authorized.status_code, 200)
            self.assertTrue(authorized.json()["ok"])
            self.assertNotIn("test-desktop-session", authorized.text)
            preflight = client.options(
                "/api/vnext/health",
                headers={
                    "Origin": "http://tauri.localhost",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "x-eai-session",
                },
            )
            self.assertEqual(preflight.status_code, 200)
            self.assertEqual(
                preflight.headers.get("access-control-allow-origin"),
                "http://tauri.localhost",
            )
        finally:
            if original is None:
                os.environ.pop("EAI_DESKTOP_SESSION_TOKEN", None)
            else:
                os.environ["EAI_DESKTOP_SESSION_TOKEN"] = original

    def test_newer_research_schema_starts_read_only_without_runtime_side_effects(self):
        original = {
            "PERSONAL_DIR": main.PERSONAL_DIR,
            "THREADS_DIR": main.THREADS_DIR,
            "BACKUPS_DIR": main.BACKUPS_DIR,
            "PROJECTS_DIR": main.PROJECTS_DIR,
            "OBJECTS_DIR": main.OBJECTS_DIR,
            "ATLAS_UPDATES_DIR": main.ATLAS_UPDATES_DIR,
            "LAB_RUNS_DIR": main.LAB_RUNS_DIR,
            "RUNTIME_V2_DIR": main.RUNTIME_V2_DIR,
            "RESEARCH_STORE": main.RESEARCH_STORE,
            "AGENT_V2_RUNTIME": main.AGENT_V2_RUNTIME,
            "CAMPAIGN_SERVICE": main.CAMPAIGN_SERVICE,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personal = root / "personal"
            try:
                main.PERSONAL_DIR = personal
                main.THREADS_DIR = personal / "threads"
                main.BACKUPS_DIR = personal / "backups"
                main.PROJECTS_DIR = personal / "projects"
                main.OBJECTS_DIR = personal / "objects"
                main.ATLAS_UPDATES_DIR = personal / "atlas_updates"
                main.LAB_RUNS_DIR = personal / "lab_runs"
                main.RUNTIME_V2_DIR = root / "runtime"
                main.RESEARCH_STORE = None
                main.AGENT_V2_RUNTIME = None
                main.CAMPAIGN_SERVICE = None
                main.ensure_dirs()

                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Future schema thread", "active_atlas_id": "I"},
                )
                self.assertEqual(created.status_code, 200, created.text)
                thread = created.json()
                main.get_agent_v2_runtime().close()
                main.AGENT_V2_RUNTIME = None
                main.RESEARCH_STORE.close()
                main.RESEARCH_STORE = None

                research_db = root / "research" / "research.db"
                connection = sqlite3.connect(research_db)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, summary) VALUES(4, 'future', 'future schema')"
                )
                connection.commit()
                connection.close()
                research_before = research_db.read_bytes()
                runtime_db = root / "runtime" / "runtime.db"
                runtime_before = runtime_db.read_bytes()

                read_only_client = TestClient(main.app)
                info = read_only_client.get("/api/vnext/system/info")
                self.assertEqual(info.status_code, 200, info.text)
                self.assertTrue(info.json()["research_store"]["read_only"])
                loaded = read_only_client.get(f"/api/vnext/threads/{thread['id']}")
                self.assertEqual(loaded.status_code, 200, loaded.text)
                searched = read_only_client.post(
                    "/api/vnext/sources/search",
                    json={"query": "robot learning", "source_policy": "local_only", "limit": 4},
                )
                self.assertEqual(searched.status_code, 200, searched.text)

                blocked_requests = {
                    "thread_update": read_only_client.put(
                        f"/api/vnext/threads/{thread['id']}",
                        json={"title": "Blocked", "expected_revision": thread["revision"]},
                    ),
                    "agent_turn": read_only_client.post(
                        f"/api/vnext/threads/{thread['id']}/agent-v2/turns",
                        json={"message": "This must not start", "intent_override": "auto"},
                    ),
                    "campaign_install": read_only_client.post("/api/vnext/campaign-runtime/install?profile=cpu", json={}),
                }
                for name, response in blocked_requests.items():
                    self.assertEqual(response.status_code, 409, f"{name}: {response.text}")
                    detail = response.json()["detail"]
                    self.assertIsInstance(detail, dict, f"{name}: {response.text}")
                    self.assertEqual(detail["code"], "schema_newer_than_app")

                if main.AGENT_V2_RUNTIME is not None:
                    main.AGENT_V2_RUNTIME.close()
                    main.AGENT_V2_RUNTIME = None
                if main.RESEARCH_STORE is not None:
                    main.RESEARCH_STORE.close()
                    main.RESEARCH_STORE = None
                self.assertEqual(research_db.read_bytes(), research_before)
                self.assertEqual(runtime_db.read_bytes(), runtime_before)
            finally:
                if main.AGENT_V2_RUNTIME is not None:
                    main.AGENT_V2_RUNTIME.close()
                if main.RESEARCH_STORE is not None:
                    main.RESEARCH_STORE.close()
                for name, value in original.items():
                    setattr(main, name, value)

    def test_legacy_runs_are_readable_and_mutation_endpoints_are_gone(self):
        fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "legacy" / "thread-with-agent-run.json"
        with tempfile.TemporaryDirectory() as directory:
            personal = Path(directory) / "personal"
            with patch.multiple(
                main,
                PERSONAL_DIR=personal,
                THREADS_DIR=personal / "threads",
                BACKUPS_DIR=personal / "backups",
                PROJECTS_DIR=personal / "projects",
                OBJECTS_DIR=personal / "objects",
                ATLAS_UPDATES_DIR=personal / "atlas_updates",
                LAB_RUNS_DIR=personal / "lab_runs",
                SECRET_CANDIDATES=[personal / "missing-secrets.json"],
            ):
                main.ensure_dirs()
                target = main.THREADS_DIR / "thread-legacy-fixture.json"
                shutil.copy2(fixture, target)
                client = TestClient(main.app)

                loaded = client.get("/api/vnext/threads/thread-legacy-fixture")
                self.assertEqual(loaded.status_code, 200, loaded.text)
                self.assertEqual(loaded.json()["agent_runs"][0]["id"], "agent-run-legacy-fixture")
                self.assertEqual(loaded.json()["changesets"][0]["status"], "undone")
                run = client.get(
                    "/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture"
                )
                self.assertEqual(run.status_code, 200, run.text)
                self.assertEqual(run.json()["answer"], "Legacy answer")

                before = target.read_bytes()
                legacy_mutations = [
                    ("/api/vnext/threads/thread-legacy-fixture/chat", {"message": "blocked"}),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/start", {"message": "blocked"}),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/complete", {"assistant_message_id": "message-legacy-assistant"}),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/message-legacy-assistant/retry", {}),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/start", {"message": "blocked"}),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/complete", {}),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/stream", {}),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/retry", {}),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/cancel", {}),
                ]
                for path, payload in legacy_mutations:
                    response = client.post(path, json=payload)
                    self.assertEqual(response.status_code, 410, f"{path}: {response.text}")
                self.assertEqual(target.read_bytes(), before)

    def test_agent_markdown_sanitizer_preserves_reading_structure(self):
        source = "## 当前判断\n\n- 证据一\n- 证据二\n\n结论。\x00"
        cleaned = main.safe_markdown_text(source)
        self.assertEqual(cleaned, "## 当前判断\n\n- 证据一\n- 证据二\n\n结论。")

    def test_source_and_core_responses_do_not_contain_mojibake(self):
        blocked = [
            "\u934b",
            "\u93c2",
            "\u6d93",
            "\u7025",
            "\u95ba",
            "\u940e",
            "\u7ecc",
            "\u951b",
            "\ufffd",
        ]
        source_paths = [
            Path(main.__file__),
            Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "Composer.jsx",
            Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "ThreadSurface.jsx",
            Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "HomeSurface.jsx",
        ]
        for path in source_paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in blocked:
                self.assertNotIn(token, text, f"{token} found in {path}")

        stages = main.default_lab_stages()
        self.assertEqual([stage.title for stage in stages], ["初始实现", "基线复现", "创新实验", "消融实验", "结果分析"])

    def legacy_thread_chat_degrades_without_key_and_cleans_ai_suggestions(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
            main.ensure_dirs()

            try:
                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Untitled research thread", "goal": "", "active_atlas_id": "G"},
                )
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]

                degraded = client.post(
                    f"/api/vnext/threads/{thread_id}/chat",
                    json={"message": "这个问题应该先从哪些证据开始？", "surface": "thread"},
                )
                self.assertEqual(degraded.status_code, 200)
                degraded_body = degraded.json()
                self.assertTrue(degraded_body["degraded"])
                self.assertEqual(degraded_body["assistant_message"]["role"], "assistant")
                self.assertEqual(degraded_body["assistant_message"]["kind"], "assistant_reply")
                self.assertGreaterEqual(len(degraded_body["assistant_message"]["refs"]["suggestions"]), 2)
                serialized = json.dumps(degraded_body, ensure_ascii=False)
                self.assertNotIn("sk-", serialized)
                self.assertNotIn("api_key", serialized.lower())
                self.assertEqual(degraded_body["thread"]["title"], "这个问题应该先从哪些证据开始？")

                os.environ["OPENAI_API_KEY"] = "sk-test-not-saved"
                os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```eai-thread-chat/v1
{"content":"先把 Atlas 中的代表性论文分成支持、质疑和待查三组。","suggestions":[{"action":"open_atlas","label":"去 Atlas 找证据","description":"先选 3-5 张核心材料","confidence":0.9},{"action":"delete_everything","label":"非法动作"}]}
```"""
                before = client.get(f"/api/vnext/threads/{thread_id}").json()
                ai = client.post(
                    f"/api/vnext/threads/{thread_id}/chat",
                    json={"message": "继续帮我判断下一步。", "surface": "thread"},
                )
                self.assertEqual(ai.status_code, 200)
                ai_body = ai.json()
                self.assertFalse(ai_body["degraded"])
                self.assertIn("代表性论文", ai_body["assistant_message"]["content"])
                suggestions = ai_body["assistant_message"]["refs"]["suggestions"]
                self.assertEqual([item["action"] for item in suggestions], ["open_atlas"])
                self.assertEqual(ai_body["thread"]["canvas"], before["canvas"])
                self.assertEqual(list(main.OBJECTS_DIR.glob("**/*.json")), [])
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                    main.SECRET_CANDIDATES,
                    old_key,
                    old_mock,
                ) = original
                if old_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_key
                if old_mock is None:
                    os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
                else:
                    os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = old_mock

    def legacy_thread_chat_start_complete_and_retry_reuse_assistant_draft(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ["OPENAI_API_KEY"] = "sk-test-not-saved"
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```eai-thread-chat/v1
{"content":"Use the selected evidence first.","suggestions":[{"action":"preview_task_pack","label":"Preview pack","description":"Check export order","confidence":0.8}]}
```"""
            main.ensure_dirs()

            try:
                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Untitled research thread", "goal": "", "active_atlas_id": "G"},
                )
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]
                started = client.post(
                    f"/api/vnext/threads/{thread_id}/chat/start",
                    json={"message": "How should I frame this research question?", "surface": "thread"},
                )
                self.assertEqual(started.status_code, 200)
                start_body = started.json()
                assistant_id = start_body["assistant_message_id"]
                self.assertEqual(start_body["thread"]["messages"][-1]["id"], assistant_id)
                self.assertEqual(start_body["thread"]["messages"][-1]["status"], "pending")
                self.assertEqual(start_body["thread"]["messages"][-1]["refs"]["steps"][0]["status"], "running")

                completed = client.post(
                    f"/api/vnext/threads/{thread_id}/chat/complete",
                    json={"assistant_message_id": assistant_id},
                )
                self.assertEqual(completed.status_code, 200)
                complete_body = completed.json()
                self.assertFalse(complete_body["degraded"])
                self.assertEqual(complete_body["assistant_message"]["id"], assistant_id)
                self.assertEqual(complete_body["assistant_message"]["status"], "done")
                self.assertEqual(
                    [item["action"] for item in complete_body["assistant_message"]["refs"]["suggestions"]],
                    ["preview_task_pack"],
                )
                self.assertTrue(all(step["status"] == "done" for step in complete_body["assistant_message"]["refs"]["steps"]))
                message_count = len(complete_body["thread"]["messages"])

                retry = client.post(f"/api/vnext/threads/{thread_id}/chat/{assistant_id}/retry", json={})
                self.assertEqual(retry.status_code, 200)
                retry_body = retry.json()
                self.assertEqual(retry_body["assistant_message"]["id"], assistant_id)
                self.assertEqual(len(retry_body["thread"]["messages"]), message_count)
                serialized = json.dumps(retry_body, ensure_ascii=False)
                self.assertNotIn("sk-test-not-saved", serialized)
                self.assertNotIn("api_key", serialized.lower())
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                    main.SECRET_CANDIDATES,
                    old_key,
                    old_mock,
                ) = original
                if old_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_key
                if old_mock is None:
                    os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
                else:
                    os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = old_mock

    def test_atlas_bundle_reads_existing_cache(self):
        client = TestClient(main.app)
        response = client.get("/api/vnext/atlases/G/bundle")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["atlas"]["id"], "G")
        self.assertGreater(len(data["papers"]), 0)
        self.assertGreater(len(data["relations"]), 0)

    def legacy_agent_run_generates_confirmable_proposals_and_writes_only_after_confirm(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("OPENROUTER_MODEL"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test-not-saved"
            os.environ["OPENROUTER_MODEL"] = "test/free-model"
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```eai-agent-run/v1
{
  "answer": "先把这篇论文作为当前路线的核心证据，但实验细节仍需要原文确认。",
  "action_proposals": [
    {
      "type": "object_memory",
      "target": {
        "atlas_id": "G",
        "object_type": "paper",
        "object_id": "agent-paper",
        "title": "Agent Paper"
      },
      "summary": "写入 Agent Paper 的对象记忆草稿",
      "diff": [
        {
          "field": "judgement",
          "before": "",
          "after": "这篇论文适合作为当前论证的基线证据。",
          "reason": "它与当前问题和已选上下文高度相关。"
        },
        {
          "field": "tags",
          "before": [],
          "after": ["baseline", "agent"],
          "reason": "便于后续复用。"
        }
      ],
      "risk": "low"
    },
    {
      "type": "atlas_candidate",
      "target": {"atlas_id": "G", "id": "candidate-agent-paper"},
      "summary": "生成一个候选论文草稿",
      "diff": [
        {"field": "title", "after": "Candidate Agent Paper"},
        {"field": "confidence", "after": 0.72}
      ],
      "risk": "medium"
    }
  ],
  "next_actions": [
    {"action": "inspect_proposal", "label": "查看修改提案", "description": "确认后写入个人数据。"},
    {"action": "delete_everything", "label": "非法动作"}
  ]
}
```"""
            main.ensure_dirs()

            try:
                client = TestClient(main.app)
                secret_status = client.get("/api/vnext/secrets/status")
                self.assertEqual(secret_status.status_code, 200)
                self.assertEqual(secret_status.json()["providers"][0]["provider"], "openrouter")
                self.assertNotIn("sk-or-test-not-saved", json.dumps(secret_status.json(), ensure_ascii=False))

                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Untitled research thread", "goal": "", "active_atlas_id": "G"},
                )
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]

                started = client.post(
                    f"/api/vnext/threads/{thread_id}/agent-runs/start",
                    json={
                        "message": "帮我判断这条路线下一步该补什么证据",
                        "surface": "thread",
                        "turn_attachments": [{
                            "type": "paper",
                            "title": "测试论文",
                            "source_ref": {"atlas_id": "G", "paper_id": "test-paper"},
                            "summary": "只影响本轮的安全短摘要",
                        }],
                    },
                )
                self.assertEqual(started.status_code, 200)
                start_body = started.json()
                run_id = start_body["run"]["id"]
                assistant_id = start_body["assistant_message_id"]
                self.assertEqual(start_body["thread"]["messages"][-1]["id"], assistant_id)
                self.assertEqual(start_body["thread"]["messages"][-1]["status"], "pending")
                user_message = next(item for item in start_body["thread"]["messages"] if item["id"] == start_body["user_message_id"])
                self.assertEqual(user_message["refs"]["turn_attachments"][0]["title"], "测试论文")
                self.assertEqual(start_body["thread"]["agent_runs"][0]["id"], run_id)

                completed = client.post(f"/api/vnext/threads/{thread_id}/agent-runs/{run_id}/complete", json={})
                self.assertEqual(completed.status_code, 200)
                body = completed.json()
                self.assertFalse(body["degraded"])
                self.assertEqual(body["run"]["status"], "waiting_confirmation")
                self.assertEqual(body["assistant_message"]["status"], "preview")
                self.assertGreaterEqual(len(body["run"]["tool_calls"]), 2)
                self.assertEqual([item["action"] for item in body["run"]["next_actions"]], ["inspect_proposal"])
                self.assertEqual(len(body["thread"]["action_proposals"]), 2)
                self.assertFalse((main.OBJECTS_DIR / "G" / "paper" / "agent-paper.json").exists())
                self.assertFalse((main.ATLAS_UPDATES_DIR / "G.json").exists())

                memory_proposal = next(
                    item for item in body["thread"]["action_proposals"] if item["type"] == "object_memory"
                )
                candidate_proposal = next(
                    item for item in body["thread"]["action_proposals"] if item["type"] == "atlas_candidate"
                )
                confirmed = client.post(
                    f"/api/vnext/threads/{thread_id}/action-proposals/{memory_proposal['id']}/confirm",
                    json={},
                )
                self.assertEqual(confirmed.status_code, 200)
                confirmed_body = confirmed.json()
                self.assertEqual(confirmed_body["proposal"]["status"], "confirmed")
                memory_path = main.OBJECTS_DIR / "G" / "paper" / "agent-paper.json"
                self.assertTrue(memory_path.exists())
                saved_memory = json.loads(memory_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_memory["judgement"], "这篇论文适合作为当前论证的基线证据。")
                self.assertEqual(saved_memory["tags"], ["baseline", "agent"])

                rejected = client.post(
                    f"/api/vnext/threads/{thread_id}/action-proposals/{candidate_proposal['id']}/reject",
                    json={},
                )
                self.assertEqual(rejected.status_code, 200)
                self.assertEqual(rejected.json()["proposal"]["status"], "rejected")
                self.assertFalse((main.ATLAS_UPDATES_DIR / "G.json").exists())
                serialized = json.dumps(rejected.json(), ensure_ascii=False)
                self.assertNotIn("sk-or-test-not-saved", serialized)
                self.assertNotIn("api_key", serialized.lower())
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                    main.SECRET_CANDIDATES,
                    old_openai,
                    old_openrouter,
                    old_openrouter_model,
                    old_mock,
                ) = original
                if old_openai is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_openai
                if old_openrouter is None:
                    os.environ.pop("OPENROUTER_API_KEY", None)
                else:
                    os.environ["OPENROUTER_API_KEY"] = old_openrouter
                if old_openrouter_model is None:
                    os.environ.pop("OPENROUTER_MODEL", None)
                else:
                    os.environ["OPENROUTER_MODEL"] = old_openrouter_model
                if old_mock is None:
                    os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
                else:
                    os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = old_mock

    def legacy_agent_run_stream_emits_work_loop_and_persists_only_on_done(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ["OPENROUTER_API_KEY"] = "sk-or-stream-not-saved"
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```eai-agent-run/v1
{
  "answer": "这是流式主对话回答，确认前不会写入长期数据。",
  "action_proposals": [
    {
      "type": "object_memory",
      "target": {"atlas_id": "G", "object_type": "paper", "object_id": "stream-paper"},
      "summary": "生成流式对象记忆草稿",
      "diff": [{"field": "judgement", "after": "流式提案仍需确认。"}],
      "risk": "low"
    }
  ],
  "next_actions": [{"action": "inspect_proposal", "label": "查看提案"}]
}
```"""
            main.ensure_dirs()

            try:
                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Untitled research thread", "goal": "", "active_atlas_id": "G"},
                )
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]
                started = client.post(
                    f"/api/vnext/threads/{thread_id}/agent-runs/start",
                    json={"message": "用流式方式分析当前路线", "surface": "thread"},
                )
                self.assertEqual(started.status_code, 200)
                run_id = started.json()["run"]["id"]
                assistant_id = started.json()["assistant_message_id"]
                self.assertEqual(started.json()["thread"]["messages"][-1]["id"], assistant_id)
                self.assertFalse((main.OBJECTS_DIR / "G" / "paper" / "stream-paper.json").exists())

                with client.stream("POST", f"/api/vnext/threads/{thread_id}/agent-runs/{run_id}/stream", json={}) as response:
                    self.assertEqual(response.status_code, 200)
                    raw = "".join(response.iter_text())
                events = self.parse_sse_events(raw)
                event_names = [name for name, _ in events]
                self.assertIn("step", event_names)
                self.assertIn("skill", event_names)
                self.assertIn("tool_call", event_names)
                self.assertIn("observation", event_names)
                self.assertIn("answer_delta", event_names)
                self.assertIn("proposal", event_names)
                self.assertIn("changeset", event_names)
                self.assertEqual(event_names[-1], "done")
                answer_text = "".join(data.get("text", "") for name, data in events if name == "answer_delta")
                self.assertNotIn("eai-agent-run/v1", answer_text)
                self.assertNotIn("action_proposals", answer_text)

                done = events[-1][1]
                self.assertFalse(done["degraded"])
                self.assertEqual(done["assistant_message"]["id"], assistant_id)
                self.assertEqual(done["assistant_message"]["status"], "preview")
                self.assertEqual(done["run"]["status"], "waiting_confirmation")
                self.assertEqual(len(done["run"]["proposals"]), 1)
                self.assertEqual(len(done["run"]["changeset_ids"]), 1)
                self.assertEqual(done["thread"]["messages"][-1]["id"], assistant_id)
                self.assertEqual(len([m for m in done["thread"]["messages"] if m["role"] == "assistant"]), 1)
                self.assertFalse((main.OBJECTS_DIR / "G" / "paper" / "stream-paper.json").exists())
                serialized = json.dumps(done, ensure_ascii=False)
                self.assertNotIn("sk-or-stream-not-saved", serialized)
                self.assertNotIn("api_key", serialized.lower())
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                    main.SECRET_CANDIDATES,
                    old_openai,
                    old_openrouter,
                    old_mock,
                ) = original
                if old_openai is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_openai
                if old_openrouter is None:
                    os.environ.pop("OPENROUTER_API_KEY", None)
                else:
                    os.environ["OPENROUTER_API_KEY"] = old_openrouter
                if old_mock is None:
                    os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
                else:
                    os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = old_mock

    def legacy_agent_run_stream_degrades_without_key(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
            main.ensure_dirs()

            try:
                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Untitled research thread", "goal": "", "active_atlas_id": "I"},
                )
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]
                started = client.post(
                    f"/api/vnext/threads/{thread_id}/agent-runs/start",
                    json={"message": "没有 key 时也要保持可回看的主对话", "surface": "thread"},
                )
                self.assertEqual(started.status_code, 200)
                with client.stream(
                    "POST",
                    f"/api/vnext/threads/{thread_id}/agent-runs/{started.json()['run']['id']}/stream",
                    json={},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    events = self.parse_sse_events("".join(response.iter_text()))
                event_names = [name for name, _ in events]
                self.assertIn("error", event_names)
                self.assertIn("answer_delta", event_names)
                self.assertEqual(event_names[-1], "done")
                done = events[-1][1]
                self.assertTrue(done["degraded"])
                self.assertEqual(done["assistant_message"]["status"], "done")
                self.assertEqual(done["thread"]["messages"][-1]["id"], started.json()["assistant_message_id"])
                self.assertIn("当前 Atlas 共 43 篇论文", done["assistant_message"]["content"])
                self.assertGreaterEqual(len(done["assistant_message"]["refs"]["citations"]), 1)
                self.assertEqual(
                    [item["action"] for item in done["assistant_message"]["refs"]["next_actions"]],
                    ["inspect_sources", "open_settings"],
                )
                self.assertEqual(
                    [item["action"] for item in done["assistant_message"]["refs"]["suggestions"]],
                    ["inspect_sources", "open_settings"],
                )
                serialized = json.dumps(done, ensure_ascii=False)
                self.assertNotIn("sk-", serialized)
                self.assertNotIn("api_key", serialized.lower())
                self.assertFalse((main.OBJECTS_DIR / "I").exists())
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                    main.SECRET_CANDIDATES,
                    old_openai,
                    old_openrouter,
                    old_mock,
                ) = original
                if old_openai is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = old_openai
                if old_openrouter is None:
                    os.environ.pop("OPENROUTER_API_KEY", None)
                else:
                    os.environ["OPENROUTER_API_KEY"] = old_openrouter
                if old_mock is None:
                    os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
                else:
                    os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = old_mock

    def test_agent_prompt_respects_context_agent_scope(self):
        doc = main.ThreadDoc(
            id="thread-scope-test",
            title="Scope test",
            goal="Check context scope",
            created_at=main.utc_now(),
            updated_at=main.utc_now(),
            active_atlas_id="G",
            context_cards=[
                main.ContextCard(
                    id="card-agent",
                    type="paper",
                    title="Visible Agent Context",
                    summary="This card should be visible to the main agent.",
                    source_ref={"atlas_id": "G", "paper_id": "visible"},
                    selected_for_export=True,
                    include_in_agent=True,
                    priority=1,
                ),
                main.ContextCard(
                    id="card-priority",
                    type="paper",
                    title="Priority Agent Context",
                    summary="This high priority card should be earlier than normal cards.",
                    source_ref={"atlas_id": "G", "paper_id": "priority"},
                    selected_for_export=False,
                    include_in_agent=True,
                    priority=3,
                    agent_note="Use this as the main framing evidence.",
                ),
                main.ContextCard(
                    id="card-pinned",
                    type="paper",
                    title="Pinned Agent Context",
                    summary="This pinned card should be first.",
                    source_ref={"atlas_id": "G", "paper_id": "pinned"},
                    selected_for_export=False,
                    include_in_agent=True,
                    priority=0,
                    pinned=True,
                ),
                main.ContextCard(
                    id="card-export-only",
                    type="paper",
                    title="Export Only Context",
                    summary="This card should stay out of the main agent prompt.",
                    source_ref={"atlas_id": "G", "paper_id": "hidden"},
                    selected_for_export=True,
                    include_in_agent=False,
                ),
            ],
        )
        prompt = main.build_agent_prompt(doc, "Which context can you use?", {"atlas": {"id": "G"}, "papers": [], "memories": []})
        summary = main.thread_chat_context_summary(doc)
        self.assertEqual(summary["context_cards"], 4)
        self.assertEqual(summary["agent_context_cards"], 3)
        self.assertEqual(summary["pinned_context_cards"], 1)
        self.assertIn("Visible Agent Context", prompt)
        self.assertIn("Priority Agent Context", prompt)
        self.assertIn("Pinned Agent Context", prompt)
        self.assertIn("Use this as the main framing evidence.", prompt)
        self.assertNotIn("Export Only Context", prompt)
        self.assertLess(prompt.index("Pinned Agent Context"), prompt.index("Priority Agent Context"))
        self.assertLess(prompt.index("Priority Agent Context"), prompt.index("Visible Agent Context"))

    def test_thread_lifecycle_uses_file_truth(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.SECRET_CANDIDATES = [tmp_path / "secrets.json"]
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
            main.ensure_dirs()

            client = TestClient(main.app)
            project = client.post(
                "/api/vnext/projects",
                json={
                    "title": "Survey chapter",
                    "goal": "Ship a literature survey chapter",
                    "default_atlas_id": "G",
                },
            )
            self.assertEqual(project.status_code, 200)
            project_id = project.json()["id"]
            self.assertTrue((main.PROJECTS_DIR / f"{project_id}.json").exists())

            created = client.post(
                "/api/vnext/threads",
                json={
                    "title": "Test thread",
                    "goal": "Assemble graph context",
                    "active_atlas_id": "G",
                    "project_id": project_id,
                },
            )
            self.assertEqual(created.status_code, 200)
            thread = created.json()
            thread_id = thread["id"]
            self.assertEqual(thread["project_id"], project_id)
            self.assertGreaterEqual(len(thread["messages"]), 1)
            thread_path = main.THREADS_DIR / f"{thread_id}.json"
            self.assertTrue(thread_path.exists())

            message = client.post(
                f"/api/vnext/threads/{thread_id}/messages",
                json={
                    "role": "user",
                    "kind": "text",
                    "content": "请把当前 Atlas 证据整理成一条研究问题。",
                    "surface": "atlas",
                    "refs": {"active_atlas_id": "G", "api_key": "must-not-be-saved"},
                    "api_key": "must-not-be-saved",
                },
            )
            self.assertEqual(message.status_code, 200)
            message_body = message.json()
            self.assertEqual(message_body["messages"][-1]["role"], "user")
            self.assertNotIn("must-not-be-saved", json.dumps(message_body["messages"][-1], ensure_ascii=False))

            card = {
                "id": "card_test",
                "type": "paper",
                "title": "Test Paper",
                "source_ref": {"atlas_id": "G", "paper_id": "test"},
                "summary": "A paper used by the test.",
                "token_estimate": 160,
                "selected_for_export": True,
            }
            updated = client.put(
                f"/api/vnext/threads/{thread_id}",
                json={
                    "context_cards": [card],
                    "active_surface": "canvas",
                    "canvas": {
                        "nodes": [
                            {
                                "id": "q_main",
                                "type": "question",
                                "title": "Main question",
                                "body": "What should the survey argue?",
                                "x": 120,
                                "y": 120,
                            },
                            {
                                "id": "c_main",
                                "type": "conclusion",
                                "title": "A conclusion",
                                "body": "The evidence points to a useful split.",
                                "x": 520,
                                "y": 120,
                            },
                            {
                                "id": "t_main",
                                "type": "task",
                                "title": "Write the next paragraph",
                                "body": "Use the conclusion.",
                                "x": 760,
                                "y": 120,
                                "status": "todo",
                                "priority": 2,
                            },
                        ],
                        "edges": [
                            {"id": "e1", "source": "q_main", "target": "c_main", "label": "leads_to"},
                            {"id": "e2", "source": "c_main", "target": "t_main", "label": "requires"},
                        ],
                    },
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["context_cards"][0]["title"], "Test Paper")
            self.assertEqual(updated.json()["active_surface"], "canvas")
            self.assertTrue(any(Path(main.BACKUPS_DIR).glob(f"{thread_id}-*.json")))

            memory = client.put(
                "/api/vnext/object-memory/G/paper/test",
                json={
                    "object_ref": {"atlas_id": "G", "object_type": "paper", "object_id": "test"},
                    "title_snapshot": "Test Paper",
                    "star": True,
                    "maturity": 4,
                    "tags": ["core"],
                    "judgement": "This is the anchor paper for the argument.",
                    "note": "Use it when framing the baseline.",
                    "core_innovation": "A compact test innovation note.",
                    "core_technology": "A structured method note.",
                    "evidence": "Evidence remains to be checked.",
                    "limitations": "Limited to the provided summary.",
                    "reusable_insight": "Use this as a reusable framing hook.",
                    "reading_status": "digested",
                    "reading_questions": ["Check the ablation table"],
                    "paper_chat": [
                        {"role": "user", "content": "What is reusable here?", "created_at": "2026-07-14T00:00:00Z"}
                    ],
                },
            )
            self.assertEqual(memory.status_code, 200)
            self.assertEqual(memory.json()["core_innovation"], "A compact test innovation note.")
            self.assertEqual(memory.json()["reading_status"], "digested")
            self.assertEqual(memory.json()["paper_chat"][0]["role"], "user")
            self.assertTrue((main.OBJECTS_DIR / "G" / "paper" / "test.json").exists())
            bad_memory = client.put(
                "/api/vnext/object-memory/G/paper/bad%2Aid",
                json={"object_ref": {}, "title_snapshot": "bad"},
            )
            self.assertEqual(bad_memory.status_code, 400)

            preview = client.post(
                f"/api/vnext/threads/{thread_id}/results/preview",
                json={
                    "raw_text": """```json
{"schema_version":"eai-result/v1","summary":"Preview result","findings":["Finding A"],"next_tasks":["Task A"]}
```"""
                },
            )
            self.assertEqual(preview.status_code, 200)
            preview_body = preview.json()
            self.assertEqual(preview_body["result_card_preview"]["title"], "Preview result")
            self.assertEqual(preview_body["canvas_nodes"][0]["type"], "conclusion")
            self.assertEqual(preview_body["canvas_nodes"][-1]["type"], "task")
            self.assertEqual(client.get(f"/api/vnext/threads/{thread_id}").json()["result_cards"], [])

            exported = client.post(
                f"/api/vnext/threads/{thread_id}/export",
                json={"selected_only": True},
            )
            self.assertEqual(exported.status_code, 200)
            body = exported.json()
            self.assertIn("EAI Task Pack：Test thread", body["markdown"])
            self.assertIn("结论: A conclusion", body["markdown"])
            self.assertIn("需要 -> 任务: Write the next paragraph", body["markdown"])
            self.assertIn("personal_judgement: This is the anchor paper", body["markdown"])
            self.assertIn("core_innovation: A compact test innovation note", body["markdown"])
            self.assertEqual(body["exported_cards"], 1)
            exported_thread = client.get(f"/api/vnext/threads/{thread_id}").json()
            self.assertEqual(exported_thread["messages"][-1]["kind"], "task_pack")
            self.assertNotIn("## 任务目标", json.dumps(exported_thread["messages"][-1], ensure_ascii=False))

            templates = client.get("/api/vnext/research-templates")
            self.assertEqual(templates.status_code, 200)
            self.assertEqual(
                [item["id"] for item in templates.json()],
                ["atlas_gap", "method_evolution", "relation_explain", "candidate_audit"],
            )

            task_pack = client.post(
                f"/api/vnext/threads/{thread_id}/task-pack/preview",
                json={
                    "template_id": "atlas_gap",
                    "focused_object": {
                        "type": "paper",
                        "id": "test",
                        "title": "Test Paper",
                        "summary": "Focused summary",
                        "source_ref": {"atlas_id": "G", "paper_id": "test"},
                    },
                    "selected_only": True,
                },
            )
            self.assertEqual(task_pack.status_code, 200)
            task_body = task_pack.json()
            self.assertEqual(task_body["template_id"], "atlas_gap")
            self.assertIn("研究空白分析", task_body["markdown"])
            self.assertIn("Test Paper", task_body["markdown"])
            self.assertIn("This is the anchor paper", task_body["markdown"])
            self.assertEqual(client.get(f"/api/vnext/threads/{thread_id}").json()["result_cards"], [])

            copy_run = client.post(
                f"/api/vnext/threads/{thread_id}/tool-runs",
                json={
                    "tool": "research_template_copy",
                    "status": "done",
                    "summary": "复制研究空白分析 Task Pack",
                    "template_id": "atlas_gap",
                    "mode": "copy",
                    "token_estimate": task_body["token_estimate"],
                    "input_summary": "1 张上下文卡片 · 聚焦 Test Paper",
                    "markdown": task_body["markdown"],
                    "api_key": "must-not-be-saved",
                },
            )
            self.assertEqual(copy_run.status_code, 200)
            copy_thread = copy_run.json()
            self.assertEqual(copy_thread["tool_runs"][0]["mode"], "copy")
            self.assertEqual(copy_thread["tool_runs"][0]["template_id"], "atlas_gap")
            self.assertEqual(copy_thread["messages"][-1]["kind"], "tool_run")
            serialized_run = json.dumps(copy_thread["tool_runs"][0], ensure_ascii=False)
            self.assertNotIn("must-not-be-saved", serialized_run)
            self.assertNotIn("## 当前研究问题", serialized_run)

            no_key = client.post(
                f"/api/vnext/threads/{thread_id}/task-pack/run",
                json={"template_id": "atlas_gap", "selected_only": True, "provider": "openai"},
            )
            self.assertEqual(no_key.status_code, 400)
            no_key_paper_chat = client.post(
                "/api/vnext/object-memory/G/paper/test/chat",
                json={
                    "message": "What is the core innovation?",
                    "paper_context": {"title": "Test Paper", "summary": "A test paper."},
                },
            )
            self.assertEqual(no_key_paper_chat.status_code, 400)
            self.assertNotIn("test-key", no_key_paper_chat.text)

            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """```json
{"schema_version":"eai-result/v1","summary":"Mock API result","findings":["API Finding"],"next_tasks":["API Task"]}
```"""
            run = client.post(
                f"/api/vnext/threads/{thread_id}/task-pack/run",
                json={"template_id": "atlas_gap", "selected_only": True, "provider": "openai"},
            )
            self.assertEqual(run.status_code, 200)
            run_body = run.json()
            self.assertEqual(run_body["tool_run"]["template_id"], "atlas_gap")
            self.assertEqual(run_body["canvas_nodes"][0]["type"], "conclusion")
            run_thread = client.get(f"/api/vnext/threads/{thread_id}").json()
            self.assertEqual(run_thread["result_cards"], [])
            self.assertEqual(run_thread["messages"][-1]["status"], "preview")

            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = """这篇论文的核心价值在于把测试对象组织成可复用的研究脚手架。

```eai-paper-reading/v1
{"core_innovation":"Mock paper innovation","core_technology":"Mock method mechanism","evidence":"Needs source verification","limitations":"Limited by summary-only context","reusable_insight":"Use it as a paper-reading scaffold","reading_questions":["Check the original experiment section"]}
```"""
            paper_chat = client.post(
                "/api/vnext/object-memory/G/paper/test/chat",
                json={
                    "message": "What is reusable here?",
                    "paper_context": {
                        "title": "Test Paper",
                        "summary": "A test paper.",
                        "route": "Test route",
                    },
                },
            )
            self.assertEqual(paper_chat.status_code, 200)
            paper_chat_body = paper_chat.json()
            self.assertEqual(paper_chat_body["paper_chat"][-2]["role"], "user")
            self.assertEqual(paper_chat_body["paper_chat"][-1]["role"], "assistant")
            self.assertEqual(paper_chat_body["core_innovation"], "Mock paper innovation")
            self.assertEqual(paper_chat_body["reading_questions"], ["Check the original experiment section"])
            serialized_memory = json.dumps(paper_chat_body, ensure_ascii=False)
            self.assertNotIn("test-key", serialized_memory)

            result = client.post(
                f"/api/vnext/threads/{thread_id}/results",
                json={"title": "Codex result", "raw_text": "Done"},
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["result_cards"][0]["raw_text"], "Done")
            self.assertEqual(result.json()["messages"][-1]["kind"], "result")

            bundle_path = main.WEB_DATA_DIR / "G.bundle.json"
            before_bundle = bundle_path.read_text(encoding="utf-8")
            update_pack = client.post(
                "/api/vnext/atlas-updates/G/task-pack/preview",
                json={"action": "recent"},
            )
            self.assertEqual(update_pack.status_code, 200)
            update_pack_body = update_pack.json()
            self.assertIn("已有论文查重清单", update_pack_body["markdown"])
            self.assertIn("candidate_papers", update_pack_body["markdown"])

            update_preview = client.post(
                "/api/vnext/atlas-updates/G/results/preview",
                json={
                    "action": "recent",
                    "raw_text": """```json
{"schema_version":"eai-atlas-update/v1","summary":"new papers","candidate_papers":[
{"id":"bad/id","title":"A Fresh Candidate Paper","authors":["A. Author"],"year":2026,"venue":"arXiv","url":"https://example.com/fresh","suggested_route_id":"foundation","why":"It fills a recent gap.","relevance":"It extends the route.","confidence":0.92},
{"title":"A Lower Confidence Candidate","year":2026,"why":"Maybe useful.","confidence":0.4}
]}
```""",
                },
            )
            self.assertEqual(update_preview.status_code, 200)
            preview_json = update_preview.json()
            self.assertEqual(len(preview_json["candidates"]), 2)
            candidate_id = preview_json["candidates"][0]["id"]
            self.assertRegex(candidate_id, r"^[A-Za-z0-9_.:-]+$")
            self.assertNotEqual(candidate_id, "bad/id")
            self.assertTrue((main.ATLAS_UPDATES_DIR / "G.json").exists())

            bad_candidate = client.put(
                "/api/vnext/atlas-updates/G/candidates/bad%2Aid",
                json={"title": "bad"},
            )
            self.assertEqual(bad_candidate.status_code, 400)

            edited = client.put(
                f"/api/vnext/atlas-updates/G/candidates/{candidate_id}",
                json={"judgement": "Worth tracking.", "tags": ["fresh", "route-gap"]},
            )
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["candidates"][0]["judgement"], "Worth tracking.")

            applied = client.post(f"/api/vnext/atlas-updates/G/candidates/{candidate_id}/apply")
            self.assertEqual(applied.status_code, 200)
            self.assertEqual(applied.json()["candidates"][0]["status"], "applied")
            memory_after_apply = client.get("/api/vnext/object-memory?atlas_id=G").json()
            self.assertTrue(any(item["object_ref"].get("object_id") == candidate_id for item in memory_after_apply))

            bulk = client.post("/api/vnext/atlas-updates/G/candidates/bulk-apply")
            self.assertEqual(bulk.status_code, 200)
            self.assertEqual(bulk.json()["applied_count"], 0)
            self.assertEqual(bundle_path.read_text(encoding="utf-8"), before_bundle)

            self.assertEqual(client.get("/api/vnext/lab-runs").status_code, 404)
            (
                main.PERSONAL_DIR,
                main.THREADS_DIR,
                main.BACKUPS_DIR,
                main.PROJECTS_DIR,
                main.OBJECTS_DIR,
                main.ATLAS_UPDATES_DIR,
                main.LAB_RUNS_DIR,
                main.SECRET_CANDIDATES,
                original_openai,
                original_mock,
            ) = original
            if original_openai is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_openai
            if original_mock is None:
                os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
            else:
                os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = original_mock
            return

            lab_run = client.post(  # Legacy fixture retained below as migration reference; no longer reachable.
                "/api/vnext/lab-runs",
                json={
                    "thread_id": thread_id,
                    "project_id": project_id,
                    "title": "Baseline reproduction",
                    "goal": "Check whether the baseline is reproducible.",
                    "hypothesis": "The baseline gap is caused by missing context.",
                    "source_refs": [{"type": "canvas_node", "id": "t_main", "api_key": "must-not-be-saved"}],
                    "linked_canvas_nodes": ["t_main"],
                },
            )
            self.assertEqual(lab_run.status_code, 200)
            lab_body = lab_run.json()
            lab_id = lab_body["id"]
            self.assertEqual(lab_body["thread_id"], thread_id)
            self.assertGreaterEqual(len(lab_body["stages"]), 5)
            self.assertTrue((main.LAB_RUNS_DIR / f"{lab_id}.json").exists())
            self.assertNotIn("must-not-be-saved", json.dumps(lab_body, ensure_ascii=False))

            listed_lab = client.get(f"/api/vnext/lab-runs?thread_id={thread_id}")
            self.assertEqual(listed_lab.status_code, 200)
            self.assertEqual(listed_lab.json()[0]["id"], lab_id)

            lab_pack = client.post(
                f"/api/vnext/lab-runs/{lab_id}/task-pack/preview",
                json={},
            )
            self.assertEqual(lab_pack.status_code, 200)
            self.assertIn("eai-lab-run/v1", lab_pack.json()["markdown"])
            self.assertIn("The baseline gap is caused by missing context.", lab_pack.json()["markdown"])
            self.assertIn("Test Paper", lab_pack.json()["markdown"])

            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
            lab_run_no_key = client.post(
                f"/api/vnext/lab-runs/{lab_id}/task-pack/run",
                json={"provider": "openai"},
            )
            self.assertEqual(lab_run_no_key.status_code, 400)

            lab_preview = client.post(
                f"/api/vnext/lab-runs/{lab_id}/results/preview",
                json={
                    "raw_text": """```json
{"schema_version":"eai-lab-run/v1","summary":"Lab preview","stages":[{"title":"基线复现","status":"completed","summary":"Baseline reproduced.","command":"python train.py"}],"artifacts":[{"type":"log","title":"training log","summary":"loss decreased","content_preview":"epoch=1 loss=0.5","stage_title":"基线复现"}],"findings":[{"title":"Baseline is stable","body":"The reproduction path is usable.","confidence":"preliminary"}],"next_tasks":[{"title":"Run ablation","body":"Disable the context module."}]}
```"""
                },
            )
            self.assertEqual(lab_preview.status_code, 200)
            lab_preview_body = lab_preview.json()
            self.assertEqual(lab_preview_body["stages"][0]["status"], "completed")
            self.assertEqual(lab_preview_body["canvas_nodes"][0]["type"], "conclusion")
            self.assertEqual(
                [item["id"] for item in lab_preview_body["apply_items"]],
                ["stage:0", "artifact:0", "finding:0", "task:0"],
            )
            self.assertEqual(client.get(f"/api/vnext/lab-runs/{lab_id}").json()["findings"], [])

            lab_path = main.LAB_RUNS_DIR / f"{lab_id}.json"
            thread_path = main.THREADS_DIR / f"{thread_id}.json"
            before_invalid_lab = lab_path.read_bytes()
            before_invalid_thread = thread_path.read_bytes()
            invalid_selection = client.post(
                f"/api/vnext/lab-runs/{lab_id}/results/confirm",
                json={
                    "raw_text": lab_preview_body["result_card_preview"]["raw_text"],
                    "selected_item_ids": ["finding:99"],
                },
            )
            self.assertEqual(invalid_selection.status_code, 400)
            self.assertEqual(lab_path.read_bytes(), before_invalid_lab)
            self.assertEqual(thread_path.read_bytes(), before_invalid_thread)

            selected_confirm = client.post(
                f"/api/vnext/lab-runs/{lab_id}/results/confirm",
                json={
                    "raw_text": lab_preview_body["result_card_preview"]["raw_text"],
                    "selected_item_ids": ["finding:0"],
                },
            )
            self.assertEqual(selected_confirm.status_code, 200)
            selected_lab = selected_confirm.json()
            self.assertEqual(selected_lab["findings"][0]["title"], "Baseline is stable")
            self.assertEqual(selected_lab["artifacts"], [])
            selected_thread = client.get(f"/api/vnext/threads/{thread_id}").json()
            selected_titles = {node["title"] for node in selected_thread["canvas"]["nodes"]}
            self.assertIn("Baseline is stable", selected_titles)
            self.assertNotIn("training log", selected_titles)
            self.assertNotIn("Run ablation", selected_titles)
            selected_result_raw = selected_thread["result_cards"][0]["raw_text"]
            self.assertIn("Baseline is stable", selected_result_raw)
            self.assertNotIn("training log", selected_result_raw)
            self.assertNotIn("Run ablation", selected_result_raw)

            before_rollback_lab = lab_path.read_bytes()
            before_rollback_thread = thread_path.read_bytes()
            with patch.object(main, "write_thread", side_effect=RuntimeError("forced thread failure")):
                with self.assertRaises(RuntimeError):
                    client.post(
                        f"/api/vnext/lab-runs/{lab_id}/results/confirm",
                        json={
                            "raw_text": lab_preview_body["result_card_preview"]["raw_text"],
                            "selected_item_ids": ["task:0"],
                        },
                    )
            self.assertEqual(lab_path.read_bytes(), before_rollback_lab)
            self.assertEqual(thread_path.read_bytes(), before_rollback_thread)

            lab_confirm = client.post(
                f"/api/vnext/lab-runs/{lab_id}/results/confirm",
                json={"raw_text": lab_preview_body["result_card_preview"]["raw_text"]},
            )
            self.assertEqual(lab_confirm.status_code, 200)
            confirmed_lab = lab_confirm.json()
            self.assertEqual(confirmed_lab["findings"][0]["title"], "Baseline is stable")
            self.assertEqual(confirmed_lab["artifacts"][0]["title"], "training log")
            thread_after_lab = client.get(f"/api/vnext/threads/{thread_id}").json()
            self.assertTrue(any(node["type"] == "conclusion" and node["title"] == "Baseline is stable" for node in thread_after_lab["canvas"]["nodes"]))

            os.environ["OPENAI_API_KEY"] = "test-key"
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = lab_preview_body["result_card_preview"]["raw_text"]
            lab_api = client.post(
                f"/api/vnext/lab-runs/{lab_id}/task-pack/run",
                json={"provider": "openai"},
            )
            self.assertEqual(lab_api.status_code, 200)
            self.assertEqual(lab_api.json()["message"]["status"], "preview")
            serialized_lab_api = json.dumps(lab_api.json(), ensure_ascii=False)
            self.assertNotIn("test-key", serialized_lab_api)
            self.assertEqual(bundle_path.read_text(encoding="utf-8"), before_bundle)

        (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
            main.SECRET_CANDIDATES,
            original_openai,
            original_mock,
        ) = original
        if original_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_openai
        if original_mock is None:
            os.environ.pop("EAI_VNEXT_MOCK_OPENAI_RESPONSE", None)
        else:
            os.environ["EAI_VNEXT_MOCK_OPENAI_RESPONSE"] = original_mock

    def test_changeset_confirm_conflict_and_safe_undo(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = main.PERSONAL_DIR / "threads"
            main.BACKUPS_DIR = main.PERSONAL_DIR / "backups"
            main.PROJECTS_DIR = main.PERSONAL_DIR / "projects"
            main.OBJECTS_DIR = main.PERSONAL_DIR / "objects"
            main.ATLAS_UPDATES_DIR = main.PERSONAL_DIR / "atlas_updates"
            main.LAB_RUNS_DIR = main.PERSONAL_DIR / "lab_runs"
            main.ensure_dirs()
            try:
                client = TestClient(main.app)
                created = client.post("/api/vnext/threads", json={"title": "ChangeSet test", "active_atlas_id": "G"})
                self.assertEqual(created.status_code, 200)
                thread_id = created.json()["id"]
                doc = main.load_thread(thread_id)
                changeset = main.build_changeset_from_raw(
                    doc,
                    "agent-run-test",
                    {
                        "summary": "补充论文判断",
                        "operations": [
                            {
                                "target_type": "object_memory",
                                "target": {"atlas_id": "G", "object_type": "paper", "object_id": "paper-a", "title": "Paper A"},
                                "op": "replace",
                                "path": "/judgement",
                                "after": "经过确认的个人判断",
                                "reason": "验证字段级写入",
                            }
                        ],
                    },
                )
                self.assertIsNotNone(changeset)
                doc.changesets = [changeset]
                doc = main.write_thread(doc)

                confirmed = client.post(
                    f"/api/vnext/threads/{thread_id}/changesets/{changeset.id}/confirm",
                    json={"selected_operation_ids": [changeset.operations[0].id], "expected_revision": doc.revision},
                )
                self.assertEqual(confirmed.status_code, 200)
                self.assertEqual(confirmed.json()["changeset"]["status"], "applied")
                memory_path = main.OBJECTS_DIR / "G" / "paper" / "paper-a.json"
                self.assertEqual(json.loads(memory_path.read_text(encoding="utf-8"))["judgement"], "经过确认的个人判断")

                undone = client.post(f"/api/vnext/threads/{thread_id}/changesets/{changeset.id}/undo", json={})
                self.assertEqual(undone.status_code, 200)
                self.assertEqual(undone.json()["changeset"]["status"], "undone")
                self.assertEqual(json.loads(memory_path.read_text(encoding="utf-8"))["judgement"], "")

                doc = main.load_thread(thread_id)
                conflict_set = main.build_changeset_from_raw(
                    doc,
                    "agent-run-conflict",
                    {
                        "summary": "冲突测试",
                        "operations": [
                            {
                                "target_type": "object_memory",
                                "target": {"atlas_id": "G", "object_type": "paper", "object_id": "paper-a"},
                                "path": "/judgement",
                                "after": "Agent 建议",
                            }
                        ],
                    },
                )
                doc.changesets.insert(0, conflict_set)
                doc = main.write_thread(doc)
                memory = main.load_object_memory("G", "paper", "paper-a")
                memory.judgement = "用户运行中手动修改"
                main.write_object_memory("G", "paper", "paper-a", memory)
                conflicted = client.post(
                    f"/api/vnext/threads/{thread_id}/changesets/{conflict_set.id}/confirm",
                    json={"expected_revision": doc.revision},
                )
                self.assertEqual(conflicted.status_code, 409)
                self.assertEqual(main.load_object_memory("G", "paper", "paper-a").judgement, "用户运行中手动修改")
                self.assertTrue(any((main.PERSONAL_DIR / "transactions").glob("*.json")))
            finally:
                (
                    main.PERSONAL_DIR,
                    main.THREADS_DIR,
                    main.BACKUPS_DIR,
                    main.PROJECTS_DIR,
                    main.OBJECTS_DIR,
                    main.ATLAS_UPDATES_DIR,
                    main.LAB_RUNS_DIR,
                ) = original

    def test_delete_project_reassigns_threads_and_delete_thread_backs_up(self):
        original = (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main.PERSONAL_DIR = tmp_path / "personal"
            main.THREADS_DIR = tmp_path / "personal" / "threads"
            main.BACKUPS_DIR = tmp_path / "personal" / "backups"
            main.PROJECTS_DIR = tmp_path / "personal" / "projects"
            main.OBJECTS_DIR = tmp_path / "personal" / "objects"
            main.ATLAS_UPDATES_DIR = tmp_path / "personal" / "atlas_updates"
            main.LAB_RUNS_DIR = tmp_path / "personal" / "lab_runs"
            main.ensure_dirs()

            client = TestClient(main.app)
            project = client.post(
                "/api/vnext/projects",
                json={"title": "Disposable project", "goal": "Temporary", "default_atlas_id": "G"},
            )
            self.assertEqual(project.status_code, 200)
            project_id = project.json()["id"]

            created = client.post(
                "/api/vnext/threads",
                json={
                    "title": "Disposable thread",
                    "goal": "Temporary context",
                    "active_atlas_id": "G",
                    "project_id": project_id,
                },
            )
            self.assertEqual(created.status_code, 200)
            thread_id = created.json()["id"]

            deleted_project = client.delete(f"/api/vnext/projects/{project_id}")
            self.assertEqual(deleted_project.status_code, 200)
            self.assertEqual(deleted_project.json()["reassigned_threads"], 1)
            self.assertFalse((main.PROJECTS_DIR / f"{project_id}.json").exists())
            self.assertIsNone(client.get(f"/api/vnext/threads/{thread_id}").json()["project_id"])
            self.assertTrue(any(Path(main.BACKUPS_DIR).glob(f"deleted-project-{project_id}-*.json")))

            deleted_thread = client.delete(f"/api/vnext/threads/{thread_id}")
            self.assertEqual(deleted_thread.status_code, 200)
            self.assertFalse((main.THREADS_DIR / f"{thread_id}.json").exists())
            self.assertTrue(any(Path(main.BACKUPS_DIR).glob(f"deleted-thread-{thread_id}-*.json")))
            self.assertEqual(client.get(f"/api/vnext/threads/{thread_id}").status_code, 404)

        (
            main.PERSONAL_DIR,
            main.THREADS_DIR,
            main.BACKUPS_DIR,
            main.PROJECTS_DIR,
            main.OBJECTS_DIR,
            main.ATLAS_UPDATES_DIR,
            main.LAB_RUNS_DIR,
        ) = original


if __name__ == "__main__":
    unittest.main()
