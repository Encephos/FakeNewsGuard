"""Tests für agents/fact_checker.py."""

from __future__ import annotations

import pytest

from agents.fact_checker import (
    FactCheckerAgent,
    _build_enriched_context,
    _build_search_queries,
    _categories_for_claim,
)
from models.schemas import Claim, ClaimType, FactRating
from tools.scrape_ranker import RankedSource
from tools.source_classifier import SourceTier
from tools.source_scraper import ScrapedSource
from tools.web_search import SearchResult


# ── _build_search_queries ─────────────────────────────────────────


def test_build_queries_includes_direct_search(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    assert sample_factual_claim.text in queries


def test_build_queries_adds_factcheck_terms(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    # Adaptive: FACTUAL mit >60 Zeichen bekommt faktencheck, kürzere nicht
    assert len(queries) >= 1
    if len(sample_factual_claim.text) > 60:
        combined = " ".join(queries)
        assert "faktencheck" in combined.lower()


def test_build_queries_statistical_has_multiple(sample_statistical_claim):
    queries = _build_search_queries(sample_statistical_claim)
    assert len(queries) >= 3  # Direktsuche + faktencheck + statistik + destatis


# ── FactCheckerAgent ──────────────────────────────────────────────


def test_fact_checker_returns_result(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim):
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)
    result = agent.execute(sample_factual_claim)

    assert result.claim_id == sample_factual_claim.id
    assert result.rating == FactRating.MISLEADING
    assert result.evidence == "Test-Evidenz"


def test_fact_checker_fallback_on_invalid_rating(minimal_config, mocker, mock_search_client, sample_factual_claim):
    mock_llm = mocker.MagicMock()
    mock_llm.complete_json.return_value = {
        "claim_id": "C1",
        "rating": "INVALID_RATING",
        "evidence": "some",
        "sources": [],
    }
    mock_llm.complete_structured.return_value = mock_llm.complete_json.return_value

    agent = FactCheckerAgent(minimal_config, mock_llm, mock_search_client)
    result = agent.execute(sample_factual_claim)
    assert result.rating == FactRating.UNVERIFIABLE


def test_fact_checker_uses_cache(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim, cache_config):
    from tools.cache import ClaimCache

    cache = ClaimCache(cache_config)
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client, cache)

    # Erster Aufruf – schreibt in Cache
    result1 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1

    # Zweiter Aufruf – sollte Cache-Treffer sein, kein LLM-Call
    result2 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1  # Kein neuer Call
    assert result1.claim_id == result2.claim_id
    assert result1.rating == result2.rating


# ── _categories_for_claim ────────────────────────────────────────


def test_categories_statistical():
    claim = Claim(id="C1", text="x", type=ClaimType.STATISTICAL)
    assert _categories_for_claim(claim) == "general,science,news"


def test_categories_causal():
    claim = Claim(id="C1", text="x", type=ClaimType.CAUSAL)
    assert _categories_for_claim(claim) == "general,science"


def test_categories_factual():
    claim = Claim(id="C1", text="x", type=ClaimType.FACTUAL)
    assert _categories_for_claim(claim) == "general,news"


def test_categories_contextual():
    claim = Claim(id="C1", text="x", type=ClaimType.CONTEXTUAL)
    assert _categories_for_claim(claim) == "general,news"


def test_categories_opinion_fallback():
    claim = Claim(id="C1", text="x", type=ClaimType.OPINION)
    assert _categories_for_claim(claim) == "general"


# ── _build_enriched_context ──────────────────────────────────────


def _make_ranked(
    url: str, tier: SourceTier, should_scrape: bool, skip_reason: str | None = None,
    title: str = "Titel", snippet: str = "Snippet",
) -> RankedSource:
    return RankedSource(
        result=SearchResult(title=title, url=url, snippet=snippet),
        tier=tier, relevance_score=0.5, should_scrape=should_scrape,
        skip_reason=skip_reason,
    )


def test_enriched_context_with_fulltext():
    ranked = [_make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True)]
    scraped = [ScrapedSource(
        url="https://correctiv.org/c", tier_label="Faktencheck-Organisation",
        passage="Laut unserer Recherche...", low_relevance=False,
        fetch_success=True, error=None,
    )]
    ctx = _build_enriched_context(ranked, scraped)
    assert "Volltext-Auszug" in ctx
    assert "Laut unserer Recherche" in ctx
    assert "[Faktencheck-Organisation]" in ctx


def test_enriched_context_with_failed_scrape():
    ranked = [_make_ranked("https://tagesschau.de/t", SourceTier.QUALITY_JOURNALISM, True)]
    scraped = [ScrapedSource(
        url="https://tagesschau.de/t", tier_label="Qualitätsjournalismus",
        passage="", low_relevance=False,
        fetch_success=False, error="Timeout",
    )]
    ctx = _build_enriched_context(ranked, scraped)
    assert "Snippet" in ctx
    assert "[Kein Volltext: Timeout]" in ctx


def test_enriched_context_with_skipped_source():
    ranked = [_make_ranked(
        "https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall",
        snippet="Paywall-Snippet",
    )]
    ctx = _build_enriched_context(ranked, [])
    assert "Paywall-Snippet" in ctx
    assert "[Kein Volltext: Paywall]" in ctx


def test_enriched_context_skip_reason_labels():
    test_cases = [
        ("paywall", "Paywall"),
        ("low_tier", "Niedriger Quellen-Tier"),
        ("irrelevant", "Kein thematischer Bezug (Snippet-Analyse)"),
        ("limit_reached", "Scrape-Limit erreicht"),
    ]
    for reason, expected_label in test_cases:
        ranked = [_make_ranked(
            f"https://example.com/{reason}", SourceTier.MEDIA, False, skip_reason=reason,
        )]
        ctx = _build_enriched_context(ranked, [])
        assert expected_label in ctx, f"Expected '{expected_label}' for skip_reason='{reason}'"


def test_enriched_context_empty():
    ctx = _build_enriched_context([], [])
    assert ctx == "Keine Suchergebnisse gefunden."


def test_enriched_context_mixed_sources():
    """Mix of scraped, failed, and skipped sources."""
    ranked = [
        _make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True),
        _make_ranked("https://tagesschau.de/t", SourceTier.QUALITY_JOURNALISM, True),
        _make_ranked("https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall"),
    ]
    scraped = [
        ScrapedSource(
            url="https://correctiv.org/c", tier_label="Faktencheck",
            passage="Volltext hier", low_relevance=False,
            fetch_success=True, error=None,
        ),
        ScrapedSource(
            url="https://tagesschau.de/t", tier_label="Qualitätsjournalismus",
            passage="", low_relevance=False,
            fetch_success=False, error="403 Forbidden",
        ),
    ]
    ctx = _build_enriched_context(ranked, scraped)
    blocks = ctx.split("\n---\n")
    assert len(blocks) == 3
    assert "Volltext-Auszug" in blocks[0]
    assert "403 Forbidden" in blocks[1]
    assert "Paywall" in blocks[2]


def test_enriched_context_scrape_first_ordering():
    """Sources with should_scrape=True should appear first."""
    ranked = [
        _make_ranked("https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall"),
        _make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True),
    ]
    scraped = [ScrapedSource(
        url="https://correctiv.org/c", tier_label="Faktencheck",
        passage="Inhalt", low_relevance=False,
        fetch_success=True, error=None,
    )]
    ctx = _build_enriched_context(ranked, scraped)
    blocks = ctx.split("\n---\n")
    assert "Volltext-Auszug" in blocks[0]  # Scraped source first
    assert "Paywall" in blocks[1]
