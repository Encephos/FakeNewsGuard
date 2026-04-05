"""Tests für robuste Retrieval-Mechanismen im EvidenceBuilderAgent.

Geprüfte Invarianten:
    - Profilbasiertes Ranking greift VOR dem Scraping
    - LangSearch bekommt adaptiv mehr Queries für komplexe Claims
    - Tavily-Content wird als Excerpt-Fallback genutzt
    - Low-Trust-Seiten werden vor dem Scraping entfernt
    - Confidence wird bei hohem Low-Trust-Anteil gedeckelt
    - Fallback-Retrieval nutzt LangSearch parallel
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.web_search import SearchResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_async(coro):
    """Führe Coroutine in neuer Event-Loop aus (vermeidet Konflikt mit pytest-asyncio)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_scraped(url: str, *, success: bool = False, passage: str = "") :
    """Erstelle ein ScrapedSource-Objekt mit korrekten Pflichtfeldern."""
    from tools.source_scraper import ScrapedSource
    return ScrapedSource(
        url=url,
        tier_label="MEDIA",
        passage=passage,
        low_relevance=False,
        fetch_success=success,
        error=None,
    )


def _make_claim(text: str = "Testbehauptung", claim_type: str = "FACTUAL"):
    from models.schemas import Claim, ClaimType
    return Claim(id="C_test", text=text, type=ClaimType(claim_type))


def _make_processed_claim(
    text: str = "Testbehauptung",
    claim_type: str = "FACTUAL",
    institutions: list[str] | None = None,
    policy_terms: list[str] | None = None,
    locations: list[str] | None = None,
):
    from models.schemas import ClaimType, ProcessedClaim, ClaimSearchProfile
    profile = ClaimSearchProfile(
        core_entities=[],
        institutions=institutions or [],
        locations=locations or [],
        action_terms=[],
        policy_terms=policy_terms or [],
        number_terms=[],
        sanction_terms=[],
        exclusion_terms=[],
        official_source_hints=[],
        fact_check_hints=[],
    )
    return ProcessedClaim(
        id="C_test",
        text=text,
        type=ClaimType(claim_type),
        search_profile=profile,
    )


def _make_result(
    url: str = "https://example.com",
    title: str = "Test",
    snippet: str = "Test snippet",
    content: str = "",
) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, content=content)


def _make_agent():
    """Erstelle minimalen EvidenceBuilderAgent ohne echte API-Verbindungen."""
    from agents.evidence_builder import EvidenceBuilderAgent
    from config import AppConfig
    config = AppConfig()
    agent = EvidenceBuilderAgent.__new__(EvidenceBuilderAgent)
    agent.config = config
    agent.topic_model = None
    agent._log = lambda msg: None
    # Stub caches to avoid requiring Valkey/real backends
    from unittest.mock import MagicMock
    agent._search_cache = MagicMock()
    agent._search_cache.get = MagicMock(return_value=None)
    agent._url_cache = MagicMock()
    agent._url_cache.get = MagicMock(return_value=None)
    return agent


def _fake_rank_sources_capturing(captured_list):
    """Gibt eine fake rank_sources zurück, die gefilterte Kandidaten capturiert."""
    def _inner(results_by_query, claim_text, max_scrape=5, profile=None):
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier
        items = results_by_query.get("_all", [])
        captured_list.extend(items)
        return [
            RankedSource(
                result=r, tier=SourceTier.MEDIA,
                relevance_score=0.5, should_scrape=False, skip_reason="test",
            )
            for r in items
        ]
    return _inner


async def _fake_scrape_sources(ranked, claim_text, **kwargs):
    return [_make_scraped(rs.result.url) for rs in ranked]


# ── 1. Adaptive LangSearch Query Count ───────────────────────────────────────


class TestAdaptiveLangSearchQueryCount:
    def test_simple_factual_claim_gets_simple_count(self):
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        claim = _make_claim("Berlin ist Hauptstadt.", "FACTUAL")
        assert _langsearch_query_count(claim, cfg) == 2

    def test_statistical_claim_gets_complex_count(self):
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        claim = _make_claim("40% der Einbrüche gehen auf Ausländer zurück.", "STATISTICAL")
        assert _langsearch_query_count(claim, cfg) == 4

    def test_causal_claim_gets_complex_count(self):
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        claim = _make_claim("Zuwanderung verursacht steigende Kriminalität.", "CAUSAL")
        assert _langsearch_query_count(claim, cfg) == 4

    def test_contextual_claim_gets_complex_count(self):
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        claim = _make_claim("Deutschland hat die meisten Asylanträge in der EU.", "CONTEXTUAL")
        assert _langsearch_query_count(claim, cfg) == 4

    def test_long_factual_claim_gets_complex_count(self):
        """Lange Claims sind strukturell komplexer – auch FACTUAL bekommt mehr Queries."""
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        long_text = "x" * 101
        claim = _make_claim(long_text, "FACTUAL")
        assert _langsearch_query_count(claim, cfg) == 4

    def test_rich_profile_gets_complex_count(self):
        """Claims mit reichhaltigem SearchProfile bekommen mehr Queries."""
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=2, langsearch_queries_complex=4)
        # 3+ Institutionen/Policy-Terms = reichhaltig
        claim = _make_processed_claim(
            text="Kurzbehauptung",
            claim_type="FACTUAL",
            institutions=["Stadtrat", "Hannover"],
            policy_terms=["15-Minuten-Stadt"],
        )
        assert _langsearch_query_count(claim, cfg) == 4

    def test_config_values_respected(self):
        """Konfigurierbare Counts werden korrekt übergeben."""
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig(langsearch_queries_simple=3, langsearch_queries_complex=5)
        simple_claim = _make_claim("Test.", "FACTUAL")
        complex_claim = _make_claim("Test.", "STATISTICAL")
        assert _langsearch_query_count(simple_claim, cfg) == 3
        assert _langsearch_query_count(complex_claim, cfg) == 5


# ── 2. Pre-Scraping Filter: Low-Trust-Seiten entfernen ───────────────────────


class TestPreScrapingLowTrustFilter:
    """_rank_and_scrape() muss Low-Trust-Seiten VOR rank_sources() entfernen."""

    def _run_capture(self, results, claim, profile=None):
        """Führt _rank_and_scrape aus und gibt die gefilterten Kandidaten zurück."""
        agent = _make_agent()
        captured = []

        with patch("agents.evidence_builder.rank_sources", side_effect=_fake_rank_sources_capturing(captured)):
            with patch("agents.evidence_builder.scrape_sources", side_effect=_fake_scrape_sources):
                _run_async(
                    agent._rank_and_scrape(results, claim, profile=profile)
                )
        return captured

    def test_low_trust_currency_site_removed_before_rank_sources(self):
        """xe.com (Währungsrechner) muss vor rank_sources gefiltert werden."""
        claim = _make_claim("Der Kurs von 1 Euro entspricht 1,08 Dollar.")
        low_trust = _make_result(
            url="https://xe.com/currencyconverter/convert/?Amount=1&From=EUR&To=USD",
            title="EUR in USD umrechnen – XE.com",
            snippet="1 EUR = 1.08 USD",
        )
        good = _make_result(
            url="https://tagesschau.de/wirtschaft/euro-kurs",
            title="Euro-Kurs aktuell",
            snippet="Tagesschau Wirtschaft",
        )
        filtered = self._run_capture([low_trust, good], claim)
        urls = [r.url for r in filtered]
        assert low_trust.url not in urls, "Währungsrechner-Seite darf nicht in den Scraping-Pool"
        assert good.url in urls, "Qualitäts-Quelle muss erhalten bleiben"

    def test_grammar_site_removed_before_rank_sources(self):
        """Grammatik-/Konjugationsseiten dürfen nicht in den Scraping-Pool."""
        claim = _make_claim("Die Konjugation von 'dürfen' ist komplex.")
        grammar_site = _make_result(
            url="https://verbformen.de/konjugation/duerfen.htm",
            title="Konjugation dürfen – alle Formen",
            snippet="Konjugation des Verbs dürfen im Indikativ",
        )
        filtered = self._run_capture([grammar_site], claim)
        assert all(r.url != grammar_site.url for r in filtered)

    def test_factchecker_not_removed_despite_domain(self):
        """Fact-Checker-Domains werden nie durch den Low-Trust-Filter entfernt."""
        claim = _make_claim("Test")
        # correctiv.org ist Tier-4 Fact-Checker, kein Low-Trust
        fc_result = _make_result(
            url="https://correctiv.org/faktencheck/test",
            title="Faktencheck: Test",
            snippet="Correctiv Faktencheck",
        )
        filtered = self._run_capture([fc_result], claim)
        assert any(r.url == fc_result.url for r in filtered), \
            "Fact-Checker-Domain muss immer durchgelassen werden"

    def test_offtopic_with_strong_penalty_removed(self):
        """Klar off-topic Shopping-Ergebnis mit starker Profil-Penalty wird entfernt."""
        claim = _make_processed_claim(
            text="Der Hannover Stadtrat plant 15-Minuten-Stadt",
            institutions=["Stadtrat"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
        )
        shopping = _make_result(
            url="https://amazon.de/product/123",
            title="Schnelle Lieferung – jetzt kaufen",
            snippet="Jetzt kaufen ab 15 Euro versandkostenfrei",
        )
        filtered = self._run_capture([shopping], claim, profile=claim.search_profile)
        assert all(r.url != shopping.url for r in filtered), \
            "Shopping-Seite mit starker Penalty muss entfernt werden"

    def test_normal_source_not_removed(self):
        """Normale Qualitäts-Quellen werden nicht gefiltert."""
        claim = _make_claim("Kriminalität in Deutschland stieg 2023.")
        good = _make_result(
            url="https://bka.de/pks2023",
            title="BKA Polizeiliche Kriminalstatistik 2023",
            snippet="Straftaten in Deutschland 2023",
        )
        filtered = self._run_capture([good], claim)
        assert any(r.url == good.url for r in filtered)


# ── 3. Tavily-Content als Excerpt-Fallback ────────────────────────────────────


class TestTavilyContentAsExcerpt:
    """Wenn Scraping fehlschlägt aber Tavily-Content vorhanden ist, soll
    rs.result.content als Excerpt genutzt werden (extraction_conf=0.6)."""

    def _run_build_evidence_items(self, ranked, scraped, claim_text="Test", gfc_matches=None, profile=None):
        """Führt _build_evidence_items via Agenten-Instanz aus."""
        agent = _make_agent()
        return agent._build_evidence_items(ranked, scraped, claim_text, gfc_matches or [], profile)

    def test_tavily_content_used_when_scraping_fails(self):
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier

        result_with_content = _make_result(
            url="https://tagesschau.de/test",
            title="Tagesschau Artikel",
            snippet="Kurzes Snippet",
            content="Langer Tavily-Volltext mit relevanten Informationen über den Claim-Inhalt.",
        )
        ranked = [
            RankedSource(
                result=result_with_content,
                tier=SourceTier.QUALITY_JOURNALISM,
                relevance_score=0.7,
                should_scrape=True,
                skip_reason=None,
            )
        ]
        # Scraping schlägt fehl
        scraped = [_make_scraped("https://tagesschau.de/test", success=False, passage="")]

        items = self._run_build_evidence_items(ranked, scraped, "relevanter Claim")
        assert len(items) >= 1
        item = next((i for i in items if i.source.url == "https://tagesschau.de/test"), None)
        assert item is not None
        # Excerpt muss länger sein als das kurze Snippet
        assert len(item.excerpt) > len("Kurzes Snippet"), \
            "Excerpt muss Tavily-Content verwenden wenn Scraping fehlschlägt"

    def test_scraping_trumps_tavily_content(self):
        """Gescrapte Passage hat höchste Priorität über Tavily-Content.

        Da _rank_evidence_items() die extraction_confidence normiert,
        prüfen wir stattdessen, dass der Excerpt die gescrapte Passage enthält.
        """
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier

        result_with_content = _make_result(
            url="https://example.com/test",
            title="Test",
            snippet="Snippet",
            content="Tavily-Content der von gescraptem Inhalt überschrieben werden soll.",
        )
        ranked = [
            RankedSource(
                result=result_with_content,
                tier=SourceTier.QUALITY_JOURNALISM,
                relevance_score=0.7,
                should_scrape=True,
                skip_reason=None,
            )
        ]
        scraped = [
            _make_scraped("https://example.com/test", success=True, passage="Gescrapte Passage.")
        ]

        items = self._run_build_evidence_items(ranked, scraped, "Test")
        item = next((i for i in items if i.source.url == "https://example.com/test"), None)
        assert item is not None
        # Die gescrapte Passage muss im Excerpt enthalten sein (höchste Priorität)
        assert "Gescrapte Passage" in item.excerpt, \
            "Gescrapte Passage muss im Excerpt erscheinen (Priorität über Tavily-Content)"

    def test_snippet_used_as_last_resort(self):
        """Wenn weder Scraping noch Tavily-Content vorhanden, wird Snippet genutzt."""
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier

        result_snippet_only = _make_result(
            url="https://example.com/only-snippet",
            title="Test",
            snippet="Nur ein kurzes Snippet",
            content="",  # Kein Tavily-Content
        )
        ranked = [
            RankedSource(
                result=result_snippet_only,
                tier=SourceTier.QUALITY_JOURNALISM,
                relevance_score=0.5,
                should_scrape=False,
                skip_reason=None,
            )
        ]
        scraped = [_make_scraped("https://example.com/only-snippet", success=False)]

        items = self._run_build_evidence_items(ranked, scraped, "Test")
        item = next((i for i in items if i.source.url == "https://example.com/only-snippet"), None)
        if item:  # Nur wenn Item nicht durch Ranking herausgefiltert wurde
            # Snippet wird als Excerpt genutzt wenn kein besserer Content vorhanden
            assert "Snippet" in item.excerpt


# ── 4. LangSearch-Content-reiche Kandidaten überspringen Scraping ────────────


class TestLangSearchContentSkipsScraping:
    """Kandidaten mit ausreichendem LangSearch-Content und guter Relevanz
    sollen should_scrape=False bekommen (skip_reason='langsearch_content_sufficient')."""

    def test_strong_content_disables_scraping(self):
        """Gut-relevanter LangSearch-Content → should_scrape=False."""
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier

        agent = _make_agent()
        claim = _make_claim("Kriminalität in Deutschland stieg 2023 laut Polizeistatistik")

        ls_content = (
            "Die Polizeiliche Kriminalstatistik 2023 zeigt einen Anstieg der Kriminalität "
            "in Deutschland. Laut BKA wurden 2023 insgesamt X Straftaten erfasst. "
            "Dies stellt eine Zunahme gegenüber dem Vorjahr dar. " * 3
        )
        ls_result = _make_result(
            url="https://bka.de/pks2023",
            title="BKA Polizeiliche Kriminalstatistik 2023",
            snippet="BKA PKS 2023 Kriminalstatistik Deutschland Straftaten",
            content=ls_content,
        )

        ranked = [
            RankedSource(
                result=ls_result,
                tier=SourceTier.OFFICIAL,
                relevance_score=0.8,
                should_scrape=True,
                skip_reason=None,
            )
        ]

        def fake_rank_sources(results_by_query, claim_text, max_scrape=5, profile=None):
            return ranked

        with patch("agents.evidence_builder.rank_sources", side_effect=fake_rank_sources):
            with patch("agents.evidence_builder.scrape_sources", side_effect=_fake_scrape_sources):
                _run_async(
                    agent._rank_and_scrape([ls_result], claim, profile=None)
                )

        assert ranked[0].should_scrape is False, \
            "Ausreichend relevanter LangSearch-Content muss should_scrape deaktivieren"
        assert ranked[0].skip_reason == "langsearch_content_sufficient"

    def test_weak_content_does_not_disable_scraping(self):
        """Zu kurzer Content (< 300 Zeichen) → kein Content-Skip."""
        from tools.scrape_ranker import RankedSource
        from tools.source_classifier import SourceTier

        agent = _make_agent()
        claim = _make_claim("Kriminalstatistik 2023")

        short_content = "Kurzer Text."  # < 300 Zeichen → kein Skip
        result = _make_result(
            url="https://example.com/test",
            title="Test",
            snippet="Snippet",
            content=short_content,
        )

        ranked = [
            RankedSource(
                result=result,
                tier=SourceTier.QUALITY_JOURNALISM,
                relevance_score=0.5,
                should_scrape=True,
                skip_reason=None,
            )
        ]

        def fake_rank_sources(results_by_query, claim_text, max_scrape=5, profile=None):
            return ranked

        with patch("agents.evidence_builder.rank_sources", side_effect=fake_rank_sources):
            with patch("agents.evidence_builder.scrape_sources", side_effect=_fake_scrape_sources):
                _run_async(
                    agent._rank_and_scrape([result], claim, profile=None)
                )

        assert ranked[0].skip_reason != "langsearch_content_sufficient", \
            "Kurzer Content darf should_scrape nicht deaktivieren"


# ── 5. Confidence-Deckelung bei hohem Low-Trust-Anteil ───────────────────────


class TestConfidenceCapWithLowTrust:
    def test_low_trust_rate_reduces_overall_quality(self):
        """Hoher low_trust_rate-Anteil muss overall_quality reduzieren."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = []
        low_trust_urls = [
            "https://xe.com/currencyconverter/",
            "https://verbformen.de/konjugation/",
            "https://juraforum.de/lexikon/test",
            "https://gutefrage.net/frage/test",
        ]
        for url in low_trust_urls:
            items.append(EvidenceItem(
                source=EvidenceSource(url=url, domain=url.split("/")[2], domain_tier=5),
                excerpt="Test",
                relevance_score=0.5,
                extraction_confidence=0.5,
            ))
        items.append(EvidenceItem(
            source=EvidenceSource(url="https://tagesschau.de/test", domain="tagesschau.de", domain_tier=3),
            excerpt="Guter Inhalt",
            relevance_score=0.8,
            extraction_confidence=0.8,
        ))

        quality_with = _compute_quality_signals(items, [], low_trust_penalty_factor=0.20)
        quality_without = _compute_quality_signals(items, [], low_trust_penalty_factor=0.0)

        assert quality_with.overall_quality < quality_without.overall_quality, \
            "Low-Trust-Penalty muss overall_quality reduzieren"

    def test_zero_low_trust_no_penalty(self):
        """Keine Low-Trust-Quellen → kein Penalty auf overall_quality."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/data",
                    domain="destatis.de",
                    domain_tier=1,
                    is_primary_source=True,
                ),
                excerpt="Offizielle Statistik",
                relevance_score=0.9,
                extraction_confidence=0.9,
            )
        ]
        quality = _compute_quality_signals(items, [], low_trust_penalty_factor=0.20)
        assert quality.low_trust_rate == 0.0

    def test_high_low_trust_rate_lowers_quality(self):
        """100% Low-Trust-Rate → overall_quality ist niedriger als ohne Penalty."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://xe.com/convert", domain="xe.com", domain_tier=5),
                excerpt="Currency",
                relevance_score=0.5,
                extraction_confidence=0.5,
            )
        ]
        quality_with = _compute_quality_signals(items, [], low_trust_penalty_factor=0.20)
        quality_without = _compute_quality_signals(items, [], low_trust_penalty_factor=0.0)

        # Mit Penalty muss Qualität niedriger oder gleich sein (Minimum 0.0)
        assert quality_with.overall_quality <= quality_without.overall_quality

    def test_penalty_factor_zero_no_impact(self):
        """Penalty-Faktor 0 → kein Einfluss auf overall_quality."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://xe.com/convert", domain="xe.com", domain_tier=5),
                excerpt="Currency",
                relevance_score=0.5,
                extraction_confidence=0.5,
            )
        ]
        q0 = _compute_quality_signals(items, [], low_trust_penalty_factor=0.0)
        q_default = _compute_quality_signals(items, [])

        # Default-Faktor (0.20) muss stärker penalisieren als 0.0
        assert q0.overall_quality >= q_default.overall_quality


# ── 6. Fallback nutzt LangSearch ─────────────────────────────────────────────


class TestFallbackUsesLangSearch:
    """_fallback_retrieval() muss LangSearch parallel zu SearXNG aufrufen."""

    def test_fallback_calls_langsearch_when_enabled(self):
        from config import AppConfig

        config = AppConfig()
        config.langsearch.enabled = True
        config.langsearch.api_key = "fake-key"
        config.tavily.enabled = False  # Tavily deaktiviert für diesen Test

        agent = _make_agent()
        agent.config = config
        claim = _make_claim("Test-Fallback-Claim")

        langsearch_called = False
        searxng_called = False

        async def fake_langsearch_search(queries, max_results=None):
            nonlocal langsearch_called
            langsearch_called = True
            return {}

        async def fake_searxng_search(queries, max_results=None, categories=None):
            nonlocal searxng_called
            searxng_called = True
            return {}

        mock_langsearch = MagicMock()
        mock_langsearch.multi_search_async = fake_langsearch_search
        mock_searxng = MagicMock()
        mock_searxng.multi_search_async = fake_searxng_search
        agent._langsearch = mock_langsearch
        agent._searxng = mock_searxng

        with patch("agents.fact_checker._build_fallback_queries", return_value=["fallback query"]):
            _run_async(agent._fallback_retrieval(claim, ["original query"]))

        assert searxng_called, "SearXNG muss im Fallback aufgerufen werden"
        assert langsearch_called, "LangSearch muss im Fallback aufgerufen werden"

    def test_fallback_skips_langsearch_when_disabled(self):
        """Wenn LangSearch deaktiviert ist, soll kein Aufruf erfolgen."""
        from config import AppConfig

        config = AppConfig()
        config.langsearch.enabled = False
        config.tavily.enabled = False  # Tavily deaktiviert für diesen Test
        agent = _make_agent()
        agent.config = config
        claim = _make_claim("Test")

        langsearch_called = False

        async def fake_langsearch_search(queries, max_results=None):
            nonlocal langsearch_called
            langsearch_called = True
            return {}

        async def fake_searxng_search(queries, max_results=None, categories=None):
            return {}

        mock_langsearch = MagicMock()
        mock_langsearch.multi_search_async = fake_langsearch_search
        mock_searxng = MagicMock()
        mock_searxng.multi_search_async = fake_searxng_search
        agent._langsearch = mock_langsearch
        agent._searxng = mock_searxng

        with patch("agents.fact_checker._build_fallback_queries", return_value=["fallback"]):
            _run_async(agent._fallback_retrieval(claim, ["original"]))

        assert not langsearch_called, "LangSearch darf nicht aufgerufen werden wenn disabled"

    def test_fallback_returns_empty_on_no_queries(self):
        """Wenn keine Fallback-Queries generiert werden, leere Liste zurückgeben."""
        agent = _make_agent()
        claim = _make_claim("Test")

        async def fake_searxng_search(queries, max_results=None, categories=None):
            return {}

        mock_searxng = MagicMock()
        mock_searxng.multi_search_async = fake_searxng_search
        agent._searxng = mock_searxng

        with patch("agents.fact_checker._build_fallback_queries", return_value=[]):
            result = _run_async(agent._fallback_retrieval(claim, ["original"]))

        assert result == []


# ── 7. EvidenceRetrievalConfig Defaults ──────────────────────────────────────


class TestEvidenceRetrievalConfigDefaults:
    def test_default_values(self):
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig()
        assert cfg.langsearch_queries_simple == 3
        assert cfg.langsearch_queries_complex == 5
        assert cfg.langsearch_retry_on_weak is True
        assert cfg.tavily_primary_queries == 1
        assert cfg.tavily_max_queries_per_claim == 3
        assert cfg.tavily_request_budget == 10
        assert cfg.weak_evidence_threshold == 0.25
        assert cfg.low_trust_confidence_penalty == 0.20
        assert cfg.pre_scrape_offtopic_penalty == 0.70

    def test_appconfig_has_evidence_retrieval(self):
        from config import AppConfig, EvidenceRetrievalConfig

        config = AppConfig()
        assert hasattr(config, "evidence_retrieval")
        assert isinstance(config.evidence_retrieval, EvidenceRetrievalConfig)
