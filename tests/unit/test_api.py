"""Tests für api.py – Endpunkte und Rate-Limiting."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """TestClient für die FastAPI-App mit frischem Rate-Limiter pro Test."""
    import api
    api._rate_limiter = None  # Singleton zurücksetzen → neuer Bucket pro Test
    return TestClient(api.app)


# ── Health Endpoint ──────────────────────────────────────────────


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Analyze Endpoint Validierung ─────────────────────────────────


def test_analyze_rejects_empty_input(client):
    resp = client.post("/api/analyze", json={"text": ""})
    assert resp.status_code == 400


def test_analyze_rejects_whitespace_only(client):
    resp = client.post("/api/analyze", json={"text": "   "})
    assert resp.status_code == 400


def test_analyze_returns_job_id(client):
    """Valider Input sollte einen job_id zurückgeben."""
    resp = client.post("/api/analyze", json={"text": "Die Erde ist rund."})
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0


# ── Job Polling ──────────────────────────────────────────────────


def test_get_job_not_found(client):
    resp = client.get("/api/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_get_job_after_analyze(client):
    """Nach analyze() sollte der Job abrufbar sein."""
    import uuid
    unique_text = f"Testbehauptung {uuid.uuid4().hex[:8]} zum Prüfen."
    resp = client.post("/api/analyze", json={"text": unique_text})
    data = resp.json()
    assert "job_id" in data

    job_resp = client.get(f"/api/jobs/{data['job_id']}")
    assert job_resp.status_code == 200
    job_data = job_resp.json()
    assert job_data["status"] in ("pending", "running", "done", "error")


# ── Rate-Limiting ────────────────────────────────────────────────


def test_rate_limit_blocks_after_burst():
    """Nach Überschreitung des Bursts sollte 429 zurückkommen."""
    from fastapi import HTTPException

    call_count = 0

    def fake_rate_limit(request):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise HTTPException(
                status_code=429,
                detail="Rate limited",
                headers={"Retry-After": "1"},
            )

    with patch("api._check_rate_limit", side_effect=fake_rate_limit):
        from api import app
        patched_client = TestClient(app)

        # Erster Request: OK
        resp1 = patched_client.post("/api/analyze", json={"text": "Rate Limit Test Eins"})
        assert resp1.status_code == 200

        # Zweiter Request: Rate-Limited
        resp2 = patched_client.post("/api/analyze", json={"text": "Rate Limit Test Zwei"})
        assert resp2.status_code == 429


# ── Extract Endpoint ─────────────────────────────────────────────


def test_extract_rejects_empty_url(client):
    resp = client.post("/api/extract", json={"url": ""})
    assert resp.status_code == 400


# ── Archive Endpoints ────────────────────────────────────────────


def test_archive_list(client):
    resp = client.get("/api/archive")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


def test_archive_not_found(client):
    resp = client.get("/api/archive/nonexistent-id")
    assert resp.status_code == 404


def test_archive_stats(client):
    resp = client.get("/api/archive-stats")
    assert resp.status_code == 200
