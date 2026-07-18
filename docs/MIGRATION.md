# Workspace And Data Migration

## Repository Migration

Version 1.0 is self-contained in this repository. Source code, tests, Atlas resources and the pinned AI Scientist v2 derivative do not depend on the former experimental workspace. Dependency folders, virtual environments, targets, distributions, logs, screenshots, sidecar binaries, installers and runtime data remain excluded.

`migration/personal-snapshot` intentionally contains only `.gitkeep`; its manifest records the SHA-256 of that empty file. No personal thread, backup, object memory, candidate, LabRun, key or database is part of the repository baseline.

## Installed User Data

Version 1.0 uses Research Store schema 4 and Runtime Store schema 2. Before runtime migration, EAI creates an online Research Store snapshot and a runtime schema backup. Runtime initialization failure restores `runtime.db`; Research Store is not modified by the runtime migration. The two backups are recovery artifacts, not a cross-database atomic transaction.

Runtime schema 2 adds ContextManifest, ResearchTask checkpoints and UICommand receipts. If either research or runtime schema is newer than the running application, the entire application starts read-only: reads, export, audit and non-writing previews remain available, while Workspace, Agent and Campaign writes return structured `409 schema_newer_than_app`.

The identifier remains `com.eai.desktop`. Startup copies the bundled personal seed only when the target personal directory does not exist. Existing data is never replaced.

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

Version 1.0 does not support downgrade to experimental 0.x packages. Before changing major versions, back up `%APPDATA%\com.eai.desktop`. A future older application that encounters a newer Research Store or Runtime Store schema must force the whole application read-only; it must not downgrade, rebuild indexes, write Campaign state or mutate Agent tasks. A failed schema migration restores the online backup; a failed JSON projection remains replayable and does not roll back canonical SQLite state.
