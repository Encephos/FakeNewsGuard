"""Analytics endpoints: time-bucketed aggregations over the analysis archive.

All endpoints accept:
    period: 7d | 30d | 90d | all   (default: 30d)
    bucket: day | week | month     (optional override; derived from period if omitted)
    date_from / date_to: ISO-8601 date strings (override period when both given)
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from tools.analytics import AnalyticsEngine

from .dependencies import get_archive

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Module-level engine cache so the AnalyticsEngine's in-memory result cache
# survives across requests (one engine instance per archive singleton).
_engine: AnalyticsEngine | None = None
_engine_archive_id: int | None = None  # id(archive) to detect archive replacement


def _get_engine() -> AnalyticsEngine:
    global _engine, _engine_archive_id
    archive = get_archive()
    if _engine is None or id(archive) != _engine_archive_id:
        _engine = AnalyticsEngine(archive)
        _engine_archive_id = id(archive)
    return _engine


def _archive_disabled() -> dict:
    return {"error": "archive_disabled"}


@router.get("/timeline")
async def analytics_timeline(
    period: str = Query(default="30d", description="7d | 30d | 90d | all"),
    bucket: str | None = Query(default=None, description="day | week | month"),
    date_from: str | None = Query(default=None, description="ISO-8601 start date"),
    date_to: str | None = Query(default=None, description="ISO-8601 end date"),
) -> dict:
    """Time-bucketed analysis volume, confidence, and rating distribution."""
    archive = get_archive()
    if not archive.config.enabled:
        return _archive_disabled()
    return _get_engine().timeline(period=period, bucket=bucket, date_from=date_from, date_to=date_to)


@router.get("/topics")
async def analytics_topics(
    period: str = Query(default="30d", description="7d | 30d | 90d | all"),
    date_from: str | None = Query(default=None, description="ISO-8601 start date"),
    date_to: str | None = Query(default=None, description="ISO-8601 end date"),
) -> dict:
    """Most frequent topics extracted from claim texts with trend indicators."""
    archive = get_archive()
    if not archive.config.enabled:
        return _archive_disabled()
    return _get_engine().topics(period=period, date_from=date_from, date_to=date_to)


@router.get("/sources")
async def analytics_sources(
    period: str = Query(default="30d", description="7d | 30d | 90d | all"),
    date_from: str | None = Query(default=None, description="ISO-8601 start date"),
    date_to: str | None = Query(default=None, description="ISO-8601 end date"),
) -> dict:
    """Top cited source domains and their citation statistics."""
    archive = get_archive()
    if not archive.config.enabled:
        return _archive_disabled()
    return _get_engine().sources(period=period, date_from=date_from, date_to=date_to)


@router.get("/accuracy")
async def analytics_accuracy(
    period: str = Query(default="30d", description="7d | 30d | 90d | all"),
    bucket: str | None = Query(default=None, description="day | week | month"),
    date_from: str | None = Query(default=None, description="ISO-8601 start date"),
    date_to: str | None = Query(default=None, description="ISO-8601 end date"),
) -> dict:
    """Confidence calibration metrics, Brier score, and accuracy over time."""
    archive = get_archive()
    if not archive.config.enabled:
        return _archive_disabled()
    return _get_engine().accuracy(period=period, bucket=bucket, date_from=date_from, date_to=date_to)


@router.get("/platforms")
async def analytics_platforms(
    period: str = Query(default="30d", description="7d | 30d | 90d | all"),
    date_from: str | None = Query(default=None, description="ISO-8601 start date"),
    date_to: str | None = Query(default=None, description="ISO-8601 end date"),
) -> dict:
    """Breakdown of analyses by platform with rating and confidence stats."""
    archive = get_archive()
    if not archive.config.enabled:
        return _archive_disabled()
    return _get_engine().platforms(period=period, date_from=date_from, date_to=date_to)
