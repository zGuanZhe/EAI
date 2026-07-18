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
from app.composition import create_application
from app.core.config import ApplicationConfig
from app.core.paths import RuntimePaths


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
        agent_v3_paths = {
            "/api/vnext/agent/capabilities",
            "/api/vnext/agent/tasks/{task_id}/ui-commands",
            "/api/vnext/agent/ui-commands/{command_id}/resolve",
            "/api/vnext/research-tasks/{task_id}",
            "/api/vnext/research-tasks/{task_id}/cancel",
            "/api/vnext/research-tasks/{task_id}/events",
            "/api/vnext/research-tasks/{task_id}/pause",
            "/api/vnext/research-tasks/{task_id}/promote-campaign",
            "/api/vnext/research-tasks/{task_id}/resume",
            "/api/vnext/research-tasks/{task_id}/steer",
            "/api/vnext/search-connectors/status",
            "/api/vnext/search-connectors/{connector_id}/check",
            "/api/vnext/threads/{thread_id}/agent-context/preview",
            "/api/vnext/threads/{thread_id}/agent/asks",
            "/api/vnext/threads/{thread_id}/research-tasks",
        }
        self.assertTrue(agent_v3_paths.issubset(schema["paths"]))
        legacy_operations = {
            path: sorted(method for method in operations if method in {
                "get", "post", "put", "delete", "patch", "options", "head",
            })
            for path, operations in schema["paths"].items()
            if path not in agent_v3_paths
        }
        legacy_serialized = json.dumps(
            legacy_operations, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(len(legacy_operations), 107)
        self.assertEqual(sum(len(operations) for operations in legacy_operations.values()), 116)
        self.assertEqual(
            hashlib.sha256(legacy_serialized.encode("utf-8")).hexdigest(),
            "e6395d9c1486b2a37d4ed6c04c84a38ac61656a3a59e202cb540dda23730144e",
        )
        self.assertEqual(len(schema["paths"]), 122)
        self.assertEqual(sum(len(operations) for operations in schema["paths"].values()), 132)
        self.assertEqual(
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "1c4e552e0ba33a094e722c8c75bca9f1503a97f027600b0c487cf91e773e1dba",
        )

    def test_application_factory_uses_isolated_immutable_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            personal = root / "personal"
            paths = RuntimePaths(
                root=root,
                atlas_cache_dir=main.WEB_DATA_DIR,
                personal_dir=personal,
                threads_dir=personal / "threads",
                backups_dir=personal / "backups",
                projects_dir=personal / "projects",
                objects_dir=personal / "objects",
                atlas_updates_dir=personal / "atlas_updates",
                lab_runs_dir=personal / "lab_runs",
            )
            config = ApplicationConfig.for_paths(
                paths,
                secret_paths=(root / "missing-secrets.json",),
            )
            isolated_app = create_application(config)
            with TestClient(isolated_app) as client:
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Isolated thread", "active_atlas_id": "I"},
                )
                self.assertEqual(created.status_code, 200, created.text)
                info = client.get("/api/vnext/system/info").json()
                self.assertEqual(Path(info["personal_dir"]), personal)
            self.assertTrue((personal / "threads" / f"{created.json()['id']}.json").exists())

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
                main.reset_app_services()
                main.ensure_dirs()

                client = TestClient(main.app)
                created = client.post(
                    "/api/vnext/threads",
                    json={"title": "Future schema thread", "active_atlas_id": "I"},
                )
                self.assertEqual(created.status_code, 200, created.text)
                thread = created.json()
                main.get_agent_v2_runtime()
                main.reset_app_services()

                research_db = root / "research" / "research.db"
                connection = sqlite3.connect(research_db)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, summary) VALUES(5, 'future', 'future schema')"
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

                main.reset_app_services()
                self.assertEqual(research_db.read_bytes(), research_before)
                self.assertEqual(runtime_db.read_bytes(), runtime_before)
            finally:
                main.reset_app_services()
                for name, value in original.items():
                    setattr(main, name, value)

    def test_newer_runtime_schema_forces_whole_application_read_only(self):
        original = {
            "PERSONAL_DIR": main.PERSONAL_DIR,
            "THREADS_DIR": main.THREADS_DIR,
            "BACKUPS_DIR": main.BACKUPS_DIR,
            "PROJECTS_DIR": main.PROJECTS_DIR,
            "OBJECTS_DIR": main.OBJECTS_DIR,
            "ATLAS_UPDATES_DIR": main.ATLAS_UPDATES_DIR,
            "LAB_RUNS_DIR": main.LAB_RUNS_DIR,
            "RUNTIME_V2_DIR": main.RUNTIME_V2_DIR,
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
                main.reset_app_services()
                main.ensure_dirs()
                client = TestClient(main.app)
                thread = client.post(
                    "/api/vnext/threads",
                    json={"title": "Runtime bridge thread", "active_atlas_id": "I"},
                ).json()
                main.get_agent_v2_runtime()
                main.reset_app_services()

                research_db = root / "research" / "research.db"
                runtime_db = root / "runtime" / "runtime.db"
                connection = sqlite3.connect(runtime_db)
                connection.execute(
                    "UPDATE runtime_meta SET value='3' WHERE key='schema_version'"
                )
                connection.commit()
                connection.close()
                research_before = research_db.read_bytes()
                runtime_before = runtime_db.read_bytes()

                read_only_client = TestClient(main.app)
                info = read_only_client.get("/api/vnext/system/info")
                self.assertEqual(info.status_code, 200, info.text)
                self.assertTrue(info.json()["research_store"]["read_only"])
                self.assertEqual(
                    info.json()["research_store"]["reason"],
                    "runtime_schema_newer_than_app",
                )
                loaded = read_only_client.get(f"/api/vnext/threads/{thread['id']}")
                self.assertEqual(loaded.status_code, 200, loaded.text)
                blocked = [
                    read_only_client.put(
                        f"/api/vnext/threads/{thread['id']}",
                        json={"title": "Blocked", "expected_revision": thread["revision"]},
                    ),
                    read_only_client.post(
                        f"/api/vnext/threads/{thread['id']}/agent/asks",
                        json={"message": "This must not start"},
                    ),
                ]
                for response in blocked:
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "schema_newer_than_app")
                main.reset_app_services()
                self.assertEqual(research_db.read_bytes(), research_before)
                self.assertEqual(runtime_db.read_bytes(), runtime_before)
            finally:
                main.reset_app_services()
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
                replay = client.get(
                    "/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/events?after_seq=7"
                )
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertTrue(replay.headers["content-type"].startswith("text/event-stream"))
                replayed_events = self.parse_sse_events(replay.text)
                self.assertEqual(replayed_events[-1][0], "done")
                self.assertGreater(replayed_events[-1][1]["seq"], 7)

                before = target.read_bytes()
                legacy_mutations = [
                    ("/api/vnext/threads/thread-legacy-fixture/chat", {"message": "blocked"}, "旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口"),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/start", {"message": "blocked"}, "旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口"),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/complete", {"assistant_message_id": "message-legacy-assistant"}, "旧 chat 写接口已停用，请使用 Agent Runtime v2 turn 接口"),
                    ("/api/vnext/threads/thread-legacy-fixture/chat/message-legacy-assistant/retry", {}, "旧 chat 重试接口已停用，请使用 Agent Runtime v2 resume 接口"),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/start", {"message": "blocked"}, "Agent v1 已转为只读历史，请使用 Agent Runtime v2"),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/complete", {}, "Agent v1 已转为只读历史，请使用 Agent Runtime v2"),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/stream", {}, "Agent v1 已转为只读历史，请使用 Agent Runtime v2"),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/retry", {}, "Agent v1 已转为只读历史，请使用 Agent Runtime v2 resume 接口"),
                    ("/api/vnext/threads/thread-legacy-fixture/agent-runs/agent-run-legacy-fixture/cancel", {}, "Agent v1 已转为只读历史"),
                ]
                for path, payload, detail in legacy_mutations:
                    response = client.post(path, json=payload)
                    self.assertEqual(response.status_code, 410, f"{path}: {response.text}")
                    self.assertEqual(response.json(), {"detail": detail})
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




    def test_atlas_bundle_reads_existing_cache(self):
        client = TestClient(main.app)
        response = client.get("/api/vnext/atlases/G/bundle")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["atlas"]["id"], "G")
        self.assertGreater(len(data["papers"]), 0)
        self.assertGreater(len(data["relations"]), 0)





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
                changeset = main.get_change_review_service().build_changeset_from_raw(
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
                conflict_set = main.get_change_review_service().build_changeset_from_raw(
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
                conflict_detail = conflicted.json()["detail"]
                self.assertEqual(conflict_detail["message"], "变更目标已发生变化")
                self.assertEqual(conflict_detail["conflicts"][0]["current"], "用户运行中手动修改")
                self.assertEqual(conflict_detail["conflicts"][0]["expected"], "")
                self.assertEqual(main.load_object_memory("G", "paper", "paper-a").judgement, "用户运行中手动修改")
                self.assertEqual(list((main.PERSONAL_DIR / "transactions").glob("*.json")), [])
                self.assertEqual(main.get_research_store().projection_status().get("failed", 0), 0)
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
