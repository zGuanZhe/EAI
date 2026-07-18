from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .core.errors import RevisionConflictError, SchemaReadOnlyError


class DesktopSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("EAI_DESKTOP_SESSION_TOKEN", "")
        if expected and request.method != "OPTIONS" and request.url.path.startswith("/api/vnext"):
            actual = request.headers.get("X-EAI-Session", "")
            if not hmac.compare_digest(actual, expected):
                return JSONResponse(status_code=401, content={"detail": "桌面会话无效，请重新启动应用。"})
        return await call_next(request)


def create_app(*, lifespan: Any = None) -> FastAPI:
    app = FastAPI(title="EAI Desktop Service", version="0.5.0", lifespan=lifespan)
    app.add_middleware(DesktopSessionMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://tauri.localhost",
            "tauri://localhost",
            "https://tauri.localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(SchemaReadOnlyError)
    async def schema_read_only_handler(_request: Request, error: SchemaReadOnlyError):
        return JSONResponse(status_code=409, content={"detail": error.detail})

    @app.exception_handler(RevisionConflictError)
    async def revision_conflict_handler(_request: Request, error: RevisionConflictError):
        return JSONResponse(status_code=409, content={"detail": error.detail})

    return app
