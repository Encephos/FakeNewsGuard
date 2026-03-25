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

    def test_max_three_contradictions(self):
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
        assert len(contradictions) <= 3  # Limit enforced


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
    """Tests für saubere Trennung von Tavily, SearXNG und LangSearch."""

    def test_tavily_not_double_used(self):
        """Wenn TavilyClient aktiv ist, darf AsyncWebSearchClient nicht auch Tavily nutzen."""
        import warnings
        from config import AppConfig, SearchConfig, TavilyConfig

        config = AppConfig()
        config.tavily = TavilyConfig(api_key="test-key", enabled=True)
        config.search = SearchConfig(provider="tavily", api_key="test-key")

        from agents.evidence_builder import EvidenceBuilderAgent

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            agent = EvidenceBuilderAgent(config=config)
            # Provider muss auf searxng umgestellt worden sein
            assert agent._async_search.config.provider == "searxng"
            # Warning muss ausgelöst worden sein
            assert len(w) >= 1
            assert "Doppelnutzung" in str(w[0].message)

    def test_searxng_active_with_tavily(self):
        """SearXNG bleibt aktiv auch wenn Tavily als eigener Layer aktiviert ist."""
        from config import AppConfig, TavilyConfig

        config = AppConfig()
        config.tavily = TavilyConfig(api_key="test-key", enabled=True)
        # search.provider default ist "searxng" → kein Konflikt
        from agents.evidence_builder import EvidenceBuilderAgent

        agent = EvidenceBuilderAgent(config=config)
        assert agent._async_search.config.provider == "searxng"

    def test_no_warning_when_provider_is_searxng(self):
        """Kein Warning wenn search.provider bereits searxng ist."""
        import warnings
        from config import AppConfig, TavilyConfig

        config = AppConfig()
        config.tavily = TavilyConfig(api_key="test-key", enabled=True)

        from agents.evidence_builder import EvidenceBuilderAgent

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            agent = EvidenceBuilderAgent(config=config)
            tavily_warnings = [x for x in w if "Doppelnutzung" in str(x.message)]
            assert len(tavily_warnings) == 0

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

    def test_searxng_concurrent_increased(self):
        """SearXNG max_concurrent_searches ist auf 8 erhöht."""
        from config import SearchConfig

        cfg = SearchConfig()
        assert cfg.max_concurrent_searches == 8

    def test_langsearch_queries_increased(self):
        """LangSearch Query-Counts sind erhöht (simple=3, complex=5)."""
        from config import EvidenceRetrievalConfig

        cfg = EvidenceRetrievalConfig()
        assert cfg.langsearch_queries_simple == 3
        assert cfg.langsearch_queries_complex == 5


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
