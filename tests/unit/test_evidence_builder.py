"""Tests für den EvidenceBuilderAgent und zugehörige Hilfsfunktionen."""

from __future__ import annotations

import pytest


# ── Unit Tests: Deduplication ─────────────────────────────────────────────────

class TestDeduplication:
    def test_dedup_exact_urls(self):
        from agents.evidence_builder import _dedup_results
        from tools.web_search import SearchResult

        results = [
            SearchResult(title="A", url="https://example.com/page", snippet=""),
            SearchResult(title="B", url="https://example.com/page", snippet=""),
            SearchResult(title="C", url="https://other.com/page", snippet=""),
        ]
        unique = _dedup_results(results)
        assert len(unique) == 2

    def test_dedup_trailing_slash(self):
        from agents.evidence_builder import _dedup_results
        from tools.web_search import SearchResult

        results = [
            SearchResult(title="A", url="https://example.com/page/", snippet=""),
            SearchResult(title="B", url="https://example.com/page", snippet=""),
        ]
        unique = _dedup_results(results)
        assert len(unique) == 1

    def test_dedup_preserves_order(self):
        from agents.evidence_builder import _dedup_results
        from tools.web_search import SearchResult

        results = [
            SearchResult(title="First", url="https://a.com", snippet=""),
            SearchResult(title="Second", url="https://b.com", snippet=""),
            SearchResult(title="Duplicate", url="https://a.com", snippet=""),
        ]
        unique = _dedup_results(results)
        assert unique[0].title == "First"
        assert unique[1].title == "Second"


# ── Unit Tests: Semantic Deduplication ───────────────────────────────────────

class TestSemanticDeduplication:
    """Trigram-based semantic dedup of near-duplicate SearchResults."""

    def _make_result(self, url: str, snippet: str) -> "SearchResult":
        from tools.web_search import SearchResult
        return SearchResult(title="T", url=url, snippet=snippet)

    def test_near_duplicates_collapsed_to_best_tier(self):
        from agents.evidence_scoring import _dedup_results
        text = (
            "Der Bundestag hat heute das neue Sicherheitsgesetz mit großer Mehrheit "
            "verabschiedet und damit einen wichtigen Schritt in der Innenpolitik vollzogen."
        )
        results = [
            self._make_result("https://random-blog.de/artikel", text),
            self._make_result("https://www.reuters.com/article", text),
            self._make_result("https://www.destatis.de/news", text),  # tier 1 wins
        ]
        unique = _dedup_results(results, semantic_threshold=0.65)
        assert len(unique) == 1
        assert "destatis.de" in unique[0].url

    def test_distinct_content_not_deduped(self):
        from agents.evidence_scoring import _dedup_results
        results = [
            self._make_result("https://a.com", "Inflation stieg im März auf 4,2 Prozent laut Statistikamt"),
            self._make_result("https://b.com", "Bundestag beschloss neues Waffengesetz mit knapper Mehrheit"),
        ]
        unique = _dedup_results(results, semantic_threshold=0.65)
        assert len(unique) == 2

    def test_threshold_zero_disables_semantic_dedup(self):
        from agents.evidence_scoring import _dedup_results
        text = "gleicher text wird nicht dedupliziert wenn threshold auf null gesetzt ist hier"
        results = [
            self._make_result("https://a.com/x", text),
            self._make_result("https://b.com/y", text),
        ]
        unique = _dedup_results(results, semantic_threshold=0.0)
        assert len(unique) == 2

    def test_text_trigrams_basic(self):
        from agents.evidence_scoring import _text_trigrams
        trigrams = _text_trigrams("the cat sat on mat")
        assert "the cat sat" in trigrams
        assert "cat sat on" in trigrams
        assert "sat on mat" in trigrams
        assert len(trigrams) == 3

    def test_text_trigrams_short_text(self):
        from agents.evidence_scoring import _text_trigrams
        assert _text_trigrams("one two") == set()
        assert _text_trigrams("") == set()

    def test_jaccard_identical(self):
        from agents.evidence_scoring import _jaccard_similarity
        s = {"a b c", "b c d"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_jaccard_disjoint(self):
        from agents.evidence_scoring import _jaccard_similarity
        assert _jaccard_similarity({"a b c"}, {"x y z"}) == 0.0

    def test_jaccard_empty(self):
        from agents.evidence_scoring import _jaccard_similarity
        assert _jaccard_similarity(set(), {"a b c"}) == 0.0


# ── Unit Tests: Domain Tier ───────────────────────────────────────────────────

class TestDomainTier:
    def test_tier1_destatis(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://www.destatis.de/DE/Themen/...") == 1

    def test_tier1_eurostat(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://eurostat.ec.europa.eu/data/...") == 1

    def test_tier2_bka(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://www.bka.de/pks2023") == 2

    def test_tier3_reuters(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://www.reuters.com/article/...") == 3

    def test_tier4_correctiv(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://correctiv.org/faktencheck/...") == 4

    def test_tier5_unknown(self):
        from agents.evidence_builder import _domain_tier
        assert _domain_tier("https://some-random-blog.de/post") == 5

    def test_is_fact_check_org(self):
        from agents.evidence_builder import _is_fact_check_org
        assert _is_fact_check_org("https://correctiv.org/faktencheck") is True
        assert _is_fact_check_org("https://destatis.de/data") is False


# ── Unit Tests: Relevance Score ───────────────────────────────────────────────

class TestRelevanceScore:
    def test_high_overlap(self):
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        result = SearchResult(
            title="Kriminalität Deutschland 2023 Statistik",
            url="https://example.com",
            snippet="Kriminalität in Deutschland stieg 2023 laut Statistik",
        )
        score = _relevance_score(result, "Kriminalität Deutschland 2023 Statistik")
        assert score > 0.5

    def test_no_overlap(self):
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        result = SearchResult(
            title="Kochrezept für Pasta",
            url="https://example.com",
            snippet="Kochen Sie die Pasta al dente",
        )
        score = _relevance_score(result, "Kriminalität Deutschland Statistik")
        assert score < 0.3


# ── Unit Tests: Evidence Quality Signals ─────────────────────────────────────

class TestEvidenceQualitySignals:
    def test_quality_with_primary_sources(self):
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/test",
                    domain="destatis.de",
                    domain_tier=1,
                    is_primary_source=True,
                ),
                excerpt="Test",
                relevance_score=0.9,
                extraction_confidence=0.8,
            )
        ]
        quality = _compute_quality_signals(items, [])
        assert quality.has_primary_sources is True
        assert quality.top_tier_count == 1

    def test_quality_with_gfc_matches(self):
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import GoogleFactCheckMatch

        gfc = [GoogleFactCheckMatch(
            claim_reviewed="Test",
            rating="Falsch",
            publisher="Correctiv",
            url="https://correctiv.org/test",
        )]
        quality = _compute_quality_signals([], gfc)
        assert quality.has_fact_check_org_result is True

    def test_empty_evidence_insufficient(self):
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus

        quality = _compute_quality_signals([], [])
        assert quality.source_consensus == SourceConsensus.INSUFFICIENT
        assert quality.overall_quality == 0.0


# ── Unit Tests: Contradiction Detection ──────────────────────────────────────

class TestContradictionDetection:
    def test_no_contradictions_if_same_sentiment(self):
        from agents.evidence_builder import _detect_contradictions
        from models.evidence_models import EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://a.com", domain="a.com", domain_tier=3),
                excerpt="Die Zahl stieg deutlich an.",
                relevance_score=0.8,
                extraction_confidence=0.7,
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://b.com", domain="b.com", domain_tier=3),
                excerpt="Es gab einen Anstieg.",
                relevance_score=0.8,
                extraction_confidence=0.7,
            ),
        ]
        contradictions = _detect_contradictions(items)
        assert len(contradictions) == 0

    def test_max_contradictions_limit(self):
        from agents.evidence_builder import _detect_contradictions
        from models.evidence_models import EvidenceItem, EvidenceSource

        # Erstelle viele potenzielle Widersprüche
        items = []
        for i in range(10):
            negation = "nicht" if i % 2 == 0 else ""
            items.append(EvidenceItem(
                source=EvidenceSource(url=f"https://source{i}.com", domain=f"source{i}.com", domain_tier=3),
                excerpt=f"Die Zahl {negation} stieg.",
                relevance_score=0.9,
                extraction_confidence=0.8,
            ))
        contradictions = _detect_contradictions(items)
        assert len(contradictions) <= 5  # Limit enforced

    def test_numeric_contradiction_detected(self):
        """Stark abweichende Zahlen mit gleicher Einheit werden erkannt."""
        from agents.evidence_builder import _detect_contradictions
        from models.evidence_models import ContradictionType, EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://a.com", domain="a.com", domain_tier=2),
                excerpt="Die Kosten betragen 100 Millionen Euro.",
                relevance_score=0.8,
                extraction_confidence=0.8,
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://b.com", domain="b.com", domain_tier=3),
                excerpt="Die Kosten betragen 200 Millionen Euro.",
                relevance_score=0.8,
                extraction_confidence=0.8,
            ),
        ]
        contradictions = _detect_contradictions(items)
        assert len(contradictions) >= 1
        assert any(c.contradiction_type == ContradictionType.NUMERIC for c in contradictions)

    def test_severity_high_for_tier1_sources(self):
        """Widersprüche mit Tier-1/2-Quellen erhalten HIGH Severity."""
        from agents.evidence_builder import _detect_contradictions
        from models.evidence_models import ContradictionSeverity, EvidenceItem, EvidenceSource

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://destatis.de/x", domain="destatis.de", domain_tier=1),
                excerpt="Die Quote lag nicht bei 5 Prozent.",
                relevance_score=0.9,
                extraction_confidence=0.9,
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://blog.com/y", domain="blog.com", domain_tier=4),
                excerpt="Die Quote lag bei 5 Prozent.",
                relevance_score=0.8,
                extraction_confidence=0.7,
            ),
        ]
        contradictions = _detect_contradictions(items)
        assert len(contradictions) >= 1
        assert contradictions[0].severity == ContradictionSeverity.HIGH

    def test_direction_contradiction_detected(self):
        """SUPPORTS vs REFUTES wird als Richtungswiderspruch erkannt."""
        from agents.evidence_builder import _detect_contradictions
        from models.evidence_models import ContradictionType, EvidenceItem, EvidenceSource, SourceDirection

        items = [
            EvidenceItem(
                source=EvidenceSource(url="https://a.com", domain="a.com", domain_tier=2),
                excerpt="Die Maßnahme wurde umgesetzt.",
                relevance_score=0.8,
                extraction_confidence=0.8,
                source_direction=SourceDirection.SUPPORTS,
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://b.com", domain="b.com", domain_tier=3),
                excerpt="Die Maßnahme wurde umgesetzt.",
                relevance_score=0.8,
                extraction_confidence=0.8,
                source_direction=SourceDirection.REFUTES,
            ),
        ]
        contradictions = _detect_contradictions(items)
        assert len(contradictions) >= 1
        assert any(c.contradiction_type == ContradictionType.DIRECTION for c in contradictions)


# ── Integration Tests: EvidencePack Format ────────────────────────────────────

class TestEvidencePackFormat:
    def test_format_for_verdict_with_gfc(self, sample_evidence_pack):
        """format_for_verdict() enthält Google Fact Check Treffer."""
        text = sample_evidence_pack.format_for_verdict()
        assert "Professionelle Faktenchecks" in text
        assert "Correctiv" in text

    def test_format_for_verdict_with_web_results(self, sample_evidence_pack):
        """format_for_verdict() enthält strukturierte Quellen."""
        text = sample_evidence_pack.format_for_verdict()
        assert "Evidenz-Quellen" in text
        assert "destatis.de" in text

    def test_format_for_verdict_no_raw_html(self, sample_evidence_pack):
        """format_for_verdict() enthält kein rohes HTML."""
        text = sample_evidence_pack.format_for_verdict()
        assert "<html" not in text.lower()
        assert "<script" not in text.lower()
        assert "<body" not in text.lower()

    def test_excerpt_max_length(self, sample_evidence_pack):
        """Excerpts in EvidenceItems sind auf 800 Zeichen begrenzt."""
        for item in sample_evidence_pack.web_results:
            assert len(item.excerpt) <= 800

    def test_empty_pack_returns_string(self):
        """Leeres EvidencePack gibt trotzdem einen String zurück."""
        from models.evidence_models import EvidencePack
        pack = EvidencePack(claim_id="C1", claim_text="Test")
        result = pack.format_for_verdict()
        assert isinstance(result, str)
        assert len(result) > 0


# ── Unit Tests: Retrieval-Entkopplung ────────────────────────────────────────

class TestRetrievalEntkopplung:
    """Tests für saubere Trennung der Such-Clients (Plugin-Isolation).

    Stellt sicher, dass SearXNG und LangSearch als Kern-Clients unabhängig
    von optionalen Plugins (Tavily etc.) funktionieren.
    """

    def test_tavily_not_double_used(self):
        """SearXNGClient ist explizit SearXNG-only – Tavily-Doppelnutzung strukturell unmöglich."""
        from config import AppConfig, TavilyConfig
        from agents.evidence_builder import EvidenceBuilderAgent
        from tools.web_search import SearXNGClient

        config = AppConfig()
        config.tavily = TavilyConfig(api_key="test-key", enabled=True)
        agent = EvidenceBuilderAgent(config=config)
        # _searxng muss eine SearXNGClient-Instanz sein – kein generischer WebSearchClient
        assert isinstance(agent._searxng, SearXNGClient)
        # SearXNGConfig hat kein Tavily-Attribut (kein search_depth, kein api_key)
        assert not hasattr(agent._searxng.config, "search_depth")

    def test_searxng_active_with_tavily(self):
        """SearXNGClient ist immer aktiv, unabhängig von Tavily-Konfiguration."""
        from config import AppConfig, TavilyConfig
        from agents.evidence_builder import EvidenceBuilderAgent
        from tools.web_search import SearXNGClient

        config = AppConfig()
        config.tavily = TavilyConfig(api_key="test-key", enabled=True)
        agent = EvidenceBuilderAgent(config=config)
        assert isinstance(agent._searxng, SearXNGClient)
        assert agent._searxng.config.base_url  # hat immer eine URL

    def test_no_legacy_async_search_attribute(self):
        """EvidenceBuilderAgent nutzt _searxng statt _async_search für SearXNG."""
        from config import AppConfig
        from agents.evidence_builder import EvidenceBuilderAgent

        agent = EvidenceBuilderAgent(config=AppConfig())
        assert hasattr(agent, "_searxng"), "_searxng muss vorhanden sein"
        assert not hasattr(agent, "_async_search"), "_async_search wurde entfernt"

    def test_langsearch_gets_more_queries_than_tavily(self):
        """LangSearch bekommt immer mindestens so viele Queries wie Tavily."""
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig
        from models.schemas import Claim

        cfg = EvidenceRetrievalConfig()

        # Einfacher Claim
        simple = Claim(id="C1", text="Berlin hat 3,6 Millionen Einwohner.", type="FACTUAL")
        ls_simple = _langsearch_query_count(simple, cfg)
        assert ls_simple >= cfg.tavily_primary_queries
        assert ls_simple == cfg.langsearch_queries_simple  # 3

        # Komplexer Claim
        complex_claim = Claim(
            id="C2",
            text="Die Kriminalitätsrate in Deutschland stieg 2023 statistisch signifikant an.",
            type="STATISTICAL",
        )
        ls_complex = _langsearch_query_count(complex_claim, cfg)
        assert ls_complex >= cfg.tavily_primary_queries
        assert ls_complex == cfg.langsearch_queries_complex  # 5
        assert ls_complex > ls_simple

    def test_fusion_order_langsearch_first(self):
        """Bei Duplikat-URLs bleibt die LangSearch-Version nach Dedup erhalten."""
        from agents.evidence_builder import _dedup_results
        from tools.web_search import SearchResult

        shared_url = "https://example.com/article"
        langsearch_result = SearchResult(
            title="LangSearch-Version", url=shared_url, snippet="Semantisch gefunden"
        )
        searxng_result = SearchResult(
            title="SearXNG-Version", url=shared_url, snippet="Breit gefunden"
        )
        tavily_result = SearchResult(
            title="Tavily-Version", url=shared_url, snippet="Content-stark",
            content="Voller Content",
        )

        # Fusion-Reihenfolge: LangSearch → SearXNG → Tavily
        all_results = [langsearch_result, searxng_result, tavily_result]
        unique = _dedup_results(all_results)

        assert len(unique) == 1
        assert unique[0].title == "LangSearch-Version"

    def test_searxng_max_results_increased(self):
        """SearXNG max_results ist auf 15 erhöht (self-hosted, keine Limits)."""
        from config import SearchConfig

        cfg = SearchConfig()
        assert cfg.max_results == 15

    def test_searxng_concurrent(self):
        """SearXNG max_concurrent_searches ist auf 5 gesetzt (optimierte Latenz)."""
        from config import SearchConfig

        cfg = SearchConfig()
        assert cfg.max_concurrent_searches == 5

    def test_langsearch_queries_increased(self):
        """LangSearch Query-Counts sind erhöht (simple=3, complex=5)."""
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig()
        assert cfg.langsearch_queries_simple == 3
        assert cfg.langsearch_queries_complex == 5


# ── Unit Tests: SearXNG-Architektur-Invarianten ──────────────────────────────

class TestSearXNGClientArchitecture:
    """Tests für die architektonische Trennung via dediziertem SearXNGClient."""

    def test_searxng_client_has_no_provider_routing(self):
        """SearXNGClient hat keine Provider-Routing-Methoden."""
        from tools.web_search import SearXNGClient
        assert not hasattr(SearXNGClient, "_search_tavily")
        assert not hasattr(SearXNGClient, "_search_serper")
        assert not hasattr(SearXNGClient, "_search_brave")

    def test_searxng_config_has_categories_list(self):
        """SearXNGConfig.categories ist eine Liste mit 'general'."""
        from config import SearXNGConfig
        cfg = SearXNGConfig()
        assert isinstance(cfg.categories, list)
        assert "general" in cfg.categories

    def test_searxng_config_engines_is_list(self):
        """SearXNGConfig.engines ist eine Liste (kein String)."""
        from config import SearXNGConfig
        cfg = SearXNGConfig()
        assert isinstance(cfg.engines, list)

    def test_searxng_json_format_in_params(self):
        """SearXNGClient setzt format=json immer zwingend."""
        from config import SearXNGConfig
        from tools.web_search import SearXNGClient
        client = SearXNGClient(config=SearXNGConfig(base_url="http://localhost:8888"))
        params = client._build_params("test", 10)
        assert params["format"] == "json"

    def test_searxng_categories_normalized_from_string(self):
        """SearXNG categories als String wird korrekt in Liste normalisiert."""
        from config import SearXNGConfig
        from tools.web_search import SearXNGClient
        client = SearXNGClient(config=SearXNGConfig(base_url="http://localhost:8888"))
        params = client._build_params("test", 10, categories="news,general")
        assert "news" in params["categories"]
        assert "general" in params["categories"]

    def test_evidence_builder_uses_searxng_client(self):
        """EvidenceBuilderAgent verwendet SearXNGClient für den SearXNG-Layer."""
        from config import AppConfig
        from agents.evidence_builder import EvidenceBuilderAgent
        from tools.web_search import SearXNGClient
        agent = EvidenceBuilderAgent(config=AppConfig())
        assert isinstance(agent._searxng, SearXNGClient)

    def test_searxng_config_max_results(self):
        """SearXNGConfig.max_results ist 15 (self-hosted, keine Limits)."""
        from config import SearXNGConfig
        assert SearXNGConfig().max_results == 15

    def test_searxng_config_concurrent(self):
        """SearXNGConfig.max_concurrent_searches ist 5 (optimierte Latenz)."""
        from config import SearXNGConfig
        assert SearXNGConfig().max_concurrent_searches == 5

    def test_langsearch_gets_more_queries_than_tavily(self):
        """LangSearch-Query-Count übersteigt immer Tavily-Budget."""
        from agents.evidence_builder import _langsearch_query_count
        from config import EvidenceRetrievalConfig
        from models.schemas import Claim
        cfg = EvidenceRetrievalConfig()
        claim = Claim(id="C1", text="Test.", type="FACTUAL")
        assert _langsearch_query_count(claim, cfg) >= cfg.tavily_primary_queries

    def test_fusion_priority_langsearch_over_tavily(self):
        """Dedup-Priorität: LangSearch schlägt Tavily bei gleicher URL."""
        from agents.evidence_builder import _dedup_results
        from tools.web_search import SearchResult
        url = "https://example.com/shared"
        ls = SearchResult(title="LangSearch", url=url, snippet="")
        tv = SearchResult(title="Tavily", url=url, snippet="", content="full")
        unique = _dedup_results([ls, tv])
        assert len(unique) == 1
        assert unique[0].title == "LangSearch"

    def test_stale_sources_reduce_quality(self):
        """Nur alte Quellen → overall_quality wird durch Stale-Penalty gesenkt."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource, EvidenceType

        def _make_item(date: str) -> EvidenceItem:
            src = EvidenceSource(
                url=f"https://example.com/{date}",
                title=f"Quelle {date}",
                domain="example.com",
                domain_tier=2,
                publication_date=date,
                is_fact_check_org=False,
            )
            return EvidenceItem(
                source=src,
                excerpt="",
                relevance_score=0.8,
                evidence_type=EvidenceType.CONTEXTUAL,
                supports_claim=None,
                claim_scope_score=0.5,
            )

        # Nur alte Quellen: Freshness << 0.35 → Penalty greift
        old_items = [_make_item("2020-01-01"), _make_item("2019-06-15")]
        signals_old = _compute_quality_signals(
            old_items, google_matches=[],
            stale_threshold=0.35, stale_penalty_factor=0.15,
        )
        # Frische Quellen (2026): kein Penalty
        fresh_items = [_make_item("2026-03-01"), _make_item("2026-02-15")]
        signals_fresh = _compute_quality_signals(
            fresh_items, google_matches=[],
            stale_threshold=0.35, stale_penalty_factor=0.15,
        )
        assert signals_old.freshness_score < 0.35
        assert signals_fresh.freshness_score >= 0.70
        assert signals_fresh.overall_quality > signals_old.overall_quality


# ── Unit Tests: Confidence-Ceilings (neue kombinierte Checks) ───────────────

class TestConfidenceCeilingsNew:
    """Tests für die neuen/verschärften Confidence-Ceilings."""

    def test_ceiling_contextual_and_low_trust_combined(self):
        """Confidence wird bei contextual + low-trust Kombination auf 0.55 gedeckelt."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_CONTEXTUAL_AND_LOW_TRUST
        from models.evidence_models import (
            EvidenceItem, EvidencePack, EvidenceQualitySignals,
            EvidenceSource, EvidenceType,
        )

        # Erstelle Pack mit hoher contextual_only_rate + low_trust_rate
        items = [
            EvidenceItem(
                source=EvidenceSource(url=f"https://site{i}.de", domain=f"site{i}.de", domain_tier=5),
                excerpt="Allgemeiner Kontext",
                relevance_score=0.3,
                evidence_type=EvidenceType.CONTEXTUAL,
            )
            for i in range(5)
        ]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=items,
            evidence_quality=EvidenceQualitySignals(
                contextual_only_rate=0.8,
                low_trust_rate=0.4,
                direct_evidence_count=0,
                overall_quality=0.5,
            ),
        )

        conf, reasons = _calibrate_confidence(0.90, pack, None)
        assert conf <= _CEILING_CONTEXTUAL_AND_LOW_TRUST
        assert any("Kontext-Evidenz" in r and "Low-Trust" in r for r in reasons)

    def test_ceiling_high_weak_rate(self):
        """Confidence wird bei >60% WEAK-Evidenz auf 0.60 gedeckelt."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_HIGH_WEAK_RATE
        from models.evidence_models import (
            EvidenceItem, EvidencePack, EvidenceQualitySignals,
            EvidenceSource, EvidenceType,
        )

        # 4/5 Items sind WEAK
        items = [
            EvidenceItem(
                source=EvidenceSource(url=f"https://weak{i}.de", domain=f"weak{i}.de", domain_tier=5),
                excerpt="Schwache Quelle",
                relevance_score=0.3,
                evidence_type=EvidenceType.WEAK,
            )
            for i in range(4)
        ] + [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://ok.de", domain="ok.de", domain_tier=3,
                    is_primary_source=True,
                ),
                excerpt="Ordentliche Quelle",
                relevance_score=0.6,
                evidence_type=EvidenceType.CONTEXTUAL,
            )
        ]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=items,
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=True,
                has_fact_check_org_result=True,
                overall_quality=0.5,
                source_consensus="agreeing",
                avg_top5_relevance=0.4,
                top_tier_count=1,
            ),
        )

        conf, reasons = _calibrate_confidence(0.85, pack, None)
        assert conf <= _CEILING_HIGH_WEAK_RATE
        assert any("Weak-Evidence-Rate" in r for r in reasons)

    def test_no_ceiling_with_good_evidence(self):
        """Keine neuen Ceilings greifen bei guter Evidenz."""
        from agents.verdict_agent import _calibrate_confidence
        from models.evidence_models import (
            EvidenceItem, EvidencePack, EvidenceQualitySignals,
            EvidenceSource, EvidenceType,
        )

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/test", domain="destatis.de",
                    domain_tier=1, is_primary_source=True,
                ),
                excerpt="Offizielle Statistik",
                relevance_score=0.9,
                evidence_type=EvidenceType.DIRECT,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://tagesschau.de/test", domain="tagesschau.de",
                    domain_tier=3,
                ),
                excerpt="Bericht",
                relevance_score=0.8,
                evidence_type=EvidenceType.DIRECT,
            ),
        ]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=items,
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=True,
                has_fact_check_org_result=True,
                overall_quality=0.85,
                source_consensus="agreeing",
                contextual_only_rate=0.0,
                low_trust_rate=0.0,
                direct_evidence_count=2,
                top_tier_count=1,
            ),
        )

        conf, reasons = _calibrate_confidence(0.85, pack, None)
        # Keine der neuen Ceilings sollte greifen
        assert not any("Weak-Evidence-Rate" in r for r in reasons)
        assert not any("Kontext-Evidenz" in r and "Low-Trust" in r for r in reasons)


# ── Unit Tests: Spezifitäts-Penalty & Wikipedia-Generik ──────────────────────


class TestSpecificityPenalty:
    """Generische Treffer die nur 1 Profil-Anker matchen werden abgewertet."""

    def _make_profile(self):
        from models.schemas import ClaimSearchProfile
        return ClaimSearchProfile(
            institutions=["Stadtrat von Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "2027", "250"],
            sanction_terms=["250 Euro Bußgeld"],
        )

    def test_generic_wikipedia_stadtrat_low_relevance(self):
        """Wikipedia 'Stadtrat' sollte nach Spezifitäts-Penalty niedrige Relevanz haben."""
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        profile = self._make_profile()
        result = SearchResult(
            title="Stadtrat – Wikipedia",
            url="https://de.wikipedia.org/wiki/Stadtrat",
            snippet="Ein Stadtrat ist ein kommunales Gremium. Die Mitglieder werden gewählt.",
        )
        score = _relevance_score(result, "Der Stadtrat von Hannover hat die 15-Minuten-Stadt beschlossen", profile)
        # Generischer Treffer: Wikipedia-Penalty + wenige Anker → unter neuem Off-topic Threshold 0.30
        assert score < 0.30, f"Generischer Wikipedia-Treffer sollte < 0.30 sein, ist {score:.2f}"

    def test_specific_article_keeps_high_relevance(self):
        """Artikel mit mehreren Profil-Ankern behält hohe Relevanz."""
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        profile = self._make_profile()
        result = SearchResult(
            title="Hannover: Stadtrat diskutiert 15-Minuten-Stadt-Konzept",
            url="https://haz.de/hannover-15-minuten-stadt",
            snippet="Der Stadtrat von Hannover berät über die Umsetzung des 15-Minuten-Stadt-Konzepts ab 2027.",
        )
        score = _relevance_score(result, "Der Stadtrat von Hannover hat die 15-Minuten-Stadt beschlossen", profile)
        assert score > 0.40, f"Spezifischer Artikel sollte > 0.40 sein, ist {score:.2f}"

    def test_count_anchor_hits_all_match(self):
        """Alle Anchor-Gruppen matchen → 5 hits."""
        from agents.evidence_builder import _count_anchor_hits
        profile = self._make_profile()
        text = "Stadtrat von Hannover 15-Minuten-Stadt 250 Euro Bußgeld 100 2027"
        assert _count_anchor_hits(text, profile) == 5

    def test_count_anchor_hits_partial_match(self):
        """Nur Institution 'Stadtrat von Hannover' (exakt) und Location 'Hannover' matchen → 2 hits."""
        from agents.evidence_builder import _count_anchor_hits
        profile = self._make_profile()
        # "Stadtrat von Hannover" muss als Ganzes enthalten sein (substring-match)
        text = "Der Stadtrat von Hannover diskutiert."
        assert _count_anchor_hits(text, profile) == 2  # institution + location

    def test_count_active_anchors(self):
        """Profil mit allen Feldern → 5 aktive Anker."""
        from agents.evidence_builder import _count_active_anchors
        profile = self._make_profile()
        assert _count_active_anchors(profile) == 5


class TestGenericReferenceDetection:
    """Wikipedia-Generik-Erkennung."""

    def test_generic_wikipedia_detected(self):
        """de.wikipedia.org/wiki/Stadtrat ohne Location → generisch."""
        from agents.evidence_builder import _is_generic_reference
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            locations=["Hannover"],
            institutions=["Stadtrat"],
        )
        assert _is_generic_reference("https://de.wikipedia.org/wiki/Stadtrat", profile) is True

    def test_specific_wikipedia_not_detected(self):
        """de.wikipedia.org/wiki/Hannover → enthält Location → nicht generisch."""
        from agents.evidence_builder import _is_generic_reference
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(locations=["Hannover"])
        assert _is_generic_reference("https://de.wikipedia.org/wiki/Hannover", profile) is False

    def test_non_wikipedia_not_detected(self):
        """Nicht-Wikipedia-URL → False."""
        from agents.evidence_builder import _is_generic_reference
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(locations=["Hannover"])
        assert _is_generic_reference("https://tagesschau.de/article", profile) is False

    def test_no_profile_conservative(self):
        """Ohne Profil → konservativ als generisch werten."""
        from agents.evidence_builder import _is_generic_reference
        assert _is_generic_reference("https://de.wikipedia.org/wiki/Stadtrat", None) is True


class TestLowTrustDomains:
    """Neue Low-Trust-Domains werden erkannt."""

    def test_alleantworten_is_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site("https://alleantworten.de/was-ist-der-stadtrat", "", "") is True

    def test_praxistipps_focus_is_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site("https://praxistipps.focus.de/was-verdient-ein-stadtrat", "", "") is True

    def test_generic_explainer_content_pattern(self):
        """'Was verdient ein Stadtrat - Überblick über Gehalt und Aufgaben' → Low-Trust."""
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://example.com/artikel",
            "Was verdient ein Stadtrat - Überblick über Gehalt und Aufgaben",
            "",
        ) is True


# ── Unit Tests: Metadata Preservation in Ranking ──────────────────────────────

class TestMetadataPreservationInRanking:
    """Teste dass Metadaten während des Ranking-Prozesses erhalten bleiben.

    Problem: Früher wurden EvidenceItems zu SearchResults konvertiert und dann
    vollständig neu rekonstruiert, wodurch wichtige Metadaten verloren gingen:
    - publication_date
    - evidence_type
    - claim_scope_score
    - extraction_confidence

    Lösung: _rank_evidence_items() akzeptiert direkt EvidenceItems und behaltet
    alle Metadaten, aktualisiert nur relevance_score und Sortierung.
    """

    def test_publication_date_preserved_after_ranking(self):
        """publication_date sollte nach Ranking erhalten bleiben."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
        )
        from models.schemas import ClaimSearchProfile

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/data1",
                    title="Statistics 2023",
                    domain="destatis.de",
                    domain_tier=1,
                    publication_date="2023-06-15",
                    is_fact_check_org=False,
                ),
                excerpt="Data point 1",
                relevance_score=0.5,
                extraction_confidence=0.8,
                evidence_type=EvidenceType.DIRECT,
                claim_scope_score=0.9,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://example.com/article",
                    title="Article",
                    domain="example.com",
                    domain_tier=4,
                    publication_date="2024-01-10",
                    is_fact_check_org=False,
                ),
                excerpt="Article content",
                relevance_score=0.6,
                extraction_confidence=0.6,
                evidence_type=EvidenceType.CONTEXTUAL,
                claim_scope_score=0.7,
            ),
        ]

        ranked = _rank_evidence_items(
            items,
            "Test claim about statistics",
            [],
        )

        # Beide Items sollten still vorhanden sein
        assert len(ranked) == 2
        # publication_date sollte erhalten bleiben
        assert ranked[0].source.publication_date == "2023-06-15"
        assert ranked[1].source.publication_date == "2024-01-10"

    def test_evidence_type_preserved_after_ranking(self):
        """evidence_type (DIRECT/CONTEXTUAL/WEAK) sollte erhalten bleiben."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
        )

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://official.de/decision",
                    title="Official Decision",
                    domain="official.de",
                    domain_tier=1,
                    is_fact_check_org=False,
                ),
                excerpt="Official text",
                relevance_score=0.8,
                extraction_confidence=0.8,
                evidence_type=EvidenceType.DIRECT,  # DIRECT
                claim_scope_score=1.0,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://blog.com/post",
                    title="Blog Post",
                    domain="blog.com",
                    domain_tier=5,
                    is_fact_check_org=False,
                ),
                excerpt="Blog content",
                relevance_score=0.3,
                extraction_confidence=0.3,
                evidence_type=EvidenceType.WEAK,  # WEAK
                claim_scope_score=0.2,
            ),
        ]

        ranked = _rank_evidence_items(
            items,
            "Test claim",
            [],
        )

        # evidence_type sollte erhalten bleiben
        assert ranked[0].evidence_type == EvidenceType.DIRECT
        assert ranked[1].evidence_type == EvidenceType.WEAK

    def test_claim_scope_score_preserved_after_ranking(self):
        """claim_scope_score sollte erhalten bleiben."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
        )

        original_scope_1 = 0.95
        original_scope_2 = 0.50

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://source1.de",
                    title="Title 1",
                    domain="source1.de",
                    domain_tier=1,
                ),
                excerpt="Content 1",
                relevance_score=0.5,
                extraction_confidence=0.8,
                evidence_type=EvidenceType.DIRECT,
                claim_scope_score=original_scope_1,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://source2.de",
                    title="Title 2",
                    domain="source2.de",
                    domain_tier=3,
                ),
                excerpt="Content 2",
                relevance_score=0.6,
                extraction_confidence=0.6,
                evidence_type=EvidenceType.CONTEXTUAL,
                claim_scope_score=original_scope_2,
            ),
        ]

        ranked = _rank_evidence_items(
            items,
            "Test claim",
            [],
        )

        # claim_scope_score sollte EXAKT erhalten bleiben
        for item in ranked:
            if item.source.url == "https://source1.de":
                assert item.claim_scope_score == original_scope_1
            elif item.source.url == "https://source2.de":
                assert item.claim_scope_score == original_scope_2

    def test_extraction_confidence_preserved_after_ranking(self):
        """extraction_confidence sollte erhalten bleiben (nicht zu 0.5 vereinfacht)."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
        )

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://scraped.de",
                    title="Scraped",
                    domain="scraped.de",
                    domain_tier=1,
                ),
                excerpt="High confidence excerpt",
                relevance_score=0.5,
                extraction_confidence=0.8,  # Scraped source
                evidence_type=EvidenceType.DIRECT,
                claim_scope_score=0.9,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://snippet.de",
                    title="Snippet",
                    domain="snippet.de",
                    domain_tier=4,
                ),
                excerpt="Low confidence snippet",
                relevance_score=0.3,
                extraction_confidence=0.3,  # Snippet source
                evidence_type=EvidenceType.WEAK,
                claim_scope_score=0.2,
            ),
        ]

        ranked = _rank_evidence_items(
            items,
            "Test claim",
            [],
        )

        # extraction_confidence sollte genau erhalten bleiben
        for item in ranked:
            if item.source.url == "https://scraped.de":
                assert item.extraction_confidence == 0.8
            elif item.source.url == "https://snippet.de":
                assert item.extraction_confidence == 0.3

    def test_all_metadata_preserved_together(self):
        """Alle vier Metadaten-Felder sollten zusammen erhalten bleiben."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import (
            EvidenceItem,
            EvidenceSource,
            EvidenceType,
        )

        # Original-Metadaten
        original = {
            "url": "https://example.de/article",
            "publication_date": "2024-03-15",
            "evidence_type": EvidenceType.DIRECT,
            "extraction_confidence": 0.8,
            "claim_scope_score": 0.85,
        }

        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url=original["url"],
                    title="Article Title",
                    domain="example.de",
                    domain_tier=2,
                    publication_date=original["publication_date"],
                ),
                excerpt="Article content here",
                relevance_score=0.6,
                extraction_confidence=original["extraction_confidence"],
                evidence_type=original["evidence_type"],
                claim_scope_score=original["claim_scope_score"],
            ),
        ]

        ranked = _rank_evidence_items(
            items,
            "Test claim text",
            [],
        )

        # Alle Metadaten sollten unverändert sein
        item = ranked[0]
        assert item.source.publication_date == original["publication_date"]
        assert item.evidence_type == original["evidence_type"]
        assert item.extraction_confidence == original["extraction_confidence"]
        assert item.claim_scope_score == original["claim_scope_score"]
        # relevance_score kann sich ändern (durch Ranking-Logik)


# ── Unit Tests: SourceDirection-Klassifikation ────────────────────────────────


class TestSourceDirectionClassification:
    """Tests für _classify_source_direction – rein generische Signale."""

    def _call(self, excerpt="", relevance=0.5, ev_type=None, is_low_trust=False):
        from agents.evidence_builder import _classify_source_direction
        from models.evidence_models import EvidenceType
        if ev_type is None:
            ev_type = EvidenceType.DIRECT
        return _classify_source_direction(excerpt, relevance, ev_type, is_low_trust)

    def test_low_relevance_is_offtopic(self):
        """Unter 0.20 Relevanz → OFFTOPIC, unabhängig vom Inhalt."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="bestätigt und belegt", relevance=0.15)
        assert result == SourceDirection.OFFTOPIC

    def test_low_trust_is_neutral(self):
        """Low-Trust-Quelle → NEUTRAL, auch wenn Text Bestätigung enthält."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="wurde bestätigt", relevance=0.6, is_low_trust=True)
        assert result == SourceDirection.NEUTRAL

    def test_weak_evidence_type_is_neutral(self):
        """WEAK EvidenceType → NEUTRAL, unabhängig vom Textinhalt."""
        from models.evidence_models import EvidenceType, SourceDirection
        result = self._call(
            excerpt="wurde bestätigt und ist korrekt",
            relevance=0.6,
            ev_type=EvidenceType.WEAK,
        )
        assert result == SourceDirection.NEUTRAL

    def test_refutation_pattern_direct_evidence(self):
        """Widerlegungsmuster in DIRECT-Quelle → REFUTES."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="Die Behauptung wurde widerlegt.", relevance=0.7)
        assert result == SourceDirection.REFUTES

    def test_confirmation_pattern_direct_evidence(self):
        """Bestätigungsmuster in DIRECT-Quelle → SUPPORTS."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="Die Angabe wurde bestätigt.", relevance=0.7)
        assert result == SourceDirection.SUPPORTS

    def test_contextual_low_relevance_is_neutral(self):
        """CONTEXTUAL + Relevanz < 0.40 → NEUTRAL, auch mit Bestätigungsmuster."""
        from models.evidence_models import EvidenceType, SourceDirection
        result = self._call(
            excerpt="wurde bestätigt",
            relevance=0.35,
            ev_type=EvidenceType.CONTEXTUAL,
        )
        assert result == SourceDirection.NEUTRAL

    def test_contextual_high_relevance_can_refute(self):
        """CONTEXTUAL + Relevanz ≥ 0.40 + Widerlegungsmuster → REFUTES."""
        from models.evidence_models import EvidenceType, SourceDirection
        result = self._call(
            excerpt="stimmt nicht und ist falsch",
            relevance=0.55,
            ev_type=EvidenceType.CONTEXTUAL,
        )
        assert result == SourceDirection.REFUTES

    def test_neutral_when_no_patterns(self):
        """Kein Muster im Excerpt → NEUTRAL."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="Der Stadtrat tagte am Dienstag.", relevance=0.6)
        assert result == SourceDirection.NEUTRAL

    def test_empty_excerpt_is_neutral(self):
        """Leerer Excerpt → NEUTRAL (kein Textbeweis möglich)."""
        from models.evidence_models import SourceDirection
        result = self._call(excerpt="", relevance=0.6)
        assert result == SourceDirection.NEUTRAL

    def test_conflicting_patterns_refute_wins(self):
        """Mehr Widerlegungs- als Bestätigungsmuster → REFUTES."""
        from models.evidence_models import SourceDirection
        result = self._call(
            excerpt="wurde widerlegt, stimmt nicht, ist unwahr, aber bestätigt",
            relevance=0.7,
        )
        assert result == SourceDirection.REFUTES

    def test_conflicting_patterns_support_wins(self):
        """Mehr Bestätigungs- als Widerlegungsmuster → SUPPORTS."""
        from models.evidence_models import SourceDirection
        result = self._call(
            excerpt="bestätigt, tatsächlich korrekt, nachgewiesen, obwohl nicht belegt",
            relevance=0.7,
        )
        assert result == SourceDirection.SUPPORTS


# ── Unit Tests: Konsens mit source_direction ─────────────────────────────────


class TestSourceDirectionConsensus:
    """Tests für _compute_quality_signals mit source_direction-basiertem Konsens."""

    def _make_item(self, direction, tier=3, ev_type=None, relevance=0.7, url=None):
        from models.evidence_models import EvidenceItem, EvidenceSource, EvidenceType, SourceDirection
        if ev_type is None:
            ev_type = EvidenceType.DIRECT
        url = url or f"https://source-{direction}-{tier}.de/article"
        return EvidenceItem(
            source=EvidenceSource(url=url, domain="example.de", domain_tier=tier),
            excerpt="Inhalt",
            relevance_score=relevance,
            evidence_type=ev_type,
            source_direction=direction,
        )

    def test_all_supports_from_trusted_is_agreeing(self):
        """Mehrere SUPPORTS aus Tier-1/2-Quellen → AGREEING."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.SUPPORTS, tier=1),
            self._make_item(SourceDirection.SUPPORTS, tier=2),
            self._make_item(SourceDirection.SUPPORTS, tier=2),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.AGREEING

    def test_all_refutes_from_trusted_is_contradictory(self):
        """Mehrere REFUTES aus vertrauenswürdigen Quellen → CONTRADICTORY."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.REFUTES, tier=2),
            self._make_item(SourceDirection.REFUTES, tier=3),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.CONTRADICTORY

    def test_mixed_signals_is_mixed(self):
        """Ähnlich gewichtete SUPPORTS und REFUTES → MIXED."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.SUPPORTS, tier=2),
            self._make_item(SourceDirection.REFUTES, tier=2),
            self._make_item(SourceDirection.SUPPORTS, tier=3),
            self._make_item(SourceDirection.REFUTES, tier=3),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.MIXED

    def test_only_neutral_offtopic_is_insufficient(self):
        """Nur NEUTRAL/OFFTOPIC-Quellen → INSUFFICIENT (kein verwertbares Signal)."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.NEUTRAL, tier=3),
            self._make_item(SourceDirection.OFFTOPIC, tier=3),
            self._make_item(SourceDirection.NEUTRAL, tier=2),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.INSUFFICIENT

    def test_low_trust_refutes_do_not_trigger_contradictory(self):
        """REFUTES aus Low-Trust-Quellen (xe.com, duden.de) zählen nicht → INSUFFICIENT."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.REFUTES, tier=5, url="https://xe.com/refute"),
            self._make_item(SourceDirection.REFUTES, tier=5, url="https://duden.de/refute"),
            self._make_item(SourceDirection.NEUTRAL, tier=3),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.INSUFFICIENT

    def test_weak_evidence_refutes_do_not_overpower_trusted_supports(self):
        """WEAK REFUTES überwiegen nicht gegenüber DIRECT SUPPORTS aus Tier-1."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType, SourceConsensus, SourceDirection
        items = [
            self._make_item(SourceDirection.SUPPORTS, tier=1, ev_type=EvidenceType.DIRECT),
            self._make_item(SourceDirection.REFUTES, tier=5, ev_type=EvidenceType.WEAK),
            self._make_item(SourceDirection.REFUTES, tier=5, ev_type=EvidenceType.WEAK),
        ]
        q = _compute_quality_signals(items, [])
        assert q.source_consensus == SourceConsensus.AGREEING

    def test_consensus_clarity_bonus_applied_for_agreeing(self):
        """AGREEING-Konsens mit ausreichendem Signal → höhere overall_quality als ohne Signal."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import SourceDirection
        items_with_signal = [
            self._make_item(SourceDirection.SUPPORTS, tier=1),
            self._make_item(SourceDirection.SUPPORTS, tier=2),
        ]
        items_without_signal = [
            self._make_item(SourceDirection.NEUTRAL, tier=1),
            self._make_item(SourceDirection.NEUTRAL, tier=2),
        ]
        q_signal = _compute_quality_signals(items_with_signal, [])
        q_no_signal = _compute_quality_signals(items_without_signal, [])
        assert q_signal.overall_quality >= q_no_signal.overall_quality


# ── Unit Tests: Granulare Primary-Source- und Faktenchecker-Signale ───────────


class TestGranularQualitySignals:
    """Testet die Unterscheidung zwischen allgemeiner Präsenz und direkter Evidenz."""

    def _make_item(
        self,
        tier: int = 3,
        evidence_type: "EvidenceType" = None,
        claim_scope: float = 0.7,
        is_fact_check_org: bool = False,
    ) -> "EvidenceItem":
        from models.evidence_models import EvidenceItem, EvidenceSource, EvidenceType
        if evidence_type is None:
            evidence_type = EvidenceType.DIRECT
        return EvidenceItem(
            source=EvidenceSource(
                url=f"https://example-tier{tier}.de/page",
                domain=f"example-tier{tier}.de",
                domain_tier=tier,
                is_fact_check_org=is_fact_check_org,
            ),
            excerpt="Relevanter Auszug",
            relevance_score=0.8,
            evidence_type=evidence_type,
            claim_scope_score=claim_scope,
        )

    # ── has_primary_source_any ────────────────────────────────────────────────

    def test_has_primary_source_any_true_if_tier1_present(self):
        """has_primary_source_any=True bei jeder Tier-1-Quelle, unabhängig vom Scope."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=1, evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.1)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_source_any is True

    def test_has_primary_source_any_false_if_no_tier1_2(self):
        """has_primary_source_any=False wenn keine Tier-1/2-Quelle vorhanden."""
        from agents.evidence_builder import _compute_quality_signals

        items = [self._make_item(tier=3), self._make_item(tier=5)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_source_any is False

    # ── has_primary_direct_evidence ───────────────────────────────────────────

    def test_has_primary_direct_evidence_true_if_tier1_direct_high_scope(self):
        """has_primary_direct_evidence=True bei Tier-1 + DIRECT + scope >= 0.50."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=1, evidence_type=EvidenceType.DIRECT, claim_scope=0.65)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_direct_evidence is True

    def test_has_primary_direct_evidence_false_if_tier1_contextual(self):
        """has_primary_direct_evidence=False wenn Tier-1-Quelle nur CONTEXTUAL ist.

        Eine allgemeine Behördenseite ohne direkten Claim-Bezug darf nicht als
        starke direkte Evidenz gelten – auch wenn die Domain vertrauenswürdig ist.
        """
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=1, evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.4)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_direct_evidence is False

    def test_has_primary_direct_evidence_false_if_tier1_direct_low_scope(self):
        """has_primary_direct_evidence=False wenn Tier-1 + DIRECT aber claim_scope < 0.50."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=1, evidence_type=EvidenceType.DIRECT, claim_scope=0.35)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_direct_evidence is False

    def test_has_primary_direct_evidence_false_if_tier3(self):
        """has_primary_direct_evidence=False für Tier-3-Quellen (nur Tier-1/2 relevant)."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=3, evidence_type=EvidenceType.DIRECT, claim_scope=0.9)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_direct_evidence is False

    # ── has_fact_check_any ────────────────────────────────────────────────────

    def test_has_fact_check_any_true_via_gfc(self):
        """has_fact_check_any=True wenn GFC-Match vorhanden."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import GoogleFactCheckMatch

        gfc = [GoogleFactCheckMatch(claim_reviewed="Test", rating="Falsch", publisher="Correctiv")]
        q = _compute_quality_signals([], gfc)
        assert q.has_fact_check_any is True

    def test_has_fact_check_any_true_via_factcheck_org(self):
        """has_fact_check_any=True wenn eine is_fact_check_org-Quelle vorhanden ist."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=4, is_fact_check_org=True, evidence_type=EvidenceType.CONTEXTUAL)]
        q = _compute_quality_signals(items, [])
        assert q.has_fact_check_any is True

    def test_has_fact_check_any_false_if_neither(self):
        """has_fact_check_any=False wenn weder GFC noch Faktenchecker-Org."""
        from agents.evidence_builder import _compute_quality_signals

        items = [self._make_item(tier=3)]
        q = _compute_quality_signals(items, [])
        assert q.has_fact_check_any is False

    # ── has_fact_check_direct_match ───────────────────────────────────────────

    def test_has_fact_check_direct_match_true_via_gfc(self):
        """GFC-Matches zählen immer als direkter Match."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import GoogleFactCheckMatch

        gfc = [GoogleFactCheckMatch(claim_reviewed="Test", rating="Wahr", publisher="Mimikama")]
        q = _compute_quality_signals([], gfc)
        assert q.has_fact_check_direct_match is True

    def test_has_fact_check_direct_match_true_via_factcheck_org_direct(self):
        """has_fact_check_direct_match=True wenn Faktenchecker-Org + evidence_type=DIRECT."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=4, is_fact_check_org=True, evidence_type=EvidenceType.DIRECT, claim_scope=0.8)]
        q = _compute_quality_signals(items, [])
        assert q.has_fact_check_direct_match is True

    def test_has_fact_check_direct_match_false_if_factcheck_org_contextual(self):
        """has_fact_check_direct_match=False wenn Faktenchecker-Org nur CONTEXTUAL.

        Ein allgemeiner Hintergrundartikel einer Faktenchecker-Organisation
        ohne direkten Claim-Bezug darf nicht als direkter Match zählen.
        """
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=4, is_fact_check_org=True, evidence_type=EvidenceType.CONTEXTUAL)]
        q = _compute_quality_signals(items, [])
        assert q.has_fact_check_direct_match is False

    # ── overall_quality: Stufenlogik ─────────────────────────────────────────

    def test_overall_quality_higher_with_primary_direct_than_primary_any(self):
        """Direkte Primärquelle erhöht overall_quality stärker als bloße Präsenz."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items_direct = [self._make_item(tier=1, evidence_type=EvidenceType.DIRECT, claim_scope=0.8)]
        items_context = [self._make_item(tier=1, evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.2)]
        q_direct = _compute_quality_signals(items_direct, [])
        q_context = _compute_quality_signals(items_context, [])
        assert q_direct.overall_quality > q_context.overall_quality

    def test_overall_quality_higher_with_fc_direct_than_fc_any(self):
        """Direkter Faktenchecker-Match erhöht overall_quality stärker als bloße Präsenz."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType, GoogleFactCheckMatch

        gfc = [GoogleFactCheckMatch(claim_reviewed="Test", rating="Falsch", publisher="Correctiv")]
        items_fc_contextual = [self._make_item(tier=4, is_fact_check_org=True, evidence_type=EvidenceType.CONTEXTUAL)]

        q_gfc = _compute_quality_signals([], gfc)
        q_fc_context = _compute_quality_signals(items_fc_contextual, [])
        assert q_gfc.overall_quality > q_fc_context.overall_quality

    # ── Backward-compat Aliase ────────────────────────────────────────────────

    def test_backward_compat_has_primary_sources(self):
        """has_primary_sources ist identisch mit has_primary_source_any."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceType

        items = [self._make_item(tier=1, evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.3)]
        q = _compute_quality_signals(items, [])
        assert q.has_primary_sources == q.has_primary_source_any

    def test_backward_compat_has_fact_check_org_result(self):
        """has_fact_check_org_result ist identisch mit has_fact_check_any."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import GoogleFactCheckMatch

        gfc = [GoogleFactCheckMatch(claim_reviewed="Test", rating="Richtig", publisher="DPA")]
        q = _compute_quality_signals([], gfc)
        assert q.has_fact_check_org_result == q.has_fact_check_any


# ── Unit Tests: _classify_evidence_type Tier-1/2 Threshold ───────────────────


class TestClassifyEvidenceTypeTierThreshold:
    """Tier-1/2-Quellen dürfen nicht automatisch als DIRECT gelten."""

    def test_tier1_with_low_scope_is_contextual_not_direct(self):
        """Tier-1-Quelle mit claim_scope < 0.60 → CONTEXTUAL, nicht DIRECT.

        Vorher: claim_scope >= 0.48 (= 0.60 * 0.80) reichte für DIRECT.
        Jetzt: Tier-1/2 braucht denselben Threshold wie alle anderen (0.60).
        """
        from agents.evidence_builder import _classify_evidence_type
        from models.evidence_models import EvidenceType

        result = _classify_evidence_type(
            item_relevance=0.7,
            claim_scope=0.50,  # Zu niedrig für DIRECT (< 0.60)
            domain_tier=1,
            is_fact_check=False,
            is_low_trust=False,
        )
        assert result == EvidenceType.CONTEXTUAL

    def test_tier1_with_high_scope_is_direct(self):
        """Tier-1-Quelle mit claim_scope >= 0.60 → DIRECT."""
        from agents.evidence_builder import _classify_evidence_type
        from models.evidence_models import EvidenceType

        result = _classify_evidence_type(
            item_relevance=0.7,
            claim_scope=0.65,
            domain_tier=1,
            is_fact_check=False,
            is_low_trust=False,
        )
        assert result == EvidenceType.DIRECT

    def test_tier2_with_scope_below_threshold_is_contextual(self):
        """Tier-2-Quelle (Behörde) mit scope < 0.60 → CONTEXTUAL."""
        from agents.evidence_builder import _classify_evidence_type
        from models.evidence_models import EvidenceType

        result = _classify_evidence_type(
            item_relevance=0.8,
            claim_scope=0.55,
            domain_tier=2,
            is_fact_check=False,
            is_low_trust=False,
        )
        assert result == EvidenceType.CONTEXTUAL

    def test_tier5_with_high_scope_and_relevance_is_direct(self):
        """Tier-5-Quelle mit scope >= 0.60 und relevance >= 0.35 → DIRECT (unverändert)."""
        from agents.evidence_builder import _classify_evidence_type
        from models.evidence_models import EvidenceType

        result = _classify_evidence_type(
            item_relevance=0.60,
            claim_scope=0.70,
            domain_tier=5,
            is_fact_check=False,
            is_low_trust=False,
        )
        assert result == EvidenceType.DIRECT
