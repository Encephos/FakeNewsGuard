"""Tests für die regelbasierte Aggregationslogik im SynthesizerAgent.

Prüft:
- _compute_aggregation_signals: korrekte Berechnung aller Signale
- _apply_rating_guardrails: alle drei Guardrail-Regeln
- _format_signals_section: korrekte Ausgabe
- Zusammenspiel: Signale fließen in Rating-Entscheidung ein
"""

from __future__ import annotations

import pytest

from agents.synthesizer import AggregationSignals, SynthesizerAgent
from config import SynthesizerConfig as _SynthCfg

# Referenzwerte aus Config-Defaults für Grenzwert-Tests
_defaults = _SynthCfg()
_FABRICATED_MIN_REFUTED_RATIO = _defaults.fabricated_min_refuted_ratio
_RHETORIC_FLOOR_MISLEADING = _defaults.rhetoric_floor_misleading
_RHETORIC_FLOOR_HIGHLY = _defaults.rhetoric_floor_highly
from models.schemas import (
    FactCheckResult,
    FactRating,
    OverallRating,
    RhetoricAnalysisResult,
    RhetoricTechnique,
    Severity,
)
from models.verdict_models import FinalVerdictMeta


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────


def _make_fc(
    claim_id: str = "C1",
    rating: FactRating = FactRating.FALSE,
    confidence: float = 0.65,
    primary_sources: bool = False,
) -> FactCheckResult:
    return FactCheckResult(
        claim_id=claim_id,
        rating=rating,
        evidence="Test-Evidenz",
        confidence=confidence,
        verdict_meta=FinalVerdictMeta(
            calibrated_confidence=confidence,
            primary_sources_consulted=primary_sources,
        ),
    )


def _make_rhetoric(
    severities: list[Severity],
) -> RhetoricAnalysisResult:
    techniques = [
        RhetoricTechnique(
            technique=f"Technik_{i}",
            example="Beispiel",
            explanation="Erklärung",
            severity=sev,
        )
        for i, sev in enumerate(severities)
    ]
    return RhetoricAnalysisResult(techniques=techniques, overall_framing="Test")


def _make_agent() -> SynthesizerAgent:
    """Erstellt einen SynthesizerAgent ohne echten LLM-Client."""
    from unittest.mock import MagicMock
    from config import AppConfig, LLMConfig
    agent = SynthesizerAgent.__new__(SynthesizerAgent)
    agent.config = AppConfig(llm=LLMConfig())
    agent.llm_client = MagicMock()
    return agent


# ── Tests: _compute_aggregation_signals ───────────────────────────────────────


class TestComputeAggregationSignals:

    def test_no_claims_returns_zero_signals(self):
        agent = _make_agent()
        signals = agent._compute_aggregation_signals([], None)
        assert signals.n_claims == 0
        assert signals.refuted_ratio == 0.0
        assert signals.unverified_ratio == 0.0
        assert signals.rhetoric_score == 0.0
        assert signals.high_quality_evidence is False

    def test_refuted_ratio_counts_false_and_mostly_false(self):
        agent = _make_agent()
        fact_checks = [
            _make_fc("C1", FactRating.FALSE),
            _make_fc("C2", FactRating.MOSTLY_FALSE),
            _make_fc("C3", FactRating.TRUE),
            _make_fc("C4", FactRating.UNVERIFIABLE),
        ]
        signals = agent._compute_aggregation_signals(fact_checks, None)
        assert signals.n_claims == 4
        assert signals.refuted_ratio == pytest.approx(0.5)

    def test_unverified_ratio_counts_only_unverifiable(self):
        agent = _make_agent()
        fact_checks = [
            _make_fc("C1", FactRating.UNVERIFIABLE),
            _make_fc("C2", FactRating.UNVERIFIABLE),
            _make_fc("C3", FactRating.MISLEADING),
        ]
        signals = agent._compute_aggregation_signals(fact_checks, None)
        assert signals.unverified_ratio == pytest.approx(2 / 3)

    def test_avg_claim_confidence_ignores_minus_one(self):
        agent = _make_agent()
        fc_with_conf = _make_fc("C1", confidence=0.60)
        fc_without_conf = FactCheckResult(
            claim_id="C2", rating=FactRating.UNVERIFIABLE, evidence="", confidence=-1.0
        )
        signals = agent._compute_aggregation_signals([fc_with_conf, fc_without_conf], None)
        assert signals.avg_claim_confidence == pytest.approx(0.60)

    def test_high_quality_evidence_when_primary_sources_consulted(self):
        agent = _make_agent()
        fc = _make_fc("C1", FactRating.FALSE, primary_sources=True)
        signals = agent._compute_aggregation_signals([fc], None)
        assert signals.high_quality_evidence is True

    def test_no_high_quality_evidence_without_primary_sources(self):
        agent = _make_agent()
        fc = _make_fc("C1", FactRating.FALSE, primary_sources=False)
        signals = agent._compute_aggregation_signals([fc], None)
        assert signals.high_quality_evidence is False

    def test_rhetoric_score_all_high_severities(self):
        """3 HIGH-Techniken → rhetoric_score = 1.0 (normalisiert gegen 9)."""
        agent = _make_agent()
        rhetoric = _make_rhetoric([Severity.HIGH, Severity.HIGH, Severity.HIGH])
        signals = agent._compute_aggregation_signals([], rhetoric)
        assert signals.rhetoric_score == pytest.approx(1.0)
        assert signals.n_high_rhetoric == 3

    def test_rhetoric_score_mixed_severities(self):
        """1 HIGH (3) + 1 MEDIUM (2) + 1 LOW (1) = 6 / 9 ≈ 0.667."""
        agent = _make_agent()
        rhetoric = _make_rhetoric([Severity.HIGH, Severity.MEDIUM, Severity.LOW])
        signals = agent._compute_aggregation_signals([], rhetoric)
        assert signals.rhetoric_score == pytest.approx(6.0 / 9.0, abs=0.01)
        assert signals.n_high_rhetoric == 1

    def test_rhetoric_score_capped_at_one(self):
        """Mehr als 3 HIGH-Techniken: score bleibt bei 1.0."""
        agent = _make_agent()
        rhetoric = _make_rhetoric([Severity.HIGH] * 5)
        signals = agent._compute_aggregation_signals([], rhetoric)
        assert signals.rhetoric_score == pytest.approx(1.0)

    def test_no_rhetoric_gives_zero_score(self):
        agent = _make_agent()
        signals = agent._compute_aggregation_signals([], None)
        assert signals.rhetoric_score == 0.0
        assert signals.n_high_rhetoric == 0

    def test_image_manipulation_increases_rhetoric_score(self):
        """Bild mit manipulation_signs erhöht rhetoric_score um 1.0 (capped)."""
        from models.schemas import ImageAnalysisItem, ImageAnalysisResult

        agent = _make_agent()
        img = ImageAnalysisResult(items=[
            ImageAnalysisItem(image_index=0, manipulation_signs=["inkonsistente Beleuchtung"]),
        ])
        signals = agent._compute_aggregation_signals([], None, image_analysis=img)
        assert signals.rhetoric_score == pytest.approx(1.0)

    def test_multiple_manipulated_images_capped_at_one(self):
        """Mehrere Bilder mit manipulation_signs: rhetoric_score bleibt ≤ 1.0."""
        from models.schemas import ImageAnalysisItem, ImageAnalysisResult

        agent = _make_agent()
        img = ImageAnalysisResult(items=[
            ImageAnalysisItem(image_index=0, manipulation_signs=["Artefakt A"]),
            ImageAnalysisItem(image_index=1, manipulation_signs=["Artefakt B"]),
            ImageAnalysisItem(image_index=2, manipulation_signs=["Artefakt C"]),
        ])
        signals = agent._compute_aggregation_signals([], None, image_analysis=img)
        assert signals.rhetoric_score == pytest.approx(1.0)

    def test_no_manipulation_signs_no_score_change(self):
        """Bild ohne manipulation_signs ändert rhetoric_score nicht."""
        from models.schemas import ImageAnalysisItem, ImageAnalysisResult

        agent = _make_agent()
        img = ImageAnalysisResult(items=[
            ImageAnalysisItem(image_index=0, manipulation_signs=[]),
        ])
        signals = agent._compute_aggregation_signals([], None, image_analysis=img)
        assert signals.rhetoric_score == pytest.approx(0.0)

    def test_empty_rhetoric_techniques_gives_zero_score(self):
        agent = _make_agent()
        rhetoric = RhetoricAnalysisResult(techniques=[], overall_framing="")
        signals = agent._compute_aggregation_signals([], rhetoric)
        assert signals.rhetoric_score == 0.0


# ── Tests: _apply_rating_guardrails ───────────────────────────────────────────


class TestApplyRatingGuardrails:

    def _signals(self, **kwargs) -> AggregationSignals:
        defaults = dict(
            n_claims=3,
            refuted_ratio=0.0,
            unverified_ratio=0.0,
            avg_claim_confidence=0.6,
            high_quality_evidence=False,
            rhetoric_score=0.0,
            n_high_rhetoric=0,
        )
        defaults.update(kwargs)
        return AggregationSignals(**defaults)

    def test_fabricated_without_evidence_downgraded_to_highly_misleading(self):
        """FABRICATED ohne Primärquellen → HIGHLY_MISLEADING."""
        agent = _make_agent()
        signals = self._signals(
            refuted_ratio=0.8,
            high_quality_evidence=False,
        )
        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_fabricated_without_sufficient_refutation_downgraded(self):
        """FABRICATED mit refuted_ratio < 0.5 → HIGHLY_MISLEADING."""
        agent = _make_agent()
        signals = self._signals(
            refuted_ratio=0.3,
            high_quality_evidence=True,
        )
        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_fabricated_allowed_with_strong_evidence_and_high_refutation(self):
        """FABRICATED bleibt, wenn ≥50 % widerlegt UND Primärquellen vorhanden."""
        agent = _make_agent()
        signals = self._signals(
            refuted_ratio=0.6,
            high_quality_evidence=True,
        )
        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.FABRICATED

    def test_fabricated_boundary_exactly_half_refuted_with_primary_sources(self):
        """Genau 50 % widerlegt + Primärquellen → FABRICATED erlaubt."""
        agent = _make_agent()
        signals = self._signals(
            refuted_ratio=_FABRICATED_MIN_REFUTED_RATIO,
            high_quality_evidence=True,
        )
        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.FABRICATED

    def test_rhetoric_floor_misleading_elevates_mixed(self):
        """MIXED + hohe Rhetorik + viele unbelegt → min MISLEADING."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=_RHETORIC_FLOOR_MISLEADING,
            unverified_ratio=0.5,
            n_claims=4,
        )
        result = agent._apply_rating_guardrails(OverallRating.MIXED, signals)
        assert result == OverallRating.MISLEADING

    def test_rhetoric_floor_misleading_elevates_mostly_reliable(self):
        """MOSTLY_RELIABLE + starke Rhetorik + unbelegt → min MISLEADING."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=0.6,
            unverified_ratio=0.6,
            n_claims=3,
        )
        result = agent._apply_rating_guardrails(OverallRating.MOSTLY_RELIABLE, signals)
        assert result == OverallRating.MISLEADING

    def test_rhetoric_floor_misleading_does_not_downgrade_highly_misleading(self):
        """HIGHLY_MISLEADING bleibt trotz Rule-2-Trigger erhalten."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=0.6,
            unverified_ratio=0.5,
            n_claims=3,
        )
        result = agent._apply_rating_guardrails(OverallRating.HIGHLY_MISLEADING, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_rhetoric_floor_does_not_trigger_below_unverified_threshold(self):
        """Hohe Rhetorik, aber unverified_ratio < 0.4 → kein Elevation."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=0.8,
            unverified_ratio=0.2,
            n_claims=5,
        )
        result = agent._apply_rating_guardrails(OverallRating.MIXED, signals)
        assert result == OverallRating.MIXED

    def test_highly_misleading_floor_elevates_misleading(self):
        """MISLEADING + sehr starke Rhetorik + überwiegend unbelegt → HIGHLY_MISLEADING."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=_RHETORIC_FLOOR_HIGHLY,
            unverified_ratio=0.7,
            refuted_ratio=0.1,
            n_claims=5,
        )
        result = agent._apply_rating_guardrails(OverallRating.MISLEADING, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_highly_misleading_floor_does_not_trigger_with_high_refutation(self):
        """Viele direkt widerlegte Claims → Rule 3 greift nicht (refuted >= 0.3)."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=0.8,
            unverified_ratio=0.5,
            refuted_ratio=0.4,  # >= 0.3 → Rule 3 inaktiv
            n_claims=5,
        )
        result = agent._apply_rating_guardrails(OverallRating.MISLEADING, signals)
        assert result == OverallRating.MISLEADING

    def test_no_claims_no_guardrails_fire(self):
        """n_claims == 0: Keine signalbasierten Regeln 2+3 feuern."""
        agent = _make_agent()
        signals = self._signals(
            n_claims=0,
            rhetoric_score=1.0,
            unverified_ratio=0.0,  # Division by zero vermieden
        )
        result = agent._apply_rating_guardrails(OverallRating.MIXED, signals)
        assert result == OverallRating.MIXED

    def test_reliable_not_affected_without_rhetoric(self):
        """RELIABLE ohne Rhetorik und ohne widerlegte Claims → bleibt RELIABLE."""
        agent = _make_agent()
        signals = self._signals(
            rhetoric_score=0.0,
            unverified_ratio=0.0,
            refuted_ratio=0.0,
            n_claims=2,
        )
        result = agent._apply_rating_guardrails(OverallRating.RELIABLE, signals)
        assert result == OverallRating.RELIABLE

    def test_fabricated_zero_claims_downgraded(self):
        """FABRICATED ohne Claims → immer auf HIGHLY_MISLEADING."""
        agent = _make_agent()
        signals = self._signals(n_claims=0, high_quality_evidence=False, refuted_ratio=0.0)
        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING


# ── Tests: _format_signals_section ────────────────────────────────────────────


class TestFormatSignalsSection:

    def test_no_claims_returns_minimal_output(self):
        signals = AggregationSignals(n_claims=0)
        output = SynthesizerAgent._format_signals_section(signals)
        assert "Keine Claims geprüft" in output

    def test_output_contains_all_key_fields(self):
        signals = AggregationSignals(
            n_claims=4,
            refuted_ratio=0.5,
            unverified_ratio=0.25,
            avg_claim_confidence=0.65,
            high_quality_evidence=True,
            rhetoric_score=0.67,
            n_high_rhetoric=2,
        )
        output = SynthesizerAgent._format_signals_section(signals)
        assert "Claims geprüft: 4" in output
        assert "50%" in output
        assert "25%" in output
        assert "Ja" in output
        assert "0.67" in output
        assert "2 HIGH" in output

    def test_no_primary_sources_shows_nein(self):
        signals = AggregationSignals(n_claims=1, high_quality_evidence=False)
        output = SynthesizerAgent._format_signals_section(signals)
        assert "Nein" in output


# ── Integrationstests: Signale beeinflussen Rating ────────────────────────────


class TestGuardrailIntegration:
    """End-to-End: compute_signals → apply_guardrails zeigt korrekte Kaskade."""

    def test_propaganda_text_with_unverifiable_claims(self):
        """Text mit unspezifischen Claims + starker Rhetorik → HIGHLY_MISLEADING."""
        agent = _make_agent()

        fact_checks = [
            _make_fc("C1", FactRating.UNVERIFIABLE, confidence=0.30),
            _make_fc("C2", FactRating.UNVERIFIABLE, confidence=0.35),
            _make_fc("C3", FactRating.MISLEADING, confidence=0.50),
        ]
        # 3 HIGH-Techniken → rhetoric_score = 1.0
        rhetoric = _make_rhetoric([Severity.HIGH, Severity.HIGH, Severity.HIGH])

        signals = agent._compute_aggregation_signals(fact_checks, rhetoric)
        assert signals.unverified_ratio == pytest.approx(2 / 3)
        assert signals.rhetoric_score == pytest.approx(1.0)

        # LLM gibt MIXED zurück → Guardrails heben auf HIGHLY_MISLEADING an
        result = agent._apply_rating_guardrails(OverallRating.MIXED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_fabricated_requires_real_evidence(self):
        """FABRICATED ohne Primärquellen → HIGHLY_MISLEADING, auch bei vielen FALSE."""
        agent = _make_agent()

        fact_checks = [
            _make_fc("C1", FactRating.FALSE, confidence=0.70, primary_sources=False),
            _make_fc("C2", FactRating.FALSE, confidence=0.65, primary_sources=False),
            _make_fc("C3", FactRating.MOSTLY_FALSE, confidence=0.60, primary_sources=False),
        ]
        signals = agent._compute_aggregation_signals(fact_checks, None)
        assert signals.refuted_ratio == pytest.approx(1.0)
        assert signals.high_quality_evidence is False

        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING

    def test_fabricated_allowed_with_full_evidence(self):
        """FABRICATED bleibt bei ausreichend Evidenz."""
        agent = _make_agent()

        fact_checks = [
            _make_fc("C1", FactRating.FALSE, confidence=0.80, primary_sources=True),
            _make_fc("C2", FactRating.FALSE, confidence=0.75, primary_sources=True),
        ]
        signals = agent._compute_aggregation_signals(fact_checks, None)
        assert signals.refuted_ratio == pytest.approx(1.0)
        assert signals.high_quality_evidence is True

        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.FABRICATED

    def test_mixed_claims_low_rhetoric_stays_mixed(self):
        """Gemischte Claims, schwache Rhetorik → MIXED bleibt."""
        agent = _make_agent()

        fact_checks = [
            _make_fc("C1", FactRating.TRUE, confidence=0.75),
            _make_fc("C2", FactRating.MISLEADING, confidence=0.55),
            _make_fc("C3", FactRating.UNVERIFIABLE, confidence=0.40),
        ]
        rhetoric = _make_rhetoric([Severity.LOW])

        signals = agent._compute_aggregation_signals(fact_checks, rhetoric)
        assert signals.rhetoric_score < _RHETORIC_FLOOR_MISLEADING

        result = agent._apply_rating_guardrails(OverallRating.MIXED, signals)
        assert result == OverallRating.MIXED


# ── Tests: Satire-Ausschluss aus Aggregation ─────────────────────────────────


class TestSatireExclusion:

    def test_satire_claim_excluded_from_refuted_ratio(self):
        """Satire-Claim (is_satire=True) zählt nicht als widerlegt."""
        agent = _make_agent()
        satire_fc = FactCheckResult(
            claim_id="C1",
            rating=FactRating.UNVERIFIABLE,
            evidence="Satire erkannt",
            is_satire=True,
            confidence=0.85,
        )
        false_fc = _make_fc("C2", FactRating.FALSE, confidence=0.90)

        signals = agent._compute_aggregation_signals([satire_fc, false_fc], None)

        # Nur false_fc zählt als widerlegt, von 2 Claims gesamt
        assert signals.n_claims == 2
        assert signals.refuted_ratio == pytest.approx(0.5)

    def test_satire_unverifiable_excluded_from_unverified_ratio(self):
        """Satire-UNVERIFIABLE zählt nicht in unverified_ratio."""
        agent = _make_agent()
        satire_fc = FactCheckResult(
            claim_id="C1",
            rating=FactRating.UNVERIFIABLE,
            evidence="Satire erkannt",
            is_satire=True,
            confidence=0.85,
        )
        normal_fc = _make_fc("C2", FactRating.MISLEADING)

        signals = agent._compute_aggregation_signals([satire_fc, normal_fc], None)

        assert signals.unverified_ratio == pytest.approx(0.0)

    def test_multiple_satire_claims_all_excluded(self):
        """Mehrere Satire-Claims beeinflussen weder refuted_ratio noch unverified_ratio."""
        agent = _make_agent()
        satire_1 = FactCheckResult(
            claim_id="C1", rating=FactRating.UNVERIFIABLE,
            evidence="", is_satire=True, confidence=0.85,
        )
        satire_2 = FactCheckResult(
            claim_id="C2", rating=FactRating.UNVERIFIABLE,
            evidence="", is_satire=True, confidence=0.85,
        )
        real_false = _make_fc("C3", FactRating.FALSE, confidence=0.80)

        signals = agent._compute_aggregation_signals([satire_1, satire_2, real_false], None)

        assert signals.n_claims == 3
        assert signals.refuted_ratio == pytest.approx(1 / 3)
        assert signals.unverified_ratio == pytest.approx(0.0)

    def test_only_satire_claims_gives_zero_ratios(self):
        """Nur Satire-Claims → refuted_ratio und unverified_ratio beide 0.0."""
        agent = _make_agent()
        satire_fc = FactCheckResult(
            claim_id="C1", rating=FactRating.UNVERIFIABLE,
            evidence="", is_satire=True, confidence=0.85,
        )

        signals = agent._compute_aggregation_signals([satire_fc], None)

        assert signals.n_claims == 1
        assert signals.refuted_ratio == pytest.approx(0.0)
        assert signals.unverified_ratio == pytest.approx(0.0)
