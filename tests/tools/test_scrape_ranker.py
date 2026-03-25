"""Tests für tools/scrape_ranker.py – Ranking- und Passage-Extraktions-Logik."""

from __future__ import annotations

import pytest

from tools.scrape_ranker import (
    KNOWN_PAYWALLS,
    SOFT_PAYWALLS,
    STOPWORDS,
    RankedSource,
    _extract_claim_keywords,
    _extract_domain,
    _keyword_overlap,
    extract_relevant_passages,
    rank_sources,
)
from tools.source_classifier import SourceTier
from tools.web_search import SearchResult


# ── _extract_claim_keywords ──────────────────────────────────────


def test_extract_keywords_basic():
    keywords = _extract_claim_keywords("Die Kriminalität ist um 50% gestiegen")
    assert "kriminalität" in keywords
    assert "gestiegen" in keywords


def test_extract_keywords_filters_stopwords():
    keywords = _extract_claim_keywords("Diese werden nicht durch dabei gefiltert")
    # All words except "gefiltert" are stopwords or < 4 chars
    assert "gefiltert" in keywords
    for sw in ("diese", "werden", "nicht", "durch", "dabei"):
        assert sw not in keywords


def test_extract_keywords_filters_short_words():
    keywords = _extract_claim_keywords("Ein Amt hat das getan")
    # "ein", "amt", "hat", "das" are all < 4 chars
    assert "getan" in keywords
    assert len(keywords) == 1


def test_extract_keywords_empty_text():
    assert _extract_claim_keywords("") == set()


def test_extract_keywords_umlauts():
    keywords = _extract_claim_keywords("Über die Flüchtlingszahlen")
    assert "flüchtlingszahlen" in keywords
    # "über" is a stopword
    assert "über" not in keywords


# ── _keyword_overlap ─────────────────────────────────────────────


def test_keyword_overlap_full_match():
    keywords = {"kriminalität", "gestiegen"}
    text = "Die Kriminalität ist gestiegen laut Statistik"
    assert _keyword_overlap(keywords, text) == 1.0


def test_keyword_overlap_partial():
    keywords = {"kriminalität", "gestiegen", "berlin"}
    text = "Die Kriminalität in München"
    assert _keyword_overlap(keywords, text) == pytest.approx(1 / 3)


def test_keyword_overlap_no_match():
    keywords = {"kriminalität", "gestiegen"}
    text = "Wetter morgen in Hamburg"
    assert _keyword_overlap(keywords, text) == 0.0


def test_keyword_overlap_empty_keywords():
    assert _keyword_overlap(set(), "irgendein Text") == 0.0


def test_keyword_overlap_case_insensitive():
    keywords = {"kriminalität"}
    text = "KRIMINALITÄT steigt an"
    assert _keyword_overlap(keywords, text) == 1.0


# ── _extract_domain ──────────────────────────────────────────────


def test_extract_domain_simple():
    assert _extract_domain("https://tagesschau.de/faktencheck") == "tagesschau.de"


def test_extract_domain_removes_www():
    assert _extract_domain("https://www.spiegel.de/artikel") == "spiegel.de"


def test_extract_domain_with_subdomain():
    assert _extract_domain("https://faktencheck.dpa.com/check") == "faktencheck.dpa.com"


def test_extract_domain_empty():
    assert _extract_domain("") == ""


def test_extract_domain_with_port():
    assert _extract_domain("http://localhost:8888/search") == "localhost"


# ── rank_sources – Basics ────────────────────────────────────────


def _make_result(url: str, title: str = "T", snippet: str = "S") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


def test_rank_sources_empty_input():
    ranked = rank_sources({}, "Irgendein Claim")
    assert ranked == []


def test_rank_sources_single_query():
    results = {
        "q1": [_make_result("https://correctiv.org/faktencheck", snippet="Kriminalität gestiegen")]
    }
    ranked = rank_sources(results, "Kriminalität gestiegen")
    assert len(ranked) == 1
    assert ranked[0].tier == SourceTier.FACT_CHECKER
    assert ranked[0].should_scrape is True


def test_rank_sources_multiple_queries_deduplicates():
    r1 = _make_result("https://correctiv.org/check1", snippet="Kriminalität")
    r2 = _make_result("https://correctiv.org/check2", snippet="Kriminalität gestiegen Fakten")
    results = {"q1": [r1], "q2": [r2]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    # Should deduplicate by domain – only 1 correctiv.org entry
    correctiv_entries = [r for r in ranked if "correctiv" in r.result.url]
    assert len(correctiv_entries) == 1


# ── rank_sources – Paywall-Erkennung ─────────────────────────────


def test_rank_sources_blocks_known_paywalls():
    results = {"q": [
        _make_result("https://www.bild.de/artikel", snippet="Kriminalität gestiegen"),
        _make_result("https://www.faz.net/artikel", snippet="Kriminalität gestiegen"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    for rs in ranked:
        assert rs.should_scrape is False
        assert rs.skip_reason == "paywall"


def test_rank_sources_soft_paywall_fallback():
    """Soft-Paywall-Domains werden gescraped wenn weniger als 2 andere da sind."""
    results = {"q": [
        _make_result("https://www.spiegel.de/artikel", snippet="Kriminalität gestiegen Fakten"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    spiegel = [r for r in ranked if "spiegel" in r.result.url][0]
    # Fallback sollte greifen weil < 2 scrape-fähige Quellen
    assert spiegel.should_scrape is True


# ── rank_sources – Tier-basierte Filterung ───────────────────────


def test_rank_sources_blocks_user_generated():
    results = {"q": [
        _make_result("https://reddit.com/r/de/post", snippet="Kriminalität gestiegen"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    assert ranked[0].should_scrape is False
    assert ranked[0].skip_reason == "low_tier"


def test_rank_sources_blocks_unknown_tier():
    results = {"q": [
        _make_result("https://obscure-blog.example.com/post", snippet="Kriminalität gestiegen"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    assert ranked[0].should_scrape is False
    assert ranked[0].skip_reason == "low_tier"


def test_rank_sources_always_scrapes_fact_checkers():
    """Fact-Checker (Tier 4+) werden immer gescraped, egal welcher Score."""
    results = {"q": [
        _make_result("https://correctiv.org/faktencheck", snippet="unrelated content xyz"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    assert ranked[0].should_scrape is True
    assert ranked[0].tier == SourceTier.FACT_CHECKER


def test_rank_sources_always_scrapes_official():
    results = {"q": [
        _make_result("https://destatis.de/daten", snippet="unrelated"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    assert ranked[0].should_scrape is True
    assert ranked[0].tier == SourceTier.OFFICIAL


def test_rank_sources_quality_journalism_needs_relevance():
    """Quality journalism needs relevance >= 0.20 to be scraped."""
    results = {"q": [
        # High relevance: matches keywords
        _make_result(
            "https://tagesschau.de/inland/check",
            snippet="Kriminalität gestiegen Statistik Fakten",
        ),
        # Low relevance: no keyword match
        _make_result(
            "https://reuters.com/article",
            snippet="Weather forecast tomorrow sunny",
        ),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    ts = [r for r in ranked if "tagesschau" in r.result.url][0]
    reuters = [r for r in ranked if "reuters" in r.result.url][0]
    assert ts.should_scrape is True
    assert reuters.should_scrape is False
    assert reuters.skip_reason == "irrelevant"


# ── rank_sources – Media-Fallback ────────────────────────────────


def test_rank_sources_media_fallback_when_few_sources():
    """Media-Tier sources get scraped as fallback when < 2 scrapable sources."""
    results = {"q": [
        _make_result("https://n-tv.de/artikel", snippet="Kriminalität gestiegen Statistik"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    ntv = ranked[0]
    # Fallback: < 2 scrapable, MEDIA with relevance >= 0.15 gets enabled
    assert ntv.should_scrape is True


def test_rank_sources_media_no_fallback_when_enough():
    """Media with low hybrid_score stays blocked when enough other sources are scrapable."""
    results = {"q": [
        _make_result("https://correctiv.org/check", snippet="Kriminalität"),
        _make_result("https://destatis.de/data", snippet="Kriminalität"),
        # Media with irrelevant content → low hybrid_score → should not scrape
        _make_result("https://n-tv.de/artikel", snippet="Wetter morgen sonnig warm"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    ntv = [r for r in ranked if "n-tv" in r.result.url][0]
    assert ntv.should_scrape is False


# ── rank_sources – max_scrape Limit ──────────────────────────────


def test_rank_sources_respects_max_scrape():
    results = {"q": [
        _make_result("https://correctiv.org/check1", snippet="Kriminalität"),
        _make_result("https://mimikama.org/check1", snippet="Kriminalität"),
        _make_result("https://destatis.de/data", snippet="Kriminalität"),
        _make_result("https://snopes.com/check", snippet="Kriminalität"),
    ]}
    ranked = rank_sources(results, "Kriminalität", max_scrape=2)
    scrape_count = sum(1 for r in ranked if r.should_scrape)
    assert scrape_count == 2
    # Überschüssige bekommen "limit_reached"
    limited = [r for r in ranked if r.skip_reason == "limit_reached"]
    assert len(limited) == 2


# ── rank_sources – Sortierung ────────────────────────────────────


def test_rank_sources_sorted_by_hybrid_score():
    """Results are sorted by hybrid_score (descending)."""
    results = {"q": [
        _make_result("https://correctiv.org/c", snippet="Kriminalität"),
        _make_result("https://destatis.de/d", snippet="Kriminalität gestiegen Fakten"),
        _make_result("https://tagesschau.de/t", snippet="Kriminalität gestiegen"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen")
    scores = [r.hybrid_score for r in ranked]
    assert scores == sorted(scores, reverse=True)


# ── rank_sources – Irrelevanz-Filter ─────────────────────────────


def test_rank_sources_irrelevance_filter():
    """Low relevance + tier < FACT_CHECKER → irrelevant."""
    results = {"q": [
        _make_result(
            "https://tagesschau.de/wetter",
            snippet="Sonnenschein morgen warm Frühling",
        ),
        # Add a scrapable source so fallback doesn't kick in
        _make_result("https://correctiv.org/c", snippet="x"),
        _make_result("https://destatis.de/d", snippet="x"),
    ]}
    ranked = rank_sources(results, "Kriminalität gestiegen Berlin")
    ts = [r for r in ranked if "tagesschau" in r.result.url][0]
    assert ts.should_scrape is False
    assert ts.skip_reason == "irrelevant"


# ── extract_relevant_passages ────────────────────────────────────


def test_passages_basic():
    article = (
        "Absatz eins über das Wetter und andere Dinge die nichts mit dem Thema zu tun haben.\n\n"
        "Die Kriminalität in Deutschland ist laut BKA um 50% gestiegen seit dem Jahr 2015.\n\n"
        "Ein weiterer Absatz über Sport und Freizeit der keine Keywords enthält hier.\n\n"
        "Die Polizeiliche Kriminalstatistik zeigt einen Anstieg der Fallzahlen um genau 50%."
    )
    passage, low_rel = extract_relevant_passages(
        article, "Kriminalität ist um 50% gestiegen"
    )
    assert not low_rel
    assert "Kriminalität" in passage or "kriminalität" in passage.lower()
    assert "50%" in passage


def test_passages_respects_max_chars():
    long_para = "Kriminalität gestiegen Fakten Zahlen. " * 50  # ~1800 chars
    article = f"{long_para}\n\n{'Weiterer Text. ' * 50}"
    passage, _ = extract_relevant_passages(article, "Kriminalität", max_chars=500)
    assert len(passage) <= 600  # Some tolerance for boundary


def test_passages_low_relevance_not_triggered_by_position_alone():
    """Position bonus alone (weight 0.2) keeps max_score >= 0.1,
    so low_relevance is False even with zero keyword overlap."""
    paras = [
        f"Absatz {i}: Heute scheint die Sonne und es wird warm in ganz Deutschland Wetterbericht aktuell."
        for i in range(20)
    ]
    article = "\n\n".join(paras)
    passage, low_rel = extract_relevant_passages(
        article, "Kriminalität ist um 50% gestiegen"
    )
    # Position bonus for first paragraph = 0.2 * 1.0 = 0.2 > 0.1
    assert low_rel is False
    assert len(passage) > 0


def test_passages_few_paragraphs_returns_truncated():
    """With < 2 paragraphs, return truncated article."""
    article = "Nur ein einziger langer Absatz ohne Umbrüche, der viele Wörter enthält zum Testen."
    passage, low_rel = extract_relevant_passages(article, "Kriminalität")
    assert passage == article[:1500]
    assert low_rel is False


def test_passages_preserves_original_order():
    """Selected paragraphs should be in their original order."""
    article = (
        "Erster Absatz enthält das Wort Kriminalität und ist damit relevant für den Test.\n\n"
        "Zweiter Absatz über Sport und andere Themen ohne Bezug zum eigentlichen Claim.\n\n"
        "Dritter Absatz auch über Kriminalität gestiegen Fakten Daten und weitere Informationen."
    )
    passage, _ = extract_relevant_passages(article, "Kriminalität gestiegen")
    lines = passage.split("\n\n")
    if len(lines) >= 2:
        # First selected paragraph should be about Kriminalität (Absatz 1 or 3)
        assert "Erster" in lines[0] or "Dritter" in lines[0]


def test_passages_number_matching():
    """Paragraphs with matching numbers score higher."""
    article = (
        "Die Wirtschaft wächst um drei Prozent pro Jahr laut neuesten Berichten.\n\n"
        "Genau 42 Prozent der Befragten stimmten zu in der aktuellen Umfrage.\n\n"
        "Insgesamt sind es nur wenige Prozent die hier betroffen sind im Land."
    )
    passage, _ = extract_relevant_passages(article, "42 Prozent der Befragten")
    assert "42" in passage


def test_passages_empty_article():
    passage, low_rel = extract_relevant_passages("", "Claim text")
    assert passage == ""
    assert low_rel is False
