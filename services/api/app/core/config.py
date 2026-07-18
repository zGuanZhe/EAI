from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import RuntimePaths, resolve_runtime_paths
from .secrets import secret_candidates


@dataclass(frozen=True)
class ApplicationConfig:
    service_version: str
    paths: RuntimePaths
    runtime_dir: Path
    secret_candidates: tuple[Path | None, ...]

    @classmethod
    def load(cls, default_root: Path, *, service_version: str = "1.0.0") -> "ApplicationConfig":
        paths = resolve_runtime_paths(default_root)
        return cls(
            service_version=service_version,
            paths=paths,
            runtime_dir=paths.personal_dir.parent / "runtime",
            secret_candidates=tuple(secret_candidates()),
        )

    @classmethod
    def for_paths(
        cls,
        paths: RuntimePaths,
        *,
        service_version: str = "1.0.0",
        secret_paths: tuple[Path | None, ...] = (),
    ) -> "ApplicationConfig":
        return cls(
            service_version=service_version,
            paths=paths,
            runtime_dir=paths.personal_dir.parent / "runtime",
            secret_candidates=secret_paths,
        )
