"""API-level tests for /api/v1/analytics/* endpoints."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient


# ── Shared test archive factory ───────────────────────────────────────

def _make_row(
    created_at: float,
    overall_rating: str = "MIXED",
    confidence: int = 60,
    claims_count: int = 2,
    platform: str = "article",
) -> dict:
    result_json = json.dumps({
        "claims": [{"text": "Testbehauptung über Klimawandel"}],
        "sources": ["https://reuters.com/test"],
        "rhetoric": [],
    })
    return {
        "id": "test-id",
        "created_at": created_at,
        "overall_rating": overall_rating,
        "confidence": confidence,
        "claims_count": claims_count,
        "techniques_count": 0,
        "platform": platform,
        "result_json": result_json,
    }


def _make_mock_archive(rows: list[dict], enabled: bool = True):
    config = SimpleNamespace(enabled=enabled)

    @contextmanager
    def _connect():
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = rows
        yield conn

    archive = MagicMock()
    archive.config = config
    archive._connect = _connect
    return archive


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def client_with_archive(request):
    """TestClient with a mock archive.  Parameterise via request.param."""
    rows = getattr(request, "param", None) or [_make_row(time.time())]
    mock_archive = _make_mock_archive(rows)

    fake_redis_client = fakeredis.FakeRedis()
    from worker.job_store import get_job_store, reset_job_store
    reset_job_store()
    get_job_store(client=fake_redis_client)

    import api
    import api.analytics as analytics_mod

    analytics_mod._engine = None  # reset engine singleton

    with patch("api.analytics.get_archive", return_value=mock_archive):
        yield TestClient(api.app)

    reset_job_store()
    fake_redis_client.flushall()
    analytics_mod._engine = None


@pytest.fixture
def client(client_with_archive):
    return client_with_archive


@pytest.fixture
def empty_client(request):
    """TestClient with an empty archive."""
    mock_archive = _make_mock_archive([])
    fake_redis_client = fakeredis.FakeRedis()
    from worker.job_store import get_job_store, reset_job_store
    reset_job_store()
    get_job_store(client=fake_redis_client)

    import api
    import api.analytics as analytics_mod

    analytics_mod._engine = None

    with patch("api.analytics.get_archive", return_value=mock_archive):
        yield TestClient(api.app)

    reset_job_store()
    fake_redis_client.flushall()
    analytics_mod._engine = None


@pytest.fixture
def disabled_client():
    """TestClient with a disabled archive."""
    mock_archive = _make_mock_archive([], enabled=False)
    fake_redis_client = fakeredis.FakeRedis()
    from worker.job_store import get_job_store, reset_job_store
    reset_job_store()
    get_job_store(client=fake_redis_client)

    import api
    import api.analytics as analytics_mod

    analytics_mod._engine = None

    with patch("api.analytics.get_archive", return_value=mock_archive):
        yield TestClient(api.app)

    reset_job_store()
    fake_redis_client.flushall()
    analytics_mod._engine = None


# ── Timeline endpoint ────────────────────────────────────────────────

def test_timeline_200(client):
    resp = client.get("/api/v1/analytics/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert "buckets" in data
    assert "total_analyses" in data
    assert "period" in data


def test_timeline_default_period(client):
    resp = client.get("/api/v1/analytics/timeline")
    data = resp.json()
    assert data["period"] == "30d"


def test_timeline_period_7d(client):
    resp = client.get("/api/v1/analytics/timeline?period=7d")
    assert resp.status_code == 200
    assert resp.json()["period"] == "7d"


def test_timeline_bucket_override(client):
    resp = client.get("/api/v1/analytics/timeline?bucket=week")
    assert resp.status_code == 200
    assert resp.json()["bucket"] == "week"


def test_timeline_unknown_period_defaults(client):
    # Invalid period should fall back to 30d (default in _PERIOD_DEFAULTS fallback)
    resp = client.get("/api/v1/analytics/timeline?period=999x")
    assert resp.status_code == 200
    # Falls back to 30-day bucket
    assert resp.json()["bucket"] == "day"


def test_timeline_bucket_shape(client):
    resp = client.get("/api/v1/analytics/timeline?period=all")
    data = resp.json()
    if data["buckets"]:
        bucket = data["buckets"][0]
        assert "date" in bucket
        assert "count" in bucket
        assert "avg_confidence" in bucket
        assert "rating_distribution" in bucket
        assert "avg_claims_per_analysis" in bucket


# ── Topics endpoint ──────────────────────────────────────────────────

def test_topics_200(client):
    resp = client.get("/api/v1/analytics/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert "topics" in data
    assert "period" in data


def test_topics_shape(client):
    resp = client.get("/api/v1/analytics/topics")
    data = resp.json()
    if data["topics"]:
        topic = data["topics"][0]
        assert "topic" in topic
        assert "count" in topic
        assert "avg_rating_score" in topic
        assert "trend" in topic
        assert topic["trend"] in ("rising", "stable", "declining")


# ── Sources endpoint ─────────────────────────────────────────────────

def test_sources_200(client):
    resp = client.get("/api/v1/analytics/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert "total_unique_sources" in data


def test_sources_shape(client):
    resp = client.get("/api/v1/analytics/sources")
    data = resp.json()
    if data["sources"]:
        src = data["sources"][0]
        assert "domain" in src
        assert "citation_count" in src
        assert "first_seen" in src
        assert "last_seen" in src


# ── Accuracy endpoint ────────────────────────────────────────────────

def test_accuracy_200(client):
    resp = client.get("/api/v1/analytics/accuracy")
    assert resp.status_code == 200
    data = resp.json()
    assert "accuracy_over_time" in data
    assert "overall_brier_score" in data
    assert "confidence_bands" in data


def test_accuracy_bands_count(client):
    resp = client.get("/api/v1/analytics/accuracy")
    bands = resp.json()["confidence_bands"]
    assert len(bands) == 5
    ranges = {b["range"] for b in bands}
    assert "0-20" in ranges
    assert "80-100" in ranges


# ── Platforms endpoint ───────────────────────────────────────────────

def test_platforms_200(client):
    resp = client.get("/api/v1/analytics/platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert "platforms" in data


def test_platforms_shape(client):
    resp = client.get("/api/v1/analytics/platforms")
    data = resp.json()
    if data["platforms"]:
        p = data["platforms"][0]
        assert "platform" in p
        assert "count" in p
        assert "avg_rating_score" in p
        assert "avg_confidence" in p


# ── Archive disabled ─────────────────────────────────────────────────

def test_timeline_archive_disabled(disabled_client):
    resp = disabled_client.get("/api/v1/analytics/timeline")
    assert resp.status_code == 200
    assert resp.json().get("error") == "archive_disabled"


def test_topics_archive_disabled(disabled_client):
    resp = disabled_client.get("/api/v1/analytics/topics")
    assert resp.json().get("error") == "archive_disabled"


def test_sources_archive_disabled(disabled_client):
    resp = disabled_client.get("/api/v1/analytics/sources")
    assert resp.json().get("error") == "archive_disabled"


def test_accuracy_archive_disabled(disabled_client):
    resp = disabled_client.get("/api/v1/analytics/accuracy")
    assert resp.json().get("error") == "archive_disabled"


def test_platforms_archive_disabled(disabled_client):
    resp = disabled_client.get("/api/v1/analytics/platforms")
    assert resp.json().get("error") == "archive_disabled"
