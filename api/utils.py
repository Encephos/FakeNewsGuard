"""Utility endpoints: /api/locales, /api/health, /metrics."""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from config import AppConfig

router = APIRouter()


@router.get("/api/locales")
async def list_locales() -> dict:
    """Verfuegbare Sprachen und aktuelle Einstellung."""
    from i18n import available_locales, get_default_locale
    return {
        "current": get_default_locale(),
        "available": available_locales(),
    }


@router.get("/api/health")
async def health():
    """Dependency-aware Health-Check (Postgres, Valkey, SearXNG)."""
    config = AppConfig()
    checks: dict[str, str] = {}

    # ── PostgreSQL ────────────────────────────────────────────────
    if config.postgres.enabled:
        try:
            import psycopg

            async with await psycopg.AsyncConnection.connect(
                config.postgres.dsn, connect_timeout=2
            ) as conn:
                await conn.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"
    else:
        checks["database"] = "skipped"

    # ── Valkey / Redis ────────────────────────────────────────────
    if config.valkey.enabled:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(config.valkey.url, socket_timeout=2)
            await r.ping()
            await r.aclose()
            checks["cache"] = "ok"
        except Exception as e:
            checks["cache"] = f"error: {e}"
    else:
        checks["cache"] = "skipped"

    # ── SearXNG ───────────────────────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.head(config.searxng.base_url, timeout=2.0)
            checks["search"] = "ok" if resp.status_code < 500 else f"error: HTTP {resp.status_code}"
    except Exception as e:
        checks["search"] = f"error: {e}"

    has_error = any(v.startswith("error") for v in checks.values())
    status = "degraded" if has_error else "ok"
    status_code = 503 if has_error else 200

    from tools.logger import _metrics

    uptime = time.time() - _metrics["started_at"]

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "checks": checks,
            "uptime_seconds": round(uptime),
        },
    )


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus-kompatible Metriken im Text-Format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
