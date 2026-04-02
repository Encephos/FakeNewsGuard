"""Versioned utility endpoint: /locales (mounted under /api/v1)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/locales")
async def list_locales() -> dict:
    """Verfuegbare Sprachen und aktuelle Einstellung."""
    from i18n import available_locales, get_default_locale
    return {
        "current": get_default_locale(),
        "available": available_locales(),
    }
