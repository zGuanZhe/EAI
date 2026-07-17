from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


UPSTREAM_COMMIT = "96bd51617cfdbb494a9fc283af00fe090edfae48"
IMAGE_PREFIX = "eai-ai-scientist-v2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hidden_process_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class CampaignRuntimeManager:
    """Builds and reports the isolated AI Scientist runtime without exposing a terminal."""

    def __init__(self, runtime_dir: Path, source_dir: Path | None = None):
        self.runtime_dir = runtime_dir / "campaign-runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[4]
        configured_source = os.environ.get("EAI_CAMPAIGN_SOURCE_DIR")
        self.source_dir = (source_dir or (Path(configured_source) if configured_source else root / "third_party" / "ai-scientist-v2")).resolve()
        self.state_path = self.runtime_dir / "state.json"
        self.log_path = self.runtime_dir / "install.log"
        self.events_path = self.runtime_dir / "events.jsonl"
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def source_hash(self) -> str:
        digest = hashlib.sha256()
        for name in ["requirements.txt", "Dockerfile.eai-cpu", "Dockerfile.eai-cuda", "eai_campaign_runner.py"]:
            path = self.source_dir / name
            if path.exists():
                digest.update(path.read_bytes())
        digest.update(UPSTREAM_COMMIT.encode("ascii"))
        return digest.hexdigest()[:16]

    def image(self, profile: str = "cpu") -> str:
        return f"{IMAGE_PREFIX}:{profile}-{self.source_hash}"

    def _docker(self, *args: str, timeout: int = 8) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, creationflags=hidden_process_flags(),
        )

    def _image_info(self, profile: str) -> dict[str, Any]:
        if not shutil.which("docker"):
            return {"installed": False, "image": self.image(profile), "digest": ""}
        try:
            result = self._docker("image", "inspect", self.image(profile), "--format", "{{.Id}}")
            return {
                "installed": result.returncode == 0,
                "image": self.image(profile),
                "digest": result.stdout.strip() if result.returncode == 0 else "",
            }
        except (OSError, subprocess.SubprocessError):
            return {"installed": False, "image": self.image(profile), "digest": ""}

    def status(self) -> dict[str, Any]:
        state = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
        docker = bool(shutil.which("docker"))
        cuda = False
        if docker:
            try:
                info = self._docker("info", "--format", "{{json .Runtimes}}", timeout=5)
                cuda = info.returncode == 0 and "nvidia" in info.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        return {
            "docker_available": docker,
            "cuda_available": cuda,
            "profiles": ["cpu", *(["cuda"] if cuda else [])],
            "host_execution_allowed": False,
            "source_commit": UPSTREAM_COMMIT,
            "source_hash": self.source_hash,
            "cpu": self._image_info("cpu"),
            "cuda": self._image_info("cuda") if cuda else {"installed": False, "image": self.image("cuda"), "digest": ""},
            "install": state,
            "log_path": str(self.log_path),
        }

    def _write_state(self, **patch: Any) -> dict[str, Any]:
        state = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        state.update(patch)
        state["updated_at"] = utc_now()
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
        return state

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        event = {"kind": kind, "payload": payload, "created_at": utc_now()}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def install(self, profile: str = "cpu") -> dict[str, Any]:
        if profile not in {"cpu", "cuda"}:
            raise ValueError("未知 Campaign Runtime profile")
        if not shutil.which("docker"):
            raise RuntimeError("Docker 不可用，无法安装 Campaign Runtime")
        with self._lock:
            if self._worker and self._worker.is_alive():
                return self.status()
            self._cancel.clear()
            self._write_state(status="installing", profile=profile, progress=0, error="")
            self._emit("runtime_install_started", {"profile": profile})
            self._worker = threading.Thread(target=self._build, args=(profile,), daemon=True)
            self._worker.start()
        return self.status()

    def _build(self, profile: str) -> None:
        dockerfile = self.source_dir / f"Dockerfile.eai-{profile}"
        command = ["docker", "build", "--progress=plain", "-f", str(dockerfile), "-t", self.image(profile), str(self.source_dir)]
        try:
            with self.log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=hidden_process_flags(),
                )
                lines = 0
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    lines += 1
                    if lines % 20 == 0:
                        progress = min(95, 5 + lines // 3)
                        self._write_state(status="installing", profile=profile, progress=progress)
                        self._emit("runtime_install_progress", {"profile": profile, "progress": progress})
                    if self._cancel.is_set():
                        process.terminate()
                        self._write_state(status="cancelled", profile=profile, progress=0)
                        self._emit("runtime_install_cancelled", {"profile": profile})
                        return
                code = process.wait()
            if code != 0:
                raise RuntimeError(f"Docker build exited with {code}")
            info = self._image_info(profile)
            self._write_state(status="ready", profile=profile, progress=100, image=info["image"], digest=info["digest"])
            self._emit("runtime_install_completed", info)
        except Exception as exc:
            self._write_state(status="failed", profile=profile, error=str(exc), progress=0)
            self._emit("runtime_install_failed", {"profile": profile, "error": str(exc)})

    def cancel_install(self) -> dict[str, Any]:
        self._cancel.set()
        return self.status()

    def events(self, after: int = 0) -> Iterator[str]:
        cursor = max(0, after)
        idle = 0
        while idle < 300:
            lines = self.events_path.read_text(encoding="utf-8").splitlines() if self.events_path.exists() else []
            while cursor < len(lines):
                cursor += 1
                yield f"id: {cursor}\nevent: runtime\ndata: {lines[cursor - 1]}\n\n"
            state = self.status().get("install", {})
            if state.get("status") in {"ready", "failed", "cancelled"}:
                break
            idle += 1
            if idle % 15 == 0:
                yield ": keep-alive\n\n"
            time.sleep(0.2)
