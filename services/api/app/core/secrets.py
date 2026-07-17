from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def secret_candidates() -> list[Path | None]:
    return [
        Path(os.environ["EAI_VNEXT_SECRETS"]).expanduser() if os.environ.get("EAI_VNEXT_SECRETS") else None,
        Path.home() / ".eai-desktop" / "secrets.json",
        Path.home() / ".config" / "eai-desktop" / "secrets.json",
    ]


def read_secret_data(candidates: list[Path | None]) -> tuple[dict[str, Any] | None, Path | None]:
    path = next((candidate for candidate in candidates if candidate and candidate.exists()), None)
    if not path:
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, path
    return data if isinstance(data, dict) else None, path


def safe_secret_status(data: dict[str, Any] | None, path: Path | None, env_key: bool) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    if data:
        raw_router = data.get("openrouter") or data.get("OpenRouter") or data.get("OPENROUTER_API_KEY")
        if raw_router:
            model = raw_router.get("model") if isinstance(raw_router, dict) else data.get("openrouter_model")
            providers.append({"provider": "openrouter", "configured": True, "model": model or "默认模型"})
        raw = data.get("openai") or data.get("OpenAI") or data.get("OPENAI_API_KEY")
        if raw:
            model = raw.get("model") if isinstance(raw, dict) else data.get("openai_model")
            providers.append({"provider": "openai", "configured": True, "model": model or "默认模型"})
    if os.environ.get("OPENROUTER_API_KEY") and not any(item["provider"] == "openrouter" for item in providers):
        providers.append({"provider": "openrouter", "configured": True, "model": os.environ.get("OPENROUTER_MODEL", "openrouter/auto")})
    if env_key and not providers:
        providers.append({"provider": "openai", "configured": True, "model": os.environ.get("OPENAI_MODEL", "默认模型")})
    return {
        "configured": bool(providers),
        "providers": providers,
        "source": "environment" if (env_key or os.environ.get("OPENROUTER_API_KEY")) else ("user_config" if path else "none"),
    }
