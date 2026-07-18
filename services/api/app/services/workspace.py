from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import RevisionConflictError
from ..core.storage import backup_file
from ..research.store import ResearchStore
from ..schemas.models import (
    CanvasNode,
    CanvasState,
    Message,
    ProjectCreate,
    ProjectDoc,
    ProjectUpdate,
    ThreadCreate,
    ThreadDoc,
    ThreadUpdate,
)
from .projection import ProjectionService


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class WorkspaceService:
    def __init__(
        self,
        store: ResearchStore,
        *,
        projects_dir: Path,
        threads_dir: Path,
        backups_dir: Path,
    ) -> None:
        self.store = store
        self.projects_dir = projects_dir.resolve()
        self.threads_dir = threads_dir.resolve()
        self.backups_dir = backups_dir.resolve()
        self.projector = ProjectionService(store, store.personal_dir)

    def ensure_dirs(self) -> None:
        for directory in (self.projects_dir, self.threads_dir, self.backups_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, identifier: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError(f"invalid {kind} id")
        directory = self.projects_dir if kind == "project" else self.threads_dir
        return directory / f"{identifier}.json"

    def _projection_target(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.store.personal_dir):
            raise ValueError("projection target must remain inside personal data")
        return resolved.relative_to(self.store.personal_dir).as_posix()

    def _load(self, kind: str, identifier: str) -> dict[str, Any]:
        payload = self.store.get_record(kind, identifier)
        if payload is not None:
            return payload
        path = self._path(kind, identifier)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"{kind} not found") from exc
        self.store.save_record(kind, identifier, payload)
        return payload

    def _save(self, kind: str, identifier: str, payload: dict[str, Any], path: Path) -> None:
        self.store.save_record(kind, identifier, payload, projection_target=self._projection_target(path))
        self.projector.replay(entity_kind=kind, entity_id=identifier, limit=20)

    def load_project(self, project_id: str) -> ProjectDoc:
        return ProjectDoc.model_validate(self._load("project", project_id))

    def write_project(self, project: ProjectDoc) -> ProjectDoc:
        self.store.ensure_writable()
        project = ProjectDoc.model_validate(project.model_dump(mode="json"))
        project.updated_at = utc_now()
        self._save("project", project.id, project.model_dump(mode="json"), self._path("project", project.id))
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        self.ensure_dirs()
        projects = [ProjectDoc.model_validate(item).model_dump(mode="json") for item in self.store.list_records("project")]
        return sorted(projects, key=lambda item: item["updated_at"], reverse=True)

    def create_project(self, payload: ProjectCreate) -> ProjectDoc:
        now = utc_now()
        return self.write_project(ProjectDoc(
            id=new_id("project"),
            title=payload.title.strip() or "Untitled outcome",
            goal=payload.goal,
            default_atlas_id=payload.default_atlas_id,
            created_at=now,
            updated_at=now,
        ))

    def update_project(self, project_id: str, payload: ProjectUpdate) -> ProjectDoc:
        raw = self.load_project(project_id).model_dump(mode="json")
        raw.update(payload.model_dump(exclude_unset=True, mode="json"))
        raw["id"] = project_id
        return self.write_project(ProjectDoc.model_validate(raw))

    def delete_project(self, project_id: str) -> dict[str, Any]:
        path = self._path("project", project_id)
        if not path.exists() and self.store.get_record("project", project_id) is None:
            raise KeyError("project not found")
        if path.exists():
            backup_file(path, self.backups_dir, prefix="deleted-project-")
        self.store.delete_record("project", project_id, projection_target=self._projection_target(path))
        self.projector.replay(entity_kind="project", entity_id=project_id, limit=20)
        reassigned = 0
        for raw in self.store.list_records("thread"):
            try:
                thread = ThreadDoc.model_validate(raw)
            except ValueError:
                continue
            if thread.project_id == project_id:
                thread.project_id = None
                self.write_thread(thread)
                reassigned += 1
        return {"deleted": project_id, "reassigned_threads": reassigned}

    def load_thread(self, thread_id: str) -> ThreadDoc:
        return ThreadDoc.model_validate(self._load("thread", thread_id))

    def write_thread(self, thread: ThreadDoc, *, backup: bool = True) -> ThreadDoc:
        self.store.ensure_writable()
        thread = ThreadDoc.model_validate(thread.model_dump(mode="json"))
        path = self._path("thread", thread.id)
        if backup:
            backup_file(path, self.backups_dir)
        thread.revision += 1
        thread.updated_at = utc_now()
        self._save("thread", thread.id, thread.model_dump(mode="json"), path)
        return thread

    @staticmethod
    def thread_summary(thread: ThreadDoc) -> dict[str, Any]:
        return {
            "id": thread.id,
            "project_id": thread.project_id,
            "title": thread.title,
            "goal": thread.goal,
            "status": thread.status,
            "updated_at": thread.updated_at,
            "active_surface": thread.active_surface,
            "active_atlas_id": thread.active_atlas_id,
            "context_count": len(thread.context_cards),
            "result_count": len(thread.result_cards),
        }

    def list_threads(self) -> list[dict[str, Any]]:
        self.ensure_dirs()
        threads = [self.thread_summary(ThreadDoc.model_validate(item)) for item in self.store.list_records("thread")]
        return sorted(threads, key=lambda item: item["updated_at"], reverse=True)

    def create_thread(self, payload: ThreadCreate) -> ThreadDoc:
        now = utc_now()
        title = payload.title.strip() or "Untitled research thread"
        thread = ThreadDoc(
            id=new_id("thread"),
            project_id=payload.project_id,
            title=title,
            goal=payload.goal,
            created_at=now,
            updated_at=now,
            active_atlas_id=payload.active_atlas_id,
            canvas=CanvasState(nodes=[CanvasNode(
                id=new_id("q"), type="question", title=title, body=payload.goal, x=120, y=120,
            )]),
            messages=[Message(
                id=new_id("msg"), role="system", kind="state",
                content=f"已创建研究问题：{title}", created_at=now, surface="thread",
                refs={"active_atlas_id": payload.active_atlas_id},
            )],
        )
        return self.write_thread(thread, backup=False)

    def update_thread(self, thread_id: str, payload: ThreadUpdate) -> ThreadDoc:
        thread = self.load_thread(thread_id)
        if payload.expected_revision is not None and payload.expected_revision != thread.revision:
            raise RevisionConflictError({
                "message": "线程已被其他操作更新",
                "current_revision": thread.revision,
            })
        raw = thread.model_dump(mode="json")
        changes = payload.model_dump(exclude_unset=True, mode="json")
        changes.pop("expected_revision", None)
        raw.update(changes)
        return self.write_thread(ThreadDoc.model_validate(raw))

    def delete_thread(self, thread_id: str) -> dict[str, str]:
        path = self._path("thread", thread_id)
        if not path.exists() and self.store.get_record("thread", thread_id) is None:
            raise KeyError("thread not found")
        if path.exists():
            backup_file(path, self.backups_dir, prefix="deleted-thread-")
        self.store.delete_record("thread", thread_id, projection_target=self._projection_target(path))
        self.projector.replay(entity_kind="thread", entity_id=thread_id, limit=20)
        return {"deleted": thread_id}
