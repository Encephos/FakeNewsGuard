"""Utility endpoints: /api/locales, /api/health."""

from __future__ import annotations

from fastapi import APIRouter

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
async def health() -> dict:
    return {"status": "ok"}
