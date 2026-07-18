from __future__ import annotations

import json
from pathlib import Path

from ..core.storage import atomic_write_json_file
from ..research.store import ResearchStore


class ProjectionService:
    def __init__(self, store: ResearchStore, personal_dir: Path):
        self.store = store
        self.personal_dir = personal_dir.resolve()

    def _target(self, relative: str) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("invalid projection target")
        target = (self.personal_dir / value).resolve()
        if not target.is_relative_to(self.personal_dir):
            raise ValueError("projection target escaped personal data directory")
        return target

    def replay(self, *, entity_kind: str | None = None, entity_id: str | None = None, limit: int = 100) -> dict[str, int]:
        result = {"applied": 0, "failed": 0, "superseded": 0}
        jobs = self.store.list_projection_jobs(entity_kind=entity_kind, entity_id=entity_id, limit=limit)
        latest: dict[tuple[str, str], int] = {
            (job["entity_kind"], job["entity_id"]): self.store.latest_projection_revision(
                job["entity_kind"], job["entity_id"]
            )
            for job in jobs
        }
        for job in jobs:
            key = (job["entity_kind"], job["entity_id"])
            if int(job["entity_revision"]) < latest[key]:
                self.store.mark_projection_job(job["id"], "superseded")
                result["superseded"] += 1
                continue
            try:
                target = self._target(job["target_relpath"])
                if job["operation"] == "delete":
                    target.unlink(missing_ok=True)
                else:
                    atomic_write_json_file(target, json.loads(job["payload"]), lambda: self.personal_dir.mkdir(parents=True, exist_ok=True))
                self.store.mark_projection_job(job["id"], "applied")
                result["applied"] += 1
            except Exception as error:
                self.store.mark_projection_job(job["id"], "failed", type(error).__name__)
                result["failed"] += 1
        return result
