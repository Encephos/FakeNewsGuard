"""Source Scraper – Async HTTP-Fetch und Textextraktion für die Scraping Pipeline.

Kapselt den HTTP-Fetch und nutzt die vorhandene Extraktions-Logik
aus content_extractor.py (trafilatura → BeautifulSoup Fallback).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from tools.content_extractor import _HEADERS, _extract_article_text, _extract_article_text_with_date
from tools.scrape_ranker import RankedSource, extract_relevant_passages


@dataclass
class ScrapedSource:
    url: str
    tier_label: str
    passage: str            # Relevanter Ausschnitt (nach extract_relevant_passages)
    low_relevance: bool     # Flag aus extract_relevant_passages
    fetch_success: bool
    error: str | None       # Falls fetch_success=False
    publication_date: str = ""  # ISO-Datum oder Freitext, leer wenn unbekannt


async def scrape_source(
    ranked: RankedSource,
    claim_text: str,
    timeout: float = 10.0,
) -> ScrapedSource:
    """Scrape eine einzelne Quelle und extrahiere relevante Passagen."""
    from tools.source_classifier import classify_source
    classified = classify_source(ranked.result)
    tier_label = classified.tier_label

    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        ) as client:
            response = await client.get(ranked.result.url)
            response.raise_for_status()
    except Exception as e:
        return ScrapedSource(
            url=ranked.result.url,
            tier_label=tier_label,
            passage="",
            low_relevance=False,
            fetch_success=False,
            error=f"{type(e).__name__}: {e}",
        )

    # Artikeltext + Publikationsdatum extrahieren
    text, pub_date = _extract_article_text_with_date(response.text)
    if not text or len(text) < 100:
        # Fallback: einfache Extraktion ohne Datum
        text = _extract_article_text(response.text)
        pub_date = ""
    if not text or len(text) < 100:
        return ScrapedSource(
            url=ranked.result.url,
            tier_label=tier_label,
            passage="",
            low_relevance=False,
            fetch_success=False,
            error="Kein Inhalt extrahierbar",
        )

    # Relevante Passagen extrahieren
    passage, low_relevance = extract_relevant_passages(text, claim_text)

    return ScrapedSource(
        url=ranked.result.url,
        tier_label=tier_label,
        passage=passage,
        low_relevance=low_relevance,
        fetch_success=True,
        error=None,
        publication_date=pub_date,
    )


async def scrape_sources(
    ranked_sources: list[RankedSource],
    claim_text: str,
    max_concurrent: int = 3,
    timeout: float = 10.0,
) -> list[ScrapedSource]:
    """Scrape alle should_scrape=True Quellen parallel mit Semaphore.

    Args:
        ranked_sources: Liste der gerankten Quellen.
        claim_text: Der zu prüfende Claim-Text.
        max_concurrent: Maximale gleichzeitige HTTP-Requests.
        timeout: HTTP-Timeout pro Request in Sekunden.

    Returns:
        Liste der ScrapedSource-Objekte (nur für gescrapte Quellen).
    """
    to_scrape = [rs for rs in ranked_sources if rs.should_scrape]
    if not to_scrape:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _bounded(ranked: RankedSource) -> ScrapedSource:
        async with semaphore:
            return await scrape_source(ranked, claim_text, timeout=timeout)

    results = await asyncio.gather(*[_bounded(rs) for rs in to_scrape])
    return list(results)
