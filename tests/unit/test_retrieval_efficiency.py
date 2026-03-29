"""Tests für Retrieval-Effizienz-Optimierungen.

Validiert:
    - Search-Cache-Verdrahtung (SearXNG + LangSearch)
    - Cache Hit/Miss Counters
    - Query-Deduplizierung
    - SearXNG-Query-Deduplizierung
    - Canonical Cache Keys
    - ClaimRouter-Singleton + Memoization
"""

from __future__ import annotations

import hashlib

import pytest

from config import SearchCacheConfig
from tools.db.valkey_search_cache import InMemorySearchCache
from tools.search.models import SearchResult


# ── Cache Hit/Miss Counters ──────────────────────────────────────────────────


class TestCacheHitMissCounters:
    """InMemorySearchCache zählt Hits und Misses korrekt."""

    def test_miss_increments_on_empty(self):
        cache = InMemorySearchCache(SearchCacheConfig(enabled=True, ttl_hours=1))
        result = cache.get("nonexistent query", "general")
        assert result is None
        assert cache.miss_count == 1
        assert cache.hit_count == 0

    def test_hit_increments_after_set(self):
        cache = InMemorySearchCache(SearchCacheConfig(enabled=True, ttl_hours=1))
        cache.set("test query", [{"title": "T", "url": "http://example.com", "snippet": "S"}], "general")
        result = cache.get("test query", "general")
        assert result is not None
        assert cache.hit_count == 1
        assert cache.miss_count == 0

    def test_hit_rate_in_stats(self):
        cache = InMemorySearchCache(SearchCacheConfig(enabled=True, ttl_hours=1))
        cache.set("q1", [{"title": "T", "url": "http://a.com", "snippet": "S"}], "")
        cache.get("q1", "")  # hit
        cache.get("q2", "")  # miss
        cache.get("q1", "")  # hit
        stats = cache.stats()
        assert stats["hit_count"] == 2
        assert stats["miss_count"] == 1
        assert abs(stats["hit_rate"] - 2 / 3) < 0.01

    def test_stats_zero_division_safe(self):
        cache = InMemorySearchCache(SearchCacheConfig(enabled=True, ttl_hours=1))
        stats = cache.stats()
        assert stats["hit_rate"] == 0.0


# ── LangSearch Cache ─────────────────────────────────────────────────────────


class TestLangSearchCache:
    """LangSearchClient nutzt search_cache wenn übergeben."""

    def test_cache_hit_avoids_http(self):
        """Zweiter Aufruf mit gleicher Query → Cache-Hit, kein HTTP-Request."""
        from tools.search.langsearch import LangSearchClient
        from config import LangSearchConfig

        cache = InMemorySearchCache(SearchCacheConfig(enabled=True, ttl_hours=1))
        # Pre-populate cache
        cache.set("test query", [{"title": "Cached", "url": "http://cached.com", "snippet": "C", "content": ""}], "langsearch")

        client = LangSearchClient(
            config=LangSearchConfig(api_key="fake-key", enabled=True),
            search_cache=cache,
        )
        # Should return cached result without HTTP call
        results = client.search("test query")
        assert len(results) == 1
        assert results[0].title == "Cached"
        assert cache.hit_count == 1

    def test_no_cache_passes_through(self):
        """Ohne Cache → normaler Ablauf (hier: disabled → leere Liste)."""
        from tools.search.langsearch import LangSearchClient
        from config import LangSearchConfig

        client = LangSearchClient(
            config=LangSearchConfig(api_key="", enabled=False),
        )
        results = client.search("test query")
        assert results == []


# ── Query-Deduplizierung ─────────────────────────────────────────────────────


class TestQueryDedup:
    """_dedup_queries entfernt normalisierte Duplikate."""

    def test_exact_duplicates(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["A B", "A B", "A B"])
        assert result == ["A B"]

    def test_case_and_whitespace_normalization(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["A B", "a  b", "A B!"])
        assert len(result) == 1
        assert result[0] == "A B"  # Erster Eintrag bleibt

    def test_preserves_order(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["first", "second", "third"])
        assert result == ["first", "second", "third"]

    def test_trailing_punctuation_stripped(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["query.", "query!", "query?"])
        assert len(result) == 1

    def test_different_queries_preserved(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["Kriminalität Deutschland", "Einbrüche Statistik"])
        assert len(result) == 2

    def test_empty_queries_filtered(self):
        from agents.evidence_builder import _dedup_queries
        result = _dedup_queries(["valid", "", "  ", "valid"])
        assert result == ["valid"]


# ── SearXNG-Query-Deduplizierung ─────────────────────────────────────────────


class TestSearXNGQueryDedup:
    """_dedup_searxng_queries dedupliziert nach (query, pageno)."""

    def test_same_query_same_page(self):
        from agents.evidence_builder import _dedup_searxng_queries
        from tools.web_search import SearXNGQuery

        queries = [
            SearXNGQuery(query="test", pageno=1),
            SearXNGQuery(query="test", pageno=1),
        ]
        result = _dedup_searxng_queries(queries)
        assert len(result) == 1

    def test_same_query_different_page_kept(self):
        from agents.evidence_builder import _dedup_searxng_queries
        from tools.web_search import SearXNGQuery

        queries = [
            SearXNGQuery(query="test", pageno=1),
            SearXNGQuery(query="test", pageno=2),
        ]
        result = _dedup_searxng_queries(queries)
        assert len(result) == 2

    def test_case_normalized(self):
        from agents.evidence_builder import _dedup_searxng_queries
        from tools.web_search import SearXNGQuery

        queries = [
            SearXNGQuery(query="Test Query", pageno=1),
            SearXNGQuery(query="test query", pageno=1),
        ]
        result = _dedup_searxng_queries(queries)
        assert len(result) == 1


# ── Canonical Cache Keys ─────────────────────────────────────────────────────


class TestCanonicalCacheKey:
    """_claim_key nutzt canonical_text wenn use_canonical=True."""

    def test_canonical_key_differs_from_raw(self):
        from tools.cache import _claim_key
        raw_key = _claim_key("Die Erde ist rund.", "FactChecker")
        canon_key = _claim_key(
            "Die Erde ist rund.",
            "FactChecker",
            canonical_text="die erde ist rund",
            use_canonical=True,
        )
        assert raw_key != canon_key

    def test_canonical_key_stable_across_variants(self):
        from tools.cache import _claim_key
        k1 = _claim_key(
            "Die Erde ist rund.",
            "FactChecker",
            canonical_text="die erde ist rund",
            use_canonical=True,
        )
        k2 = _claim_key(
            "DIE ERDE IST RUND!!!",
            "FactChecker",
            canonical_text="die erde ist rund",
            use_canonical=True,
        )
        assert k1 == k2

    def test_without_canonical_uses_raw(self):
        from tools.cache import _claim_key
        k1 = _claim_key("Die Erde ist rund.", "FactChecker")
        k2 = _claim_key(
            "Die Erde ist rund.",
            "FactChecker",
            canonical_text=None,
            use_canonical=True,
        )
        assert k1 == k2  # None canonical → fallback to raw

    def test_use_canonical_false_ignores_canonical(self):
        from tools.cache import _claim_key
        k1 = _claim_key("Die Erde ist rund.", "FactChecker")
        k2 = _claim_key(
            "Die Erde ist rund.",
            "FactChecker",
            canonical_text="something completely different",
            use_canonical=False,
        )
        assert k1 == k2


# ── ClaimRouter Memoization ──────────────────────────────────────────────────


class TestClaimRouterMemoization:
    """ClaimRouter cached Ergebnisse pro Claim-Text."""

    def test_route_and_apply_caches_result(self):
        from tools.claim_router import ClaimRouter
        from models.schemas import Claim

        router = ClaimRouter()
        claim = Claim(id="C1", text="Die Arbeitslosenquote in der EU liegt bei 6%.", type="STATISTICAL")

        result1, _ = router.route_and_apply(claim)
        result2, _ = router.route_and_apply(claim)

        # Gleiche Instanz aus dem Cache
        assert result1 is result2

    def test_different_claims_get_different_routes(self):
        from tools.claim_router import ClaimRouter
        from models.schemas import Claim

        router = ClaimRouter()
        claim1 = Claim(id="C1", text="Die Arbeitslosenquote in der EU liegt bei 6%.", type="STATISTICAL")
        claim2 = Claim(id="C2", text="Das Patent wurde von der FDA abgelehnt.", type="FACTUAL")

        result1, _ = router.route_and_apply(claim1)
        result2, _ = router.route_and_apply(claim2)

        # Verschiedene Claims → verschiedene Routen (möglicherweise gleiche Domänen)
        assert result1 is not result2
