from __future__ import annotations

import os
from pathlib import Path

from ..core.paths import public_path
from ..core.secrets import read_secret_data, safe_secret_status


def current_runtime_mode() -> str:
    return "desktop" if os.environ.get("EAI_DESKTOP_MODE") == "1" else "browser"


def build_system_info(
    *,
    service_version: str,
    root: Path,
    personal_dir: Path,
    atlas_cache_dir: Path,
    secret_candidates: list[Path | None],
) -> dict[str, object]:
    data, path = read_secret_data(secret_candidates)
    runtime_mode = current_runtime_mode()
    return {
        "service_version": service_version,
        "runtime_mode": runtime_mode,
        "app_root": public_path(root),
        "personal_dir": public_path(personal_dir),
        "atlas_cache_dir": public_path(atlas_cache_dir),
        "log_dir": public_path(Path(os.environ.get("EAI_DESKTOP_LOG_DIR", personal_dir / "logs"))),
        "active_backend_id": f"{root.name}:{service_version}",
        "secrets_status": safe_secret_status(data, path, bool(os.environ.get("OPENAI_API_KEY"))),
    }
