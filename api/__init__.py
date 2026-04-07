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
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace as _otrace

from config import AppConfig
from i18n import set_default_locale
from tools.logger import record_request, setup_logging
from tools.telemetry import (
    REQUEST_COUNT,
    REQUEST_DURATION,
    normalize_path,
    setup_telemetry,
)

from .dependencies import correlation_id, logger

# ── Logging einrichten ─────────────────────────────────────────────
setup_logging()

_app_config = AppConfig()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────
    setup_telemetry(_app_config.telemetry)
    if _app_config.telemetry.otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    yield
    # ── Shutdown ───────────────────────────────────────────────────


app = FastAPI(
    title="FakeNewsGuard API",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    lifespan=_lifespan,
)

# i18n auf konfigurierte Sprache setzen
set_default_locale(_app_config.language)

# CORS -- konfigurierbar via CORS_ORIGINS Umgebungsvariable.
# Standard: "*" (alle Origins), fuer Produktion explizit setzen.
_cors_origins = _app_config.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
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
    # OTEL: correlation_id als Span-Attribut setzen
    span = _otrace.get_current_span()
    if span.is_recording():
        span.set_attribute("request.correlation_id", cid)

    try:
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, response.status_code, duration_ms)

        # Prometheus-Metriken
        norm_path = normalize_path(path)
        REQUEST_COUNT.labels(method=method, path=norm_path, status_code=str(response.status_code)).inc()
        REQUEST_DURATION.labels(method=method, path=norm_path).observe(duration_ms / 1000)

        logger.info(
            "<- %s %s %d %.1fms rid=%s",
            method, path, response.status_code, duration_ms, cid,
        )
        response.headers["X-Request-ID"] = cid
        return response
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, 500, duration_ms)

        norm_path = normalize_path(path)
        REQUEST_COUNT.labels(method=method, path=norm_path, status_code="500").inc()
        REQUEST_DURATION.labels(method=method, path=norm_path).observe(duration_ms / 1000)

        logger.exception("x %s %s ERROR %.1fms rid=%s: %s", method, path, duration_ms, cid, exc)
        raise


# ── Legacy-redirect middleware: /api/* → /api/v1/* (308) ──────────
from starlette.responses import RedirectResponse as _RedirectResponse


@app.middleware("http")
async def legacy_api_redirect(request: Request, call_next: Any) -> Any:
    path = request.url.path
    if (
        path.startswith("/api/")
        and not path.startswith("/api/v1/")
        and path not in ("/api/health",)
    ):
        new_path = "/api/v1" + path[4:]  # /api/foo → /api/v1/foo
        query = str(request.url.query)
        new_url = new_path + ("?" + query if query else "")
        return _RedirectResponse(url=new_url, status_code=308)
    return await call_next(request)


# ── Include all routers ────────────────────────────────────────────
from .auth import router as auth_router
from .admin import router as admin_router
from .analysis import router as analysis_router
from .analytics import router as analytics_router
from .archive import router as archive_router
from .share import router as share_router
from .export import router as export_router
from .graph import router as graph_router
from .utils import router as utils_router
from .evaluation import router as evaluation_router
from .unversioned import router as unversioned_router

V1_PREFIX = "/api/v1"

app.include_router(auth_router,      prefix=V1_PREFIX)
app.include_router(admin_router,     prefix=V1_PREFIX)
app.include_router(analysis_router,  prefix=V1_PREFIX)
app.include_router(analytics_router, prefix=V1_PREFIX)
app.include_router(archive_router,   prefix=V1_PREFIX)
app.include_router(share_router,     prefix=V1_PREFIX)
app.include_router(export_router,    prefix=V1_PREFIX)
app.include_router(graph_router,     prefix=V1_PREFIX)
app.include_router(utils_router,     prefix=V1_PREFIX)
app.include_router(evaluation_router, prefix=V1_PREFIX)

# Unversioned infrastructure endpoints (health, metrics)
app.include_router(unversioned_router)

# ── Backward-compatibility re-exports ──────────────────────────────
from .dependencies import _get_rate_limiter
from .dependencies import get_job_store
from .dependencies import transform_result as _transform_result

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
