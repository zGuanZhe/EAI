from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    atlas_cache_dir: Path
    personal_dir: Path
    threads_dir: Path
    backups_dir: Path
    projects_dir: Path
    objects_dir: Path
    atlas_updates_dir: Path
    lab_runs_dir: Path


def resolve_runtime_paths(default_root: Path) -> RuntimePaths:
    root = Path(os.environ.get("EAI_VNEXT_ROOT", default_root)).expanduser().resolve()
    default_atlas = root / "resources" / "atlas-cache"
    if not default_atlas.exists():
        default_atlas = root / "data" / "atlas-cache"
    default_personal = root / "runtime" / "dev-personal"
    personal_dir = Path(os.environ.get("EAI_PERSONAL_DIR", default_personal)).expanduser().resolve()
    atlas_cache_dir = Path(os.environ.get("EAI_ATLAS_CACHE_DIR", default_atlas)).expanduser().resolve()
    return RuntimePaths(
        root=root,
        atlas_cache_dir=atlas_cache_dir,
        personal_dir=personal_dir,
        threads_dir=personal_dir / "threads",
        backups_dir=personal_dir / "backups",
        projects_dir=personal_dir / "projects",
        objects_dir=personal_dir / "objects",
        atlas_updates_dir=personal_dir / "atlas_updates",
        lab_runs_dir=personal_dir / "lab_runs",
    )


def public_path(path: Path) -> str:
    return str(path.resolve())
