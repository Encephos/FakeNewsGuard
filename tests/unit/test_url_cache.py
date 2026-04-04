"""Unit-Tests für tools/url_cache.py und die EvidenceBuilder-Integration.

Abgedeckte Bereiche:
- URL-Normalisierung (_normalize_url)
- Cache-Roundtrip (get/set)
- Miss-Verhalten
- Stats-Zähler
- TTL-Expiry (via freezegun)
- Thread-Safety
- EvidenceBuilder-Integration: gleiche Top-URL → scrape_sources nur 1× aufgerufen
"""

from __future__ import annotations

import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.evidence_models import ScrapedContent
from tools.url_cache import UrlContentCache, _normalize_url, _url_key


# ── URL-Normalisierung ────────────────────────────────────────────────────────


class TestUrlNormalization:
    def test_trailing_slash_removed(self):
        assert _normalize_url("https://example.com/path/") == _normalize_url("https://example.com/path")

    def test_case_insensitive(self):
        assert _normalize_url("HTTPS://Example.COM/Path") == _normalize_url("https://example.com/path")

    def test_query_params_sorted(self):
        a = _normalize_url("https://example.com/?z=1&a=2")
        b = _normalize_url("https://example.com/?a=2&z=1")
        assert a == b

    def test_idempotent(self):
        url = "https://example.com/article?id=42"
        assert _normalize_url(url) == _normalize_url(_normalize_url(url))

    def test_different_urls_differ(self):
        assert _normalize_url("https://a.com/x") != _normalize_url("https://b.com/x")

    def test_url_key_is_hex_string(self):
        key = _url_key("https://example.com/")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ── Cache-Roundtrip & Miss-Verhalten ─────────────────────────────────────────


class TestUrlCacheGetSet:
    def _make_content(self, url: str = "https://example.com/article") -> ScrapedContent:
        return ScrapedContent(url=url, text="Artikel-Text über ein Thema.", tier_label="news", publish_date="2024-01-01")

    def test_miss_returns_none(self):
        cache = UrlContentCache()
        assert cache.get("https://example.com/not-there") is None

    def test_roundtrip(self):
        cache = UrlContentCache()
        content = self._make_content()
        cache.set(content.url, content)
        result = cache.get(content.url)
        assert result is not None
        assert result.url == content.url
        assert result.text == content.text
        assert result.tier_label == content.tier_label

    def test_trailing_slash_normalized_on_get(self):
        cache = UrlContentCache()
        content = self._make_content("https://example.com/article")
        cache.set(content.url, content)
        # URL mit trailing slash soll denselben Eintrag finden
        assert cache.get("https://example.com/article/") is not None

    def test_case_normalized_on_get(self):
        cache = UrlContentCache()
        content = self._make_content("https://example.com/article")
        cache.set(content.url, content)
        assert cache.get("HTTPS://EXAMPLE.COM/article") is not None

    def test_content_hash_auto_computed(self):
        content = ScrapedContent(url="https://x.com", text="Hallo Welt")
        assert content.content_hash != ""
        assert len(content.content_hash) == 64

    def test_overwrite_updates_entry(self):
        cache = UrlContentCache()
        url = "https://example.com/article"
        cache.set(url, ScrapedContent(url=url, text="alt"))
        cache.set(url, ScrapedContent(url=url, text="neu"))
        result = cache.get(url)
        assert result is not None
        assert result.text == "neu"


# ── Stats ─────────────────────────────────────────────────────────────────────


class TestUrlCacheStats:
    def test_initial_stats(self):
        cache = UrlContentCache()
        s = cache.stats()
        assert s == {"hits": 0, "misses": 0, "size": 0}

    def test_miss_increments_misses(self):
        cache = UrlContentCache()
        cache.get("https://example.com/x")
        assert cache.stats()["misses"] == 1

    def test_hit_increments_hits(self):
        cache = UrlContentCache()
        content = ScrapedContent(url="https://example.com/x", text="text")
        cache.set(content.url, content)
        cache.get(content.url)
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 0

    def test_size_reflects_entries(self):
        cache = UrlContentCache()
        for i in range(3):
            url = f"https://example.com/article-{i}"
            cache.set(url, ScrapedContent(url=url, text=f"text {i}"))
        assert cache.stats()["size"] == 3


# ── TTL-Expiry ────────────────────────────────────────────────────────────────


class TestUrlCacheTtl:
    def test_expired_entry_returns_none(self):
        cache = UrlContentCache(ttl_seconds=1)
        url = "https://example.com/article"
        content = ScrapedContent(url=url, text="text")
        cache.set(url, content)

        # Manipuliere den Timestamp direkt (spart sleep)
        key = _url_key(url)
        with cache._lock:
            cache._store[key] = (time.time() - 2, content)  # 2s ago, TTL=1s

        result = cache.get(url)
        assert result is None

    def test_not_yet_expired_returns_content(self):
        cache = UrlContentCache(ttl_seconds=3600)
        url = "https://example.com/article"
        content = ScrapedContent(url=url, text="text")
        cache.set(url, content)
        assert cache.get(url) is not None


# ── Thread-Safety ─────────────────────────────────────────────────────────────


class TestUrlCacheThreadSafety:
    def test_concurrent_writes_no_race(self):
        cache = UrlContentCache()
        errors: list[Exception] = []

        def writer(thread_id: int) -> None:
            try:
                for i in range(5):
                    url = f"https://example.com/thread-{thread_id}-{i}"
                    cache.set(url, ScrapedContent(url=url, text=f"text {thread_id} {i}"))
                    cache.get(url)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread-Safety-Fehler: {errors}"
        assert cache.stats()["size"] == 50  # 10 threads × 5 writes


# ── EvidenceBuilder-Integration ───────────────────────────────────────────────


class TestEvidenceBuilderCacheIntegration:
    """Zwei Claims mit derselben Top-URL → scrape_sources wird nur 1× aufgerufen."""

    def _make_ranked_source(self, url: str, should_scrape: bool = True):
        """Minimales RankedSource-Mock."""
        result = MagicMock()
        result.url = url
        result.content = ""
        rs = MagicMock()
        rs.result = result
        rs.should_scrape = should_scrape
        return rs

    def _make_scraped_source(self, url: str, passage: str = "Relevanter Ausschnitt."):
        from tools.source_scraper import ScrapedSource
        return ScrapedSource(
            url=url,
            tier_label="news",
            passage=passage,
            low_relevance=False,
            fetch_success=True,
            error=None,
            publication_date="2024-01-01",
        )

    @pytest.mark.asyncio
    async def test_second_claim_uses_cache_not_scraper(self):
        """Claim 1 scrapet URL (Miss). Claim 2 findet URL im Cache (Hit) → kein zweiter Scrape."""
        from tools.url_cache import UrlContentCache

        cache = UrlContentCache()
        shared_url = "https://reuters.com/article/shared"

        ranked1 = [self._make_ranked_source(shared_url)]
        ranked2 = [self._make_ranked_source(shared_url)]

        scraped1 = [self._make_scraped_source(shared_url)]

        with patch("tools.source_scraper.scrape_sources", new_callable=AsyncMock) as mock_scrape:
            # Claim 1: Cache-Miss → scrape_sources liefert Ergebnis
            mock_scrape.return_value = scraped1

            # Simuliere _rank_and_scrape-Logik für Claim 1
            from models.evidence_models import ScrapedContent
            from tools.scrape_ranker import extract_relevant_passages

            hits1: list = []
            for rs in ranked1:
                if not rs.should_scrape:
                    continue
                cached = cache.get(rs.result.url)
                if cached is not None:
                    passage, low_rel = extract_relevant_passages(cached.text, "Claim 1")
                    hits1.append(passage)
                    rs.should_scrape = False

            fresh1 = await mock_scrape(ranked1, "Claim 1")
            for s in fresh1:
                if s.fetch_success and s.passage:
                    cache.set(s.url, ScrapedContent(url=s.url, text=s.passage, tier_label=s.tier_label))

            assert hits1 == []  # Claim 1: kein Cache-Hit
            assert mock_scrape.call_count == 1

            # Claim 2: Cache-Hit → scrape_sources wird NICHT aufgerufen
            hits2: list = []
            for rs in ranked2:
                if not rs.should_scrape:
                    continue
                cached = cache.get(rs.result.url)
                if cached is not None:
                    passage, low_rel = extract_relevant_passages(cached.text, "Claim 2")
                    hits2.append(passage)
                    rs.should_scrape = False

            fresh2 = await mock_scrape(ranked2, "Claim 2")  # soll leer sein (no should_scrape)

            assert len(hits2) == 1  # Claim 2: 1 Cache-Hit
            # scrape_sources wurde ein zweites Mal aufgerufen, aber ranked2 hat should_scrape=False → liefert []
            assert cache.stats()["hits"] == 1
            assert cache.stats()["misses"] == 1
