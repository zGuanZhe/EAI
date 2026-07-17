from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path


def write_desktop_log(log_dir: Path, message: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "service.log").open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EAI Desktop FastAPI sidecar")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--session-token", required=True)
    parser.add_argument("--atlas-cache-dir", type=Path, required=True)
    parser.add_argument("--personal-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--campaign-source-dir", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--root", type=Path)
    return parser.parse_args()


def configure_environment(args: argparse.Namespace) -> None:
    args.personal_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["EAI_DESKTOP_MODE"] = "1"
    os.environ["EAI_DESKTOP_SESSION_TOKEN"] = args.session_token
    os.environ["EAI_PERSONAL_DIR"] = str(args.personal_dir.resolve())
    os.environ["EAI_ATLAS_CACHE_DIR"] = str(args.atlas_cache_dir.resolve())
    os.environ["EAI_DESKTOP_LOG_DIR"] = str(args.log_dir.resolve())
    if args.campaign_source_dir:
        os.environ["EAI_CAMPAIGN_SOURCE_DIR"] = str(args.campaign_source_dir.resolve())
    if args.root:
        os.environ["EAI_VNEXT_ROOT"] = str(args.root.resolve())


def main() -> None:
    args = parse_args()
    configure_environment(args)

    import uvicorn
    from app.main import app

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    port = listener.getsockname()[1]
    ready_payload = {"event": "ready", "port": port}
    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.ready_file.with_suffix(args.ready_file.suffix + ".tmp")
        temporary.write_text(json.dumps(ready_payload), encoding="utf-8")
        temporary.replace(args.ready_file)
    if sys.stdout is not None:
        print(json.dumps(ready_payload), flush=True)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        log_config=None,
        access_log=False,
    )
    uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        payload = json.dumps({"event": "fatal", "message": str(exc)}, ensure_ascii=False)
        log_dir = Path(os.environ.get("EAI_DESKTOP_LOG_DIR", Path.cwd()))
        write_desktop_log(log_dir, payload)
        if sys.stderr is not None:
            print(payload, file=sys.stderr, flush=True)
        raise
