"""FakeNewsGuard -- FastAPI Server with job-queue + polling.

Instead of SSE (which drops on mobile / screen lock), analysis runs as a
background job.  The client:
  1. POST /api/analyze          -> { job_id }
  2. GET  /api/jobs/{job_id}    -> { status, steps, result?, error? }
     (poll every ~1.5 s until status == "done" | "error")

Usage:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import AppConfig
from i18n import set_default_locale
from tools.logger import record_request, setup_logging

from .dependencies import cleanup_old_jobs, correlation_id, logger

# ── Logging einrichten ─────────────────────────────────────────────
setup_logging()

app = FastAPI(title="FakeNewsGuard API")

# i18n auf konfigurierte Sprache setzen
set_default_locale(AppConfig().language)

# CORS -- konfigurierbar via CORS_ORIGINS Umgebungsvariable.
# Standard: "*" (alle Origins), fuer Produktion explizit setzen.
_cors_origins = AppConfig().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ── HTTP Middleware: Request-Logging + Metriken ────────────────────
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any) -> Any:
    cid = str(uuid.uuid4())[:8]
    correlation_id.set(cid)
    start = time.monotonic()
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else "unknown"

    logger.info("-> %s %s [%s] rid=%s", method, path, client_ip, cid)
    try:
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, response.status_code, duration_ms)
        logger.info(
            "<- %s %s %d %.1fms rid=%s",
            method, path, response.status_code, duration_ms, cid,
        )
        response.headers["X-Request-ID"] = cid
        # Periodisch abgelaufene Jobs bereinigen (guenstig, laeuft nur bei Requests)
        cleanup_old_jobs()
        return response
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, 500, duration_ms)
        logger.exception("x %s %s ERROR %.1fms rid=%s: %s", method, path, duration_ms, cid, exc)
        raise


# ── Include all routers ────────────────────────────────────────────
from .auth import router as auth_router
from .admin import router as admin_router
from .analysis import router as analysis_router
from .archive import router as archive_router
from .export import router as export_router
from .graph import router as graph_router
from .utils import router as utils_router

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(analysis_router)
app.include_router(archive_router)
app.include_router(export_router)
app.include_router(graph_router)
app.include_router(utils_router)

# ── Backward-compatibility re-exports ──────────────────────────────
# Tests and other code may reference api._jobs, api._run_job, etc.
from .dependencies import jobs as _jobs
from .dependencies import _get_rate_limiter, cleanup_old_jobs as _cleanup_old_jobs
from .dependencies import transform_result as _transform_result
from .analysis import _run_job

# Allow tests to reset rate limiter via api._rate_limiter = None
import api.dependencies as _deps


def __getattr__(name: str):
    if name == "_rate_limiter":
        return _deps._rate_limiter
    raise AttributeError(f"module 'api' has no attribute {name!r}")


def _set_rate_limiter(value):
    _deps._rate_limiter = value


# Support api._rate_limiter = None via module-level setattr
import sys
_this = sys.modules[__name__]
_original_class = type(_this)


class _ModuleWithSetattr(_original_class):
    def __setattr__(self, name, value):
        if name == "_rate_limiter":
            _deps._rate_limiter = value
            return
        super().__setattr__(name, value)


_this.__class__ = _ModuleWithSetattr

__all__ = ["app"]
