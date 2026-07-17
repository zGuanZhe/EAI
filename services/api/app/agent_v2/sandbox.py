from __future__ import annotations

import shutil
import subprocess
import json
from pathlib import Path

from .models import SandboxCommand


class SandboxUnavailable(RuntimeError):
    pass


def docker_available() -> bool:
    return shutil.which("docker") is not None


def docker_gpu_available() -> bool:
    if not docker_available():
        return False
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5, check=False,
        )
        runtimes = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
        return "nvidia" in runtimes
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def docker_arguments(spec: SandboxCommand, workspace: Path, readonly_inputs: list[Path] | None = None) -> list[str]:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    args = [
        "docker",
        "run",
        "--rm",
        "--cpus",
        str(spec.cpus),
        "--memory",
        f"{spec.memory_mb}m",
        "--network",
        "bridge" if spec.network else "none",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--workdir",
        "/workspace",
    ]
    if spec.container_name:
        args[2:2] = ["--name", spec.container_name]
    if spec.gpus:
        args.extend(["--gpus", spec.gpus])
    for index, path in enumerate(readonly_inputs or []):
        resolved = path.resolve()
        args.extend(["--mount", f"type=bind,src={resolved},dst=/inputs/{index},readonly"])
    args.extend([spec.image, "sh", "-lc", spec.command])
    return args


def run_docker_command(spec: SandboxCommand, workspace: Path, readonly_inputs: list[Path] | None = None) -> dict:
    if not docker_available():
        raise SandboxUnavailable("Docker 不可用，已拒绝回退到宿主机执行。")
    args = docker_arguments(spec, workspace, readonly_inputs)
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=spec.timeout_seconds,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-12000:],
        "command": spec.command,
        "network": spec.network,
        "workspace": str(workspace),
    }


def stop_docker_container(container_name: str) -> bool:
    if not docker_available() or not container_name:
        return False
    completed = subprocess.run(
        ["docker", "stop", "--time", "2", container_name],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
    )
    return completed.returncode == 0
