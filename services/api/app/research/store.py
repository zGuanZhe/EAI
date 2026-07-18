from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.errors import SchemaReadOnlyError
from ..core.errors import RevisionConflictError
from .models import ClaimRecord, EvidenceBundle, EvidenceSpan, KnowledgeStatus, ResearchWork, SyncJob
from .ontology import ontology_packs
from .embeddings import DIMENSIONS, PROFILE_ID, LocalEmbeddingProfile


SCHEMA_VERSION = 4
LAYER_PRIORITY = {"derived": 1, "curated": 2, "personal": 3}
QUERY_ALIASES = {
    "强化学习": ["reinforcement learning", "rl"],
    "后训练": ["posttraining", "post-training", "rlhf"],
    "世界模型": ["world model", "predictive learning"],
    "视觉语言动作": ["vision language action", "vla"],
    "具身": ["embodied", "robot"],
    "动作表示": ["action representation", "action tokenization"],
    "推理规划": ["reasoning", "planning"],
    "跨具身": ["cross embodiment"],
    "安全": ["safety", "risk"],
    "数据集": ["dataset", "corpus"],
    "基准": ["benchmark", "evaluation"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "\n".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def content_hash(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_title(value: str) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def normalize_identifier(scheme: str, value: str) -> str:
    normalized = str(value or "").strip().lower()
    if scheme == "doi":
        normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    if scheme == "arxiv":
        normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized).removesuffix(".pdf")
        normalized = re.sub(r"v\d+$", "", normalized)
    return normalized


def safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def managed_store_connections(cls):
    """Release SQLite handles after each top-level repository operation.

    Windows keeps an open handle on SQLite databases even when no statement is
    active. ResearchStore instances can outlive a desktop data-directory switch,
    so a permanently open connection prevents backup, restore and test cleanup.
    Nested repository calls share one handle and only the outer call releases it.
    """

    def wrap(method):
        def managed(self, *args, **kwargs):
            self._enter_call()
            try:
                return method(self, *args, **kwargs)
            finally:
                self._exit_call()

        managed.__name__ = method.__name__
        managed.__doc__ = method.__doc__
        return managed

    excluded = {"close", "transaction"}
    for name, member in list(vars(cls).items()):
        if name.startswith("_") or name in excluded or not callable(member):
            continue
        setattr(cls, name, wrap(member))
    return cls


@managed_store_connections
class ResearchStore:
    def __init__(
        self,
        research_dir: Path,
        atlas_dir: Path,
        personal_dir: Path,
        *,
        supported_schema_version: int = SCHEMA_VERSION,
        force_read_only: bool = False,
        read_only_reason: str = "",
    ):
        self.research_dir = research_dir.resolve()
        self.atlas_dir = atlas_dir.resolve()
        self.personal_dir = personal_dir.resolve()
        self.blob_dir = self.research_dir / "blobs"
        self.model_dir = self.research_dir / "models"
        self.index_dir = self.research_dir / "indexes"
        self.backup_dir = self.research_dir / "migration-backups"
        if not force_read_only:
            for directory in [self.research_dir, self.blob_dir, self.model_dir, self.index_dir, self.backup_dir]:
                directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.research_dir / "research.db"
        self.embedding_profile = LocalEmbeddingProfile(self.model_dir)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._active_calls = 0
        self.supported_schema_version = supported_schema_version
        self.schema_version = 0
        self.read_only = force_read_only
        self.read_only_reason = read_only_reason
        self._open_connection()
        startup_backup: Path | None = None
        try:
            previous_version = self._current_schema_version()
            self.schema_version = previous_version
            if force_read_only:
                self.close()
                return
            if previous_version > self.supported_schema_version:
                self.read_only = True
                self.read_only_reason = "schema_newer_than_app"
                self.close()
                self._open_connection()
                self.close()
                return
            if self.supported_schema_version != SCHEMA_VERSION:
                raise ValueError("schema compatibility override is only valid for a newer existing database")
            if previous_version and previous_version < SCHEMA_VERSION:
                startup_backup = self._backup_database_handle(f"schema-v{previous_version}-to-v{SCHEMA_VERSION}")
            self._initialize()
            self.schema_version = SCHEMA_VERSION
            self.bootstrap()
            self._open_connection()
            self._write_index_manifest()
            self.close()
        except Exception:
            self.close()
            if startup_backup and startup_backup.exists():
                shutil.copy2(startup_backup, self.db_path)
            raise

    def _current_schema_version(self) -> int:
        try:
            row = self._connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])
        except sqlite3.OperationalError:
            return 0

    def _backup_database_handle(self, label: str) -> Path:
        target = self.backup_dir / f"research-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}.db"
        backup = sqlite3.connect(target)
        try:
            self._connection.backup(backup)
        finally:
            backup.close()
        return target

    def _write_index_manifest(self) -> None:
        with self._lock:
            snapshots = self._connection.execute(
                "SELECT source_id, content_hash FROM source_snapshots WHERE source_kind='atlas' ORDER BY source_id"
            ).fetchall()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "atlas_snapshots": {row["source_id"]: row["content_hash"] for row in snapshots},
            "fts": ["works_fts", "document_chunks_fts"],
            "embedding_profile": PROFILE_ID,
            "generated_at": utc_now(),
        }
        target = self.index_dir / "manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _open_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            if self.read_only:
                connection = sqlite3.connect(
                    f"{self.db_path.as_uri()}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                    timeout=30,
                )
            else:
                connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            self._connection = connection
        return self._connection

    def compatibility_status(self) -> dict[str, Any]:
        status = {
            "schema_version": self.schema_version,
            "supported_schema_version": self.supported_schema_version,
            "read_only": self.read_only,
            "reason": self.read_only_reason or ("schema_newer_than_app" if self.read_only else ""),
        }
        if not self.read_only and self.schema_version >= 4:
            status["projection"] = self.projection_status()
        return status

    def ensure_writable(self) -> None:
        if self.read_only:
            raise SchemaReadOnlyError(
                database_schema=self.schema_version,
                supported_schema=self.supported_schema_version,
            )

    def _enter_call(self) -> None:
        with self._lock:
            self._open_connection()
            self._active_calls += 1

    def _exit_call(self) -> None:
        with self._lock:
            self._active_calls = max(0, self._active_calls - 1)
            if self._active_calls == 0 and self._connection is not None:
                self._connection.close()
                self._connection = None

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._active_calls = 0

    def backup_database(self, label: str = "manual") -> dict[str, Any]:
        with self._lock:
            target = self._backup_database_handle(re.sub(r"[^a-zA-Z0-9_-]+", "-", label)[:60] or "manual")
        return {"path": str(target), "bytes": target.stat().st_size, "created_at": utc_now()}

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.ensure_writable()
        with self._lock:
            connection = self._open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provenance (
                    id TEXT PRIMARY KEY, origin_kind TEXT NOT NULL, origin_ref TEXT NOT NULL,
                    content_hash TEXT NOT NULL, extractor TEXT NOT NULL, created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    source_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, content_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL, counts TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS works (
                    id TEXT PRIMARY KEY, legacy_paper_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL, year INTEGER, venue TEXT NOT NULL,
                    canonical_key TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_works_title ON works(normalized_title);
                CREATE TABLE IF NOT EXISTS work_identifiers (
                    work_id TEXT NOT NULL, scheme TEXT NOT NULL, value TEXT NOT NULL,
                    normalized_value TEXT NOT NULL, provenance_id TEXT NOT NULL,
                    PRIMARY KEY(work_id, scheme, normalized_value),
                    FOREIGN KEY(work_id) REFERENCES works(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_identifier_unique
                    ON work_identifiers(scheme, normalized_value) WHERE scheme IN ('doi', 'arxiv', 'openalex');
                CREATE TABLE IF NOT EXISTS work_fields (
                    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, field TEXT NOT NULL, value_json TEXT NOT NULL,
                    layer TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL,
                    provenance_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(work_id, field, layer, provenance_id),
                    FOREIGN KEY(work_id) REFERENCES works(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_work_fields_resolve ON work_fields(work_id, field, layer, confidence DESC);
                CREATE TABLE IF NOT EXISTS atlas_collections (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, title_cn TEXT NOT NULL, slug TEXT NOT NULL,
                    description TEXT NOT NULL, snapshot_hash TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS atlas_routes (
                    id TEXT PRIMARY KEY, atlas_id TEXT NOT NULL, title TEXT NOT NULL, title_cn TEXT NOT NULL,
                    rationale TEXT NOT NULL, color TEXT NOT NULL, order_index INTEGER NOT NULL, data TEXT NOT NULL,
                    FOREIGN KEY(atlas_id) REFERENCES atlas_collections(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS atlas_placements (
                    id TEXT PRIMARY KEY, atlas_id TEXT NOT NULL, route_id TEXT NOT NULL, work_id TEXT NOT NULL,
                    tier TEXT NOT NULL, include_type TEXT NOT NULL, local_role TEXT NOT NULL,
                    why_included TEXT NOT NULL, boundary_note TEXT NOT NULL, review_status TEXT NOT NULL,
                    order_index INTEGER NOT NULL, provenance_id TEXT NOT NULL, data TEXT NOT NULL,
                    FOREIGN KEY(atlas_id) REFERENCES atlas_collections(id) ON DELETE CASCADE,
                    FOREIGN KEY(work_id) REFERENCES works(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_placements_work ON atlas_placements(work_id, atlas_id);
                CREATE TABLE IF NOT EXISTS research_relations (
                    id TEXT PRIMARY KEY, atlas_id TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    predicate TEXT NOT NULL, label TEXT NOT NULL, rationale TEXT NOT NULL,
                    layer TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL,
                    provenance_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_relations_source ON research_relations(source_id, atlas_id);
                CREATE INDEX IF NOT EXISTS idx_relations_target ON research_relations(target_id, atlas_id);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, work_id TEXT, content_hash TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    media_type TEXT NOT NULL, blob_path TEXT NOT NULL, source_kind TEXT NOT NULL,
                    access TEXT NOT NULL, evictable INTEGER NOT NULL, page_count INTEGER NOT NULL,
                    parser TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, work_id TEXT, chunk_index INTEGER NOT NULL,
                    section TEXT NOT NULL, page INTEGER, start_offset INTEGER, end_offset INTEGER,
                    text TEXT NOT NULL, content_hash TEXT NOT NULL, locator TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    chunk_id UNINDEXED, document_id UNINDEXED, work_id UNINDEXED, title, section, text,
                    tokenize='unicode61'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
                    work_id UNINDEXED, title, summary, keywords, tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS evidence_spans (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, chunk_id TEXT NOT NULL, work_id TEXT,
                    section TEXT NOT NULL, page INTEGER, start_offset INTEGER, end_offset INTEGER,
                    quote TEXT NOT NULL, locator TEXT NOT NULL, evidence_level TEXT NOT NULL,
                    content_hash TEXT NOT NULL, provenance_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, predicate TEXT NOT NULL, text TEXT NOT NULL,
                    object_entity_id TEXT, object_value TEXT, polarity TEXT NOT NULL, modality TEXT NOT NULL,
                    layer TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL,
                    provenance_id TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id, status, confidence DESC);
                CREATE TABLE IF NOT EXISTS claim_evidence (
                    claim_id TEXT NOT NULL, evidence_id TEXT NOT NULL, stance TEXT NOT NULL,
                    PRIMARY KEY(claim_id, evidence_id),
                    FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE,
                    FOREIGN KEY(evidence_id) REFERENCES evidence_spans(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS research_records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_hash TEXT NOT NULL,
                    PRIMARY KEY(kind, id)
                );
                CREATE TABLE IF NOT EXISTS projection_journal (
                    id TEXT PRIMARY KEY, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    entity_revision INTEGER NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, target_relpath TEXT NOT NULL, status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL, last_error_code TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(entity_kind, entity_id, entity_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_projection_journal_status
                    ON projection_journal(status, updated_at);
                CREATE TABLE IF NOT EXISTS thread_drafts (
                    thread_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, text TEXT NOT NULL,
                    agent_mode TEXT NOT NULL, attachment_refs TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_normalization (
                    evidence_id TEXT PRIMARY KEY, algorithm_version TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL, page INTEGER, char_map TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_entities (
                    id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, layer TEXT NOT NULL,
                    title TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
                    parent_id TEXT, source_record_kind TEXT NOT NULL, source_record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_entities_source
                    ON research_entities(source_record_kind, source_record_id, entity_type);
                CREATE TABLE IF NOT EXISTS research_state_edges (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    predicate TEXT NOT NULL, layer TEXT NOT NULL, status TEXT NOT NULL,
                    source_record_kind TEXT NOT NULL, source_record_id TEXT NOT NULL,
                    data TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_state_edges_source ON research_state_edges(source_id, predicate);
                CREATE INDEX IF NOT EXISTS idx_research_state_edges_target ON research_state_edges(target_id, predicate);
                CREATE TABLE IF NOT EXISTS identity_candidates (
                    id TEXT PRIMARY KEY, source_work_id TEXT NOT NULL, target_work_id TEXT NOT NULL,
                    score REAL NOT NULL, reasons TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    object_type TEXT NOT NULL, object_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    dimensions INTEGER NOT NULL, vector BLOB NOT NULL, content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(object_type, object_id, model_id)
                );
                CREATE TABLE IF NOT EXISTS sync_jobs (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    progress INTEGER NOT NULL, total INTEGER NOT NULL, summary TEXT NOT NULL,
                    error TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_events (
                    job_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(job_id, seq)
                );
                CREATE TABLE IF NOT EXISTS ontology_packs (
                    version TEXT PRIMARY KEY, payload TEXT NOT NULL, loaded_at TEXT NOT NULL
                );
                """
            )
            previous_version = int(
                self._connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            )
            if previous_version and previous_version < SCHEMA_VERSION:
                self._connection.execute("DELETE FROM source_snapshots WHERE source_kind='atlas'")
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at, summary) VALUES(?, ?, ?)",
                (SCHEMA_VERSION, utc_now(), "Thread drafts, projection outbox, and evidence normalization"),
            )
            for pack in ontology_packs():
                self._connection.execute(
                    "INSERT OR REPLACE INTO ontology_packs(version, payload, loaded_at) VALUES(?, ?, ?)",
                    (pack["version"], json.dumps(pack, ensure_ascii=False), utc_now()),
                )

    def bootstrap(self) -> None:
        self.import_atlas_snapshots()
        self.reconcile_exact_identities()
        self.import_personal_snapshot()

    def reconcile_exact_identities(self) -> dict[str, int]:
        """Collapse legacy duplicate Works only when a stable identifier is exact."""
        merged = 0
        with self.transaction() as connection:
            rows = connection.execute("SELECT id, data FROM works ORDER BY created_at, id").fetchall()
            for row in rows:
                if not connection.execute("SELECT 1 FROM works WHERE id=?", (row["id"],)).fetchone():
                    continue
                data = json.loads(row["data"])
                identifiers = [
                    ("doi", data.get("doi")),
                    ("arxiv", data.get("arxiv") or data.get("arxiv_id")),
                ]
                target_id = None
                for scheme, value in identifiers:
                    if not value:
                        continue
                    target = connection.execute(
                        "SELECT work_id FROM work_identifiers WHERE scheme=? AND normalized_value=?",
                        (scheme, normalize_identifier(scheme, str(value))),
                    ).fetchone()
                    if target and target["work_id"] != row["id"]:
                        target_id = target["work_id"]
                        break
                if target_id:
                    self._merge_works(connection, row["id"], target_id)
                    merged += 1
        return {"merged": merged}

    def _provenance(self, connection: sqlite3.Connection, origin_kind: str, origin_ref: str, digest: str, payload: dict[str, Any] | None = None) -> str:
        provenance_id = stable_id("prov", origin_kind, origin_ref, digest)
        connection.execute(
            """INSERT OR REPLACE INTO provenance
               (id, origin_kind, origin_ref, content_hash, extractor, created_at, payload)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (provenance_id, origin_kind, origin_ref, digest, "eai-research-store/v1", utc_now(), json.dumps(payload or {}, ensure_ascii=False)),
        )
        return provenance_id

    def import_atlas_snapshots(self) -> dict[str, int]:
        counts = {"works": 0, "placements": 0, "routes": 0, "relations": 0}
        for path in sorted(self.atlas_dir.glob("*.bundle.json")):
            raw = path.read_bytes()
            digest = content_hash(raw)
            atlas_id = path.name.removesuffix(".bundle.json")
            with self._lock:
                existing = self._connection.execute(
                    "SELECT content_hash FROM source_snapshots WHERE source_id=?", (f"atlas:{atlas_id}",)
                ).fetchone()
            if existing and existing["content_hash"] == digest:
                continue
            payload = json.loads(raw.decode("utf-8"))
            self._import_atlas_bundle(atlas_id, payload, digest)
        with self._lock:
            counts["works"] = self._connection.execute("SELECT COUNT(*) FROM works").fetchone()[0]
            counts["placements"] = self._connection.execute("SELECT COUNT(*) FROM atlas_placements").fetchone()[0]
            counts["routes"] = self._connection.execute("SELECT COUNT(*) FROM atlas_routes").fetchone()[0]
            counts["relations"] = self._connection.execute("SELECT COUNT(*) FROM research_relations").fetchone()[0]
        return counts

    def _import_atlas_bundle(self, atlas_id: str, bundle: dict[str, Any], digest: str) -> None:
        now = utc_now()
        atlas = bundle.get("atlas") or {"id": atlas_id}
        with self.transaction() as connection:
            old_provenance = [
                row["id"] for row in connection.execute(
                    "SELECT id FROM provenance WHERE origin_kind='atlas_bundle' AND origin_ref=?",
                    (f"{atlas_id}.bundle.json",),
                ).fetchall()
            ]
            if old_provenance:
                placeholders = ",".join("?" for _ in old_provenance)
                connection.execute(f"DELETE FROM claims WHERE provenance_id IN ({placeholders})", old_provenance)
                connection.execute(f"DELETE FROM evidence_spans WHERE provenance_id IN ({placeholders})", old_provenance)
                connection.execute(f"DELETE FROM work_fields WHERE provenance_id IN ({placeholders})", old_provenance)
                connection.execute(f"DELETE FROM provenance WHERE id IN ({placeholders})", old_provenance)
            provenance_id = self._provenance(connection, "atlas_bundle", f"{atlas_id}.bundle.json", digest, {"atlas_id": atlas_id})
            connection.execute("DELETE FROM research_relations WHERE atlas_id=? AND layer='curated'", (atlas_id,))
            connection.execute("DELETE FROM atlas_placements WHERE atlas_id=?", (atlas_id,))
            connection.execute("DELETE FROM atlas_routes WHERE atlas_id=?", (atlas_id,))
            connection.execute(
                """INSERT OR REPLACE INTO atlas_collections
                   (id, title, title_cn, slug, description, snapshot_hash, data) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (atlas_id, atlas.get("title") or atlas_id, atlas.get("title_cn") or "", atlas.get("slug") or "",
                 atlas.get("description") or "", digest, json.dumps(atlas, ensure_ascii=False)),
            )
            for index, route in enumerate(bundle.get("routes") or []):
                connection.execute(
                    """INSERT OR REPLACE INTO atlas_routes
                       (id, atlas_id, title, title_cn, rationale, color, order_index, data)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (route.get("id"), atlas_id, route.get("name") or route.get("title") or "", route.get("cn") or route.get("title_cn") or "",
                     route.get("rationale") or "", route.get("color") or "", index, json.dumps(route, ensure_ascii=False)),
                )
            paper_work_ids: dict[str, str] = {}
            for paper in bundle.get("papers") or []:
                paper_work_ids[str(paper.get("id"))] = self._upsert_curated_work(
                    connection, atlas_id, paper, provenance_id, now
                )
            for index, entry in enumerate(bundle.get("entries") or []):
                work_id = paper_work_ids.get(str(entry.get("paper_id")), f"work_{entry.get('paper_id')}")
                connection.execute(
                    """INSERT OR REPLACE INTO atlas_placements
                       (id, atlas_id, route_id, work_id, tier, include_type, local_role, why_included,
                        boundary_note, review_status, order_index, provenance_id, data)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (entry.get("id") or stable_id("placement", atlas_id, entry.get("paper_id"), index), atlas_id,
                     entry.get("route_id") or "", work_id, entry.get("tier") or "", entry.get("include_type") or "primary",
                     entry.get("local_role") or "", entry.get("why_included") or "", entry.get("boundary_note") or "",
                     entry.get("review_status") or "", safe_int(entry.get("order_index"), index), provenance_id,
                     json.dumps(entry, ensure_ascii=False)),
                )
                self._import_curated_claims(connection, work_id, atlas_id, entry, provenance_id, now)
            for relation in bundle.get("relations") or []:
                raw_confidence = str(relation.get("confidence") or "medium").lower()
                confidence = {"high": 0.9, "medium": 0.65, "low": 0.35}.get(raw_confidence, 0.5)
                connection.execute(
                    """INSERT OR REPLACE INTO research_relations
                       (id, atlas_id, source_id, target_id, predicate, label, rationale, layer, status,
                        confidence, provenance_id, data) VALUES(?, ?, ?, ?, ?, ?, ?, 'curated', ?, ?, ?, ?)""",
                    (relation.get("id") or stable_id("relation", atlas_id, relation.get("source"), relation.get("target"), relation.get("type")),
                     atlas_id, paper_work_ids.get(str(relation.get("source")), f"work_{relation.get('source')}"),
                     paper_work_ids.get(str(relation.get("target")), f"work_{relation.get('target')}"), relation.get("type") or "linked_to",
                     relation.get("label") or "", relation.get("reason") or "",
                     "verified" if relation.get("reason") else "unverified", confidence, provenance_id,
                     json.dumps(relation, ensure_ascii=False)),
                )
            imported_counts = {
                "papers": len(bundle.get("papers") or []), "placements": len(bundle.get("entries") or []),
                "routes": len(bundle.get("routes") or []), "relations": len(bundle.get("relations") or []),
            }
            connection.execute(
                """INSERT OR REPLACE INTO source_snapshots
                   (source_id, source_kind, content_hash, imported_at, counts) VALUES(?, 'atlas', ?, ?, ?)""",
                (f"atlas:{atlas_id}", digest, now, json.dumps(imported_counts, ensure_ascii=False)),
            )

    def _upsert_curated_work(self, connection: sqlite3.Connection, atlas_id: str, paper: dict[str, Any], provenance_id: str, now: str) -> str:
        legacy_id = str(paper.get("id") or stable_id("paper", paper.get("title")))
        title = str(paper.get("title") or paper.get("display_title") or legacy_id)
        arxiv = str(paper.get("arxiv") or paper.get("arxiv_id") or "")
        doi = str(paper.get("doi") or "")
        work_id = f"work_{legacy_id}"
        for scheme, value in [("doi", doi), ("arxiv", arxiv)]:
            if not value:
                continue
            existing = connection.execute(
                "SELECT work_id FROM work_identifiers WHERE scheme=? AND normalized_value=?",
                (scheme, normalize_identifier(scheme, value)),
            ).fetchone()
            if existing:
                work_id = existing["work_id"]
                break
        canonical_key = f"doi:{normalize_identifier('doi', doi)}" if doi else f"arxiv:{normalize_identifier('arxiv', arxiv)}" if arxiv else f"title:{normalize_title(title)}"
        if not connection.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
            connection.execute(
                """INSERT INTO works
               (id, legacy_paper_id, title, normalized_title, year, venue, canonical_key, status, data,
                revision, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, 0, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, normalized_title=excluded.normalized_title,
                 year=COALESCE(works.year, excluded.year), venue=CASE WHEN works.venue='' THEN excluded.venue ELSE works.venue END,
                 data=excluded.data, updated_at=excluded.updated_at""",
                (work_id, legacy_id, title, normalize_title(title), paper.get("year"), paper.get("venue") or "",
                 canonical_key, json.dumps(paper, ensure_ascii=False), now, now),
            )
        identifiers = [("legacy_paper_id", legacy_id)]
        if arxiv:
            identifiers.append(("arxiv", arxiv))
        if doi:
            identifiers.append(("doi", doi))
        if paper.get("url"):
            identifiers.append(("url", str(paper["url"])))
        for scheme, value in identifiers:
            normalized = normalize_identifier(scheme, value)
            existing = connection.execute(
                "SELECT work_id FROM work_identifiers WHERE scheme=? AND normalized_value=?", (scheme, normalized)
            ).fetchone()
            if not existing:
                connection.execute(
                    "INSERT INTO work_identifiers(work_id, scheme, value, normalized_value, provenance_id) VALUES(?, ?, ?, ?, ?)",
                    (work_id, scheme, value, normalized, provenance_id),
                )
        raw_fields = paper.get("raw_fields") if isinstance(paper.get("raw_fields"), dict) else {}
        fields = {
            "title": title,
            "year": paper.get("year"),
            "venue": paper.get("venue") or "",
            "summary": paper.get("summary") or raw_fields.get("desc"),
            "authors": paper.get("authors") or [],
            "tags": paper.get("tags") or [],
            "source_confidence": paper.get("source_confidence") or raw_fields.get("sourceConfidence"),
        }
        for field, value in fields.items():
            if value not in (None, "", []):
                self._save_field(connection, work_id, field, value, "curated", 0.9, "verified", provenance_id, now)
        summary = str(fields.get("summary") or "")
        connection.execute("DELETE FROM works_fts WHERE work_id=?", (work_id,))
        connection.execute(
            "INSERT INTO works_fts(work_id, title, summary, keywords) VALUES(?, ?, ?, ?)",
            (work_id, title, summary, " ".join(str(item) for item in (paper.get("tags") or []))),
        )
        return work_id

    def _save_field(self, connection: sqlite3.Connection, work_id: str, field: str, value: Any, layer: str, confidence: float, status: str, provenance_id: str, now: str) -> None:
        field_id = stable_id("field", work_id, field, layer, provenance_id)
        connection.execute(
            """INSERT OR REPLACE INTO work_fields
               (id, work_id, field, value_json, layer, confidence, status, provenance_id, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (field_id, work_id, field, json.dumps(value, ensure_ascii=False), layer, confidence, status, provenance_id, now),
        )

    def _import_curated_claims(self, connection: sqlite3.Connection, work_id: str, atlas_id: str, entry: dict[str, Any], provenance_id: str, now: str) -> None:
        raw = entry.get("raw_fields") if isinstance(entry.get("raw_fields"), dict) else {}
        candidates = [
            ("atlas_evidence", raw.get("evidence"), "reported"),
            ("curator_judgement", raw.get("judgement"), "inferred"),
            ("atlas_role", entry.get("why_included"), "inferred"),
            ("limitation", entry.get("boundary_note"), "reported"),
        ]
        for predicate, value, modality in candidates:
            text = str(value or "").strip()
            if not text:
                continue
            evidence_id = stable_id("evidence", atlas_id, work_id, predicate, text)
            connection.execute(
                """INSERT OR REPLACE INTO evidence_spans
                   (id, document_id, chunk_id, work_id, section, page, start_offset, end_offset,
                    quote, locator, evidence_level, content_hash, provenance_id)
                   VALUES(?, ?, '', ?, ?, NULL, NULL, NULL, ?, ?, 'curated_summary', ?, ?)""",
                (evidence_id, f"atlas:{atlas_id}", work_id, predicate, text,
                 json.dumps({"atlas_id": atlas_id, "placement_id": entry.get("id"), "field": predicate}, ensure_ascii=False),
                 content_hash(text), provenance_id),
            )
            claim_id = stable_id("claim", work_id, predicate, text, provenance_id)
            connection.execute(
                """INSERT OR REPLACE INTO claims
                   (id, subject_id, predicate, text, object_entity_id, object_value, polarity, modality,
                    layer, status, confidence, provenance_id, created_at)
                   VALUES(?, ?, ?, ?, NULL, NULL, 'neutral', ?, 'curated', 'verified', 0.82, ?, ?)""",
                (claim_id, work_id, predicate, text, modality, provenance_id, now),
            )
            connection.execute(
                "INSERT OR REPLACE INTO claim_evidence(claim_id, evidence_id, stance) VALUES(?, ?, 'supports')",
                (claim_id, evidence_id),
            )

    def import_personal_snapshot(self) -> dict[str, int]:
        snapshot_id = "personal-json-v1"
        paths: list[tuple[str, Path]] = []
        for kind, directory in [
            ("project", self.personal_dir / "projects"), ("thread", self.personal_dir / "threads"),
            ("object_memory", self.personal_dir / "objects"), ("atlas_update", self.personal_dir / "atlas_updates"),
            ("lab_run", self.personal_dir / "lab_runs"),
        ]:
            if directory.exists():
                paths.extend((kind, path) for path in directory.rglob("*.json"))
        digest = content_hash("\n".join(f"{kind}:{path}:{content_hash(path.read_bytes())}" for kind, path in paths))
        with self._lock:
            existing = self._connection.execute("SELECT content_hash FROM source_snapshots WHERE source_id=?", (snapshot_id,)).fetchone()
        if existing and existing["content_hash"] == digest:
            return {"records": len(paths)}
        if paths and not any(self.backup_dir.iterdir()):
            target = self.backup_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copytree(self.personal_dir, target, ignore=shutil.ignore_patterns("backups"), dirs_exist_ok=False)
        with self.transaction() as connection:
            for kind, path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if kind == "object_memory":
                    relative = path.relative_to(self.personal_dir / "objects")
                    record_id = ":".join(relative.with_suffix("").parts)
                else:
                    record_id = str(payload.get("id") or path.stem)
                revision = int(payload.get("revision") or 0)
                created_at = str(payload.get("created_at") or utc_now())
                updated_at = str(payload.get("updated_at") or created_at)
                connection.execute(
                    """INSERT INTO research_records(kind, id, payload, revision, created_at, updated_at, source_hash)
                       VALUES(?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(kind, id) DO NOTHING""",
                    (kind, record_id, json.dumps(payload, ensure_ascii=False), revision, created_at, updated_at, content_hash(path.read_bytes())),
                )
                self._project_personal_record(connection, kind, record_id, payload)
            connection.execute(
                """INSERT OR REPLACE INTO source_snapshots
                   (source_id, source_kind, content_hash, imported_at, counts) VALUES(?, 'personal_json', ?, ?, ?)""",
                (snapshot_id, digest, utc_now(), json.dumps({"records": len(paths)})),
            )
        return {"records": len(paths)}

    def save_record(
        self,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        projection_target: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            self._save_record_in_transaction(connection, kind, record_id, payload, projection_target)
        return payload

    def apply_record_batch(self, mutations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply canonical record mutations and their projection outbox rows atomically.

        Each mutation must include kind, record_id, payload (or None for delete),
        projection_target, and expected_payload. The expected payload is checked
        after BEGIN IMMEDIATE so concurrent changes fail before any write occurs.
        JSON projection is intentionally not performed in this transaction.
        """
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for mutation in mutations:
            kind = str(mutation.get("kind") or "")
            record_id = str(mutation.get("record_id") or "")
            key = (kind, record_id)
            if not kind or not record_id:
                raise ValueError("record mutation requires kind and record_id")
            if key in seen:
                raise ValueError(f"duplicate record mutation: {kind}:{record_id}")
            if "expected_payload" not in mutation:
                raise ValueError("record mutation requires expected_payload")
            seen.add(key)
            normalized.append({
                "kind": kind,
                "record_id": record_id,
                "payload": mutation.get("payload"),
                "expected_payload": mutation.get("expected_payload"),
                "projection_target": mutation.get("projection_target"),
            })

        results: list[dict[str, Any]] = []
        with self.transaction() as connection:
            for mutation in normalized:
                row = connection.execute(
                    "SELECT payload FROM research_records WHERE kind=? AND id=?",
                    (mutation["kind"], mutation["record_id"]),
                ).fetchone()
                current_payload = json.loads(row["payload"]) if row else None
                if current_payload != mutation["expected_payload"]:
                    raise RevisionConflictError({
                        "message": "操作目标发生变化",
                        "record_kind": mutation["kind"],
                        "record_id": mutation["record_id"],
                        "expected": mutation["expected_payload"],
                        "current": current_payload,
                    })

            for mutation in normalized:
                if mutation["payload"] is None:
                    revision = self._delete_record_in_transaction(
                        connection,
                        mutation["kind"],
                        mutation["record_id"],
                        mutation["projection_target"],
                    )
                    operation = "delete"
                else:
                    revision = self._save_record_in_transaction(
                        connection,
                        mutation["kind"],
                        mutation["record_id"],
                        mutation["payload"],
                        mutation["projection_target"],
                    )
                    operation = "upsert"
                results.append({
                    "kind": mutation["kind"],
                    "record_id": mutation["record_id"],
                    "revision": revision,
                    "operation": operation,
                    "projection_status": "pending" if mutation["projection_target"] else "none",
                })
        return results

    def _save_record_in_transaction(
        self,
        connection: sqlite3.Connection,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        projection_target: str | None,
    ) -> int:
        now = str(payload.get("updated_at") or utc_now())
        created_at = str(payload.get("created_at") or now)
        serialized = json.dumps(payload, ensure_ascii=False)
        serialized_hash = content_hash(serialized)
        current = connection.execute(
            "SELECT revision, source_hash FROM research_records WHERE kind=? AND id=?", (kind, record_id)
        ).fetchone()
        journal_revision = int(connection.execute(
            "SELECT COALESCE(MAX(entity_revision), 0) FROM projection_journal WHERE entity_kind=? AND entity_id=?",
            (kind, record_id),
        ).fetchone()[0])
        if current and current["source_hash"] == serialized_hash:
            revision = int(current["revision"])
        else:
            revision = max(
                int(payload.get("revision") or 0),
                int(current["revision"] if current else 0) + 1,
                journal_revision + 1,
            )
        connection.execute(
            """INSERT INTO research_records(kind, id, payload, revision, created_at, updated_at, source_hash)
               VALUES(?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, id) DO UPDATE SET payload=excluded.payload, revision=excluded.revision,
                 updated_at=excluded.updated_at, source_hash=excluded.source_hash""",
            (kind, record_id, serialized, revision, created_at, now, serialized_hash),
        )
        self._project_personal_record(connection, kind, record_id, payload)
        if projection_target:
            connection.execute(
                """INSERT INTO projection_journal
                   (id, entity_kind, entity_id, entity_revision, operation, payload, payload_hash,
                    target_relpath, status, attempt_count, last_error_code, created_at, updated_at)
                   VALUES(?, ?, ?, ?, 'upsert', ?, ?, ?, 'pending', 0, '', ?, ?)
                   ON CONFLICT(entity_kind, entity_id, entity_revision) DO NOTHING""",
                (
                    stable_id("projection", kind, record_id, str(revision)), kind, record_id, revision,
                    serialized, serialized_hash, projection_target, now, now,
                ),
            )
        return revision

    def get_record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM research_records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def delete_record(self, kind: str, record_id: str, *, projection_target: str | None = None) -> None:
        with self.transaction() as connection:
            self._delete_record_in_transaction(connection, kind, record_id, projection_target)

    def _delete_record_in_transaction(
        self,
        connection: sqlite3.Connection,
        kind: str,
        record_id: str,
        projection_target: str | None,
    ) -> int:
        current = connection.execute(
            "SELECT revision FROM research_records WHERE kind=? AND id=?", (kind, record_id)
        ).fetchone()
        latest_journal = connection.execute(
            """SELECT entity_revision, operation FROM projection_journal
               WHERE entity_kind=? AND entity_id=? ORDER BY entity_revision DESC LIMIT 1""",
            (kind, record_id),
        ).fetchone()
        if current is None and latest_journal and latest_journal["operation"] == "delete":
            return int(latest_journal["entity_revision"])
        revision = max(
            int(current["revision"] if current else 0),
            int(latest_journal["entity_revision"] if latest_journal else 0),
        ) + 1
        connection.execute("DELETE FROM research_records WHERE kind=? AND id=?", (kind, record_id))
        connection.execute(
            "DELETE FROM research_state_edges WHERE source_record_kind=? AND source_record_id=?", (kind, record_id)
        )
        connection.execute(
            "DELETE FROM research_entities WHERE source_record_kind=? AND source_record_id=?", (kind, record_id)
        )
        if projection_target:
            now = utc_now()
            connection.execute(
                """INSERT INTO projection_journal
                   (id, entity_kind, entity_id, entity_revision, operation, payload, payload_hash,
                    target_relpath, status, attempt_count, last_error_code, created_at, updated_at)
                   VALUES(?, ?, ?, ?, 'delete', '{}', '', ?, 'pending', 0, '', ?, ?)
                   ON CONFLICT(entity_kind, entity_id, entity_revision) DO NOTHING""",
                (stable_id("projection", kind, record_id, str(revision)), kind, record_id, revision, projection_target, now, now),
            )
        return revision

    def list_projection_jobs(
        self, *, statuses: tuple[str, ...] = ("pending", "failed"), entity_kind: str | None = None,
        entity_id: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = [f"status IN ({','.join('?' for _ in statuses)})"]
        values: list[Any] = list(statuses)
        if entity_kind:
            clauses.append("entity_kind=?")
            values.append(entity_kind)
        if entity_id:
            clauses.append("entity_id=?")
            values.append(entity_id)
        values.append(max(1, min(limit, 1000)))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM projection_journal WHERE {' AND '.join(clauses)} ORDER BY entity_revision, created_at LIMIT ?",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_projection_job(self, job_id: str, status: str, error_code: str = "") -> None:
        if status not in {"applied", "failed", "superseded"}:
            raise ValueError("invalid projection status")
        with self.transaction() as connection:
            connection.execute(
                """UPDATE projection_journal SET status=?, attempt_count=attempt_count+1,
                   last_error_code=?, updated_at=? WHERE id=?""",
                (status, error_code[:120], utc_now(), job_id),
            )

    def latest_projection_revision(self, entity_kind: str, entity_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """SELECT COALESCE(MAX(entity_revision), 0) revision FROM projection_journal
                   WHERE entity_kind=? AND entity_id=?""",
                (entity_kind, entity_id),
            ).fetchone()
        return int(row["revision"])

    def projection_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) count FROM projection_journal GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def get_thread_draft(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM thread_drafts WHERE thread_id=?", (thread_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "thread_id": row["thread_id"], "revision": int(row["revision"]), "text": row["text"],
            "agent_mode": row["agent_mode"], "attachment_refs": json.loads(row["attachment_refs"]),
            "updated_at": row["updated_at"],
        }

    def put_thread_draft(
        self, thread_id: str, *, expected_revision: int, text: str, agent_mode: str,
        attachment_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM thread_drafts WHERE thread_id=?", (thread_id,)
            ).fetchone()
            current_revision = int(current["revision"]) if current else 0
            if expected_revision != current_revision:
                current_payload = None if not current else {
                    "thread_id": current["thread_id"], "revision": current_revision, "text": current["text"],
                    "agent_mode": current["agent_mode"], "attachment_refs": json.loads(current["attachment_refs"]),
                    "updated_at": current["updated_at"],
                }
                raise RevisionConflictError({
                    "code": "draft_revision_conflict", "message": "草稿已在另一窗口更新。",
                    "expected_revision": expected_revision, "current_revision": current_revision,
                    "current_draft": current_payload,
                })
            revision = current_revision + 1
            connection.execute(
                """INSERT INTO thread_drafts(thread_id, revision, text, agent_mode, attachment_refs, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET revision=excluded.revision, text=excluded.text,
                     agent_mode=excluded.agent_mode, attachment_refs=excluded.attachment_refs,
                     updated_at=excluded.updated_at""",
                (thread_id, revision, text, agent_mode, json.dumps(attachment_refs, ensure_ascii=False), now),
            )
        return {
            "thread_id": thread_id, "revision": revision, "text": text, "agent_mode": agent_mode,
            "attachment_refs": attachment_refs, "updated_at": now,
        }

    def delete_thread_draft(self, thread_id: str, *, expected_revision: int) -> None:
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM thread_drafts WHERE thread_id=?", (thread_id,)
            ).fetchone()
            current_revision = int(current["revision"]) if current else 0
            if current and expected_revision != current_revision:
                raise RevisionConflictError({
                    "code": "draft_revision_conflict", "message": "草稿已在另一窗口更新，未删除较新的内容。",
                    "expected_revision": expected_revision, "current_revision": current_revision,
                    "current_draft": {
                        "thread_id": current["thread_id"], "revision": current_revision, "text": current["text"],
                        "agent_mode": current["agent_mode"], "attachment_refs": json.loads(current["attachment_refs"]),
                        "updated_at": current["updated_at"],
                    },
                })
            connection.execute("DELETE FROM thread_drafts WHERE thread_id=?", (thread_id,))

    def list_records(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM research_records WHERE kind=? ORDER BY updated_at DESC", (kind,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def _project_personal_record(
        self, connection: sqlite3.Connection, kind: str, record_id: str, payload: dict[str, Any]
    ) -> None:
        connection.execute(
            "DELETE FROM research_state_edges WHERE source_record_kind=? AND source_record_id=?", (kind, record_id)
        )
        connection.execute(
            "DELETE FROM research_entities WHERE source_record_kind=? AND source_record_id=?", (kind, record_id)
        )
        now = str(payload.get("updated_at") or utc_now())
        revision = safe_int(payload.get("revision"), 0)

        def entity(entity_id: str, entity_type: str, title: str, summary: str = "", status: str = "", parent_id: str | None = None, data: dict[str, Any] | None = None) -> None:
            connection.execute(
                """INSERT OR REPLACE INTO research_entities
                   (id, entity_type, layer, title, summary, status, parent_id, source_record_kind,
                    source_record_id, revision, data, updated_at) VALUES(?, ?, 'personal', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entity_id, entity_type, title[:500], summary[:4000], status[:80], parent_id,
                 kind, record_id, revision, json.dumps(data or {}, ensure_ascii=False), now),
            )

        def edge(source_id: str, target_id: str, predicate: str, data: dict[str, Any] | None = None) -> None:
            edge_id = stable_id("state_edge", kind, record_id, source_id, target_id, predicate, json.dumps(data or {}, sort_keys=True))
            connection.execute(
                """INSERT OR REPLACE INTO research_state_edges
                   (id, source_id, target_id, predicate, layer, status, source_record_kind,
                    source_record_id, data, updated_at) VALUES(?, ?, ?, ?, 'personal', 'verified', ?, ?, ?, ?)""",
                (edge_id, source_id, target_id, predicate, kind, record_id,
                 json.dumps(data or {}, ensure_ascii=False), now),
            )

        root_id = f"{kind}:{record_id}"
        if kind == "project":
            entity(root_id, "project", str(payload.get("title") or record_id), str(payload.get("goal") or ""), str(payload.get("status") or "active"), data=payload)
        elif kind == "thread":
            entity(root_id, "thread", str(payload.get("title") or record_id), str(payload.get("goal") or ""), "active", data={"conversation_summary": payload.get("conversation_summary") or ""})
            project_id = payload.get("project_id")
            if project_id:
                edge(f"project:{project_id}", root_id, "contains")
            canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else {}
            node_ids: dict[str, str] = {}
            for node in canvas.get("nodes") or []:
                node_id = str(node.get("id") or stable_id("canvas_node", record_id, node.get("title")))
                entity_id = f"thread:{record_id}:canvas:{node_id}"
                node_ids[node_id] = entity_id
                entity(entity_id, str(node.get("type") or "artifact"), str(node.get("title") or node.get("label") or "未命名节点"),
                       str(node.get("body") or node.get("summary") or ""), str(node.get("status") or ""), root_id, node)
                edge(root_id, entity_id, "contains")
            for item in canvas.get("edges") or []:
                source = node_ids.get(str(item.get("source") or item.get("from") or ""))
                target = node_ids.get(str(item.get("target") or item.get("to") or ""))
                if source and target:
                    edge(source, target, str(item.get("type") or item.get("label") or "supports"), item)
        elif kind == "lab_run":
            entity(root_id, "experiment", str(payload.get("title") or record_id), str(payload.get("goal") or ""), str(payload.get("status") or "planned"), data=payload)
            if payload.get("thread_id"):
                edge(f"thread:{payload['thread_id']}", root_id, "contains")
            for category, entity_type, predicate in [("findings", "finding", "reports"), ("artifacts", "artifact", "produces")]:
                for index, item in enumerate(payload.get(category) or []):
                    item_id = str(item.get("id") if isinstance(item, dict) else "") or stable_id(entity_type, record_id, index)
                    value = item if isinstance(item, dict) else {"title": str(item)}
                    child_id = f"lab_run:{record_id}:{entity_type}:{item_id}"
                    entity(child_id, entity_type, str(value.get("title") or value.get("summary") or item_id), str(value.get("summary") or value.get("body") or ""), str(value.get("status") or ""), root_id, value)
                    edge(root_id, child_id, predicate)
        elif kind == "object_memory":
            entity(root_id, "memory", str(payload.get("title_snapshot") or record_id), str(payload.get("judgement") or payload.get("note") or ""), "confirmed", data=payload)
        elif kind == "atlas_update":
            entity(root_id, "artifact", f"Atlas {payload.get('atlas_id') or record_id} 个人候选层", str(payload.get("summary") or ""), "active", data=payload)
        elif kind == "research_campaign":
            thread_id = payload.get("thread_id")
            entity(
                root_id, "research_campaign", str(payload.get("title") or record_id),
                str(payload.get("objective") or ""), str(payload.get("status") or "planned"),
                f"thread:{thread_id}" if thread_id else None, data={
                    "hypothesis": payload.get("hypothesis") or "",
                    "current_stage_id": payload.get("current_stage_id"),
                    "disclosure_required": payload.get("disclosure_required", True),
                },
            )
            if thread_id:
                edge(f"thread:{thread_id}", root_id, "contains")
            idea = payload.get("selected_idea") if isinstance(payload.get("selected_idea"), dict) else {}
            if idea:
                idea_id = f"campaign:{record_id}:idea:{idea.get('id') or 'selected'}"
                entity(idea_id, "idea", str(idea.get("title") or "研究想法"), str(idea.get("short_hypothesis") or ""), str(idea.get("status") or "selected"), root_id, idea)
                edge(root_id, idea_id, "contains")
            for branch in payload.get("promoted_branches") or []:
                if not isinstance(branch, dict):
                    continue
                branch_id = f"campaign:{record_id}:branch:{branch.get('id')}"
                entity(branch_id, "experiment_branch", str(branch.get("title") or "实验分支"), str(branch.get("analysis") or branch.get("plan_summary") or ""), str(branch.get("status") or "promoted"), root_id, branch)
                edge(root_id, branch_id, "contains")

    def get_research_state(self, *, thread_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if thread_id:
            clauses.append("(id=? OR parent_id=? OR source_record_id=?)")
            params.extend([f"thread:{thread_id}", f"thread:{thread_id}", thread_id])
        if project_id:
            clauses.append("(id=? OR parent_id=? OR source_record_id=?)")
            params.extend([f"project:{project_id}", f"project:{project_id}", project_id])
        where = " WHERE " + " OR ".join(clauses) if clauses else ""
        with self._lock:
            entities = self._connection.execute(
                f"SELECT * FROM research_entities{where} ORDER BY updated_at DESC", params
            ).fetchall()
            ids = {row["id"] for row in entities}
            edges = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                edges = self._connection.execute(
                    f"SELECT * FROM research_state_edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                    list(ids) + list(ids),
                ).fetchall()
        return {
            "entities": [{**dict(row), "data": json.loads(row["data"])} for row in entities],
            "edges": [{**dict(row), "data": json.loads(row["data"])} for row in edges],
        }

    def _resolved_fields(self, work_id: str) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT field, value_json, layer, confidence, status, provenance_id FROM work_fields WHERE work_id=? AND status!='rejected'",
                (work_id,),
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["field"], []).append(
                {"value": json.loads(row["value_json"]), "layer": row["layer"], "confidence": row["confidence"],
                 "status": row["status"], "provenance_id": row["provenance_id"]}
            )
        resolved: dict[str, Any] = {}
        conflicts: list[dict[str, Any]] = []
        for field, values in grouped.items():
            ordered = sorted(values, key=lambda item: (LAYER_PRIORITY.get(item["layer"], 0), item["confidence"]), reverse=True)
            grouped[field] = ordered
            resolved[field] = ordered[0]["value"]
            unique = {json.dumps(item["value"], ensure_ascii=False, sort_keys=True) for item in ordered}
            if len(unique) > 1:
                conflicts.append({"work_id": work_id, "field": field, "values": ordered})
        return resolved, grouped, conflicts

    def get_work(self, work_id: str) -> ResearchWork | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM works WHERE id=? OR legacy_paper_id=?", (work_id, work_id.removeprefix("work_"))).fetchone()
            if row is None:
                alias = self._connection.execute(
                    "SELECT work_id FROM work_identifiers WHERE scheme='legacy_paper_id' AND normalized_value=?",
                    (normalize_identifier("legacy_paper_id", work_id.removeprefix("work_")),),
                ).fetchone()
                if alias:
                    row = self._connection.execute("SELECT * FROM works WHERE id=?", (alias["work_id"],)).fetchone()
        if not row:
            return None
        actual_id = row["id"]
        with self._lock:
            identifier_rows = self._connection.execute("SELECT scheme, value FROM work_identifiers WHERE work_id=?", (actual_id,)).fetchall()
            placements = self._connection.execute(
                """SELECT p.*, r.title AS route_title, r.title_cn AS route_title_cn
                   FROM atlas_placements p LEFT JOIN atlas_routes r ON r.id=p.route_id WHERE p.work_id=? ORDER BY p.atlas_id, p.order_index""",
                (actual_id,),
            ).fetchall()
            document_count = self._connection.execute("SELECT COUNT(*) FROM documents WHERE work_id=?", (actual_id,)).fetchone()[0]
            claim_count = self._connection.execute("SELECT COUNT(*) FROM claims WHERE subject_id=? AND status='verified'", (actual_id,)).fetchone()[0]
        identifiers: dict[str, list[str]] = {}
        for item in identifier_rows:
            identifiers.setdefault(item["scheme"], []).append(item["value"])
        resolved, field_sources, _ = self._resolved_fields(actual_id)
        return ResearchWork(
            id=actual_id, title=str(resolved.get("title") or row["title"]),
            year=safe_int(resolved.get("year"), row["year"]) if resolved.get("year") is not None else row["year"],
            venue=str(resolved.get("venue") or row["venue"]), canonical_key=row["canonical_key"],
            identifiers=identifiers, resolved_fields=resolved, field_sources=field_sources,
            atlas_placements=[{**dict(item), "data": json.loads(item["data"])} for item in placements],
            evidence_status={"metadata": bool(identifiers), "curated": bool(placements), "full_text": document_count > 0,
                             "verified_claims": claim_count, "documents": document_count},
            revision=row["revision"],
        )

    def atlas_evidence_index(self, atlas_id: str) -> dict[str, Any]:
        with self._lock:
            work_rows = self._connection.execute(
                """SELECT w.legacy_paper_id,
                          EXISTS(SELECT 1 FROM work_identifiers i WHERE i.work_id=w.id AND i.scheme IN ('doi','arxiv','openalex')) metadata,
                          EXISTS(SELECT 1 FROM documents d WHERE d.work_id=w.id) full_text,
                          (SELECT COUNT(*) FROM claims c WHERE c.subject_id=w.id AND c.status='verified') verified_claims
                   FROM works w JOIN atlas_placements p ON p.work_id=w.id WHERE p.atlas_id=? GROUP BY w.id""",
                (atlas_id,),
            ).fetchall()
            relation_rows = self._connection.execute(
                "SELECT id, status, rationale, confidence, layer FROM research_relations WHERE atlas_id=?", (atlas_id,)
            ).fetchall()
        return {
            "works": {
                row["legacy_paper_id"]: {
                    "metadata": bool(row["metadata"]), "full_text": bool(row["full_text"]),
                    "verified_claims": int(row["verified_claims"]),
                }
                for row in work_rows
            },
            "relations": {
                row["id"]: {
                    "verification_status": row["status"], "evidence_rationale": row["rationale"],
                    "evidence_confidence": row["confidence"], "evidence_layer": row["layer"],
                }
                for row in relation_rows
            },
        }

    def claims_for_work(self, work_id: str, *, verified_only: bool = True) -> tuple[list[ClaimRecord], list[EvidenceSpan]]:
        clause = "AND c.status='verified'" if verified_only else ""
        with self._lock:
            claim_rows = self._connection.execute(
                f"SELECT c.*, GROUP_CONCAT(ce.evidence_id) evidence_ids FROM claims c LEFT JOIN claim_evidence ce ON ce.claim_id=c.id WHERE c.subject_id=? {clause} GROUP BY c.id ORDER BY c.confidence DESC",
                (work_id,),
            ).fetchall()
            evidence_ids = [item for row in claim_rows for item in str(row["evidence_ids"] or "").split(",") if item]
            evidence_rows = []
            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                evidence_rows = self._connection.execute(f"SELECT * FROM evidence_spans WHERE id IN ({placeholders})", evidence_ids).fetchall()
        claims = [ClaimRecord(
            id=row["id"], subject_id=row["subject_id"], predicate=row["predicate"], text=row["text"],
            object_entity_id=row["object_entity_id"], object_value=json.loads(row["object_value"]) if row["object_value"] else None,
            polarity=row["polarity"], modality=row["modality"], layer=row["layer"], status=row["status"],
            confidence=row["confidence"], evidence_ids=[item for item in str(row["evidence_ids"] or "").split(",") if item],
            provenance_id=row["provenance_id"], created_at=row["created_at"],
        ) for row in claim_rows]
        evidence = [EvidenceSpan(
            id=row["id"], document_id=row["document_id"], chunk_id=row["chunk_id"], work_id=row["work_id"],
            section=row["section"], page=row["page"], start_offset=row["start_offset"], end_offset=row["end_offset"],
            quote=row["quote"], locator=json.loads(row["locator"]), evidence_level=row["evidence_level"], content_hash=row["content_hash"],
        ) for row in evidence_rows]
        return claims, evidence

    def get_claim_evidence(self, claim_id: str) -> tuple[ClaimRecord | None, list[EvidenceSpan]]:
        with self._lock:
            row = self._connection.execute("SELECT subject_id FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not row:
            return None, []
        claims, evidence = self.claims_for_work(row["subject_id"], verified_only=False)
        claim = next((item for item in claims if item.id == claim_id), None)
        if not claim:
            return None, []
        wanted = set(claim.evidence_ids)
        return claim, [item for item in evidence if item.id in wanted]

    def get_evidence_span(self, evidence_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM evidence_spans WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "locator": json.loads(row["locator"])}

    def save_evidence_normalization(
        self, evidence_id: str, *, algorithm_version: str, normalized_hash_value: str,
        page: int | None, char_map: list[list[int]],
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO evidence_normalization
                   (evidence_id, algorithm_version, normalized_hash, page, char_map, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(evidence_id) DO UPDATE SET algorithm_version=excluded.algorithm_version,
                     normalized_hash=excluded.normalized_hash, page=excluded.page,
                     char_map=excluded.char_map, updated_at=excluded.updated_at""",
                (evidence_id, algorithm_version, normalized_hash_value, page, json.dumps(char_map), utc_now()),
            )

    def find_document_by_hash(self, digest: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM documents WHERE content_hash=?", (digest,)).fetchone()
        return self._document_row(row) if row else None

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return self._document_row(row) if row else None

    def _document_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {**dict(row), "data": json.loads(row["data"])}

    def save_document(self, document: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO documents
                   (id, work_id, content_hash, title, media_type, blob_path, source_kind, access,
                    evictable, page_count, parser, created_at, updated_at, data)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(content_hash) DO UPDATE SET work_id=COALESCE(documents.work_id, excluded.work_id),
                     title=excluded.title, updated_at=excluded.updated_at, data=excluded.data""",
                (document["id"], document.get("work_id"), document["content_hash"], document["title"],
                 document["media_type"], document["blob_path"], document.get("source_kind", "user"),
                 document.get("access", "local"), int(bool(document.get("evictable"))), document.get("page_count", 0),
                 document.get("parser", "pypdf"), document.get("created_at", now), now,
                 json.dumps(document, ensure_ascii=False)),
            )
            existing = connection.execute("SELECT id FROM documents WHERE content_hash=?", (document["content_hash"],)).fetchone()
            document_id = existing["id"]
            connection.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM document_chunks_fts WHERE document_id=?", (document_id,))
            for chunk in chunks:
                connection.execute(
                    """INSERT INTO document_chunks
                       (id, document_id, work_id, chunk_index, section, page, start_offset, end_offset,
                        text, content_hash, locator) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (chunk["id"], document_id, document.get("work_id"), chunk["index"], chunk.get("section", ""),
                     chunk.get("page"), chunk.get("start_offset"), chunk.get("end_offset"), chunk["text"],
                     chunk["content_hash"], json.dumps(chunk.get("locator") or {}, ensure_ascii=False)),
                )
                connection.execute(
                    "INSERT INTO document_chunks_fts(chunk_id, document_id, work_id, title, section, text) VALUES(?, ?, ?, ?, ?, ?)",
                    (chunk["id"], document_id, document.get("work_id") or "", document["title"], chunk.get("section", ""), chunk["text"]),
                )
        return self.get_document(document_id) or document

    def search_document_chunks(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        terms = " ".join(token for token in query.replace('"', " ").split() if token)
        with self._lock:
            try:
                rows = self._connection.execute(
                    """SELECT f.chunk_id, f.document_id, f.work_id, f.title, f.section, f.text,
                              bm25(document_chunks_fts) rank, c.page, c.locator, c.content_hash
                       FROM document_chunks_fts f JOIN document_chunks c ON c.id=f.chunk_id
                       WHERE document_chunks_fts MATCH ? ORDER BY rank LIMIT ?""", (terms, limit)
                ).fetchall()
            except sqlite3.OperationalError:
                rows = self._connection.execute(
                    """SELECT c.id chunk_id, c.document_id, c.work_id, d.title, c.section, c.text,
                              0 rank, c.page, c.locator, c.content_hash
                       FROM document_chunks c JOIN documents d ON d.id=c.document_id
                       WHERE c.text LIKE ? LIMIT ?""", (f"%{query}%", limit)
                ).fetchall()
        return [{**dict(row), "locator": json.loads(row["locator"])} for row in rows]

    def save_claim_with_evidence(
        self,
        *,
        work_id: str,
        predicate: str,
        text: str,
        chunk_id: str,
        quote: str,
        layer: str = "derived",
        confidence: float = 0.7,
        modality: str = "reported",
        extractor: str = "agent",
    ) -> ClaimRecord:
        with self._lock:
            chunk = self._connection.execute("SELECT * FROM document_chunks WHERE id=?", (chunk_id,)).fetchone()
        if not chunk:
            raise ValueError("evidence chunk not found")
        normalized_quote = re.sub(r"\s+", " ", quote).strip()
        normalized_text = re.sub(r"\s+", " ", chunk["text"])
        if not normalized_quote or normalized_quote not in normalized_text:
            raise ValueError("claim quote does not occur in the evidence chunk")
        start_offset = normalized_text.index(normalized_quote)
        now = utc_now()
        provenance_digest = content_hash(f"{chunk_id}\n{predicate}\n{text}\n{quote}")
        with self.transaction() as connection:
            provenance_id = self._provenance(connection, "claim_extraction", chunk_id, provenance_digest, {"extractor": extractor})
            evidence_id = stable_id("evidence", chunk_id, quote)
            connection.execute(
                """INSERT OR REPLACE INTO evidence_spans
                   (id, document_id, chunk_id, work_id, section, page, start_offset, end_offset,
                    quote, locator, evidence_level, content_hash, provenance_id)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'full_text', ?, ?)""",
                (evidence_id, chunk["document_id"], chunk_id, work_id, chunk["section"], chunk["page"],
                 start_offset, start_offset + len(normalized_quote), quote, chunk["locator"], content_hash(quote), provenance_id),
            )
            claim_id = stable_id("claim", work_id, predicate, text, evidence_id)
            connection.execute(
                """INSERT OR REPLACE INTO claims
                   (id, subject_id, predicate, text, object_entity_id, object_value, polarity, modality,
                    layer, status, confidence, provenance_id, created_at)
                   VALUES(?, ?, ?, ?, NULL, NULL, 'neutral', ?, ?, 'verified', ?, ?, ?)""",
                (claim_id, work_id, predicate, text, modality, layer, confidence, provenance_id, now),
            )
            connection.execute(
                "INSERT OR REPLACE INTO claim_evidence(claim_id, evidence_id, stance) VALUES(?, ?, 'supports')",
                (claim_id, evidence_id),
            )
        claims, _ = self.claims_for_work(work_id, verified_only=False)
        return next(claim for claim in claims if claim.id == claim_id)

    def enforce_cache_limit(self, limit_bytes: int = 10 * 1024**3) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, blob_path, content_hash FROM documents WHERE evictable=1 ORDER BY updated_at ASC"
            ).fetchall()
        files = [(row, Path(row["blob_path"])) for row in rows]
        total = sum(path.stat().st_size for path in self.blob_dir.rglob("*") if path.is_file())
        evicted: list[str] = []
        for row, path in files:
            if total <= limit_bytes:
                break
            if not path.exists() or self.blob_dir not in path.resolve().parents:
                continue
            size = path.stat().st_size
            path.unlink()
            total -= size
            evicted.append(row["id"])
            with self._lock, self._connection:
                current = self._connection.execute("SELECT data FROM documents WHERE id=?", (row["id"],)).fetchone()
                payload = json.loads(current["data"]) if current else {}
                payload["cache_evicted"] = True
                self._connection.execute(
                    "UPDATE documents SET blob_path='', data=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )
        return {"bytes": total, "limit_bytes": limit_bytes, "evicted_document_ids": evicted}

    def prepare_embedding_profile(self, progress=None) -> dict[str, Any]:
        return self.embedding_profile.download(progress=progress)

    def rebuild_embeddings(self, *, limit: int | None = None) -> dict[str, int]:
        if not self.embedding_profile.ready:
            raise RuntimeError("embedding profile is not ready")
        items: list[tuple[str, str, str, str]] = []
        for raw in self.works_for_sync():
            fields = raw.get("resolved_fields") or {}
            text = "\n".join(
                str(value) for value in [raw.get("title"), fields.get("abstract"), fields.get("summary"), fields.get("tags")]
                if value not in (None, "", [])
            )
            items.append(("work", raw["id"], text, content_hash(text)))
        work_count = len(items)
        with self._lock:
            chunk_rows = self._connection.execute(
                "SELECT id, text, content_hash FROM document_chunks ORDER BY rowid"
            ).fetchall()
        items.extend(("chunk", row["id"], row["text"], row["content_hash"]) for row in chunk_rows)
        if limit is not None:
            items = items[:limit]
        indexed = 0
        for offset in range(0, len(items), 16):
            batch = items[offset:offset + 16]
            vectors = self.embedding_profile.embed([item[2] for item in batch])
            try:
                import numpy as np
            except ImportError as exc:
                raise RuntimeError("numpy is required for local embeddings") from exc
            with self.transaction() as connection:
                for item, vector in zip(batch, vectors, strict=True):
                    connection.execute(
                        """INSERT OR REPLACE INTO embeddings
                           (object_type, object_id, model_id, dimensions, vector, content_hash, updated_at)
                           VALUES(?, ?, ?, ?, ?, ?, ?)""",
                        (item[0], item[1], PROFILE_ID, DIMENSIONS,
                         np.asarray(vector, dtype=np.float16).tobytes(), item[3], utc_now()),
                    )
                    indexed += 1
        return {"indexed": indexed, "works": min(work_count, len(items)), "chunks": max(0, len(items) - work_count)}

    def vector_search(self, query: str, *, limit: int = 30) -> list[dict[str, Any]]:
        if not self.embedding_profile.ready:
            return []
        try:
            import numpy as np
            query_vector = np.asarray(self.embedding_profile.embed([query], query=True)[0], dtype=np.float32)
        except (ImportError, RuntimeError, ValueError):
            return []
        with self._lock:
            rows = self._connection.execute(
                """SELECT e.object_type, e.object_id, e.vector, e.dimensions,
                          CASE WHEN e.object_type='work' THEN e.object_id ELSE c.work_id END work_id
                   FROM embeddings e LEFT JOIN document_chunks c
                     ON e.object_type='chunk' AND c.id=e.object_id
                   WHERE e.model_id=?""",
                (PROFILE_ID,),
            ).fetchall()
        ranked = []
        for row in rows:
            if not row["work_id"] or int(row["dimensions"]) != DIMENSIONS:
                continue
            vector = np.frombuffer(row["vector"], dtype=np.float16).astype(np.float32)
            if vector.size != DIMENSIONS:
                continue
            ranked.append({"object_type": row["object_type"], "object_id": row["object_id"],
                           "work_id": row["work_id"], "score": float(np.dot(query_vector, vector))})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]

    def search(self, query: str, *, atlas_ids: list[str] | None = None, work_ids: list[str] | None = None, include_graph: bool = True, include_claims: bool = True, limit: int = 12) -> EvidenceBundle:
        query = query.strip()[:1200]
        normalized = normalize_title(query)
        tokens = list(dict.fromkeys(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+|[\u4e00-\u9fff]{2,}", query.lower())))[:24]
        for source, aliases in QUERY_ALIASES.items():
            if source in query:
                tokens.extend(alias for value in aliases for alias in value.lower().split())
        tokens = list(dict.fromkeys(tokens))[:40]
        scores: dict[str, float] = {}
        methods: dict[str, set[str]] = {}
        if exact_work := self.resolve_work(query):
            scores[exact_work.id] = 1000
            methods.setdefault(exact_work.id, set()).add("exact")
        vector_hits = self.vector_search(query, limit=30)
        for rank, item in enumerate(vector_hits, start=1):
            work_id = item["work_id"]
            scores[work_id] = max(scores.get(work_id, 0), 60 / (rank + 1))
            methods.setdefault(work_id, set()).add("vector")
        with self._lock:
            exact_rows = self._connection.execute(
                """SELECT DISTINCT w.id FROM works w LEFT JOIN work_identifiers i ON i.work_id=w.id
                   WHERE w.normalized_title=? OR i.normalized_value=?""", (normalized, query.lower())
            ).fetchall()
            for row in exact_rows:
                scores[row["id"]] = 1000
                methods.setdefault(row["id"], set()).add("exact")
            rows = self._connection.execute("SELECT id, title, normalized_title, data FROM works").fetchall()
            field_text: dict[str, str] = {}
            for item in self._connection.execute(
                "SELECT work_id, value_json FROM work_fields WHERE status!='rejected'"
            ):
                field_text[item["work_id"]] = f"{field_text.get(item['work_id'], '')} {item['value_json']}".lower()
            placement_text: dict[str, str] = {}
            for item in self._connection.execute(
                """SELECT p.work_id, p.local_role, p.why_included, p.tier, r.title route_title,
                          r.title_cn route_title_cn, r.rationale, a.title_cn atlas_title_cn
                   FROM atlas_placements p JOIN atlas_routes r ON r.id=p.route_id
                   JOIN atlas_collections a ON a.id=p.atlas_id"""
            ):
                placement_text[item["work_id"]] = " ".join(
                    [placement_text.get(item["work_id"], ""), item["local_role"], item["why_included"], item["tier"],
                     item["route_title"], item["route_title_cn"], item["rationale"], item["atlas_title_cn"]]
                ).lower()
            placement_atlas: dict[str, set[str]] = {}
            if atlas_ids:
                placeholders = ",".join("?" for _ in atlas_ids)
                for item in self._connection.execute(f"SELECT work_id, atlas_id FROM atlas_placements WHERE atlas_id IN ({placeholders})", atlas_ids):
                    placement_atlas.setdefault(item["work_id"], set()).add(item["atlas_id"])
            allowed_ids = set(work_ids or [])
            for row in rows:
                work_id = row["id"]
                if allowed_ids and work_id not in allowed_ids and work_id.removeprefix("work_") not in allowed_ids:
                    continue
                if allowed_ids:
                    scores[work_id] = max(scores.get(work_id, 0), 900)
                    methods.setdefault(work_id, set()).add("explicit")
                if atlas_ids and work_id not in placement_atlas:
                    continue
                data = json.loads(row["data"])
                haystack = " ".join([row["title"], str(data.get("summary") or ""), str(data.get("why_included") or ""),
                                     str(data.get("local_role") or ""), " ".join(str(item) for item in (data.get("tags") or [])),
                                     placement_text.get(work_id, ""), field_text.get(work_id, "")]).lower()
                score = sum(8 if token in row["title"].lower() else 2 if token in haystack else 0 for token in tokens)
                if normalized and normalized in row["normalized_title"]:
                    score += 12
                if score:
                    scores[work_id] = max(scores.get(work_id, 0), float(score))
                    methods.setdefault(work_id, set()).add("fts")
            if include_graph and scores:
                seeds = sorted(scores, key=scores.get, reverse=True)[:5]
                placeholders = ",".join("?" for _ in seeds)
                relation_rows = self._connection.execute(
                    f"SELECT * FROM research_relations WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders}) LIMIT 100",
                    seeds + seeds,
                ).fetchall()
                for relation in relation_rows:
                    source_id, target_id = relation["source_id"], relation["target_id"]
                    neighbor = target_id if source_id in seeds else source_id
                    seed = source_id if source_id in seeds else target_id
                    scores[neighbor] = max(scores.get(neighbor, 0), scores.get(seed, 0) * 0.32 + relation["confidence"])
                    methods.setdefault(neighbor, set()).add("graph")
            else:
                relation_rows = []
        ranked = sorted(scores, key=lambda item: scores[item], reverse=True)[:limit]
        works = [work for work_id in ranked if (work := self.get_work(work_id))]
        claims: list[ClaimRecord] = []
        evidence: list[EvidenceSpan] = []
        conflicts: list[dict[str, Any]] = []
        if include_claims:
            for work in works:
                work_claims, work_evidence = self.claims_for_work(work.id)
                claims.extend(work_claims[:8])
                evidence.extend(work_evidence)
                _, _, work_conflicts = self._resolved_fields(work.id)
                conflicts.extend(work_conflicts)
        graph_paths = [{"id": row["id"], "atlas_id": row["atlas_id"], "source_id": row["source_id"],
                        "target_id": row["target_id"], "predicate": row["predicate"], "label": row["label"],
                        "rationale": row["rationale"], "status": row["status"], "confidence": row["confidence"]}
                       for row in relation_rows if row["source_id"] in ranked or row["target_id"] in ranked]
        missing = [] if works else ["没有找到匹配的策展论文或本地证据"]
        return EvidenceBundle(
            query=query, works=works, claims=claims, evidence=list({item.id: item for item in evidence}.values()),
            graph_paths=graph_paths, conflicts=conflicts, missing=missing,
            retrieval={"methods": {key: sorted(value) for key, value in methods.items() if key in ranked},
                       "embedding": "fallback", "ranked_count": len(ranked)},
        )

    def works_for_sync(self, *, atlas_ids: list[str] | None = None, work_ids: list[str] | None = None) -> list[dict[str, Any]]:
        query = "SELECT DISTINCT w.* FROM works w"
        params: list[Any] = []
        clauses: list[str] = []
        if atlas_ids:
            query += " JOIN atlas_placements p ON p.work_id=w.id"
            clauses.append(f"p.atlas_id IN ({','.join('?' for _ in atlas_ids)})")
            params.extend(atlas_ids)
        if work_ids:
            normalized_ids = [item if item.startswith("work_") else f"work_{item}" for item in work_ids]
            clauses.append(f"w.id IN ({','.join('?' for _ in normalized_ids)})")
            params.extend(normalized_ids)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY w.year DESC, w.title"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        results = []
        for row in rows:
            work = self.get_work(row["id"])
            if work:
                results.append(work.model_dump(mode="json"))
        return results

    def apply_derived_metadata(self, work_id: str, metadata: dict[str, Any], *, provider: str) -> ResearchWork:
        now = utc_now()
        digest = content_hash(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        with self.transaction() as connection:
            provenance_id = self._provenance(connection, "metadata_sync", f"{provider}:{work_id}", digest, {"provider": provider})
            field_map = {
                "title": metadata.get("title"), "authors": metadata.get("authors"), "abstract": metadata.get("abstract"),
                "year": metadata.get("year"), "venue": metadata.get("venue"), "open_access_url": metadata.get("pdf_url"),
            }
            for field, value in field_map.items():
                if value not in (None, "", []):
                    self._save_field(connection, work_id, field, value, "derived", float(metadata.get("confidence") or 0.82), "verified", provenance_id, now)
            for scheme in ["doi", "arxiv", "openalex"]:
                value = str(metadata.get(scheme) or "").strip()
                if not value:
                    continue
                normalized = normalize_identifier(scheme, value)
                existing = connection.execute(
                    "SELECT work_id FROM work_identifiers WHERE scheme=? AND normalized_value=?", (scheme, normalized)
                ).fetchone()
                if existing and existing["work_id"] != work_id:
                    candidate_id = stable_id("identity", scheme, normalized, work_id, existing["work_id"])
                    connection.execute(
                        """INSERT OR IGNORE INTO identity_candidates
                           (id, source_work_id, target_work_id, score, reasons, status, created_at)
                           VALUES(?, ?, ?, 1.0, ?, 'pending', ?)""",
                        (candidate_id, work_id, existing["work_id"], json.dumps([f"共享 {scheme}:{normalized}"], ensure_ascii=False), now),
                    )
                else:
                    connection.execute(
                        "INSERT OR IGNORE INTO work_identifiers(work_id, scheme, value, normalized_value, provenance_id) VALUES(?, ?, ?, ?, ?)",
                        (work_id, scheme, value, normalized, provenance_id),
                    )
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        return work

    def apply_personal_work_fields(
        self,
        work_id: str,
        fields: dict[str, Any],
        *,
        source_ref: str,
        expected_revision: int | None = None,
    ) -> ResearchWork:
        """Persist reviewed fields through the personal-layer transaction primitive."""
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT revision FROM works WHERE id=?", (work_id,)).fetchone()
            if not row:
                raise KeyError(work_id)
            if expected_revision is not None and int(row["revision"]) != expected_revision:
                raise ValueError("research work revision conflict")
            sanitized = {str(key)[:80]: value for key, value in fields.items() if value not in (None, "")}
            digest = content_hash(json.dumps(sanitized, ensure_ascii=False, sort_keys=True))
            provenance_id = self._provenance(
                connection, "personal_review", source_ref[:500], digest, {"fields": sorted(sanitized)}
            )
            for field, value in sanitized.items():
                self._save_field(connection, work_id, field, value, "personal", 1.0, "verified", provenance_id, now)
            connection.execute("UPDATE works SET revision=revision+1, updated_at=? WHERE id=?", (now, work_id))
        work = self.get_work(work_id)
        if not work:
            raise KeyError(work_id)
        return work

    def resolve_work(self, identifier: str, scheme: str | None = None) -> ResearchWork | None:
        value = str(identifier or "").strip()
        if not value:
            return None
        if direct := self.get_work(value):
            return direct
        inferred = scheme
        if inferred is None:
            lowered = value.lower()
            inferred = "doi" if "doi.org/" in lowered or lowered.startswith("10.") else "arxiv" if "arxiv.org/" in lowered else None
        with self._lock:
            row = None
            if inferred:
                row = self._connection.execute(
                    "SELECT work_id FROM work_identifiers WHERE scheme=? AND normalized_value=?",
                    (inferred, normalize_identifier(inferred, value)),
                ).fetchone()
            if row is None:
                row = self._connection.execute(
                    "SELECT id work_id FROM works WHERE normalized_title=? OR legacy_paper_id=?",
                    (normalize_title(value), value.removeprefix("work_")),
                ).fetchone()
        return self.get_work(row["work_id"]) if row else None

    def latest_sync_job(self, scope: str) -> SyncJob | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM sync_jobs WHERE scope=? ORDER BY created_at DESC LIMIT 1", (scope,)).fetchone()
        return SyncJob.model_validate_json(row["payload"]) if row else None

    def graph_neighborhood(self, entity_id: str, *, depth: int = 1, predicates: list[str] | None = None, limit: int = 40) -> dict[str, Any]:
        frontier = {entity_id if entity_id.startswith("work_") else f"work_{entity_id}"}
        visited = set(frontier)
        relations: list[dict[str, Any]] = []
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            params: list[Any] = list(frontier) + list(frontier)
            predicate_clause = ""
            if predicates:
                predicate_clause = f" AND predicate IN ({','.join('?' for _ in predicates)})"
                params.extend(predicates)
            with self._lock:
                rows = self._connection.execute(
                    f"SELECT * FROM research_relations WHERE (source_id IN ({placeholders}) OR target_id IN ({placeholders})){predicate_clause} LIMIT ?",
                    params + [limit - len(relations)],
                ).fetchall()
            next_frontier: set[str] = set()
            for row in rows:
                relations.append(dict(row))
                for value in [row["source_id"], row["target_id"]]:
                    if value not in visited:
                        visited.add(value)
                        next_frontier.add(value)
            frontier = next_frontier
        works = [work.model_dump(mode="json") for item in visited if (work := self.get_work(item))]
        return {"entity_id": entity_id, "depth": depth, "works": works, "relations": relations[:limit]}

    def create_sync_job(self, scope: str, atlas_ids: list[str], work_ids: list[str]) -> SyncJob:
        now = utc_now()
        job = SyncJob(id=f"sync_{uuid.uuid4().hex[:16]}", scope=scope, atlas_ids=atlas_ids, work_ids=work_ids, created_at=now, updated_at=now)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sync_jobs(id, scope, status, payload, progress, total, summary, error, created_at, updated_at)
                   VALUES(?, ?, ?, ?, 0, 0, '', '', ?, ?)""",
                (job.id, scope, job.status, json.dumps(job.model_dump(mode="json"), ensure_ascii=False), now, now),
            )
        return job

    def save_sync_job(self, job: SyncJob) -> SyncJob:
        job.updated_at = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE sync_jobs SET status=?, payload=?, progress=?, total=?, summary=?, error=?, updated_at=? WHERE id=?""",
                (job.status, json.dumps(job.model_dump(mode="json"), ensure_ascii=False), job.progress, job.total,
                 job.summary, job.error, job.updated_at, job.id),
            )
        return job

    def get_sync_job(self, job_id: str) -> SyncJob | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM sync_jobs WHERE id=?", (job_id,)).fetchone()
        return SyncJob.model_validate_json(row["payload"]) if row else None

    def append_sync_event(self, job_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT COALESCE(MAX(seq), 0) seq FROM sync_events WHERE job_id=?", (job_id,)).fetchone()
            seq = int(row["seq"]) + 1
            created_at = utc_now()
            self._connection.execute(
                "INSERT INTO sync_events(job_id, seq, kind, payload, created_at) VALUES(?, ?, ?, ?, ?)",
                (job_id, seq, kind, json.dumps(payload, ensure_ascii=False), created_at),
            )
        return {"job_id": job_id, "seq": seq, "kind": kind, "payload": payload, "created_at": created_at}

    def sync_events(self, job_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sync_events WHERE job_id=? AND seq>? ORDER BY seq", (job_id, after_seq)
            ).fetchall()
        return [{"job_id": row["job_id"], "seq": row["seq"], "kind": row["kind"],
                 "payload": json.loads(row["payload"]), "created_at": row["created_at"]} for row in rows]

    def resolve_claim(self, claim_id: str, status: str, *, note: str = "") -> ClaimRecord | None:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
            if not row:
                return None
            layer = "personal" if status == "verified" else row["layer"]
            self._connection.execute("UPDATE claims SET status=?, layer=? WHERE id=?", (status, layer, claim_id))
            if note:
                provenance_id = self._provenance(self._connection, "user_review", claim_id, content_hash(note), {"note": note})
                self._connection.execute("UPDATE claims SET provenance_id=? WHERE id=?", (provenance_id, claim_id))
        claims, _ = self.claims_for_work(row["subject_id"], verified_only=False)
        return next((claim for claim in claims if claim.id == claim_id), None)

    def resolve_identity_candidate(self, candidate_id: str, decision: str, target_work_id: str | None = None) -> dict[str, Any] | None:
        metadata: dict[str, Any] | None = None
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM identity_candidates WHERE id=?", (candidate_id,)).fetchone()
            if not row:
                return None
            status = "merged" if decision == "merge" else "separate" if decision == "keep_separate" else "rejected"
            resolved_target = target_work_id or row["target_work_id"]
            if decision == "merge" and str(resolved_target).startswith("work_"):
                self._merge_works(connection, row["source_work_id"], resolved_target)
            elif decision == "merge" and str(resolved_target).startswith("openalex:"):
                reasons = json.loads(row["reasons"])
                metadata = next(
                    (item.get("metadata") for item in reasons if isinstance(item, dict) and item.get("metadata")), None
                )
            connection.execute("UPDATE identity_candidates SET status=?, resolved_at=? WHERE id=?", (status, utc_now(), candidate_id))
        if metadata:
            self.apply_derived_metadata(row["source_work_id"], metadata, provider="openalex_identity_review")
        return {**dict(row), "status": status, "target_work_id": target_work_id or row["target_work_id"]}

    def _merge_works(self, connection: sqlite3.Connection, source_id: str, target_id: str) -> None:
        if source_id == target_id:
            return
        source = connection.execute("SELECT id FROM works WHERE id=?", (source_id,)).fetchone()
        target = connection.execute("SELECT id FROM works WHERE id=?", (target_id,)).fetchone()
        if not source or not target:
            raise ValueError("identity merge target is not a local ResearchWork")
        connection.execute(
            """DELETE FROM work_identifiers WHERE work_id=? AND EXISTS(
                 SELECT 1 FROM work_identifiers target
                 WHERE target.work_id=? AND target.scheme=work_identifiers.scheme
                   AND target.normalized_value=work_identifiers.normalized_value)""",
            (source_id, target_id),
        )
        connection.execute(
            """DELETE FROM work_fields WHERE work_id=? AND EXISTS(
                 SELECT 1 FROM work_fields target
                 WHERE target.work_id=? AND target.field=work_fields.field
                   AND target.layer=work_fields.layer AND target.provenance_id=work_fields.provenance_id)""",
            (source_id, target_id),
        )
        for table, column in [
            ("work_identifiers", "work_id"), ("work_fields", "work_id"),
            ("atlas_placements", "work_id"), ("documents", "work_id"),
            ("document_chunks", "work_id"), ("evidence_spans", "work_id"),
            ("claims", "subject_id"),
        ]:
            connection.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (target_id, source_id))
        connection.execute("UPDATE research_relations SET source_id=? WHERE source_id=?", (target_id, source_id))
        connection.execute("UPDATE research_relations SET target_id=? WHERE target_id=?", (target_id, source_id))
        connection.execute("DELETE FROM works_fts WHERE work_id=?", (source_id,))
        connection.execute("DELETE FROM embeddings WHERE object_type='work' AND object_id=?", (source_id,))
        connection.execute("UPDATE works SET revision=revision+1, updated_at=? WHERE id=?", (utc_now(), target_id))
        connection.execute("DELETE FROM works WHERE id=?", (source_id,))

    def save_identity_candidate(
        self,
        *,
        source_work_id: str,
        target_ref: str,
        score: float,
        reasons: list[Any],
    ) -> dict[str, Any]:
        candidate_id = stable_id("identity", source_work_id, target_ref)
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO identity_candidates
                   (id, source_work_id, target_work_id, score, reasons, status, created_at)
                   VALUES(?, ?, ?, ?, ?, 'pending', ?)""",
                (candidate_id, source_work_id, target_ref[:500], max(0.0, min(1.0, score)),
                 json.dumps(reasons, ensure_ascii=False), now),
            )
            row = self._connection.execute("SELECT * FROM identity_candidates WHERE id=?", (candidate_id,)).fetchone()
        return {**dict(row), "reasons": json.loads(row["reasons"])}

    def status(self) -> KnowledgeStatus:
        tables = {
            "works": "works", "placements": "atlas_placements", "routes": "atlas_routes", "relations": "research_relations",
            "claims": "claims", "evidence": "evidence_spans", "documents": "documents", "chunks": "document_chunks",
            "personal_records": "research_records", "research_entities": "research_entities",
            "research_state_edges": "research_state_edges", "identity_candidates": "identity_candidates",
        }
        with self._lock:
            counts = {key: int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for key, table in tables.items()}
            full_text = int(self._connection.execute("SELECT COUNT(DISTINCT work_id) FROM documents WHERE work_id IS NOT NULL").fetchone()[0])
            verified_relations = int(self._connection.execute("SELECT COUNT(*) FROM research_relations WHERE status='verified'").fetchone()[0])
            metadata = int(self._connection.execute("SELECT COUNT(DISTINCT work_id) FROM work_identifiers WHERE scheme IN ('doi','arxiv','openalex')").fetchone()[0])
            job_rows = self._connection.execute("SELECT payload FROM sync_jobs WHERE status IN ('pending','running','paused') ORDER BY created_at DESC").fetchall()
            snapshots = self._connection.execute("SELECT source_id, content_hash, imported_at, counts FROM source_snapshots ORDER BY source_id").fetchall()
        total = max(1, counts["works"])
        relation_total = max(1, counts["relations"])
        cache_bytes = sum(path.stat().st_size for path in self.blob_dir.rglob("*") if path.is_file())
        with self._lock:
            embedding_count = int(self._connection.execute("SELECT COUNT(*) FROM embeddings WHERE model_id=?", (PROFILE_ID,)).fetchone()[0])
        return KnowledgeStatus(
            ready=counts["works"] > 0, schema_version=self.schema_version, database_path=str(self.db_path), counts=counts,
            coverage={"metadata": round(metadata / total, 4), "full_text": round(full_text / total, 4),
                      "verified_relations": round(verified_relations / relation_total, 4),
                      "claim_evidence": round(counts["evidence"] / max(1, counts["claims"]), 4)},
            cache={"bytes": cache_bytes, "limit_bytes": 10 * 1024**3, "blob_dir": str(self.blob_dir)},
            embedding={"profile": PROFILE_ID, "dimensions": DIMENSIONS, "ready": self.embedding_profile.ready,
                       "indexed": embedding_count, "fallback": "fts5+graph"},
            active_jobs=[SyncJob.model_validate_json(row["payload"]) for row in job_rows],
            migration={"snapshots": [{"source_id": row["source_id"], "content_hash": row["content_hash"],
                                       "imported_at": row["imported_at"], "counts": json.loads(row["counts"])} for row in snapshots]},
        )

    def integrity_check(self) -> dict[str, Any]:
        with self._lock:
            result = self._connection.execute("PRAGMA integrity_check").fetchone()[0]
            broken_placements = self._connection.execute(
                "SELECT COUNT(*) FROM atlas_placements p LEFT JOIN works w ON w.id=p.work_id WHERE w.id IS NULL"
            ).fetchone()[0]
            broken_relations = self._connection.execute(
                """SELECT COUNT(*) FROM research_relations r LEFT JOIN works s ON s.id=r.source_id
                   LEFT JOIN works t ON t.id=r.target_id WHERE s.id IS NULL OR t.id IS NULL"""
            ).fetchone()[0]
        return {"sqlite": result, "broken_placements": broken_placements, "broken_relations": broken_relations,
                "ok": result == "ok" and broken_placements == 0 and broken_relations == 0}
