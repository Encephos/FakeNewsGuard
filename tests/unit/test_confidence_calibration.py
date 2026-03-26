"""Tests für den regelbasierten Confidence-Postprocessor.

Testet:
    - Confidence Ceilings (ohne Primärquelle, Off-topic, schwache Evidenz)
    - Penalties (zu wenige gute Quellen, CoVe-Widersprüche)
    - Korrekte Kalibrierung bei gemischter Evidenz
    - Keine unnötige Absenkung bei guter Evidenz
"""

from __future__ import annotations

import pytest

from agents.verdict_agent import (
    _calibrate_confidence,
    _CEILING_INSUFFICIENT_CONSENSUS,
    _CEILING_NO_PRIMARY_SOURCE,
    _CEILING_OFFTOPIC_CONTAMINATION,
    _CEILING_WEAK_EVIDENCE,
)
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    GoogleFactCheckMatch,
    SourceConsensus,
)
from models.verdict_models import (
    BaselineAssessment,
    CoVeTrace,
    VerificationAnswer,
    VerificationQuestion,
)


def _make_evidence_pack(
    has_primary: bool = True,
    has_fc: bool = True,
    overall_quality: float = 0.85,
    consensus: SourceConsensus = SourceConsensus.AGREEING,
    web_results: list[EvidenceItem] | None = None,
    gfc_matches: list[GoogleFactCheckMatch] | None = None,
) -> EvidencePack:
    """Erstelle ein EvidencePack mit konfigurierbaren Qualitätssignalen."""
    if web_results is None:
        web_results = [
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/test",
                    title="Statistik",
                    domain="destatis.de",
                    domain_tier=1 if has_primary else 5,
                    is_primary_source=has_primary,
                ),
                excerpt="Test excerpt",
                relevance_score=0.8,
                extraction_confidence=0.8,
            ),
            EvidenceItem(
                source=EvidenceSource(
                    url="https://tagesschau.de/test",
                    title="Nachricht",
                    domain="tagesschau.de",
                    domain_tier=3,
                ),
                excerpt="Test excerpt 2",
                relevance_score=0.7,
                extraction_confidence=0.7,
            ),
        ]
    return EvidencePack(
        claim_id="C1",
        claim_text="Test-Claim",
        queries_used=["test query"],
        google_fact_check_matches=gfc_matches or [],
        web_results=web_results,
        evidence_quality=EvidenceQualitySignals(
            has_primary_sources=has_primary,
            has_fact_check_org_result=has_fc,
            source_consensus=consensus,
            freshness_score=0.8,
            overall_quality=overall_quality,
            top_tier_count=1 if has_primary else 0,
        ),
        source_count=len(web_results),
    )


def _make_cove_trace(
    contradictions: bool = False,
    unanswered: int = 0,
    confidence_delta: float = 0.0,
) -> CoVeTrace:
    """Erstelle einen CoVe-Trace."""
    answers = []
    if contradictions:
        answers.append(VerificationAnswer(
            question_id="Q1",
            answer="Widerspricht der Baseline",
            contradicts_baseline=True,
        ))
    return CoVeTrace(
        claim_id="C1",
        baseline=BaselineAssessment(
            rating="MISLEADING",
            reasoning="Test",
            confidence=0.7,
        ),
        verification_answers=answers,
        contradictions_found=["Widerspruch 1"] if contradictions else [],
        confidence_delta=confidence_delta,
        unanswered_questions=[f"Q{i}" for i in range(unanswered)],
    )


class TestConfidenceCeilings:
    """Confidence wird durch Ceilings begrenzt."""

    def test_no_primary_source_ceiling(self):
        """Ohne Primärquelle max 0.82."""
        pack = _make_evidence_pack(has_primary=False, has_fc=False)
        confidence, reasons = _calibrate_confidence(0.95, pack, None)
        assert confidence <= _CEILING_NO_PRIMARY_SOURCE
        assert any("Primärquelle" in r or "Ceiling" in r for r in reasons)

    def test_weak_evidence_ceiling(self):
        """Bei schwacher Evidenz max 0.70."""
        pack = _make_evidence_pack(overall_quality=0.2)
        confidence, reasons = _calibrate_confidence(0.90, pack, None)
        assert confidence <= _CEILING_WEAK_EVIDENCE

    def test_insufficient_consensus_ceiling(self):
        """Bei unzureichendem Konsens max 0.65."""
        pack = _make_evidence_pack(consensus=SourceConsensus.INSUFFICIENT)
        confidence, reasons = _calibrate_confidence(0.90, pack, None)
        assert confidence <= _CEILING_INSUFFICIENT_CONSENSUS

    def test_offtopic_contamination_ceiling(self):
        """Bei Off-topic Contamination max 0.75."""
        # Erstelle Pack mit überwiegend irrelevanten Top-Treffern
        low_rel_items = [
            EvidenceItem(
                source=EvidenceSource(
                    url=f"https://example{i}.com",
                    title=f"Irrelevant {i}",
                    domain=f"example{i}.com",
                    domain_tier=5,
                ),
                excerpt="Irgendwas Irrelevantes",
                relevance_score=0.05,
            )
            for i in range(4)
        ]
        # Ein guter Treffer
        good_item = EvidenceItem(
            source=EvidenceSource(
                url="https://destatis.de/test",
                title="Statistik",
                domain="destatis.de",
                domain_tier=1,
                is_primary_source=True,
            ),
            excerpt="Relevanter Inhalt",
            relevance_score=0.9,
        )
        pack = _make_evidence_pack(web_results=low_rel_items + [good_item])
        confidence, reasons = _calibrate_confidence(0.90, pack, None)
        assert confidence <= _CEILING_OFFTOPIC_CONTAMINATION


class TestConfidencePenalties:
    """Confidence wird durch Penalties gesenkt."""

    def test_cove_contradictions_reduce_confidence(self):
        """CoVe-Widersprüche senken die Confidence."""
        pack = _make_evidence_pack()
        cove = _make_cove_trace(contradictions=True, confidence_delta=-0.15)
        confidence, reasons = _calibrate_confidence(0.80, pack, cove)
        assert confidence < 0.80
        assert any("CoVe" in r for r in reasons)

    def test_unanswered_cove_questions_reduce_confidence(self):
        """Unbeantwortete CoVe-Fragen senken die Confidence."""
        pack = _make_evidence_pack()
        cove = _make_cove_trace(unanswered=3)
        confidence, reasons = _calibrate_confidence(0.80, pack, cove)
        assert confidence < 0.80
        assert any("unbeantwortete" in r.lower() for r in reasons)

    def test_few_good_sources_penalty(self):
        """Zu wenige gute Quellen senken die Confidence."""
        # Nur Tier-5 Quellen
        weak_items = [
            EvidenceItem(
                source=EvidenceSource(
                    url=f"https://blog{i}.de",
                    title=f"Blog {i}",
                    domain=f"blog{i}.de",
                    domain_tier=5,
                ),
                excerpt="Blog-Inhalt",
                relevance_score=0.5,
            )
            for i in range(3)
        ]
        pack = _make_evidence_pack(
            has_primary=False, has_fc=False,
            web_results=weak_items
        )
        conf_weak, _ = _calibrate_confidence(0.80, pack, None)

        # Mit guten Quellen
        pack_good = _make_evidence_pack(has_primary=True, has_fc=True)
        conf_good, _ = _calibrate_confidence(0.80, pack_good, None)

        assert conf_weak < conf_good


class TestConfidenceNoFalseReduction:
    """Confidence wird NICHT unnötig reduziert bei guter Evidenz."""

    def test_good_evidence_keeps_confidence(self):
        """Gute Evidenz → Confidence bleibt hoch."""
        pack = _make_evidence_pack(
            has_primary=True,
            has_fc=True,
            overall_quality=0.9,
            consensus=SourceConsensus.AGREEING,
        )
        confidence, reasons = _calibrate_confidence(0.90, pack, None)
        # Sollte nicht unter 0.80 fallen (nur minimale Penalty möglich)
        assert confidence >= 0.75

    def test_with_fact_check_match_keeps_confidence(self):
        """Mit Google Fact Check Match bleibt Confidence hoch."""
        gfc = [GoogleFactCheckMatch(
            claim_reviewed="Test-Claim",
            rating="Falsch",
            publisher="Correctiv",
            url="https://correctiv.org/test",
        )]
        pack = _make_evidence_pack(has_fc=True, gfc_matches=gfc)
        confidence, _ = _calibrate_confidence(0.85, pack, None)
        assert confidence >= 0.70


class TestFreshnessCeilings:
    """Tests für Freshness-basierte Confidence Ceilings."""

    def test_stale_sources_ceiling(self):
        """Veraltete Quellen → Ceiling auf 0.72."""
        from agents.verdict_agent import _CEILING_STALE_SOURCES
        pack = _make_evidence_pack(
            has_primary=True, has_fc=False, overall_quality=0.85,
        )
        # Freshness manuell auf veraltet setzen
        pack.evidence_quality.freshness_score = 0.20
        confidence, reasons = _calibrate_confidence(
            0.90, pack, None,
            stale_freshness_threshold=0.40,
        )
        assert confidence <= _CEILING_STALE_SOURCES
        assert any("Veraltete Quellen" in r for r in reasons)

    def test_current_state_claim_stale_ceiling(self):
        """Aktuell-Zustand-Claim + veraltete Quellen → strengerer Ceiling 0.55.

        freshness_score=0.50 simuliert Quellen ohne Datum (default 0.5),
        die mit dem neuen Threshold von 0.60 als veraltet gelten.
        """
        from agents.verdict_agent import _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_evidence_pack(
            has_primary=True, has_fc=False, overall_quality=0.85,
        )
        pack.evidence_quality.freshness_score = 0.50
        confidence, reasons = _calibrate_confidence(
            0.90, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence <= _CEILING_CURRENT_STATE_NO_FRESH  # 0.55
        assert any("Aktuell-Zustand-Claim" in r for r in reasons)

    def test_fresh_sources_no_stale_ceiling(self):
        """Frische Quellen (0.80) → kein Stale-Ceiling für current-state angewandt."""
        pack = _make_evidence_pack(
            has_primary=True, has_fc=True, overall_quality=0.90,
        )
        pack.evidence_quality.freshness_score = 0.80
        confidence, reasons = _calibrate_confidence(
            0.85, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert not any("Veraltete Quellen" in r for r in reasons)
        assert not any("Aktuell-Zustand-Claim" in r for r in reasons)

    def test_current_state_fresh_sources_no_ceiling(self):
        """Aktuell-Zustand-Claim MIT frischen Quellen (0.70) → kein Freshness-Ceiling."""
        from agents.verdict_agent import _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_evidence_pack(
            has_primary=True, has_fc=True, overall_quality=0.90,
        )
        pack.evidence_quality.freshness_score = 0.70
        confidence, _ = _calibrate_confidence(
            0.85, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence > _CEILING_CURRENT_STATE_NO_FRESH  # 0.55

    def test_current_state_moderate_freshness_hits_stale_ceiling(self):
        """Freshness 0.50 (unbekannte Daten) für current-state → beide Ceilings greifen.

        Simuliert den realen Fall: Quellen ohne Datumsinformation (default 0.5)
        gelten bei current-state Claims mit Threshold 0.60 als veraltet.
        """
        from agents.verdict_agent import _CEILING_STALE_SOURCES, _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_evidence_pack(
            has_primary=True, has_fc=False, overall_quality=0.85,
        )
        pack.evidence_quality.freshness_score = 0.50
        confidence, reasons = _calibrate_confidence(
            0.85, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence <= _CEILING_STALE_SOURCES        # 0.72
        assert confidence <= _CEILING_CURRENT_STATE_NO_FRESH  # 0.55


class TestZeroUsefulEvidenceCeiling:
    """Ceiling für den Fall, dass keinerlei brauchbare Evidenz vorliegt."""

    def test_zero_useful_evidence_ceiling(self):
        """Kein DIRECT, kein Primary, kein FC, Konsens insufficient → max 0.50."""
        from agents.verdict_agent import _CEILING_ZERO_USEFUL_EVIDENCE

        pack = _make_evidence_pack(
            has_primary=False,
            has_fc=False,
            overall_quality=0.1,
            consensus=SourceConsensus.INSUFFICIENT,
        )
        # Stelle sicher, dass kein DIRECT evidence vorhanden
        pack.evidence_quality.direct_evidence_count = 0
        confidence, reasons = _calibrate_confidence(0.95, pack, None)
        assert confidence <= _CEILING_ZERO_USEFUL_EVIDENCE
        assert any("brauchbare Evidenz" in r for r in reasons)

    def test_zero_evidence_ceiling_not_triggered_with_direct_evidence(self):
        """Mit DIRECT evidence soll das Zero-Ceiling NICHT greifen."""
        from agents.verdict_agent import _CEILING_ZERO_USEFUL_EVIDENCE

        pack = _make_evidence_pack(
            has_primary=False,
            has_fc=False,
            overall_quality=0.4,
            consensus=SourceConsensus.INSUFFICIENT,
        )
        pack.evidence_quality.direct_evidence_count = 2
        confidence, reasons = _calibrate_confidence(0.80, pack, None)
        assert not any("brauchbare Evidenz" in r for r in reasons)

    def test_zero_evidence_ceiling_not_triggered_with_fc(self):
        """Mit Fact-Check-Ergebnis soll das Zero-Ceiling NICHT greifen."""
        from agents.verdict_agent import _CEILING_ZERO_USEFUL_EVIDENCE

        pack = _make_evidence_pack(
            has_primary=False,
            has_fc=True,
            overall_quality=0.3,
            consensus=SourceConsensus.INSUFFICIENT,
        )
        pack.evidence_quality.direct_evidence_count = 0
        confidence, reasons = _calibrate_confidence(0.80, pack, None)
        assert not any("brauchbare Evidenz" in r for r in reasons)
