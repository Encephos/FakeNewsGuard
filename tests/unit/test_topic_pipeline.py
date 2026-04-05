"""Tests for topic-anchored pipeline features.

Covers TopicExtractor, DependencyDetector, topical centrality,
topic relevance scoring, cross-claim evidence map, and consistency checks.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from models.schemas import (
    ArticleTopicModel,
    ClaimFrame,
    ClaimType,
    ProcessedClaim,
)


# ── Helper factories ────────────────────────────────────────────────────────


def _make_claim(
    id: str,
    text: str = "Test claim",
    claim_type: ClaimType = ClaimType.FACTUAL,
    frame: ClaimFrame | None = None,
) -> ProcessedClaim:
    return ProcessedClaim(
        id=id,
        text=text,
        type=claim_type,
        canonical_text=text,
        frame=frame or ClaimFrame(raw_text=text),
        is_checkworthy=True,
        is_valid_claim=True,
        priority_score=0.8,
        checkworthiness_score=0.8,
    )


def _make_topic_model(**overrides) -> ArticleTopicModel:
    defaults = dict(
        primary_topic="Kommunale Verkehrsregulierung in Hannover",
        key_entities=["Hannover", "Stadtrat", "15-Minuten-Stadt"],
        topic_keywords=["Fahrverbot", "Verkehrszone", "Bußgeld"],
        domain="REGULATORY",
        geographic_scope="Hannover, Niedersachsen",
        temporal_scope="2024-2025",
        narrative_arc="Hannover führt 15-Minuten-Stadt ein.",
    )
    defaults.update(overrides)
    return ArticleTopicModel(**defaults)


# ── TopicExtractor ──────────────────────────────────────────────────────────


class TestTopicExtractor:
    def test_extract_success(self):
        from agents.claim_processor import TopicExtractor

        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "primary_topic": "Klima-Regulierung in Deutschland",
            "key_entities": ["Deutschland", "Bundestag"],
            "topic_keywords": ["CO2", "Emissionen"],
            "domain": "REGULATORY",
            "geographic_scope": "Deutschland",
            "temporal_scope": "2025",
            "narrative_arc": "Deutschland verschärft Klimaziele.",
        }

        extractor = TopicExtractor(mock_llm)
        result = extractor.extract("Langer Artikeltext...")
        assert result is not None
        assert result.primary_topic == "Klima-Regulierung in Deutschland"
        assert "Deutschland" in result.key_entities
        assert result.domain == "REGULATORY"

    def test_extract_invalid_domain_defaults_to_general(self):
        from agents.claim_processor import TopicExtractor

        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = {
            "primary_topic": "Test",
            "domain": "INVALID_DOMAIN",
        }

        extractor = TopicExtractor(mock_llm)
        result = extractor.extract("Text")
        assert result is not None
        assert result.domain == "GENERAL"

    def test_extract_llm_error_returns_none(self):
        from agents.claim_processor import TopicExtractor

        mock_llm = MagicMock()
        mock_llm.complete_json.side_effect = RuntimeError("API error")

        extractor = TopicExtractor(mock_llm)
        assert extractor.extract("Text") is None

    def test_extract_non_dict_returns_none(self):
        from agents.claim_processor import TopicExtractor

        mock_llm = MagicMock()
        mock_llm.complete_json.return_value = "not a dict"

        extractor = TopicExtractor(mock_llm)
        assert extractor.extract("Text") is None


# ── _compute_topical_centrality ─────────────────────────────────────────────


class TestTopicalCentrality:
    def test_central_claim(self):
        from agents.claim_processor import _compute_topical_centrality

        tm = _make_topic_model()
        claims = [_make_claim("c1", "Hannover Stadtrat beschließt Fahrverbot in Verkehrszone")]
        result = _compute_topical_centrality(claims, tm)
        assert result[0].topical_centrality > 0.7

    def test_peripheral_claim(self):
        from agents.claim_processor import _compute_topical_centrality

        tm = _make_topic_model()
        claims = [_make_claim("c1", "Die Inflationsrate in Europa steigt weiter")]
        result = _compute_topical_centrality(claims, tm)
        assert result[0].topical_centrality < 0.3

    def test_empty_signals(self):
        from agents.claim_processor import _compute_topical_centrality

        tm = _make_topic_model(key_entities=[], topic_keywords=[])
        claims = [_make_claim("c1", "Test")]
        result = _compute_topical_centrality(claims, tm)
        # No signals → returns unchanged
        assert result[0].topical_centrality == 0.5  # default


# ── _frames_share_context ──────────────────────────────────────────────────


class TestFramesShareContext:
    def test_shared_institution_and_location(self):
        from agents.claim_processor import _frames_share_context

        a = ClaimFrame(raw_text="a", institution="Stadtrat", location="Hannover")
        b = ClaimFrame(raw_text="b", institution="Stadtrat Hannover", location="Hannover")
        assert _frames_share_context(a, b) is True

    def test_no_overlap(self):
        from agents.claim_processor import _frames_share_context

        a = ClaimFrame(raw_text="a", institution="EZB", location="Frankfurt")
        b = ClaimFrame(raw_text="b", institution="WHO", location="Genf")
        assert _frames_share_context(a, b) is False

    def test_none_frames(self):
        from agents.claim_processor import _frames_share_context

        assert _frames_share_context(None, None) is False
        a = ClaimFrame(raw_text="a", institution="X")
        assert _frames_share_context(a, None) is False


# ── DependencyDetector ──────────────────────────────────────────────────────


class TestDependencyDetector:
    def test_policy_sanction_dependency(self):
        from agents.claim_processor import DependencyDetector

        policy = _make_claim(
            "p1", "Hannover führt Fahrverbot ein",
            frame=ClaimFrame(
                raw_text="Fahrverbot",
                institution="Stadtrat",
                location="Hannover",
                policy_context="Fahrverbot in Verkehrszone",
            ),
        )
        sanction = _make_claim(
            "s1", "250 EUR Bußgeld bei Verstoß",
            frame=ClaimFrame(
                raw_text="Bußgeld",
                institution="Stadtrat",
                location="Hannover",
                sanction="250 EUR Bußgeld",
            ),
        )
        result = DependencyDetector.detect([policy, sanction])
        sanction_result = next(c for c in result if c.id == "s1")
        assert "p1" in sanction_result.depends_on
        assert sanction_result.dependency_type == "policy_sanction"

    def test_no_dependencies_for_single_claim(self):
        from agents.claim_processor import DependencyDetector

        claims = [_make_claim("c1")]
        result = DependencyDetector.detect(claims)
        assert len(result) == 1
        assert result[0].depends_on == []

    def test_no_false_dependencies(self):
        from agents.claim_processor import DependencyDetector

        a = _make_claim(
            "a", "EZB senkt Leitzins",
            frame=ClaimFrame(raw_text="a", institution="EZB", location="Frankfurt"),
        )
        b = _make_claim(
            "b", "WHO warnt vor Pandemie",
            frame=ClaimFrame(raw_text="b", institution="WHO", location="Genf"),
        )
        result = DependencyDetector.detect([a, b])
        assert all(c.depends_on == [] for c in result)


# ── _compute_topic_relevance ───────────────────────────────────────────────


class TestComputeTopicRelevance:
    def test_high_relevance(self):
        from agents.evidence_scoring import _compute_topic_relevance

        tm = _make_topic_model()
        score = _compute_topic_relevance(
            "Hannover Stadtrat beschließt Verkehrszone",
            "Fahrverbot und Bußgeld für Verstöße",
            tm,
        )
        assert score > 0.7

    def test_low_relevance(self):
        from agents.evidence_scoring import _compute_topic_relevance

        tm = _make_topic_model()
        score = _compute_topic_relevance(
            "Bitcoin price analysis",
            "Cryptocurrency markets continue volatile",
            tm,
        )
        assert score < 0.2

    def test_no_topic_model(self):
        from agents.evidence_scoring import _compute_topic_relevance

        assert _compute_topic_relevance("any", "text", None) == 1.0

    def test_empty_text(self):
        from agents.evidence_scoring import _compute_topic_relevance

        tm = _make_topic_model()
        assert _compute_topic_relevance("", "", tm) == 0.0


# ── Cross-Claim Evidence Map ───────────────────────────────────────────────


class TestCrossClaimEvidenceMap:
    def test_shared_url_detected(self):
        from orchestrator import _build_cross_claim_evidence_map
        from models.evidence_models import EvidenceItem, EvidencePack, EvidenceSource, SourceDirection
        from models.schemas import FactCheckResult, FactRating

        def _make_evidence(url, direction="supports"):
            return EvidenceItem(
                source=EvidenceSource(url=url, title="T", domain_tier=1),
                excerpt="text",
                source_direction=SourceDirection(direction),
            )

        fc1 = FactCheckResult(
            claim_id="c1",
            rating=FactRating.TRUE,
            evidence="some evidence",
            confidence=0.9,
            evidence_pack=EvidencePack(
                claim_id="c1", claim_text="claim 1",
                web_results=[
                    _make_evidence("https://example.com/shared"),
                    _make_evidence("https://other.com/unique1"),
                ],
            ),
        )
        fc2 = FactCheckResult(
            claim_id="c2",
            rating=FactRating.FALSE,
            evidence="some evidence",
            confidence=0.8,
            evidence_pack=EvidencePack(
                claim_id="c2", claim_text="claim 2",
                web_results=[
                    _make_evidence("https://example.com/shared", "refutes"),
                    _make_evidence("https://other.com/unique2"),
                ],
            ),
        )

        result = _build_cross_claim_evidence_map([fc1, fc2])
        assert "https://example.com/shared" in result
        assert len(result["https://example.com/shared"]) == 2
        # Unique URLs should not appear
        assert "https://other.com/unique1" not in result

    def test_empty_fact_checks(self):
        from orchestrator import _build_cross_claim_evidence_map

        assert _build_cross_claim_evidence_map([]) == {}


# ── Cross-Claim Consistency ────────────────────────────────────────────────


class TestCrossClaimConsistency:
    def test_contradiction_detected(self):
        from orchestrator import _apply_cross_claim_consistency
        from models.schemas import FactCheckResult, FactRating

        parent = _make_claim(
            "p1", "Policy exists",
            frame=ClaimFrame(raw_text="p", institution="X", location="Y"),
        )
        child = _make_claim(
            "ch1", "Sanction for policy",
            frame=ClaimFrame(raw_text="c", institution="X", location="Y"),
        )
        child = child.model_copy(update={
            "depends_on": ["p1"],
            "dependency_type": "policy_sanction",
        })

        fc_parent = FactCheckResult(
            claim_id="p1", rating=FactRating.FALSE, confidence=0.8,
            evidence="false",
        )
        fc_child = FactCheckResult(
            claim_id="ch1", rating=FactRating.TRUE, confidence=0.9,
            evidence="true",
        )

        updated, warnings = _apply_cross_claim_consistency(
            [fc_parent, fc_child], [parent, child]
        )
        assert len(warnings) == 1
        assert "Widerspruch" in warnings[0]
        # Child confidence should be reduced
        child_fc = next(fc for fc in updated if fc.claim_id == "ch1")
        assert child_fc.confidence < 0.9

    def test_no_contradiction(self):
        from orchestrator import _apply_cross_claim_consistency
        from models.schemas import FactCheckResult, FactRating

        parent = _make_claim("p1", "Policy exists")
        child = _make_claim("ch1", "Sanction")
        child = child.model_copy(update={"depends_on": ["p1"], "dependency_type": "policy_sanction"})

        fc_parent = FactCheckResult(
            claim_id="p1", rating=FactRating.TRUE, confidence=0.9,
            evidence="true",
        )
        fc_child = FactCheckResult(
            claim_id="ch1", rating=FactRating.TRUE, confidence=0.9,
            evidence="true",
        )

        updated, warnings = _apply_cross_claim_consistency(
            [fc_parent, fc_child], [parent, child]
        )
        assert len(warnings) == 0
        # No penalty
        child_fc = next(fc for fc in updated if fc.claim_id == "ch1")
        assert child_fc.confidence == 0.9
