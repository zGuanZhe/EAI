# Workspace And Data Migration

## Repository Migration

Version 0.4 is a new repository rooted at `D:\Test\GUAN\EAI`. Source code, tests, Atlas resources and the pinned AI Scientist v2 derivative were copied without Git history. Dependency folders, virtual environments, targets, distributions, logs, screenshots, sidecar binaries, installers and runtime data were excluded.

`migration/personal-snapshot` intentionally contains only `.gitkeep`; its manifest records the SHA-256 of that empty file. No personal thread, backup, object memory, candidate, LabRun, key or database is part of the repository baseline.

## Installed User Data

The identifier remains `com.eai.desktop`, so 0.4 opens the same `%APPDATA%\com.eai.desktop` directory as an existing installation. Startup copies the bundled personal seed only when the target personal directory does not exist. Existing data is never replaced.

Research Store migration backs up compatible personal JSON, imports IDs/revisions/timestamps transactionally, and keeps old JSON available for recovery and export. Atlas resources are imported idempotently by file hash into the curated layer; derived and personal layers are preserved across resource updates.

Legacy LabRun files are backed up and converted by source ID plus file hash into `source_kind=legacy_lab`, `status=archived` Campaigns. Stages become manual branches; artifacts, findings, transcript, Canvas links and timestamps remain available. The source file stops participating in writes after successful migration.

## Test Isolation

Automated web and service tests use synthetic data in temporary directories. Installed-app smoke sets:

```text
EAI_DESKTOP_TEST_MODE=1
EAI_DESKTOP_APP_DATA_DIR=<absolute temporary directory>
```

Without test mode, the override is ignored. Smoke validation hashes the real AppData tree before and after and fails on any change.

## Rollback

The former workspace and old JSON are not deleted. SQLite schema changes use migration journals and backups. OperationBatch and legacy ChangeSet undo remain conflict-checked. If a Research Store migration cannot complete atomically, the incomplete database is discarded and the legacy data remains readable without writes.
