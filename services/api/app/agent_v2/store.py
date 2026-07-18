from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..core.errors import SchemaReadOnlyError
from .models import (
    AgentArtifact,
    AgentAttempt,
    AgentEvent,
    AgentTask,
    ApprovalRequest,
    DocumentRecord,
    MemoryDraft,
    Observation,
    SourceRecord,
    ToolCall,
)


SUPPORTED_RUNTIME_SCHEMA = 2


class RuntimeStore:
    def __init__(
        self,
        runtime_dir: Path,
        *,
        read_only: bool = False,
        database_schema: int = 0,
        supported_schema: int = 0,
    ):
        self.runtime_dir = runtime_dir
        self.read_only = read_only
        self.database_schema = database_schema
        self.supported_schema = supported_schema
        if not read_only:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir = self.runtime_dir / "documents"
        self.workspaces_dir = self.runtime_dir / "workspaces"
        if not read_only:
            self.documents_dir.mkdir(parents=True, exist_ok=True)
            self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_dir / "runtime.db"
        self._lock = threading.RLock()
        self.runtime_schema_version = self._read_runtime_schema(self.db_path)
        self.supported_runtime_schema = SUPPORTED_RUNTIME_SCHEMA
        if self.runtime_schema_version > self.supported_runtime_schema:
            self.read_only = True
        if read_only and self.db_path.exists():
            self._connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
        elif self.read_only and self.db_path.exists():
            self._connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
        else:
            self._connection = sqlite3.connect(":memory:" if read_only else self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        if not self.read_only or not self.db_path.exists():
            migration_backup = None
            if self.db_path.exists() and self.runtime_schema_version < self.supported_runtime_schema:
                migration_backup = self._backup_before_upgrade()
            try:
                self._initialize()
            except Exception:
                self._connection.close()
                if migration_backup and migration_backup.exists():
                    shutil.copy2(migration_backup, self.db_path)
                raise

    @staticmethod
    def _read_runtime_schema(path: Path) -> int:
        if not path.exists():
            return 0
        connection = sqlite3.connect(path)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_meta'"
            ).fetchone()
            if not table:
                return 1
            row = connection.execute("SELECT value FROM runtime_meta WHERE key='schema_version'").fetchone()
            return int(row[0]) if row else 1
        finally:
            connection.close()

    def _backup_before_upgrade(self) -> Path | None:
        if self.runtime_schema_version <= 0:
            return None
        target = self.runtime_dir / f"runtime.schema-{self.runtime_schema_version}.backup.db"
        if target.exists():
            return target
        backup = sqlite3.connect(target)
        try:
            self._connection.backup(backup)
        finally:
            backup.close()
        return target

    def ensure_writable(self) -> None:
        if self.read_only:
            raise SchemaReadOnlyError(
                database_schema=self.database_schema,
                supported_schema=self.supported_schema,
            )

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS runtime_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_thread ON agent_tasks(thread_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS agent_attempts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_attempts_task ON agent_attempts(task_id, number);
                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_attempt ON agent_tool_calls(attempt_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_observations (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_observations_attempt ON agent_observations(attempt_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_steers (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_steers_task ON agent_steers(task_id, status, created_at);
                CREATE TABLE IF NOT EXISTS agent_events (
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, seq)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    canonical_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sources_task ON sources(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sources_canonical ON sources(canonical_key);
                CREATE TABLE IF NOT EXISTS task_sources (
                    task_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, source_id),
                    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_sources_task ON task_sources(task_id, linked_at);
                CREATE TABLE IF NOT EXISTS source_query_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS operation_batches (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_operation_batches_task ON operation_batches(task_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_drafts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    text TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    document_id UNINDEXED,
                    title,
                    section,
                    text,
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS campaign_checkpoints (
                    campaign_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_campaign_checkpoints_thread
                    ON campaign_checkpoints(thread_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS campaign_events (
                    campaign_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, seq)
                );
                CREATE TABLE IF NOT EXISTS context_manifests (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_manifests_task ON context_manifests(task_id, created_at);
                CREATE TABLE IF NOT EXISTS research_checkpoints (
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS ui_commands (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                """
            )
            self._connection.execute(
                "INSERT INTO runtime_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SUPPORTED_RUNTIME_SCHEMA),),
            )
            self.runtime_schema_version = SUPPORTED_RUNTIME_SCHEMA
            self._connection.execute(
                """INSERT OR IGNORE INTO task_sources(task_id, source_id, linked_at)
                   SELECT task_id, id, created_at FROM sources WHERE task_id IS NOT NULL AND task_id != ''"""
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def save_context_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        self.ensure_writable()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO context_manifests(id, task_id, thread_id, payload, content_hash, created_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, content_hash=excluded.content_hash""",
                (
                    manifest["id"], manifest["task_id"], manifest["thread_id"],
                    json.dumps(manifest, ensure_ascii=False), manifest["content_hash"], manifest["created_at"],
                ),
            )
        return manifest

    def get_context_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM context_manifests WHERE id=?", (manifest_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_research_checkpoint(
        self, task_id: str, phase: str, payload: dict[str, Any], created_at: str
    ) -> dict[str, Any]:
        self.ensure_writable()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence FROM research_checkpoints WHERE task_id=?",
                (task_id,),
            ).fetchone()
            sequence = int(row["sequence"])
            value = {"task_id": task_id, "sequence": sequence, "phase": phase, "payload": payload, "created_at": created_at}
            self._connection.execute(
                "INSERT INTO research_checkpoints(task_id, sequence, phase, payload, created_at) VALUES(?, ?, ?, ?, ?)",
                (task_id, sequence, phase, json.dumps(value, ensure_ascii=False), created_at),
            )
        return value

    def list_research_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM research_checkpoints WHERE task_id=? ORDER BY sequence", (task_id,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_ui_command(self, command: dict[str, Any]) -> dict[str, Any]:
        self.ensure_writable()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO ui_commands(id, task_id, status, payload, created_at, resolved_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                     payload=excluded.payload, resolved_at=excluded.resolved_at""",
                (
                    command["id"], command["task_id"], command["status"],
                    json.dumps(command, ensure_ascii=False), command["created_at"], command.get("resolved_at"),
                ),
            )
        return command

    def get_ui_command(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM ui_commands WHERE id=?", (command_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_ui_commands(self, task_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload FROM ui_commands WHERE task_id=?"
        values: list[Any] = [task_id]
        if status:
            query += " AND status=?"
            values.append(status)
        query += " ORDER BY created_at, id"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def resolve_ui_command(self, command_id: str, status: str, resolved_at: str) -> dict[str, Any]:
        self.ensure_writable()
        if status not in {"applied", "dismissed"}:
            raise ValueError("invalid UI command resolution")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload FROM ui_commands WHERE id=?", (command_id,)
            ).fetchone()
            if not row:
                raise KeyError("UI command not found")
            command = json.loads(row["payload"])
            if command.get("status") == status:
                return command
            if command.get("status") != "pending":
                raise ValueError("UI command is already resolved")
            command["status"] = status
            command["resolved_at"] = resolved_at
            self._connection.execute(
                "UPDATE ui_commands SET status=?, payload=?, resolved_at=? WHERE id=?",
                (status, json.dumps(command, ensure_ascii=False), resolved_at, command_id),
            )
        return command

    def save_campaign_checkpoint(
        self, campaign_id: str, thread_id: str, status: str, payload: dict[str, Any], updated_at: str
    ) -> dict[str, Any]:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO campaign_checkpoints(campaign_id, thread_id, status, payload, updated_at)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(campaign_id) DO UPDATE SET status=excluded.status,
                     payload=excluded.payload, updated_at=excluded.updated_at""",
                (campaign_id, thread_id, status, json.dumps(payload, ensure_ascii=False), updated_at),
            )
        return payload

    def get_campaign_checkpoint(self, campaign_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM campaign_checkpoints WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_campaign_checkpoints(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM campaign_checkpoints WHERE thread_id=? ORDER BY updated_at DESC", (thread_id,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def append_campaign_event(
        self, campaign_id: str, kind: str, payload: dict[str, Any], created_at: str
    ):
        from ..campaign.models import CampaignEvent

        with self._lock, self._connection:
            seq = int(self._connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM campaign_events WHERE campaign_id=?", (campaign_id,)
            ).fetchone()[0])
            event = CampaignEvent(
                campaign_id=campaign_id, seq=seq, kind=kind, payload=payload, created_at=created_at
            )
            self._connection.execute(
                "INSERT INTO campaign_events(campaign_id, seq, kind, payload, created_at) VALUES(?, ?, ?, ?, ?)",
                (campaign_id, seq, kind, event.model_dump_json(), created_at),
            )
        return event

    def list_campaign_events(self, campaign_id: str, after_seq: int = 0):
        from ..campaign.models import CampaignEvent

        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM campaign_events WHERE campaign_id=? AND seq>? ORDER BY seq",
                (campaign_id, max(0, after_seq)),
            ).fetchall()
        return [CampaignEvent.model_validate_json(row["payload"]) for row in rows]

    def mark_incomplete_interrupted(self, now: str) -> int:
        changed = 0
        for task in self.list_tasks(statuses={"pending", "running", "waiting_approval"}):
            if task.status == "waiting_approval":
                continue
            task.status = "interrupted"
            task.updated_at = now
            self.save_task(task)
            self.append_event(task.id, "error", {"message": "应用重启中断了任务，可继续运行。", "recoverable": True}, now)
            changed += 1
        return changed

    def save_task(self, task: AgentTask) -> AgentTask:
        payload = task.model_dump_json()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_tasks(id, thread_id, status, payload, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at""",
                (task.id, task.thread_id, task.status, payload, task.created_at, task.updated_at),
            )
        return task

    def save_attempt(self, attempt: AgentAttempt) -> AgentAttempt:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_attempts(id, task_id, number, status, payload, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload,
                     updated_at=excluded.updated_at""",
                (attempt.id, attempt.task_id, attempt.number, attempt.status, attempt.model_dump_json(), attempt.created_at, attempt.updated_at),
            )
        return attempt

    def get_attempt(self, attempt_id: str) -> AgentAttempt | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM agent_attempts WHERE id=?", (attempt_id,)).fetchone()
        return AgentAttempt.model_validate_json(row["payload"]) if row else None

    def list_attempts(self, task_id: str) -> list[AgentAttempt]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM agent_attempts WHERE task_id=? ORDER BY number", (task_id,)
            ).fetchall()
        return [AgentAttempt.model_validate_json(row["payload"]) for row in rows]

    def save_tool_call(self, call: ToolCall) -> ToolCall:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_tool_calls(id, task_id, attempt_id, status, payload, created_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload""",
                (call.id, call.task_id, call.attempt_id, call.status, call.model_dump_json(), call.created_at),
            )
        return call

    def list_tool_calls(self, attempt_id: str) -> list[ToolCall]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM agent_tool_calls WHERE attempt_id=? ORDER BY created_at", (attempt_id,)
            ).fetchall()
        return [ToolCall.model_validate_json(row["payload"]) for row in rows]

    def save_observation(self, observation: Observation) -> Observation:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_observations(id, task_id, attempt_id, tool_call_id, payload, created_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                (
                    observation.id,
                    observation.task_id,
                    observation.attempt_id,
                    observation.tool_call_id,
                    observation.model_dump_json(),
                    observation.created_at,
                ),
            )
        return observation

    def list_observations(self, attempt_id: str) -> list[Observation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM agent_observations WHERE attempt_id=? ORDER BY created_at", (attempt_id,)
            ).fetchall()
        return [Observation.model_validate_json(row["payload"]) for row in rows]

    def append_steer(self, steer_id: str, task_id: str, message: str, created_at: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_steers(id, task_id, message, status, created_at) VALUES(?, ?, ?, 'pending', ?)",
                (steer_id, task_id, message, created_at),
            )

    def consume_steers(self, task_id: str, applied_at: str) -> list[dict[str, str]]:
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT id, message, created_at FROM agent_steers WHERE task_id=? AND status='pending' ORDER BY created_at",
                (task_id,),
            ).fetchall()
            if rows:
                self._connection.executemany(
                    "UPDATE agent_steers SET status='applied', applied_at=? WHERE id=?",
                    [(applied_at, row["id"]) for row in rows],
                )
        return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> AgentTask | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        return AgentTask.model_validate_json(row["payload"]) if row else None

    def list_tasks(self, *, thread_id: str | None = None, statuses: set[str] | None = None) -> list[AgentTask]:
        query = "SELECT payload FROM agent_tasks"
        parameters: list[Any] = []
        if thread_id:
            query += " WHERE thread_id=?"
            parameters.append(thread_id)
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        tasks = [AgentTask.model_validate_json(row["payload"]) for row in rows]
        return [task for task in tasks if not statuses or task.status in statuses]

    def append_event(self, task_id: str, kind: str, payload: dict[str, Any], created_at: str) -> AgentEvent:
        with self._lock, self._connection:
            row = self._connection.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM agent_events WHERE task_id=?", (task_id,)).fetchone()
            seq = int(row["seq"]) + 1
            event = AgentEvent(task_id=task_id, seq=seq, kind=kind, payload=payload, created_at=created_at)
            self._connection.execute(
                "INSERT INTO agent_events(task_id, seq, kind, payload, created_at) VALUES(?, ?, ?, ?, ?)",
                (task_id, seq, kind, json.dumps(payload, ensure_ascii=False), created_at),
            )
        return event

    def list_events(self, task_id: str, after_seq: int = 0) -> list[AgentEvent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT task_id, seq, kind, payload, created_at FROM agent_events WHERE task_id=? AND seq>? ORDER BY seq",
                (task_id, after_seq),
            ).fetchall()
        return [
            AgentEvent(task_id=row["task_id"], seq=row["seq"], kind=row["kind"], payload=json.loads(row["payload"]), created_at=row["created_at"])
            for row in rows
        ]

    def save_source(self, source: SourceRecord) -> SourceRecord:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO sources(id, task_id, canonical_key, payload, created_at) VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id, canonical_key=excluded.canonical_key, payload=excluded.payload""",
                (source.id, source.task_id, source.canonical_key, source.model_dump_json(), source.retrieved_at),
            )
            if source.task_id:
                self._connection.execute(
                    "INSERT OR IGNORE INTO task_sources(task_id, source_id, linked_at) VALUES(?, ?, ?)",
                    (source.task_id, source.id, source.retrieved_at),
                )
        return source

    def get_source(self, source_id: str) -> SourceRecord | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM sources WHERE id=?", (source_id,)).fetchone()
        return SourceRecord.model_validate_json(row["payload"]) if row else None

    def list_sources(self, task_id: str) -> list[SourceRecord]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT sources.payload FROM task_sources
                   JOIN sources ON sources.id = task_sources.source_id
                   WHERE task_sources.task_id=? ORDER BY task_sources.linked_at""",
                (task_id,),
            ).fetchall()
        sources = [SourceRecord.model_validate_json(row["payload"]) for row in rows]
        for source in sources:
            source.task_id = task_id
        return sources

    def save_source_query_cache(self, cache_key: str, payload: dict[str, Any], updated_at: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO source_query_cache(cache_key, payload, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (cache_key, json.dumps(payload, ensure_ascii=False), updated_at),
            )

    def get_source_query_cache(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload, updated_at FROM source_query_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        return {**json.loads(row["payload"]), "updated_at": row["updated_at"]}

    def save_artifact(self, artifact: AgentArtifact) -> AgentArtifact:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO artifacts(id, task_id, payload, created_at) VALUES(?, ?, ?, ?)",
                (artifact.id, artifact.task_id, artifact.model_dump_json(), artifact.created_at),
            )
        return artifact

    def get_artifact(self, artifact_id: str) -> AgentArtifact | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return AgentArtifact.model_validate_json(row["payload"]) if row else None

    def save_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO approvals(id, task_id, status, payload, created_at, resolved_at) VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload, resolved_at=excluded.resolved_at""",
                (approval.id, approval.task_id, approval.status, approval.model_dump_json(), approval.created_at, approval.resolved_at),
            )
        return approval

    def save_operation_batch(self, batch):
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO operation_batches(id, task_id, thread_id, status, payload, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at""",
                (batch.id, batch.task_id, batch.thread_id, batch.status, batch.model_dump_json(), batch.created_at, batch.updated_at),
            )
        return batch

    def get_operation_batch(self, batch_id: str):
        from .models import OperationBatch

        with self._lock:
            row = self._connection.execute("SELECT payload FROM operation_batches WHERE id=?", (batch_id,)).fetchone()
        return OperationBatch.model_validate_json(row["payload"]) if row else None

    def list_operation_batches(self, task_id: str):
        from .models import OperationBatch

        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM operation_batches WHERE task_id=? ORDER BY updated_at DESC",
                (task_id,),
            ).fetchall()
        return [OperationBatch.model_validate_json(row["payload"]) for row in rows]

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return ApprovalRequest.model_validate_json(row["payload"]) if row else None

    def save_memory_draft(self, draft: MemoryDraft) -> MemoryDraft:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO memory_drafts(id, task_id, thread_id, scope, status, payload, created_at, resolved_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET scope=excluded.scope, status=excluded.status, payload=excluded.payload, resolved_at=excluded.resolved_at""",
                (draft.id, draft.task_id, draft.thread_id, draft.scope, draft.status, draft.model_dump_json(), draft.created_at, draft.resolved_at),
            )
        return draft

    def get_memory_draft(self, draft_id: str) -> MemoryDraft | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM memory_drafts WHERE id=?", (draft_id,)).fetchone()
        return MemoryDraft.model_validate_json(row["payload"]) if row else None

    def list_memories(self, *, thread_id: str | None = None, scope: str | None = None) -> list[dict[str, Any]]:
        clauses = []
        parameters: list[Any] = []
        if thread_id:
            clauses.append("thread_id=?")
            parameters.append(thread_id)
        if scope:
            clauses.append("scope=?")
            parameters.append(scope)
        query = "SELECT payload FROM long_term_memories"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def promote_memory(self, draft_id: str, resolved_at: str) -> dict[str, Any]:
        draft = self.get_memory_draft(draft_id)
        if not draft:
            raise KeyError("memory draft not found")
        if draft.status != "draft":
            raise ValueError("memory draft is not pending")
        memory = {
            "id": f"memory_{draft.id.removeprefix('memory_draft_')}",
            "source_draft_id": draft.id,
            "thread_id": draft.thread_id,
            "scope": draft.scope,
            "kind": draft.kind,
            "title": draft.title,
            "content": draft.content,
            "rationale": draft.rationale,
            "created_at": resolved_at,
        }
        draft.status = "promoted"
        draft.resolved_at = resolved_at
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO long_term_memories(id, thread_id, scope, payload, created_at) VALUES(?, ?, ?, ?, ?)",
                (memory["id"], draft.thread_id, draft.scope, json.dumps(memory, ensure_ascii=False), resolved_at),
            )
            self._connection.execute(
                "UPDATE memory_drafts SET status=?, payload=?, resolved_at=? WHERE id=?",
                (draft.status, draft.model_dump_json(), resolved_at, draft.id),
            )
        return memory

    def revert_promoted_memory(self, draft_id: str) -> None:
        draft = self.get_memory_draft(draft_id)
        if not draft:
            return
        memory_id = f"memory_{draft.id.removeprefix('memory_draft_')}"
        draft.status = "draft"
        draft.resolved_at = None
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM long_term_memories WHERE id=?", (memory_id,))
            self._connection.execute(
                "UPDATE memory_drafts SET status=?, payload=?, resolved_at=NULL WHERE id=?",
                (draft.status, draft.model_dump_json(), draft.id),
            )

    def save_document(self, document: DocumentRecord, chunks: list[dict[str, Any]]) -> DocumentRecord:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO documents(id, title, file_name, media_type, path, content_hash, payload, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (document.id, document.title, document.file_name, document.media_type, document.path, document.content_hash, document.model_dump_json(), document.created_at),
            )
            self._connection.execute("DELETE FROM document_chunks WHERE document_id=?", (document.id,))
            self._connection.execute("DELETE FROM document_chunks_fts WHERE document_id=?", (document.id,))
            for chunk in chunks:
                self._connection.execute(
                    "INSERT INTO document_chunks(id, document_id, chunk_index, section, text, locator) VALUES(?, ?, ?, ?, ?, ?)",
                    (chunk["id"], document.id, chunk["index"], chunk["section"], chunk["text"], json.dumps(chunk.get("locator") or {}, ensure_ascii=False)),
                )
                self._connection.execute(
                    "INSERT INTO document_chunks_fts(chunk_id, document_id, title, section, text) VALUES(?, ?, ?, ?, ?)",
                    (chunk["id"], document.id, document.title, chunk["section"], chunk["text"]),
                )
        return document

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM documents WHERE id=?", (document_id,)).fetchone()
        return DocumentRecord.model_validate_json(row["payload"]) if row else None

    def find_document_by_hash(self, content_hash: str) -> DocumentRecord | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM documents WHERE content_hash=?", (content_hash,)).fetchone()
        return DocumentRecord.model_validate_json(row["payload"]) if row else None

    def search_documents(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        terms = " ".join(token for token in query.replace('"', " ").split() if token) or query
        with self._lock:
            try:
                rows = self._connection.execute(
                    """SELECT chunk_id, document_id, title, section, text, bm25(document_chunks_fts) AS rank
                       FROM document_chunks_fts WHERE document_chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (terms, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = self._connection.execute(
                    """SELECT id AS chunk_id, document_id, '' AS title, section, text, 0 AS rank
                       FROM document_chunks WHERE text LIKE ? LIMIT ?""",
                    (f"%{query}%", limit),
                ).fetchall()
        return [dict(row) for row in rows]
