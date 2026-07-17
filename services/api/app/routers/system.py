from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ..services.system import build_system_info, current_runtime_mode


def create_system_router(
    *,
    service_version: str,
    root: Path,
    personal_dir: Path,
    atlas_cache_dir: Path,
    secret_candidates: list[Path | None],
) -> APIRouter:
    router = APIRouter(prefix="/api/vnext", tags=["system"])

    @router.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service_version": service_version,
            "runtime_mode": current_runtime_mode(),
        }

    @router.get("/system/info")
    def system_info() -> dict[str, object]:
        return build_system_info(
            service_version=service_version,
            root=root,
            personal_dir=personal_dir,
            atlas_cache_dir=atlas_cache_dir,
            secret_candidates=secret_candidates,
        )

    return router
