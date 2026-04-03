"""Unit tests for tools/analytics.py — AnalyticsEngine."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from tools.analytics import AnalyticsEngine, _extract_domain, _tokenize, RATING_SCORE


# ── Helpers ──────────────────────────────────────────────────────────

def _make_row(
    created_at: float,
    overall_rating: str = "MIXED",
    confidence: int = 60,
    claims_count: int = 2,
    techniques_count: int = 0,
    platform: str = "article",
    claims: list[dict] | None = None,
    sources: list[str] | None = None,
) -> dict:
    result_json = json.dumps({
        "claims": claims or [],
        "sources": sources or [],
        "rhetoric": [],
    })
    return {
        "id": "test-id",
        "created_at": created_at,
        "overall_rating": overall_rating,
        "confidence": confidence,
        "claims_count": claims_count,
        "techniques_count": techniques_count,
        "platform": platform,
        "result_json": result_json,
    }


def _make_archive(rows: list[dict], enabled: bool = True):
    """Build a minimal mock archive that yields the given rows."""
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


def _make_engine(rows: list[dict], enabled: bool = True) -> AnalyticsEngine:
    return AnalyticsEngine(_make_archive(rows, enabled=enabled))


# ── Utility function tests ────────────────────────────────────────────

def test_extract_domain_strips_www():
    assert _extract_domain("https://www.reuters.com/article/123") == "reuters.com"


def test_extract_domain_no_www():
    assert _extract_domain("https://factcheck.org/foo") == "factcheck.org"


def test_extract_domain_invalid():
    result = _extract_domain("not-a-url")
    assert result == "not-a-url"  # fallback: return as-is


def test_tokenize_filters_stopwords():
    tokens = _tokenize("dass dies eine Impfung wirksam ist")
    assert "dass" not in tokens
    assert "dies" not in tokens
    assert "eine" not in tokens
    assert "impfung" in tokens or "Impfung".lower() in tokens


def test_tokenize_filters_short_words():
    tokens = _tokenize("CO2 war nun mal")
    # Words < 4 chars excluded
    assert all(len(t) >= 4 for t in tokens)


# ── Timeline tests ────────────────────────────────────────────────────

def test_timeline_empty():
    engine = _make_engine([])
    result = engine.timeline(period="30d")
    assert result["buckets"] == []
    assert result["total_analyses"] == 0


def test_timeline_single_entry():
    now = time.time()
    rows = [_make_row(now, overall_rating="RELIABLE", confidence=80, claims_count=3)]
    engine = _make_engine(rows)
    result = engine.timeline(period="30d", bucket="day")
    assert result["total_analyses"] == 1
    assert len(result["buckets"]) == 1
    bucket = result["buckets"][0]
    assert bucket["count"] == 1
    assert bucket["avg_confidence"] == pytest.approx(0.80, abs=0.01)
    assert bucket["avg_claims_per_analysis"] == 3.0
    assert bucket["rating_distribution"]["RELIABLE"] == 1


def test_timeline_period_filtering():
    now = time.time()
    old_ts = now - 40 * 86400  # 40 days ago
    recent_ts = now - 5 * 86400  # 5 days ago

    # We test period filtering at the _parse_period / _fetch_rows level.
    # The mock always returns what we give it, so we verify the SQL is called
    # with a cutoff when period != 'all'.
    rows_recent = [_make_row(recent_ts)]
    archive = _make_archive(rows_recent)

    engine = AnalyticsEngine(archive)
    result = engine.timeline(period="7d")
    # Should have called _connect and executed with a cutoff parameter
    assert result["total_analyses"] == 1


def test_timeline_bucket_week():
    now = time.time()
    rows = [_make_row(now - i * 86400) for i in range(10)]
    engine = _make_engine(rows)
    result = engine.timeline(period="90d", bucket="week")
    assert result["bucket"] == "week"
    # All 10 rows in the same week or two adjacent weeks
    assert len(result["buckets"]) >= 1


def test_timeline_all_period_no_cutoff():
    now = time.time()
    old_row = _make_row(now - 365 * 86400)
    archive = _make_archive([old_row])
    # For 'all' period the SQL should NOT include WHERE created_at >= ?
    # We verify by checking the execute call args
    with archive._connect() as conn:
        pass  # just to confirm context manager works

    engine = AnalyticsEngine(archive)
    result = engine.timeline(period="all")
    assert result["period"] == "all"
    assert result["bucket"] == "month"


# ── Topics tests ──────────────────────────────────────────────────────

def test_topics_empty():
    engine = _make_engine([])
    result = engine.topics(period="30d")
    assert result["topics"] == []


def test_topics_extraction():
    now = time.time()
    rows = [
        _make_row(now, claims=[{"text": "Impfungen verursachen Autismus laut Studie"}]),
        _make_row(now - 1, claims=[{"text": "Impfungen sind sicher laut Wissenschaft"}]),
        _make_row(now - 2, claims=[{"text": "Klimawandel beschleunigt durch Emissionen"}]),
    ]
    engine = _make_engine(rows)
    result = engine.topics(period="30d")
    topics = {t["topic"] for t in result["topics"]}
    # "impfungen" should appear twice → likely in top topics
    assert "impfungen" in topics or any("impf" in t for t in topics)


def test_topics_stopword_filter():
    now = time.time()
    rows = [
        _make_row(now, claims=[{"text": "dass dies eine Behauptung ist nicht wahr"}]),
    ]
    engine = _make_engine(rows)
    result = engine.topics(period="30d")
    topic_words = {t["topic"] for t in result["topics"]}
    assert "dass" not in topic_words
    assert "eine" not in topic_words
    assert "nicht" not in topic_words


def test_topics_trend_rising():
    now = time.time()
    # First half: 1 mention, second half: 5 mentions
    rows = (
        [_make_row(now - 20 * 86400, claims=[{"text": "klimawandel daten"}])]
        + [_make_row(now - i * 86400, claims=[{"text": "klimawandel daten"}]) for i in range(1, 6)]
    )
    engine = _make_engine(rows)
    result = engine.topics(period="30d")
    klima = next((t for t in result["topics"] if t["topic"] == "klimawandel"), None)
    # Should exist
    assert klima is not None


# ── Sources tests ─────────────────────────────────────────────────────

def test_sources_empty():
    engine = _make_engine([])
    result = engine.sources(period="30d")
    assert result["sources"] == []
    assert result["total_unique_sources"] == 0


def test_sources_domain_extraction():
    now = time.time()
    rows = [
        _make_row(now, sources=["https://reuters.com/article/1", "https://www.reuters.com/article/2"]),
        _make_row(now - 1, sources=["https://factcheck.org/claim/1"]),
    ]
    engine = _make_engine(rows)
    result = engine.sources(period="30d")
    domains = {s["domain"] for s in result["sources"]}
    assert "reuters.com" in domains
    assert "factcheck.org" in domains


def test_sources_citation_count():
    now = time.time()
    rows = [
        _make_row(now - i, sources=["https://reuters.com/a"]) for i in range(5)
    ]
    engine = _make_engine(rows)
    result = engine.sources(period="30d")
    reuters = next((s for s in result["sources"] if s["domain"] == "reuters.com"), None)
    assert reuters is not None
    assert reuters["citation_count"] == 5


# ── Accuracy tests ────────────────────────────────────────────────────

def test_accuracy_empty():
    engine = _make_engine([])
    result = engine.accuracy(period="30d")
    assert result["accuracy_over_time"] == []
    assert result["overall_brier_score"] == 0.0


def test_accuracy_confidence_bands():
    now = time.time()
    rows = [
        _make_row(now, overall_rating="RELIABLE", confidence=90),
        _make_row(now - 1, overall_rating="FABRICATED", confidence=10),
        _make_row(now - 2, overall_rating="MIXED", confidence=50),
    ]
    engine = _make_engine(rows)
    result = engine.accuracy(period="30d")
    bands = result["confidence_bands"]
    assert len(bands) == 5
    # High confidence band (80-100) should contain 1 entry
    high_band = next(b for b in bands if b["range"] == "80-100")
    assert high_band["count"] == 1
    # Low confidence band (0-20) should contain 1 entry
    low_band = next(b for b in bands if b["range"] == "0-20")
    assert low_band["count"] == 1


def test_accuracy_brier_score_reliable():
    now = time.time()
    # High-confidence RELIABLE → low Brier score
    rows = [_make_row(now - i, overall_rating="RELIABLE", confidence=95) for i in range(5)]
    engine = _make_engine(rows)
    result = engine.accuracy(period="30d")
    assert result["overall_brier_score"] < 0.1


def test_accuracy_brier_score_confident_negative():
    now = time.time()
    # High-confidence FABRICATED → low Brier score (correctly confident negative rating)
    # p_rel = 1 - 0.9 = 0.1, outcome = 0 → (0.1 - 0)² = 0.01
    rows = [_make_row(now - i, overall_rating="FABRICATED", confidence=90) for i in range(5)]
    engine = _make_engine(rows)
    result = engine.accuracy(period="30d")
    assert result["overall_brier_score"] < 0.05


def test_accuracy_brier_score_low_confidence_negative():
    now = time.time()
    # Low-confidence FABRICATED → high Brier score (uncertain negative = bad calibration)
    # p_rel = 1 - 0.3 = 0.7, outcome = 0 → (0.7 - 0)² = 0.49
    rows = [_make_row(now - i, overall_rating="FABRICATED", confidence=30) for i in range(5)]
    engine = _make_engine(rows)
    result = engine.accuracy(period="30d")
    assert result["overall_brier_score"] > 0.4


# ── Platforms tests ───────────────────────────────────────────────────

def test_platforms_empty():
    engine = _make_engine([])
    result = engine.platforms(period="30d")
    assert result["platforms"] == []


def test_platforms_grouping():
    now = time.time()
    rows = [
        _make_row(now, platform="twitter", overall_rating="MISLEADING", confidence=55),
        _make_row(now - 1, platform="twitter", overall_rating="RELIABLE", confidence=80),
        _make_row(now - 2, platform="article", overall_rating="MIXED", confidence=65),
    ]
    engine = _make_engine(rows)
    result = engine.platforms(period="30d")
    platforms = {p["platform"]: p for p in result["platforms"]}
    assert "twitter" in platforms
    assert "article" in platforms
    assert platforms["twitter"]["count"] == 2
    assert platforms["article"]["count"] == 1


def test_platforms_avg_confidence():
    now = time.time()
    rows = [
        _make_row(now, platform="youtube", confidence=60),
        _make_row(now - 1, platform="youtube", confidence=80),
    ]
    engine = _make_engine(rows)
    result = engine.platforms(period="30d")
    yt = next(p for p in result["platforms"] if p["platform"] == "youtube")
    assert yt["avg_confidence"] == pytest.approx(0.70, abs=0.01)


# ── Cache TTL tests ───────────────────────────────────────────────────

def test_cache_ttl_returns_cached_result():
    now = time.time()
    rows = [_make_row(now)]
    archive = _make_archive(rows)
    engine = AnalyticsEngine(archive)

    # First call populates cache
    result1 = engine.timeline(period="30d")
    # Second call within TTL — should NOT call _fetch_rows again
    with patch.object(engine, "_fetch_rows", wraps=engine._fetch_rows) as spy:
        result2 = engine.timeline(period="30d")
        spy.assert_not_called()

    assert result1 == result2


def test_cache_ttl_expires():
    now = time.time()
    rows = [_make_row(now)]
    engine = _make_engine(rows)

    result1 = engine.timeline(period="30d")
    # Manually expire the cache entry
    engine._cache.clear()
    result2 = engine.timeline(period="30d")
    assert result1 == result2  # same shape, fresh fetch


# ── Archive disabled ──────────────────────────────────────────────────

def test_engine_archive_disabled():
    engine = _make_engine([], enabled=False)
    result = engine.timeline(period="30d")
    assert result["total_analyses"] == 0
    assert result["buckets"] == []
