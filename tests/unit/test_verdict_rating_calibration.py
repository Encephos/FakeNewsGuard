"""Tests für den regelbasierten Rating-Postprocessor (_calibrate_rating).

Testet die Kerntrennung:
    - Fehlendes Beweis              → UNVERIFIABLE  (nicht FALSE)
    - Kontextquellen ohne Widerlegung → MISLEADING  (nicht FALSE)
    - Aktive direkte Widerlegung    → FALSE bleibt FALSE
    - Schwache Widerlegungssignale  → MOSTLY_FALSE bleibt MOSTLY_FALSE
    - Kein Signal bei MOSTLY_FALSE  → UNVERIFIABLE
    - Rhetorische Sprache im Claim  → ändert Rating nicht
    - Konfigurierbarkeit via VerdictRatingCalibrationConfig
    - has_direct_refutation Signal in _compute_quality_signals
"""

from __future__ import annotations

import pytest

from agents.verdict_agent import (
    VerdictRatingCalibrationConfig,
    _calibrate_rating,
)
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    EvidenceType,
    GoogleFactCheckMatch,
    SourceConsensus,
    SourceDirection,
)
from models.schemas import FactRating


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _make_item(
    url: str = "https://example.com",
    tier: int = 3,
    direction: SourceDirection = SourceDirection.NEUTRAL,
    ev_type: EvidenceType = EvidenceType.CONTEXTUAL,
    relevance: float = 0.7,
    scope: float = 0.5,
) -> EvidenceItem:
    return EvidenceItem(
        source=EvidenceSource(url=url, title="Test", domain="example.com", domain_tier=tier),
        excerpt="Test excerpt",
        relevance_score=relevance,
        source_direction=direction,
        evidence_type=ev_type,
        claim_scope_score=scope,
    )


def _make_pack(
    items: list[EvidenceItem] | None = None,
    consensus: SourceConsensus = SourceConsensus.INSUFFICIENT,
    has_direct_refutation: bool = False,
    direct_refutation_count: int = 0,
    has_fc_direct: bool = False,
    has_fc_any: bool = False,
    direct_count: int = 0,
    contextual_only_rate: float = 1.0,
    gfc_matches: list[GoogleFactCheckMatch] | None = None,
) -> EvidencePack:
    return EvidencePack(
        claim_id="C1",
        claim_text="Test-Claim",
        web_results=items or [],
        google_fact_check_matches=gfc_matches or [],
        evidence_quality=EvidenceQualitySignals(
            source_consensus=consensus,
            has_direct_refutation=has_direct_refutation,
            direct_refutation_count=direct_refutation_count,
            has_fact_check_direct_match=has_fc_direct,
            has_fact_check_any=has_fc_any,
            direct_evidence_count=direct_count,
            contextual_only_rate=contextual_only_rate,
            overall_quality=0.5,
            freshness_score=0.8,
        ),
    )


# ── FALSE-Kalibrierung ────────────────────────────────────────────────────────


class TestFalseCalibration:
    def test_false_without_active_refutation_insufficient_consensus_becomes_unverifiable(self):
        """FALSE ohne Widerlegungssignal + INSUFFICIENT Konsens → UNVERIFIABLE."""
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert reasons, "Kalibrierungsgrund muss angegeben werden"
        assert any("UNVERIFIABLE" in r for r in reasons)

    def test_false_without_active_refutation_mixed_consensus_becomes_misleading(self):
        """FALSE ohne Widerlegungssignal + MIXED Konsens → MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("MISLEADING" in r for r in reasons)

    def test_false_without_active_refutation_agreeing_consensus_becomes_misleading(self):
        """FALSE ohne Widerlegungssignal + AGREEING Konsens (Quellen stützen Claim!) → MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING

    def test_false_with_direct_refutation_stays_false(self):
        """FALSE mit DIRECT+REFUTES Quelle → bleibt FALSE."""
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=True,
            direct_refutation_count=1,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE
        assert not reasons, f"Kein Grund erwartet, aber: {reasons}"

    def test_false_with_contradictory_consensus_stays_false(self):
        """FALSE mit CONTRADICTORY source_consensus + direkter Evidenz → bleibt FALSE.

        CONTRADICTORY Konsens basiert auf gewichteten REFUTES-Signalen; mit mind.
        einer DIRECT-Quelle (direct_count=1) ist FALSE gerechtfertigt.
        """
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=False,
            direct_count=1,           # konsistent: CONTRADICTORY → min. eine direkte Quelle
            contextual_only_rate=0.0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE

    def test_false_with_fc_direct_match_stays_false(self):
        """FALSE mit direktem Faktenchecker-Match → bleibt FALSE."""
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_direct=True,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE

    def test_contextual_only_false_becomes_misleading(self):
        """FALSE mit ausschließlich CONTEXTUAL-Quellen (kein DIRECT, kein FC) → MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,  # gewichteter Konsens widerlegend
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("MISLEADING" in r for r in reasons)

    def test_contextual_only_false_with_fc_direct_stays_false(self):
        """Contextual-only-Cap wird durch FC-Direct-Match aufgehoben."""
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=False,
            has_fc_direct=True,  # FC überschreibt den Cap
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, _ = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE


# ── MOSTLY_FALSE-Kalibrierung ────────────────────────────────────────────────


class TestMostlyFalseCalibration:
    def test_mostly_false_without_any_refutation_signal_becomes_unverifiable(self):
        """MOSTLY_FALSE ohne jegliches Widerlegungssignal → UNVERIFIABLE."""
        pack = _make_pack(
            items=[],
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert reasons

    def test_mostly_false_with_contextual_refutes_stays(self):
        """MOSTLY_FALSE mit CONTEXTUAL+REFUTES Quelle → bleibt MOSTLY_FALSE."""
        items = [_make_item(direction=SourceDirection.REFUTES, ev_type=EvidenceType.CONTEXTUAL)]
        pack = _make_pack(
            items=items,
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE

    def test_mostly_false_with_mixed_consensus_stays(self):
        """MOSTLY_FALSE mit MIXED Konsens (schwache Widerlegungssignale) → bleibt MOSTLY_FALSE."""
        pack = _make_pack(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
        )
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE

    def test_mostly_false_with_fc_any_stays(self):
        """MOSTLY_FALSE mit beliebigem FC-Ergebnis → bleibt MOSTLY_FALSE."""
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_fc_any=True,
        )
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE

    def test_mostly_false_with_contradictory_consensus_stays(self):
        """MOSTLY_FALSE mit CONTRADICTORY Konsens → bleibt MOSTLY_FALSE."""
        pack = _make_pack(consensus=SourceConsensus.CONTRADICTORY)
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE


# ── Andere Ratings unberührt ──────────────────────────────────────────────────


class TestOtherRatingsUntouched:
    @pytest.mark.parametrize("input_rating", [
        FactRating.TRUE,
        FactRating.MOSTLY_TRUE,
        FactRating.MISLEADING,
        FactRating.UNVERIFIABLE,
    ])
    def test_non_negative_ratings_not_changed(self, input_rating: FactRating):
        """TRUE, MOSTLY_TRUE, MISLEADING, UNVERIFIABLE werden nicht geändert."""
        pack = _make_pack(consensus=SourceConsensus.INSUFFICIENT)
        rating, reasons = _calibrate_rating(input_rating, pack)
        assert rating == input_rating
        assert not reasons


# ── Keine Qualitätssignale (Fallback) ─────────────────────────────────────────


class TestNoQualitySignals:
    def test_false_without_quality_signals_unchanged(self):
        """Ohne evidence_quality kein Eingriff (keine Signale → kein Urteil möglich)."""
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=[],
            evidence_quality=None,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE
        assert not reasons


# ── Konfigurierbarkeit ────────────────────────────────────────────────────────


class TestRatingCalibrationConfig:
    def test_config_disable_false_calibration(self):
        """Mit false_requires_active_refutation=False wird FALSE nicht korrigiert.

        Da auch der Kontextueller Cap deaktiviert wird, müssen beide Flags False sein,
        damit FALSE ohne jegliche Evidenz bestehen bleibt.
        """
        config = VerdictRatingCalibrationConfig(
            false_requires_active_refutation=False,
            contextual_only_caps_false_at_misleading=False,
        )
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack, config=config)
        assert rating == FactRating.FALSE
        assert not reasons

    def test_config_disable_mostly_false_calibration(self):
        """Mit mostly_false_requires_refutation_signal=False wird MOSTLY_FALSE nicht korrigiert."""
        config = VerdictRatingCalibrationConfig(mostly_false_requires_refutation_signal=False)
        pack = _make_pack(
            items=[],
            consensus=SourceConsensus.INSUFFICIENT,
            has_fc_any=False,
        )
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack, config=config)
        assert rating == FactRating.MOSTLY_FALSE

    def test_config_disable_contextual_cap(self):
        """Mit contextual_only_caps_false_at_misleading=False greift der Kontext-Cap nicht."""
        config = VerdictRatingCalibrationConfig(contextual_only_caps_false_at_misleading=False)
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, _ = _calibrate_rating(FactRating.FALSE, pack, config=config)
        # Ohne Contextual-Cap: CONTRADICTORY Konsens reicht für FALSE
        assert rating == FactRating.FALSE

    def test_config_custom_downgrade_target(self):
        """Downgrade-Ziel kann konfiguriert werden."""
        config = VerdictRatingCalibrationConfig(
            false_no_refutation_downgrade_insufficient="MISLEADING",
        )
        pack = _make_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
        )
        rating, _ = _calibrate_rating(FactRating.FALSE, pack, config=config)
        assert rating == FactRating.MISLEADING


# ── has_direct_refutation Signal-Berechnung ───────────────────────────────────


class TestHasDirectRefutationSignal:
    def test_direct_refutes_item_sets_signal(self):
        """DIRECT+REFUTES Item → has_direct_refutation=True."""
        from agents.evidence_builder import _compute_quality_signals

        items = [_make_item(direction=SourceDirection.REFUTES, ev_type=EvidenceType.DIRECT)]
        signals = _compute_quality_signals(items, [])
        assert signals.has_direct_refutation is True
        assert signals.direct_refutation_count == 1

    def test_contextual_refutes_does_not_set_signal(self):
        """CONTEXTUAL+REFUTES → has_direct_refutation bleibt False."""
        from agents.evidence_builder import _compute_quality_signals

        items = [_make_item(direction=SourceDirection.REFUTES, ev_type=EvidenceType.CONTEXTUAL)]
        signals = _compute_quality_signals(items, [])
        assert signals.has_direct_refutation is False
        assert signals.direct_refutation_count == 0

    def test_direct_supports_does_not_set_refutation_signal(self):
        """DIRECT+SUPPORTS → has_direct_refutation bleibt False."""
        from agents.evidence_builder import _compute_quality_signals

        items = [_make_item(direction=SourceDirection.SUPPORTS, ev_type=EvidenceType.DIRECT)]
        signals = _compute_quality_signals(items, [])
        assert signals.has_direct_refutation is False

    def test_multiple_direct_refutes_counted(self):
        """Mehrere DIRECT+REFUTES Items werden korrekt gezählt."""
        from agents.evidence_builder import _compute_quality_signals

        items = [
            _make_item(url=f"https://example.com/{i}",
                       direction=SourceDirection.REFUTES,
                       ev_type=EvidenceType.DIRECT)
            for i in range(3)
        ]
        signals = _compute_quality_signals(items, [])
        assert signals.has_direct_refutation is True
        assert signals.direct_refutation_count == 3

    def test_signal_only_from_top5(self):
        """has_direct_refutation berücksichtigt nur Top-5."""
        from agents.evidence_builder import _compute_quality_signals

        # 5 CONTEXTUAL+NEUTRAL Items, dann 1 DIRECT+REFUTES an Position 6
        items = [
            _make_item(url=f"https://neutral.com/{i}",
                       direction=SourceDirection.NEUTRAL,
                       ev_type=EvidenceType.CONTEXTUAL)
            for i in range(5)
        ]
        items.append(_make_item(
            url="https://refutes.com/6",
            direction=SourceDirection.REFUTES,
            ev_type=EvidenceType.DIRECT,
        ))
        signals = _compute_quality_signals(items, [])
        # Position 6 ist außerhalb Top-5
        assert signals.has_direct_refutation is False
        assert signals.direct_refutation_count == 0

    def test_empty_items_returns_false(self):
        """Leere Liste → has_direct_refutation=False."""
        from agents.evidence_builder import _compute_quality_signals

        signals = _compute_quality_signals([], [])
        assert signals.has_direct_refutation is False
        assert signals.direct_refutation_count == 0


# ── Realistisches Integrationsszenario ───────────────────────────────────────


class TestRealisticScenarios:
    def test_regulatory_claim_no_evidence_not_false(self):
        """Regulierungs-Claim ohne Evidenz → UNVERIFIABLE, nicht FALSE.

        Szenario: Claim behauptet ein spezifisches Bußgeld.
        Gefundene Quellen: nur allgemeine Kontext-Seiten, kein direkter Widerruf.
        Erwartung: LLM sagt FALSE (früher Bug), Kalibrierung korrigiert auf UNVERIFIABLE.
        """
        items = [
            _make_item(
                url="https://stadtinfo.de/15min",
                direction=SourceDirection.NEUTRAL,
                ev_type=EvidenceType.CONTEXTUAL,
            ),
            _make_item(
                url="https://wiki.de/15-minuten-stadt",
                direction=SourceDirection.NEUTRAL,
                ev_type=EvidenceType.CONTEXTUAL,
            ),
        ]
        pack = _make_pack(
            items=items,
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        # LLM liefert fälschlicherweise FALSE (alter Bug)
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert reasons

    def test_claim_directly_refuted_by_official_source_stays_false(self):
        """Claim durch offizielle Quelle direkt widerlegt → FALSE bleibt.

        Szenario: Faktenchecker-Organisation mit DIRECT-Klassifikation widerlegt Claim.
        """
        items = [
            _make_item(
                url="https://correctiv.org/faktencheck/claim-xyz",
                tier=4,
                direction=SourceDirection.REFUTES,
                ev_type=EvidenceType.DIRECT,
                scope=0.9,
            ),
        ]
        pack = _make_pack(
            items=items,
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=True,
            direct_refutation_count=1,
            has_fc_direct=False,
            direct_count=1,
            contextual_only_rate=0.0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE
        assert not reasons

    def test_misleading_claim_with_contextual_sources_stays_misleading(self):
        """MISLEADING + nur Kontext-Quellen → unberührt, kein Upgrade zu FALSE."""
        pack = _make_pack(
            consensus=SourceConsensus.MIXED,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, reasons = _calibrate_rating(FactRating.MISLEADING, pack)
        assert rating == FactRating.MISLEADING
        assert not reasons

    def test_mostly_false_with_low_trust_sources_only_no_signal_unverifiable(self):
        """MOSTLY_FALSE wenn Quellen nur Low-Trust + kein Widerlegungssignal → UNVERIFIABLE.

        Szenario: Alle gefundenen Quellen sind Bußgeldrechner, Währungskonverter etc.
        Kein echter Widerlegungssignal → MOSTLY_FALSE nicht gerechtfertigt.
        """
        pack = _make_pack(
            items=[],  # keine Web-Items → kein Richtungssignal
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE


# ── Konsens-Widerspruch-Korrektur ────────────────────────────────────────────


class TestConsensusContradictionOverride:
    def test_agreeing_consensus_false_rating_downgraded(self):
        """AGREEING Konsens + FALSE Rating → MISLEADING (LLM ignoriert Evidenz)."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("Konsens-Widerspruch" in r for r in reasons)

    def test_agreeing_consensus_false_with_fc_direct_stays(self):
        """AGREEING + FALSE + Faktenchecker-Match → FALSE bleibt (FC hat Vorrang)."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=True,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE

    def test_contradictory_consensus_true_rating_downgraded(self):
        """CONTRADICTORY Konsens + TRUE Rating → MISLEADING (inverse Korrektur)."""
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=True,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.TRUE, pack)
        assert rating == FactRating.MISLEADING
        assert any("Inverser Konsens-Widerspruch" in r for r in reasons)

    def test_contradictory_consensus_true_with_fc_stays(self):
        """CONTRADICTORY + TRUE + Faktenchecker-Match → TRUE bleibt (FC bestätigt)."""
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=True,
            has_fc_direct=True,
        )
        rating, reasons = _calibrate_rating(FactRating.TRUE, pack)
        assert rating == FactRating.TRUE

    def test_contradictory_consensus_mostly_true_downgraded(self):
        """CONTRADICTORY Konsens + MOSTLY_TRUE → MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=True,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_TRUE, pack)
        assert rating == FactRating.MISLEADING

    def test_mixed_consensus_not_overridden(self):
        """MIXED Konsens löst keinen Override aus (nur AGREEING/CONTRADICTORY)."""
        pack = _make_pack(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        # TRUE bei MIXED bleibt TRUE (kein Override)
        rating, reasons = _calibrate_rating(FactRating.TRUE, pack)
        assert rating == FactRating.TRUE


# ── Positive Promotion ───────────────────────────────────────────────────────


class TestPositivePromotion:
    """Tests for the new positive promotion rules that correct negative bias."""

    def test_misleading_agreeing_direct_evidence_promoted_to_mostly_true(self):
        """MISLEADING + AGREEING consensus + direct evidence → MOSTLY_TRUE."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=2,
        )
        rating, reasons = _calibrate_rating(FactRating.MISLEADING, pack)
        assert rating == FactRating.MOSTLY_TRUE
        assert any("MOSTLY_TRUE" in r for r in reasons)

    def test_misleading_agreeing_no_direct_evidence_stays_misleading(self):
        """MISLEADING + AGREEING but no direct evidence → stays MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,
        )
        rating, reasons = _calibrate_rating(FactRating.MISLEADING, pack)
        assert rating == FactRating.MISLEADING

    def test_misleading_agreeing_with_refutation_stays_misleading(self):
        """MISLEADING + AGREEING but has refutation → stays MISLEADING."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=True,
            has_fc_direct=False,
            direct_count=2,
        )
        rating, reasons = _calibrate_rating(FactRating.MISLEADING, pack)
        # With direct refutation present, promotion should not fire
        assert rating == FactRating.MISLEADING

    def test_misleading_mixed_consensus_not_promoted(self):
        """MISLEADING + MIXED consensus → stays MISLEADING (not promoted)."""
        pack = _make_pack(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=2,
        )
        rating, reasons = _calibrate_rating(FactRating.MISLEADING, pack)
        assert rating == FactRating.MISLEADING

    def test_consensus_contradiction_with_direct_evidence_corrects_to_mostly_true(self):
        """AGREEING + FALSE + direct evidence → MOSTLY_TRUE (not MISLEADING)."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=2,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MOSTLY_TRUE
        assert any("MOSTLY_TRUE" in r for r in reasons)

    def test_consensus_contradiction_without_direct_evidence_corrects_to_misleading(self):
        """AGREEING + FALSE + no direct evidence → MISLEADING (fallback)."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING

    def test_negated_claim_not_promoted(self):
        """Negated claim ("ist kein") should not be promoted even with AGREEING."""
        pack = _make_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=2,
        )
        rating, reasons = _calibrate_rating(
            FactRating.MISLEADING, pack,
            claim_text="Das ist kein gültiger Beschluss",
        )
        # Negated claims should NOT be promoted
        assert rating == FactRating.MISLEADING
