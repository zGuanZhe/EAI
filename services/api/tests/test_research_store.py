from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import time
import fitz
from pathlib import Path
from unittest.mock import patch

from app.research.documents import import_document
from app.research.enrichment import KnowledgeEnrichmentService
from app.research.store import ResearchStore
from app.core.errors import SchemaReadOnlyError
from app.services.projection import ProjectionService
from app.services.container import AppServices
from app.research.normalization import normalize_with_map
from app.research.page_preview import PagePreviewService


ROOT = Path(__file__).resolve().parents[3]
ATLAS_DIR = ROOT / "resources" / "atlas-cache"


class ResearchStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.personal_dir = root / "personal"
        cls.store = ResearchStore(root / "research", ATLAS_DIR, cls.personal_dir)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.temp.cleanup()

    def test_curated_atlas_import_preserves_exact_graph_counts(self):
        status = self.store.status()
        self.assertEqual(status.counts["works"], 686)
        self.assertEqual(status.counts["placements"], 907)
        self.assertEqual(status.counts["routes"], 113)
        self.assertEqual(status.counts["relations"], 1556)
        self.assertEqual(self.store.import_atlas_snapshots(), {
            "works": 686, "placements": 907, "routes": 113, "relations": 1556,
        })
        self.assertTrue(self.store.integrity_check()["ok"])

    def test_newer_schema_reopens_read_only_and_preserves_database_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            research_dir = root / "research"
            personal_dir = root / "personal"
            store = ResearchStore(research_dir, ATLAS_DIR, personal_dir)
            store.save_record("thread", "thread-newer", {"id": "thread-newer", "title": "Readable"})
            store.close()

            database = research_dir / "research.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, summary) VALUES(5, 'future', 'future schema')"
            )
            connection.commit()
            connection.close()
            before = database.read_bytes()

            read_only = ResearchStore(research_dir, ATLAS_DIR, personal_dir)
            self.assertEqual(
                read_only.compatibility_status(),
                {
                    "schema_version": 5,
                    "supported_schema_version": 4,
                    "read_only": True,
                    "reason": "schema_newer_than_app",
                },
            )
            self.assertEqual(read_only.get_record("thread", "thread-newer")["title"], "Readable")
            with self.assertRaises(SchemaReadOnlyError):
                read_only.save_record("thread", "thread-newer", {"id": "thread-newer", "title": "Blocked"})
            read_only.close()

            self.assertEqual(database.read_bytes(), before)

    def test_app_services_rebuilds_store_when_data_root_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atlas = root / "atlas"
            atlas.mkdir()
            services = AppServices()
            first = services.research_store(root / "first" / "research", atlas, root / "first" / "personal")
            first.save_record("thread", "thread-first", {"id": "thread-first", "title": "First"})
            first_database = first.db_path

            second = services.research_store(root / "second" / "research", atlas, root / "second" / "personal")
            self.assertIsNot(first, second)
            self.assertIsNone(second.get_record("thread", "thread-first"))
            services.close()

            connection = sqlite3.connect(first_database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM research_records").fetchone()[0], 1)
            connection.close()

    def test_projection_outbox_commits_canonical_state_and_replays_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            personal = root / "personal"
            store = ResearchStore(root / "research", ATLAS_DIR, personal)
            projector = ProjectionService(store, personal)
            payload = {"id": "thread-outbox", "title": "Canonical", "revision": 1}
            store.save_record("thread", "thread-outbox", payload, projection_target="threads/thread-outbox.json")

            with patch("app.services.projection.atomic_write_json_file", side_effect=OSError("disk unavailable")):
                failed = projector.replay(entity_kind="thread", entity_id="thread-outbox")
            self.assertEqual(failed["failed"], 1)
            self.assertEqual(store.get_record("thread", "thread-outbox")["title"], "Canonical")
            self.assertFalse((personal / "threads" / "thread-outbox.json").exists())
            self.assertEqual(store.projection_status()["failed"], 1)

            replayed = projector.replay(entity_kind="thread", entity_id="thread-outbox")
            self.assertEqual(replayed["applied"], 1)
            self.assertEqual(
                json.loads((personal / "threads" / "thread-outbox.json").read_text(encoding="utf-8"))["title"],
                "Canonical",
            )
            self.assertEqual(store.projection_status()["applied"], 1)

            updated = {**payload, "title": "Newer", "revision": 2}
            store.save_record("thread", "thread-outbox", updated, projection_target="threads/thread-outbox.json")
            store.save_record("thread", "thread-outbox", updated, projection_target="threads/thread-outbox.json")
            jobs = store.list_projection_jobs(entity_kind="thread", entity_id="thread-outbox")
            self.assertEqual(len(jobs), 1)
            projector.replay(entity_kind="thread", entity_id="thread-outbox")
            self.assertEqual(
                json.loads((personal / "threads" / "thread-outbox.json").read_text(encoding="utf-8"))["title"],
                "Newer",
            )

            store.delete_record("thread", "thread-outbox", projection_target="threads/thread-outbox.json")
            projector.replay(entity_kind="thread", entity_id="thread-outbox")
            self.assertFalse((personal / "threads" / "thread-outbox.json").exists())

            recreated = {**payload, "title": "Recreated", "revision": 1}
            store.save_record("thread", "thread-outbox", recreated, projection_target="threads/thread-outbox.json")
            projector.replay(entity_kind="thread", entity_id="thread-outbox")
            self.assertEqual(
                json.loads((personal / "threads" / "thread-outbox.json").read_text(encoding="utf-8"))["title"],
                "Recreated",
            )
            store.close()

    def test_projection_replay_never_applies_failed_old_revision_after_newer_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            personal = root / "personal"
            store = ResearchStore(root / "research", ATLAS_DIR, personal)
            projector = ProjectionService(store, personal)
            target = "threads/out-of-order.json"
            store.save_record("thread", "out-of-order", {"id": "out-of-order", "title": "Old"}, projection_target=target)
            with patch("app.services.projection.atomic_write_json_file", side_effect=OSError("disk unavailable")):
                projector.replay(entity_kind="thread", entity_id="out-of-order")
            store.save_record("thread", "out-of-order", {"id": "out-of-order", "title": "New"}, projection_target=target)
            result = projector.replay(entity_kind="thread", entity_id="out-of-order")
            self.assertEqual(result["superseded"], 1)
            self.assertEqual(json.loads((personal / target).read_text(encoding="utf-8"))["title"], "New")
            store.close()

    def test_pdf_locator_normalizes_text_and_enforces_render_limits(self):
        normalized, mapping = normalize_with_map("Ａgent\u00ad work-\nflow 证 据")
        self.assertEqual(normalized, "agent workflow 证据")
        self.assertTrue(mapping)

        pdf = fitz.open()
        page = pdf.new_page(width=612, height=792)
        quote = "Evidence locator verifies this exact quote."
        page.insert_text((72, 100), quote)
        payload = pdf.tobytes()
        pdf.close()

        work = self.store.search("QT-Opt", atlas_ids=["I"], limit=1).works[0]
        document = import_document(
            self.store, file_name="locator.pdf", media_type="application/pdf", content=payload,
            work_id=work.id, source_kind="user", access="local", evictable=False,
        )
        chunk = self.store.search_document_chunks("Evidence locator", limit=1)[0]
        claim = self.store.save_claim_with_evidence(
            work_id=work.id, predicate="has_locator", text="Evidence has an original page locator.",
            chunk_id=chunk["chunk_id"], quote=quote,
        )
        evidence_id = claim.evidence_ids[0]
        preview = PagePreviewService(self.store)
        locator = preview.locator(evidence_id)
        self.assertEqual(locator["status"], "verified")
        self.assertEqual(locator["page"], 1)
        self.assertTrue(locator["rects"])
        image, _ = preview.render(document["id"], 1, 144)
        self.assertTrue(image.startswith(b"\x89PNG"))
        cached_before = set(preview.cache_dir.rglob("*.png"))

        def slow_renderer(_path: Path, _page_number: int, _dpi: int) -> bytes:
            time.sleep(0.06)
            return b"\x89PNG-late-result"

        timed_preview = PagePreviewService(
            self.store, render_timeout_seconds=0.01, render_page=slow_renderer,
        )
        try:
            with self.assertRaisesRegex(TimeoutError, "time budget"):
                timed_preview.render(document["id"], 1, 145)
            time.sleep(0.08)
            self.assertEqual(set(preview.cache_dir.rglob("*.png")), cached_before)
        finally:
            timed_preview.close()
        with self.assertRaisesRegex(ValueError, "DPI"):
            preview.render(document["id"], 1, 181)
        with self.assertRaisesRegex(ValueError, "outside"):
            preview.render(document["id"], 2, 144)
        with self.assertRaises(FileNotFoundError):
            preview._document_path({**document, "blob_path": str(Path(self.temp.name) / "escape.pdf")})

    def test_identity_resolution_and_graph_neighborhood(self):
        work = next(item for item in self.store.works_for_sync() if item["identifiers"].get("arxiv"))
        arxiv_id = work["identifiers"]["arxiv"][0]
        resolved = self.store.resolve_work(f"https://arxiv.org/abs/{arxiv_id}v2")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, work["id"])
        neighborhood = self.store.graph_neighborhood(work["id"], depth=1)
        self.assertTrue(any(item["id"] == work["id"] for item in neighborhood["works"]))

    def test_exact_identifier_merges_cross_atlas_placements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atlas = root / "atlas"
            atlas.mkdir()
            for atlas_id, paper_id in [("A", "preprint"), ("B", "published")]:
                payload = {
                    "atlas": {"id": atlas_id, "title": atlas_id},
                    "routes": [{"id": f"route-{atlas_id}", "name": "route"}],
                    "papers": [{"id": paper_id, "title": f"Shared work {paper_id}", "doi": "10.1000/shared"}],
                    "entries": [{"id": f"placement-{atlas_id}", "paper_id": paper_id, "route_id": f"route-{atlas_id}"}],
                    "relations": [],
                }
                (atlas / f"{atlas_id}.bundle.json").write_text(json.dumps(payload), encoding="utf-8")
            store = ResearchStore(root / "research", atlas, root / "personal")
            try:
                status = store.status()
                self.assertEqual(status.counts["works"], 1)
                self.assertEqual(status.counts["placements"], 2)
                self.assertEqual(store.resolve_work("10.1000/shared").id, store.resolve_work("preprint").id)
                self.assertEqual(store.resolve_work("published").id, store.resolve_work("preprint").id)
            finally:
                store.close()

    def test_personal_fields_override_curated_and_derived_without_deleting_conflicts(self):
        bundle = self.store.search("QT-Opt", atlas_ids=["I"], limit=3)
        self.assertTrue(bundle.works)
        work = bundle.works[0]
        self.store.apply_derived_metadata(
            work.id, {"title": "Derived title", "abstract": "Derived abstract", "confidence": 0.99}, provider="test"
        )
        curated = self.store.get_work(work.id)
        self.assertNotEqual(curated.title, "Derived title")
        personal = self.store.apply_personal_work_fields(
            work.id, {"title": "My reviewed title"}, source_ref="test:review", expected_revision=curated.revision
        )
        self.assertEqual(personal.title, "My reviewed title")
        self.assertEqual(personal.field_sources["title"][0]["layer"], "personal")
        conflicts = self.store.search("My reviewed title", work_ids=[work.id], limit=1).conflicts
        self.assertTrue(any(item["field"] == "title" for item in conflicts))
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.store.apply_personal_work_fields(
                work.id, {"title": "Stale edit"}, source_ref="test:stale", expected_revision=curated.revision
            )

    def test_claim_requires_a_real_quote_and_resolves_to_chunk(self):
        work = self.store.search("QT-Opt", atlas_ids=["I"], limit=1).works[0]
        body = "Results\nThe policy improves task success by twelve percent on the held-out benchmark."
        document = import_document(
            self.store, file_name="result.md", media_type="text/markdown", content=body.encode(),
            work_id=work.id, source_kind="user", access="local", evictable=False,
        )
        chunk = self.store.search_document_chunks("held-out benchmark", limit=1)[0]
        claim = self.store.save_claim_with_evidence(
            work_id=work.id, predicate="improves_metric", text="The policy improves held-out success.",
            chunk_id=chunk["chunk_id"], quote="improves task success by twelve percent",
        )
        claims, evidence = self.store.claims_for_work(work.id)
        span = next(item for item in evidence if item.id in claim.evidence_ids)
        self.assertEqual(span.document_id, document["id"])
        self.assertEqual(span.evidence_level, "full_text")
        self.assertEqual(span.page, 1)
        with self.assertRaisesRegex(ValueError, "does not occur"):
            self.store.save_claim_with_evidence(
                work_id=work.id, predicate="unsupported", text="Unsupported output",
                chunk_id=chunk["chunk_id"], quote="a sentence that is not in the document",
            )

    def test_personal_records_project_to_research_state_graph(self):
        thread = {
            "id": "thread_state_test", "title": "State graph", "goal": "Verify a hypothesis", "revision": 3,
            "project_id": "project_state_test", "canvas": {
                "nodes": [
                    {"id": "q1", "type": "question", "title": "What changes?"},
                    {"id": "h1", "type": "hypothesis", "title": "Method A improves robustness"},
                ],
                "edges": [{"id": "e1", "source": "q1", "target": "h1", "type": "supports"}],
            },
        }
        self.store.save_record("thread", thread["id"], thread)
        state = self.store.get_research_state(thread_id=thread["id"])
        types = {item["entity_type"] for item in state["entities"]}
        self.assertTrue({"thread", "question", "hypothesis"}.issubset(types))
        self.assertTrue(any(item["predicate"] == "supports" for item in state["edges"]))

    def test_json_migration_is_idempotent_and_preserves_object_memory_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            personal = root / "personal"
            object_path = personal / "objects" / "I" / "paper" / "paper-1.json"
            object_path.parent.mkdir(parents=True)
            object_path.write_text(json.dumps({"title_snapshot": "Paper one", "judgement": "Keep"}), encoding="utf-8")
            thread_path = personal / "threads" / "thread-1.json"
            thread_path.parent.mkdir(parents=True)
            thread_path.write_text(json.dumps({"id": "thread-1", "title": "Imported", "canvas": {}}), encoding="utf-8")
            store = ResearchStore(root / "research", root / "empty-atlas", personal)
            try:
                self.assertEqual(store.get_record("object_memory", "I:paper:paper-1")["judgement"], "Keep")
                self.assertEqual(store.import_personal_snapshot()["records"], 2)
                self.assertEqual(len(store.list_records("thread")), 1)
                self.assertTrue(any((root / "research" / "migration-backups").iterdir()))
            finally:
                store.close()

    def test_cache_evicts_only_recoverable_documents(self):
        user = import_document(
            self.store, file_name="user-note.txt", media_type="text/plain", content=b"user-owned-content-unique",
            source_kind="user", access="local", evictable=False,
        )
        cached = import_document(
            self.store, file_name="cached-note.txt", media_type="text/plain", content=b"recoverable-cache-content-unique",
            source_kind="open_access", access="open", evictable=True,
        )
        result = self.store.enforce_cache_limit(limit_bytes=Path(user["blob_path"]).stat().st_size)
        self.assertTrue(Path(user["blob_path"]).exists())
        self.assertFalse(Path(cached["blob_path"]).exists())
        self.assertIn(cached["id"], result["evicted_document_ids"])

    def test_chinese_query_uses_cross_language_alias_fallback(self):
        result = self.store.search("强化学习后训练", atlas_ids=["I"], limit=10)
        self.assertTrue(result.works)
        titles = " ".join(item.title.lower() for item in result.works)
        self.assertTrue(any(term in titles for term in ["reinforcement", "rlhf", "qt-opt", "rlpd"]))

    def test_openalex_and_crossref_metadata_keep_evidence_levels_separate(self):
        responses = {
            "api.openalex.org": {
                "id": "https://openalex.org/W123", "display_name": "OpenAlex title", "publication_year": 2025,
                "authorships": [{"author": {"display_name": "Ada Researcher"}}],
                "doi": "https://doi.org/10.1000/example", "abstract_inverted_index": {"robot": [0], "policy": [1]},
                "primary_location": {"source": {"display_name": "Robotics Journal"}, "pdf_url": None},
                "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
            },
            "api.crossref.org": {"message": {
                "DOI": "10.1000/example", "title": ["Crossref title"], "container-title": ["Proceedings"],
                "author": [{"given": "Grace", "family": "Hopper"}], "published-online": {"date-parts": [[2024, 1, 2]]},
                "abstract": "<jats:p>Verified abstract.</jats:p>",
            }},
        }

        class Response:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): return None
            def json(self): return self.payload

        class Client:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def get(self, url): return Response(next(payload for host, payload in responses.items() if host in url))

        service = KnowledgeEnrichmentService(self.store, client_factory=Client)
        openalex = service._fetch_openalex("10.1000/example", is_doi=True)
        crossref = service._fetch_crossref("10.1000/example")
        self.assertEqual(openalex["openalex"], "W123")
        self.assertEqual(openalex["abstract"], "robot policy")
        self.assertEqual(crossref["year"], 2024)
        self.assertEqual(crossref["abstract"], "Verified abstract.")

    def test_running_sync_job_is_resumed_after_service_restart(self):
        job = self.store.create_sync_job("metadata", [], [])
        job.status = "running"
        self.store.save_sync_job(job)

        class ResumingService(KnowledgeEnrichmentService):
            def _run(self, job_id):
                current = self.store.get_sync_job(job_id)
                current.status = "done"
                current.summary = "resumed"
                self.store.save_sync_job(current)

        service = ResumingService(self.store)
        resumed = service.ensure_initial_metadata_sync()
        self.assertEqual(resumed.id, job.id)
        deadline = time.time() + 2
        while time.time() < deadline and self.store.get_sync_job(job.id).status != "done":
            time.sleep(0.01)
        self.assertEqual(self.store.get_sync_job(job.id).summary, "resumed")


if __name__ == "__main__":
    unittest.main()
