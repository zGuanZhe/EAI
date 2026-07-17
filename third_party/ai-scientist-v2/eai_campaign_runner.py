"""EAI's sandbox entrypoint around the vendored AI Scientist v2 Journal.

The process accepts one JSON document on stdin and emits JSONL events. Provider
secrets are deliberately absent; model planning is prepared by the EAI sidecar.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from ai_scientist.treesearch.journal import Journal, Node


def emit(kind: str, payload: dict) -> None:
    print(json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False), flush=True)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    workspace = Path("/workspace")
    code_path = workspace / "runfile.py"
    journal_path = workspace / "journal.json"
    node = Node(
        id=str(payload.get("journal_node_id") or payload.get("branch_id") or "branch"),
        plan=str(payload.get("plan") or ""), overall_plan=str(payload.get("objective") or ""),
        code=code_path.read_text(encoding="utf-8") if code_path.exists() else "",
    )
    journal = Journal()
    journal.append(node)
    emit("command_started", {"command": payload.get("command", "python runfile.py")})
    started = time.monotonic()
    result = subprocess.run(
        ["sh", "-lc", str(payload.get("command") or "python runfile.py")], cwd=workspace,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    node._term_out = (result.stdout + "\n" + result.stderr).splitlines()
    node.exec_time = time.monotonic() - started
    node.is_buggy = result.returncode != 0
    node.is_buggy_plots = False
    node.exc_type = "ProcessError" if result.returncode else None
    node.analysis = "Execution failed" if result.returncode else "Execution completed"
    checkpoint = {
        "journal_version": "ai-scientist-v2/eai-1", "nodes": [{
            "id": node.id, "step": node.step, "plan": node.plan, "overall_plan": node.overall_plan,
            "code_hash": hashlib.sha256(node.code.encode("utf-8")).hexdigest(),
            "is_buggy": node.is_buggy, "analysis": node.analysis, "exec_time": node.exec_time,
        }],
    }
    journal_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("checkpoint_saved", {"path": "journal.json", "step": node.step, "journal_node_id": node.id})
    emit("command_completed", {
        "exit_code": result.returncode, "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-12000:], "exec_time": node.exec_time,
    })
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
