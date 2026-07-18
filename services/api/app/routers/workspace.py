from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from ..schemas.models import ProjectCreate, ProjectDoc, ProjectUpdate, ThreadCreate, ThreadDoc, ThreadUpdate
from ..services.workspace import WorkspaceService


def create_workspace_router(get_service: Callable[[], WorkspaceService]) -> APIRouter:
    router = APIRouter(prefix="/api/vnext")

    def service() -> WorkspaceService:
        return get_service()

    @router.get("/projects")
    def list_projects() -> list[dict[str, Any]]:
        return service().list_projects()

    @router.post("/projects", response_model=ProjectDoc)
    def create_project(payload: ProjectCreate) -> ProjectDoc:
        return service().create_project(payload)

    @router.get("/projects/{project_id}", response_model=ProjectDoc)
    def get_project(project_id: str) -> ProjectDoc:
        try:
            return service().load_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/projects/{project_id}", response_model=ProjectDoc)
    def update_project(project_id: str, payload: ProjectUpdate) -> ProjectDoc:
        try:
            return service().update_project(project_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, Any]:
        try:
            return service().delete_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/threads")
    def list_threads() -> list[dict[str, Any]]:
        return service().list_threads()

    @router.post("/threads", response_model=ThreadDoc)
    def create_thread(payload: ThreadCreate) -> ThreadDoc:
        return service().create_thread(payload)

    @router.get("/threads/{thread_id}", response_model=ThreadDoc)
    def get_thread(thread_id: str) -> ThreadDoc:
        try:
            return service().load_thread(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/threads/{thread_id}", response_model=ThreadDoc)
    def update_thread(thread_id: str, payload: ThreadUpdate) -> ThreadDoc:
        try:
            return service().update_thread(thread_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/threads/{thread_id}")
    def delete_thread(thread_id: str) -> dict[str, str]:
        try:
            return service().delete_thread(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="thread not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
