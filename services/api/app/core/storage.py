from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"{path.name} not found") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{path.name} is invalid JSON") from exc


def atomic_write_json_file(path: Path, data: Any, ensure_dirs: Callable[[], None]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def backup_file(path: Path, backup_dir: Path, prefix: str = "") -> None:
    if not path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{prefix}{path.stem}-{stamp}.json"
    shutil.copy2(path, backup_dir / name)
