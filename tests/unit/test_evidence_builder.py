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
