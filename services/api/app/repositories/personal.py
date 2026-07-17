from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException


def safe_document_path(directory: Path, identifier: str, label: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise HTTPException(status_code=400, detail=f"invalid {label} id")
    return directory / f"{identifier}.json"


def safe_object_memory_path(objects_dir: Path, atlas_id: str, object_type: str, object_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", atlas_id):
        raise HTTPException(status_code=400, detail="invalid atlas id")
    if object_type not in {"paper", "relation", "path", "file"}:
        raise HTTPException(status_code=400, detail="invalid object type")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", object_id):
        raise HTTPException(status_code=400, detail="invalid object id")
    return objects_dir / atlas_id / object_type / f"{object_id}.json"
