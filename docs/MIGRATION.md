# Workspace And Data Migration

## Repository Migration

Version 0.5 continues in the repository rooted at `D:\Test\GUAN\EAI`. Source code, tests, Atlas resources and the pinned AI Scientist v2 derivative are self-contained here; `D:\Test\GUAN\EAI-Desktop` is not a build dependency. Dependency folders, virtual environments, targets, distributions, logs, screenshots, sidecar binaries, installers and runtime data remain excluded.

`migration/personal-snapshot` intentionally contains only `.gitkeep`; its manifest records the SHA-256 of that empty file. No personal thread, backup, object memory, candidate, LabRun, key or database is part of the repository baseline.

## Installed User Data

The identifier remains `com.eai.desktop`, so 0.5 uses the same `%APPDATA%\com.eai.desktop` directory as an existing installation. Startup copies the bundled personal seed only when the target personal directory does not exist. Existing data is never replaced.

Before schema 3 -> 4, startup creates a SQLite online backup. Schema 4 adds only `thread_drafts`, `projection_journal`, and `evidence_normalization`. Migration failure restores the original database and does not modify old JSON or Atlas resources. Compatible personal JSON is imported idempotently with IDs/revisions/timestamps; Atlas resources are imported by file hash into the curated layer, while derived and personal layers survive resource updates.

After migration, SQLite is authoritative. A canonical transaction writes the entity and a pending projection journal entry together. JSON projection runs after commit and may fail independently; failure leaves the canonical commit intact and records a rebuildable pending/failed backlog. Replay is keyed by entity revision, ignores duplicates, prevents stale revisions from replacing newer data, and represents deletion with tombstones. This is eventual consistency, not a SQLite/JSON distributed transaction.

Thread drafts store `revision`, `updated_at`, text, Agent mode, and restricted attachment references. PUT and DELETE require expected revision. Sending flushes debounce first and deletes only the revision whose user message has been durably accepted; a newer draft remains on conflict.

Legacy LabRun files are backed up and converted by source ID plus file hash into `source_kind=legacy_lab`, `status=archived` Campaigns. Stages become manual branches; artifacts, findings, transcript, Canvas links and timestamps remain available. The source file stops participating in writes after successful migration.

## Test Isolation

Automated web and service tests use synthetic data in temporary directories. Installed-app smoke sets:

```text
EAI_DESKTOP_TEST_MODE=1
EAI_DESKTOP_APP_DATA_DIR=<absolute temporary directory>
```

Without test mode, the override is ignored. Smoke validation hashes the real AppData tree before and after and fails on any change.

## Downgrade And Rollback

The official downgrade target is `0.4.1`, never the original `0.4.0`. The 0.4.1 bridge detects a schema newer than it supports and reopens SQLite with `mode=ro`; reads, export and audit remain available, while every repository/service write boundary returns structured `409 schema_newer_than_app`. Read-only POST previews and searches remain usable. It does not downgrade, rebuild indexes, write Campaign state, or attempt to preserve compatibility by mutating schema 4 data.

Rollback validation must prove more than “0.4 opens”: 0.4.1 must read a schema 4 fixture, every attempted write must have zero side effects, and reopening in 0.5 must retain drafts, projection journal, Campaign state and revisions. OperationBatch and legacy ChangeSet undo remain conflict-checked. A failed schema migration restores the online backup; a failed JSON projection is replayed and does not roll back canonical SQLite state.
