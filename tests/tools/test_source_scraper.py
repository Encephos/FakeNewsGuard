"""Tests für tools/source_scraper.py – Async Scraping Pipeline."""

from __future__ import annotations

import pytest

from tools.scrape_ranker import RankedSource
from tools.source_classifier import SourceTier
from tools.source_scraper import ScrapedSource, scrape_source, scrape_sources
from tools.web_search import SearchResult


# ── Fixtures ─────────────────────────────────────────────────────


def _make_ranked(
    url: str = "https://tagesschau.de/inland/test",
    snippet: str = "Kriminalität gestiegen",
    tier: SourceTier = SourceTier.QUALITY_JOURNALISM,
    should_scrape: bool = True,
    relevance: float = 0.5,
) -> RankedSource:
    return RankedSource(
        result=SearchResult(title="Test-Artikel", url=url, snippet=snippet),
        tier=tier,
        relevance_score=relevance,
        should_scrape=should_scrape,
        skip_reason=None,
    )


# ── scrape_source ────────────────────────────────────────────────


SAMPLE_HTML = """
<html><body>
<article>
<p>Die Kriminalität in Deutschland ist laut dem Bundeskriminalamt deutlich gestiegen im Jahr 2024 und das ist eine relevante Entwicklung.</p>

<p>Insbesondere die Zahl der Wohnungseinbrüche stieg um 50 Prozent im Vergleich zum Vorjahr und erreichte damit ein neues Rekordniveau.</p>

<p>Das BKA veröffentlichte die Polizeiliche Kriminalstatistik mit detaillierten Zahlen zu allen Deliktsarten im Bundesgebiet.</p>

<p>Experten warnen vor voreiligen Schlüssen und betonen die Notwendigkeit einer differenzierten Betrachtung der Statistiken und Hintergründe.</p>
</article>
</body></html>
"""


@pytest.mark.asyncio
async def test_scrape_source_success(httpx_mock):
    """Successful scrape extracts text and passages."""
    ranked = _make_ranked()
    httpx_mock.add_response(url=ranked.result.url, html=SAMPLE_HTML)

    result = await scrape_source(ranked, "Kriminalität gestiegen 50%")

    assert result.fetch_success is True
    assert result.error is None
    assert result.url == ranked.result.url
    assert len(result.passage) > 0
    assert result.tier_label == "Qualitätsjournalismus"


@pytest.mark.asyncio
async def test_scrape_source_http_error(httpx_mock):
    """HTTP errors return fetch_success=False with error message."""
    ranked = _make_ranked()
    httpx_mock.add_response(url=ranked.result.url, status_code=403)

    result = await scrape_source(ranked, "Kriminalität")

    assert result.fetch_success is False
    assert result.error is not None
    assert "403" in result.error or "Client" in result.error


@pytest.mark.asyncio
async def test_scrape_source_timeout(httpx_mock):
    """Timeout returns fetch_success=False."""
    import httpx as _httpx

    ranked = _make_ranked()
    httpx_mock.add_exception(_httpx.ReadTimeout("timeout"), url=ranked.result.url)

    result = await scrape_source(ranked, "Kriminalität", timeout=0.1)

    assert result.fetch_success is False
    assert "Timeout" in result.error or "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_scrape_source_empty_content(httpx_mock):
    """Pages with < 100 chars of extractable text → fetch_success=False."""
    ranked = _make_ranked()
    httpx_mock.add_response(url=ranked.result.url, html="<html><body>Short</body></html>")

    result = await scrape_source(ranked, "Kriminalität")

    assert result.fetch_success is False
    assert result.error == "Kein Inhalt extrahierbar"


# ── scrape_sources ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_sources_filters_should_scrape(httpx_mock):
    """Only sources with should_scrape=True are fetched."""
    r1 = _make_ranked(url="https://tagesschau.de/a", should_scrape=True)
    r2 = _make_ranked(url="https://reddit.com/post", should_scrape=False)

    httpx_mock.add_response(url=r1.result.url, html=SAMPLE_HTML)
    # r2 should NOT be fetched – no mock needed

    results = await scrape_sources([r1, r2], "Kriminalität gestiegen")

    assert len(results) == 1
    assert results[0].url == r1.result.url


@pytest.mark.asyncio
async def test_scrape_sources_empty_list():
    """Empty input returns empty list."""
    results = await scrape_sources([], "Kriminalität")
    assert results == []


@pytest.mark.asyncio
async def test_scrape_sources_all_not_scrapable():
    """All should_scrape=False → empty list."""
    r1 = _make_ranked(should_scrape=False)
    results = await scrape_sources([r1], "Kriminalität")
    assert results == []


@pytest.mark.asyncio
async def test_scrape_sources_multiple_parallel(httpx_mock):
    """Multiple sources are scraped in parallel."""
    urls = [f"https://example{i}.de/article" for i in range(3)]
    ranked = [_make_ranked(url=u) for u in urls]

    for url in urls:
        httpx_mock.add_response(url=url, html=SAMPLE_HTML)

    results = await scrape_sources(ranked, "Kriminalität gestiegen", max_concurrent=2)

    assert len(results) == 3
    assert all(r.fetch_success for r in results)


@pytest.mark.asyncio
async def test_scrape_sources_graceful_degradation(httpx_mock):
    """Mix of success and failure – all results returned, no exception."""
    r1 = _make_ranked(url="https://good.de/article")
    r2 = _make_ranked(url="https://bad.de/article")

    httpx_mock.add_response(url=r1.result.url, html=SAMPLE_HTML)
    httpx_mock.add_response(url=r2.result.url, status_code=500)

    results = await scrape_sources([r1, r2], "Kriminalität")

    assert len(results) == 2
    assert results[0].fetch_success is True
    assert results[1].fetch_success is False
