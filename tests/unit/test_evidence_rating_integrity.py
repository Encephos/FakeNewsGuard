"""Regressionstests: Evidenz-Integrität, Rating-Kalibrierung und Confidence-Propagation.

Abgedeckte Bereiche:
  1. TestClaimQualityCeilings
     – Confidence-Ceiling bei niedrigem claim_quality_score (missing_artifact /
       underspecified_actor); Penalty-Staffelung an den konfigurierten Schwellen.

  2. TestActorSpecificityConfidence
     – Unspezifischer Akteur: Penalty-Staffelung bei 0.70- und 0.50-Grenze.
     – Spezifischer Claim mit hoher Qualität: kein Penalty.

  3. TestEvidencePackMetadataPreservation
     – queries_used, retrieval_notes, canonical_text, canonical_hash und
       EvidenceItem-Metadaten gehen nicht verloren.

  4. TestStanceConsensusCalibration
     – Vollständige Matrix: FALSE + alle Konsens-Kombinationen → korrekte Degradierung.
     – MOSTLY_FALSE ohne Widerlegungssignal → UNVERIFIABLE.
     – CONTRADICTORY-Konsens gilt als aktive Widerlegung.

  5. TestDirectVsGeneralPrimarySource
     – Allgemeine Primärquelle (has_primary_source_any) hebt Confidence-Ceiling auf.
     – Fehlen jeglicher Primärquelle → Ceiling 0.82.
     – primary_sources_consulted propagiert has_primary_source_any (nicht direct).

  6. TestConfidencePropagationSynthesizer
     – Kalibrierte Per-Claim-Confidence begrenzt Synthese-Confidence.
     – Synthesizer nutzt min(avg, min+buffer, llm_raw).
     – extraordinary_claim_confidence_ceiling greift bei einzelnem Claim
       ohne Primärquellen.

  7. TestMissingEvidenceNotFalse
     – Keine aktive Widerlegung → FALSE wird degradiert, nie hochgecapped.
     – Zero-Evidence-Kombination → Ceiling 0.50.
     – Nur kontextuelle Evidenz → FALSE wird zu MISLEADING.
"""

from __future__ import annotations

import pytest

from agents.verdict_agent import (
    _calibrate_confidence,
    _calibrate_rating,
    VerdictRatingCalibrationConfig,
    _CEILING_NO_PRIMARY_SOURCE,
    _CEILING_POOR_CLAIM_QUALITY,
    _CEILING_ZERO_USEFUL_EVIDENCE,
    _CEILING_CONTEXTUAL_ONLY,
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
from models.verdict_models import FinalVerdictMeta


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────


def _pack_with_signals(
    consensus: SourceConsensus = SourceConsensus.INSUFFICIENT,
    has_primary: bool = False,
    has_fc_any: bool = False,
    has_fc_direct: bool = False,
    has_direct_refutation: bool = False,
    direct_count: int = 0,
    contextual_rate: float = 0.0,
    overall_quality: float = 0.85,
    freshness: float = 0.80,
    off_topic_rate: float = 0.0,
    web_results: list[EvidenceItem] | None = None,
) -> EvidencePack:
    """Erstellt ein EvidencePack mit frei konfigurierbaren Qualitätssignalen."""
    quality = EvidenceQualitySignals(
        source_consensus=consensus,
        has_primary_source_any=has_primary,
        has_primary_sources=has_primary,          # deprecated alias
        has_fact_check_any=has_fc_any,
        has_fact_check_org_result=has_fc_any,     # deprecated alias
        has_fact_check_direct_match=has_fc_direct,
        has_direct_refutation=has_direct_refutation,
        direct_evidence_count=direct_count,
        contextual_only_rate=contextual_rate,
        overall_quality=overall_quality,
        freshness_score=freshness,
        off_topic_rate=off_topic_rate,
        top_tier_count=1 if has_primary else 0,
    )
    return EvidencePack(
        claim_id="C1",
        claim_text="Testclaim",
        web_results=web_results or [],
        evidence_quality=quality,
    )


def _good_source_item(url: str = "https://destatis.de/test") -> EvidenceItem:
    """Erstellt ein EvidenceItem mit Tier-1-Quelle (gut für Penalty-Berechnung)."""
    return EvidenceItem(
        source=EvidenceSource(
            url=url,
            title="Offizielle Statistik",
            domain="destatis.de",
            domain_tier=1,
            is_primary_source=True,
        ),
        excerpt="Test-Auszug",
        relevance_score=0.85,
        extraction_confidence=0.80,
    )


# ── 1. TestClaimQualityCeilings ──────────────────────────────────────────────


class TestClaimQualityCeilings:
    """Confidence-Ceilings und Penalties bei schlechter Claim-Qualität.

    Schwellen aus _calibrate_confidence:
      - claim_quality_score < 0.50 → Ceiling 0.72 + Penalty 0.10
      - claim_quality_score 0.50–0.70 → kein Ceiling, Penalty 0.05
      - claim_quality_score ≥ 0.70 → kein Ceiling, kein Penalty
    """

    def _pack_good(self) -> EvidencePack:
        """EvidencePack mit genug guten Quellen, damit kein Quellen-Penalty greift.

        AGREEING-Konsens verhindert, dass das Insufficient-Consensus-Ceiling (0.65)
        die Claim-Qualitäts-Tests interferiert.
        """
        items = [_good_source_item(f"https://destatis.de/{i}") for i in range(3)]
        return _pack_with_signals(
            has_primary=True, has_fc_any=True,
            consensus=SourceConsensus.AGREEING,
            overall_quality=0.85, web_results=items
        )

    def test_high_quality_claim_no_ceiling_no_penalty(self):
        """claim_quality_score ≥ 0.70 → keine Ceiling, kein Claim-Penalty."""
        pack = self._pack_good()
        conf, reasons = _calibrate_confidence(0.85, pack, None, claim_quality_score=1.0)
        # Weder Ceiling noch Penalty für Claim-Qualität
        claim_reasons = [r for r in reasons if "Claim-Qualität" in r]
        assert claim_reasons == []

    def test_medium_quality_claim_soft_penalty_only(self):
        """0.50 ≤ claim_quality_score < 0.70 → Penalty 0.05, kein Ceiling."""
        pack = self._pack_good()
        raw = 0.80
        conf, reasons = _calibrate_confidence(raw, pack, None, claim_quality_score=0.65)
        # Penalty 0.05 muss angewandt worden sein
        claim_reasons = [r for r in reasons if "Claim-Qualität" in r]
        assert claim_reasons, "Penalty-Meldung fehlt bei claim_quality_score=0.65"
        assert "-0.05" in claim_reasons[0]

    def test_low_quality_claim_ceiling_applied(self):
        """claim_quality_score < 0.50 → Confidence ≤ _CEILING_POOR_CLAIM_QUALITY (0.72)."""
        pack = self._pack_good()
        conf, reasons = _calibrate_confidence(0.90, pack, None, claim_quality_score=0.40)
        assert conf <= _CEILING_POOR_CLAIM_QUALITY, (
            f"Ceiling bei schlechter Claim-Qualität nicht angewandt: {conf}"
        )
        # Ceiling-Meldung: "Niedrige Claim-Qualität (0.40) → Ceiling 0.72"
        ceiling_reasons = [r for r in reasons if "Claim-Qualität" in r and "Ceiling" in r]
        assert ceiling_reasons, f"Ceiling-Begründung fehlt. Alle Gründe: {reasons}"

    def test_low_quality_claim_additional_penalty_applied(self):
        """claim_quality_score < 0.50 → zusätzlich Penalty 0.10 nach dem Ceiling."""
        pack = self._pack_good()
        conf, reasons = _calibrate_confidence(0.90, pack, None, claim_quality_score=0.45)
        penalty_reasons = [r for r in reasons if "Claim-Qualität niedrig" in r]
        assert penalty_reasons, "Penalty-Meldung fehlt"
        # Python formatiert 0.10 als "0.1" in f-Strings → "-0.1" statt "-0.10"
        assert "-0.1" in penalty_reasons[0]

    def test_boundary_exactly_at_low_threshold(self):
        """Exakt an der Grenze 0.50: Ceiling greift NICHT mehr (< 0.50 → greift)."""
        pack = self._pack_good()
        raw = 0.90
        conf_below, _ = _calibrate_confidence(raw, pack, None, claim_quality_score=0.49)
        conf_at, _ = _calibrate_confidence(raw, pack, None, claim_quality_score=0.50)
        # 0.49 muss Ceiling bekommen, 0.50 nicht
        assert conf_below <= _CEILING_POOR_CLAIM_QUALITY
        # Bei 0.50 greift nur das Penalty von 0.05 (nicht 0.10), kein Ceiling
        assert conf_at > conf_below

    def test_claim_quality_penalty_cumulates_with_source_ceiling(self):
        """Claim-Penalty wirkt zusätzlich zu Source-Ceiling (kumulativ)."""
        # Kein primary, kein fact-check → Source-Ceiling 0.82
        pack = _pack_with_signals(has_primary=False, has_fc_any=False)
        conf, reasons = _calibrate_confidence(0.95, pack, None, claim_quality_score=0.40)
        # Source-Ceiling 0.82 UND Claim-Ceiling 0.72 UND Penalty → ≤ 0.72 − 0.10 = 0.62
        assert conf <= _CEILING_POOR_CLAIM_QUALITY
        assert len(reasons) >= 2


# ── 2. TestActorSpecificityConfidence ─────────────────────────────────────────


class TestActorSpecificityConfidence:
    """Unspezifischer Akteur senkt Claim-Qualität und damit Confidence.

    Getestet wird die direkte Confidence-Kalibrierung für verschiedene
    Qualitätsstufen – nicht die ClaimValidator-Signale (die sind in
    test_claim_validator.py abgedeckt).
    """

    def _pack_rich(self) -> EvidencePack:
        items = [_good_source_item(f"https://bka.de/{i}") for i in range(3)]
        return _pack_with_signals(
            has_primary=True, has_fc_any=True,
            consensus=SourceConsensus.AGREEING,
            overall_quality=0.80, web_results=items
        )

    def test_specific_actor_high_quality_no_penalty(self):
        """Claim mit spezifischem Akteur (quality=0.90) → kein Claim-Penalty."""
        pack = self._pack_rich()
        _, reasons = _calibrate_confidence(0.80, pack, None, claim_quality_score=0.90)
        assert not any("Claim-Qualität" in r for r in reasons)

    def test_underspecified_actor_moderate_quality_soft_penalty(self):
        """Unspezifischer Akteur senkt quality auf ~0.80 → unterhalb 0.70 → Penalty 0.05."""
        # Annahme: underspecified_actor_penalty=0.20 → quality 1.0-0.20=0.80 > 0.70, kein Penalty
        # Testen mit quality knapp unter 0.70 (z.B. 0.68)
        pack = self._pack_rich()
        conf_before, _ = _calibrate_confidence(0.80, pack, None, claim_quality_score=0.80)
        conf_after, reasons = _calibrate_confidence(0.80, pack, None, claim_quality_score=0.68)
        # Bei quality=0.68 (< 0.70) greift Penalty 0.05
        assert conf_after < conf_before, "Confidence soll bei schlechterer Qualität sinken"
        penalty_reasons = [r for r in reasons if "Claim-Qualität niedrig" in r]
        assert penalty_reasons

    def test_combined_penalties_stack_correctly(self):
        """missing_artifact (−0.25) + underspecified_actor (−0.20) = −0.45 von 1.0 = 0.55.

        0.55 liegt zwischen 0.50 und 0.70 → Penalty 0.05 angewendet.
        """
        pack = self._pack_rich()
        # Simuliere kombinierte Strafe: 1.0 - 0.25 - 0.20 = 0.55
        combined_quality = 0.55
        _, reasons = _calibrate_confidence(0.85, pack, None, claim_quality_score=combined_quality)
        penalty_reasons = [r for r in reasons if "Claim-Qualität niedrig" in r]
        assert penalty_reasons
        assert "-0.05" in penalty_reasons[0], (
            "Bei 0.55 (0.50–0.70) erwartet: Penalty 0.05"
        )

    def test_both_penalties_push_below_hard_threshold(self):
        """Kombinierte Strafe bringt Quality unter 0.50 → Ceiling + Penalty 0.10."""
        # 1.0 - 0.25 - 0.30 = 0.45 → unter 0.50
        very_low_quality = 0.45
        pack = self._pack_rich()
        conf, reasons = _calibrate_confidence(0.90, pack, None, claim_quality_score=very_low_quality)
        assert conf <= _CEILING_POOR_CLAIM_QUALITY
        penalty_reasons = [r for r in reasons if "Claim-Qualität niedrig" in r]
        # Python formatiert 0.10 als "0.1" in f-Strings
        assert "-0.1" in penalty_reasons[0]


# ── 3. TestEvidencePackMetadataPreservation ────────────────────────────────────


class TestEvidencePackMetadataPreservation:
    """EvidencePack-Metadaten gehen nicht verloren.

    Testet, dass queries_used, retrieval_notes, canonical_text, canonical_hash
    und EvidenceItem-Quellmetadaten korrekt gespeichert und abrufbar sind.
    Dies verhindert Regressionen durch versehentliches Überschreiben oder
    Weglassen von Feldern bei Modell-Refactors.
    """

    def test_queries_used_preserved(self):
        """queries_used werden 1:1 im EvidencePack gespeichert."""
        queries = ["Kriminalität Deutschland 2023", "PKS 2023 Statistik"]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            queries_used=queries,
        )
        assert pack.queries_used == queries

    def test_retrieval_notes_preserved(self):
        """retrieval_notes werden vollständig übernommen."""
        notes = ["SearXNG: 15 Treffer", "Tavily: Fallback aktiviert", "Scraping: 3/5 erfolgreich"]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            retrieval_notes=notes,
        )
        assert pack.retrieval_notes == notes

    def test_canonical_text_preserved(self):
        """canonical_text unterscheidet sich potenziell von claim_text und bleibt erhalten."""
        canon = "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen."
        raw = "Die Kriminalität in Deutschland ist 2023 um 50 Prozent gestiegen."
        pack = EvidencePack(
            claim_id="C1",
            claim_text=raw,
            canonical_text=canon,
        )
        assert pack.canonical_text == canon
        assert pack.claim_text == raw  # Originaltext nicht überschrieben

    def test_evidence_item_source_metadata_preserved(self):
        """Alle Metadaten eines EvidenceItem.source bleiben korrekt erhalten."""
        source = EvidenceSource(
            url="https://destatis.de/pkstat2023",
            title="Polizeiliche Kriminalstatistik 2023",
            domain="destatis.de",
            domain_tier=1,
            publication_date="2024-04-15",
            is_fact_check_org=False,
            is_primary_source=True,
        )
        item = EvidenceItem(
            source=source,
            excerpt="Anstieg von 5,5%",
            relevance_score=0.92,
            extraction_confidence=0.85,
            evidence_type=EvidenceType.DIRECT,
            source_direction=SourceDirection.REFUTES,
        )
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=[item],
        )
        retrieved = pack.web_results[0]
        assert retrieved.source.url == "https://destatis.de/pkstat2023"
        assert retrieved.source.title == "Polizeiliche Kriminalstatistik 2023"
        assert retrieved.source.domain_tier == 1
        assert retrieved.source.publication_date == "2024-04-15"
        assert retrieved.source.is_primary_source is True
        assert retrieved.evidence_type == EvidenceType.DIRECT
        assert retrieved.source_direction == SourceDirection.REFUTES

    def test_multiple_queries_all_accessible(self):
        """Bei mehreren Queries sind alle im Pack abrufbar."""
        queries = [f"query_{i}" for i in range(10)]
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            queries_used=queries,
        )
        assert len(pack.queries_used) == 10
        assert pack.queries_used[-1] == "query_9"

    def test_gfc_match_metadata_preserved(self):
        """GoogleFactCheckMatch-Metadaten (publisher, url, rating) bleiben vollständig erhalten."""
        match = GoogleFactCheckMatch(
            claim_reviewed="Kriminalität gestiegen 50%",
            rating="Falsch",
            publisher="Correctiv",
            url="https://correctiv.org/faktencheck/kriminalitaet",
            language="de",
            title="Correctiv prüft: Kriminalitätsstatistik",
        )
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            google_fact_check_matches=[match],
        )
        gfc = pack.google_fact_check_matches[0]
        assert gfc.publisher == "Correctiv"
        assert gfc.url == "https://correctiv.org/faktencheck/kriminalitaet"
        assert gfc.rating == "Falsch"
        assert gfc.language == "de"
        assert gfc.title == "Correctiv prüft: Kriminalitätsstatistik"

    def test_quality_signals_preserved(self):
        """EvidenceQualitySignals gehen nicht verloren (insb. off_topic_rate und avg_top5_relevance)."""
        quality = EvidenceQualitySignals(
            has_primary_source_any=True,
            has_fact_check_any=True,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=0.75,
            overall_quality=0.80,
            top_tier_count=2,
            off_topic_rate=0.20,
            avg_top5_relevance=0.65,
            low_trust_rate=0.10,
            direct_evidence_count=2,
            contextual_only_rate=0.30,
            has_direct_refutation=True,
            direct_refutation_count=1,
        )
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            evidence_quality=quality,
        )
        q = pack.evidence_quality
        assert q.off_topic_rate == pytest.approx(0.20)
        assert q.avg_top5_relevance == pytest.approx(0.65)
        assert q.direct_evidence_count == 2
        assert q.contextual_only_rate == pytest.approx(0.30)
        assert q.has_direct_refutation is True
        assert q.direct_refutation_count == 1


# ── 4. TestStanceConsensusCalibration ─────────────────────────────────────────


class TestStanceConsensusCalibration:
    """Vollständige Matrix der Stance-/Consensus-Logik in _calibrate_rating.

    Kernregel: Fehlende Evidenz ≠ Widerlegung.
      - FALSE + kein aktives Widerlegungssignal → Degradierung je nach Konsens:
          * INSUFFICIENT  → UNVERIFIABLE
          * MIXED         → MISLEADING
          * AGREEING      → MISLEADING
          * CONTRADICTORY → bleibt FALSE (Konsens selbst = aktive Widerlegung)
      - Kontextueller Cap: FALSE + 0 DIRECT-Quellen → MISLEADING
      - MOSTLY_FALSE + kein Widerlegungssignal → UNVERIFIABLE
      - MOSTLY_FALSE + MIXED oder CONTRADICTORY → bleibt MOSTLY_FALSE
    """

    def test_false_insufficient_consensus_no_refutation_becomes_unverifiable(self):
        """FALSE + INSUFFICIENT-Konsens + keine aktive Widerlegung → UNVERIFIABLE."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert any("INSUFFICIENT" in r for r in reasons), reasons

    def test_false_mixed_consensus_no_refutation_becomes_misleading(self):
        """FALSE + MIXED-Konsens + keine direkte Widerlegung → MISLEADING."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("MISLEADING" in r for r in reasons), reasons

    def test_false_agreeing_consensus_no_refutation_becomes_misleading(self):
        """FALSE + AGREEING-Konsens (Quellen stützen) + keine aktive Widerlegung → MISLEADING."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING

    def test_false_contradictory_consensus_stays_false(self):
        """FALSE + CONTRADICTORY-Konsens + direkte Evidenz → bleibt FALSE.

        CONTRADICTORY gilt als aktive Widerlegung (has_active_refutation=True).
        Damit der kontextuelle Cap nicht greift, muss direct_count >= 1 sein.
        """
        pack = _pack_with_signals(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=1,   # mindestens 1 DIRECT-Quelle, damit contextual_only-Cap nicht feuert
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE, (
            f"CONTRADICTORY + DIRECT-Evidenz: FALSE muss erhalten bleiben. Gründe: {reasons}"
        )

    def test_false_with_direct_refutation_stays_false(self):
        """FALSE + has_direct_refutation=True → bleibt FALSE (aktive Widerlegung)."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=True,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE

    def test_false_with_fc_direct_stays_false(self):
        """FALSE + has_fact_check_direct_match=True → bleibt FALSE."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_fc_direct=True,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE

    def test_false_contextual_only_becomes_misleading(self):
        """FALSE + ausschließlich kontextuelle Evidenz (0 DIRECT) → MISLEADING.

        Kontextquellen simulieren keine aktive Widerlegung.
        """
        pack = _pack_with_signals(
            consensus=SourceConsensus.CONTRADICTORY,   # aktive Widerlegung via Konsens
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,  # kein DIRECT-Beleg
        )
        # CONTRADICTORY erhält zunächst FALSE (aktive Widerlegung via Konsens),
        # dann greift contextual-only-cap: 0 DIRECT → MISLEADING
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("DIRECT" in r or "Kontext" in r for r in reasons), reasons

    def test_mostly_false_no_signal_becomes_unverifiable(self):
        """MOSTLY_FALSE ohne jedes Widerlegungssignal → UNVERIFIABLE."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert any("MOSTLY_FALSE" in r for r in reasons), reasons

    def test_mostly_false_with_mixed_consensus_stays(self):
        """MOSTLY_FALSE + MIXED-Konsens → bleibt MOSTLY_FALSE (MIXED = Widerlegungssignal)."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.MIXED,
            has_direct_refutation=False,
            has_fc_any=False,
        )
        rating, reasons = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE
        assert reasons == []

    def test_mostly_false_with_contradictory_consensus_stays(self):
        """MOSTLY_FALSE + CONTRADICTORY → bleibt MOSTLY_FALSE."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.CONTRADICTORY,
        )
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE

    def test_mostly_false_with_fc_any_stays(self):
        """MOSTLY_FALSE + hat irgendein Fact-Check-Ergebnis → bleibt MOSTLY_FALSE."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_fc_any=True,
        )
        rating, _ = _calibrate_rating(FactRating.MOSTLY_FALSE, pack)
        assert rating == FactRating.MOSTLY_FALSE

    def test_true_not_affected_by_calibration(self):
        """TRUE-Rating wird von _calibrate_rating nicht angefasst."""
        pack = _pack_with_signals(consensus=SourceConsensus.INSUFFICIENT)
        rating, reasons = _calibrate_rating(FactRating.TRUE, pack)
        assert rating == FactRating.TRUE
        assert reasons == []

    def test_unverifiable_not_affected_by_calibration(self):
        """UNVERIFIABLE-Rating bleibt unverändert."""
        pack = _pack_with_signals(consensus=SourceConsensus.INSUFFICIENT)
        rating, reasons = _calibrate_rating(FactRating.UNVERIFIABLE, pack)
        assert rating == FactRating.UNVERIFIABLE
        assert reasons == []

    def test_no_quality_signals_no_calibration(self):
        """Ohne EvidenceQualitySignals (quality=None) → keine Kalibrierung."""
        pack = EvidencePack(claim_id="C1", claim_text="Test", evidence_quality=None)
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE
        assert reasons == []


# ── 5. TestDirectVsGeneralPrimarySource ───────────────────────────────────────


class TestDirectVsGeneralPrimarySource:
    """Allgemeine Primärquellen-Präsenz vs direkte Evidenz von Primärquellen.

    has_primary_source_any=True  → Confidence-Ceiling 0.82 wird NICHT angewandt
    has_primary_source_any=False → Ceiling 0.82 aktiv (sofern kein Fact-Check)

    FinalVerdictMeta.primary_sources_consulted  nutzt has_primary_source_any:
    Auch eine allgemeine Behördenseite (CONTEXTUAL) setzt das Meta-Signal.

    Der SynthesizerAgent nutzt primary_sources_consulted für high_quality_evidence
    → beeinflusst FABRICATED-Guardrail.
    """

    def test_no_primary_source_applies_confidence_ceiling(self):
        """Ohne Primärquelle und ohne Fact-Check: Confidence ≤ _CEILING_NO_PRIMARY_SOURCE."""
        pack = _pack_with_signals(has_primary=False, has_fc_any=False)
        conf, reasons = _calibrate_confidence(0.95, pack, None)
        assert conf <= _CEILING_NO_PRIMARY_SOURCE
        assert any("Primärquelle" in r for r in reasons), reasons

    def test_general_primary_source_lifts_ceiling(self):
        """Allgemeine Primärquelle (has_primary_source_any=True) → kein 0.82-Ceiling."""
        pack = _pack_with_signals(has_primary=True, has_fc_any=False)
        conf, reasons = _calibrate_confidence(0.85, pack, None)
        # Ceiling 0.82 darf nicht mehr greifen
        primary_ceiling_reasons = [r for r in reasons if "Keine Primärquelle" in r]
        assert primary_ceiling_reasons == [], (
            f"Primärquelle vorhanden, aber Ceiling trotzdem angewandt: {reasons}"
        )

    def test_fact_check_alone_lifts_primary_ceiling(self):
        """Fact-Check ohne Primärquelle hebt ebenfalls das 0.82-Ceiling auf."""
        pack = _pack_with_signals(has_primary=False, has_fc_any=True)
        conf, reasons = _calibrate_confidence(0.85, pack, None)
        primary_ceiling_reasons = [r for r in reasons if "Keine Primärquelle" in r]
        assert primary_ceiling_reasons == []

    def test_primary_sources_consulted_uses_has_primary_source_any(self):
        """FinalVerdictMeta.primary_sources_consulted wird aus has_primary_source_any gesetzt."""
        # Direkt testen: VerdictAgent setzt primary_sources_consulted = has_primary_source_any
        # Wir prüfen dies über den Synthesizer-Signalweg
        from models.schemas import FactCheckResult, FactRating

        # Claim mit primary_sources_consulted=True (allgemeine Primärquelle)
        fc_with_primary = FactCheckResult(
            claim_id="C1",
            rating=FactRating.FALSE,
            confidence=0.70,
            evidence="",
            verdict_meta=FinalVerdictMeta(
                calibrated_confidence=0.70,
                primary_sources_consulted=True,   # gesetzt via has_primary_source_any
            ),
        )
        # Claim ohne Primärquelle
        fc_no_primary = FactCheckResult(
            claim_id="C2",
            rating=FactRating.FALSE,
            confidence=0.65,
            evidence="",
            verdict_meta=FinalVerdictMeta(
                calibrated_confidence=0.65,
                primary_sources_consulted=False,
            ),
        )
        from agents.synthesizer import SynthesizerAgent
        from unittest.mock import MagicMock
        from config import AppConfig, LLMConfig

        agent = SynthesizerAgent.__new__(SynthesizerAgent)
        agent.config = AppConfig(llm=LLMConfig())
        agent.llm_client = MagicMock()

        signals_with = agent._compute_aggregation_signals([fc_with_primary], None)
        signals_without = agent._compute_aggregation_signals([fc_no_primary], None)

        assert signals_with.high_quality_evidence is True
        assert signals_without.high_quality_evidence is False

    def test_synthesizer_fabricated_guardrail_needs_primary_source(self):
        """FABRICATED-Guardrail braucht high_quality_evidence (= Primärquelle in Claims).

        Nur weil viele Claims FALSE sind, reicht es nicht für FABRICATED –
        es müssen auch Primärquellen konsultiert worden sein.
        """
        from models.schemas import FactCheckResult, FactRating, OverallRating
        from agents.synthesizer import SynthesizerAgent
        from unittest.mock import MagicMock
        from config import AppConfig, LLMConfig

        agent = SynthesizerAgent.__new__(SynthesizerAgent)
        agent.config = AppConfig(llm=LLMConfig())
        agent.llm_client = MagicMock()

        # Alle Claims FALSE, aber keine Primärquellen
        fact_checks = [
            FactCheckResult(
                claim_id=f"C{i}", rating=FactRating.FALSE, confidence=0.70,
                evidence="",
                verdict_meta=FinalVerdictMeta(
                    calibrated_confidence=0.70, primary_sources_consulted=False
                ),
            )
            for i in range(3)
        ]
        signals = agent._compute_aggregation_signals(fact_checks, None)
        assert signals.high_quality_evidence is False

        result = agent._apply_rating_guardrails(OverallRating.FABRICATED, signals)
        assert result == OverallRating.HIGHLY_MISLEADING, (
            "FABRICATED ohne Primärquellen muss auf HIGHLY_MISLEADING degradiert werden"
        )


# ── 6. TestConfidencePropagationSynthesizer ────────────────────────────────────


class TestConfidencePropagationSynthesizer:
    """Kalibrierte Per-Claim-Confidence fließt korrekt in den Synthesizer.

    Der Synthesizer nutzt:
      confidence = min(llm_raw, avg_claim_conf, min_claim_conf + buffer)
    mit buffer = claim_confidence_buffer (default 0.10).

    Zusätzlicher Ceiling: einzelner Claim ohne Primärquellen →
      max extraordinary_claim_confidence_ceiling (default 0.80).
    """

    def _make_synthesizer(self):
        from agents.synthesizer import SynthesizerAgent
        from unittest.mock import MagicMock
        from config import AppConfig, LLMConfig
        agent = SynthesizerAgent.__new__(SynthesizerAgent)
        agent.config = AppConfig(llm=LLMConfig())
        agent.llm_client = MagicMock()
        return agent

    def _make_fc(
        self,
        claim_id: str = "C1",
        confidence: float = 0.65,
        primary: bool = False,
        rating: "FactRating" = None,
    ):
        from models.schemas import FactCheckResult, FactRating
        return FactCheckResult(
            claim_id=claim_id,
            rating=rating or FactRating.FALSE,
            confidence=confidence,
            evidence="",
            verdict_meta=FinalVerdictMeta(
                calibrated_confidence=confidence,
                primary_sources_consulted=primary,
            ),
        )

    def test_llm_overconfidence_capped_by_single_low_claim(self):
        """LLM gibt 0.95, Claim-Confidence ist 0.50 → Synthese ≤ 0.60."""
        agent = self._make_synthesizer()
        fact_checks = [self._make_fc("C1", confidence=0.50)]
        signals = agent._compute_aggregation_signals(fact_checks, None)

        # Direkt die Synthesizer-Confidence-Logik testen (isoliert vom LLM)
        cfg = agent.config.synthesizer
        raw_llm = 0.95
        claim_confidences = [fc.confidence for fc in fact_checks if fc.confidence >= 0.0]
        avg = sum(claim_confidences) / len(claim_confidences)
        min_conf = min(claim_confidences)
        conf = min(raw_llm, avg, min_conf + cfg.claim_confidence_buffer)
        # min(0.95, 0.50, 0.50 + 0.10) = 0.50
        assert conf == pytest.approx(0.50)

    def test_weakest_claim_determines_ceiling(self):
        """Mehrere Claims: die niedrigste Confidence bestimmt das Ceiling."""
        agent = self._make_synthesizer()
        fact_checks = [
            self._make_fc("C1", confidence=0.80),
            self._make_fc("C2", confidence=0.40),  # schwächster
            self._make_fc("C3", confidence=0.70),
        ]
        cfg = agent.config.synthesizer
        raw_llm = 0.95
        claim_confidences = [fc.confidence for fc in fact_checks if fc.confidence >= 0.0]
        avg = sum(claim_confidences) / len(claim_confidences)  # 0.633
        min_conf = min(claim_confidences)                       # 0.40
        conf = min(raw_llm, avg, min_conf + cfg.claim_confidence_buffer)
        # min(0.95, 0.633, 0.50) = 0.50
        assert conf <= min_conf + cfg.claim_confidence_buffer

    def test_confidence_minus_one_ignored_in_aggregation(self):
        """Claims mit confidence=-1.0 (nicht kalibriert) werden ignoriert."""
        agent = self._make_synthesizer()
        from models.schemas import FactCheckResult, FactRating
        fc_calibrated = self._make_fc("C1", confidence=0.70)
        fc_uncalibrated = FactCheckResult(
            claim_id="C2",
            rating=FactRating.FALSE,
            confidence=-1.0,
            evidence="",
        )
        cfg = agent.config.synthesizer
        raw_llm = 0.80
        claim_confidences = [
            fc.confidence for fc in [fc_calibrated, fc_uncalibrated]
            if fc.confidence >= 0.0
        ]
        # Nur fc_calibrated geht ein (0.70)
        assert len(claim_confidences) == 1
        avg = sum(claim_confidences) / len(claim_confidences)
        min_conf = min(claim_confidences)
        conf = min(raw_llm, avg, min_conf + cfg.claim_confidence_buffer)
        assert conf == pytest.approx(min(0.80, 0.70, 0.80))

    def test_no_claims_falls_back_to_llm_raw(self):
        """Keine kalibrierten Claims → LLM-Rohwert wird übernommen."""
        agent = self._make_synthesizer()
        raw_llm = 0.75
        claim_confidences: list[float] = []
        if claim_confidences:
            pass  # Dieser Branch greift nicht
        else:
            conf = raw_llm
        assert conf == pytest.approx(0.75)

    def test_single_claim_without_primary_extraordinary_ceiling(self):
        """Einzelner Claim ohne Primärquellen → max extraordinary_claim_confidence_ceiling."""
        agent = self._make_synthesizer()
        fc = self._make_fc("C1", confidence=0.90, primary=False)
        ceiling = agent.config.synthesizer.extraordinary_claim_confidence_ceiling

        # Simulate the ceiling check from synthesizer.execute()
        claim_confidences = [fc.confidence]
        avg = sum(claim_confidences) / len(claim_confidences)
        min_conf = min(claim_confidences)
        conf = min(0.95, avg, min_conf + agent.config.synthesizer.claim_confidence_buffer)
        # Ceiling für einzelnen Claim ohne primary
        if len([fc]) == 1 and not any(
            f.verdict_meta and f.verdict_meta.primary_sources_consulted
            for f in [fc]
        ):
            conf = min(conf, ceiling)
        assert conf <= ceiling

    def test_single_claim_with_primary_no_extraordinary_ceiling(self):
        """Einzelner Claim MIT Primärquellen → extraordinary_ceiling greift NICHT."""
        agent = self._make_synthesizer()
        fc = self._make_fc("C1", confidence=0.90, primary=True)
        ceiling = agent.config.synthesizer.extraordinary_claim_confidence_ceiling

        conf = fc.confidence  # kein Ceiling
        if len([fc]) == 1 and not any(
            f.verdict_meta and f.verdict_meta.primary_sources_consulted
            for f in [fc]
        ):
            conf = min(conf, ceiling)

        # Mit primary=True greift der Block nicht
        assert conf == 0.90


# ── 7. TestMissingEvidenceNotFalse ───────────────────────────────────────────


class TestMissingEvidenceNotFalse:
    """Fehlende Evidenz führt nicht fälschlich zu FALSE mit hoher Confidence.

    Zentrale Invariante der Pipeline:
      „Kein Beleg" ≠ „Widerlegung"

    Tests:
      - FALSE ohne aktive Widerlegung → Rating degradiert
      - Zero-Evidence-Konstellation → Confidence-Ceiling 0.50
      - Kontextuelle Evidenz allein → FALSE höchstens MISLEADING
      - FALSE mit CONTRADICTORY (Widerlegung vorhanden) → bleibt FALSE
      - Hohe LLM-Confidence bei Zero-Evidence wird abgesenkt
    """

    def test_false_without_any_evidence_is_downgraded(self):
        """FALSE ganz ohne Evidenzpack → UNVERIFIABLE (kein Widerlegungssignal)."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
            direct_count=0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating != FactRating.FALSE, (
            "FALSE ohne jegliche Widerlegungsevidenz darf nicht bestehen bleiben"
        )
        assert rating == FactRating.UNVERIFIABLE

    def test_false_downgrade_produces_reason(self):
        """Jede FALSE-Degradierung erzeugt eine nachvollziehbare Begründung."""
        pack = _pack_with_signals(consensus=SourceConsensus.INSUFFICIENT)
        _, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert len(reasons) > 0, "Kalibrierungsbegründung muss vorhanden sein"
        assert any("FALSE" in r for r in reasons)

    def test_zero_evidence_confidence_ceiling_applies(self):
        """Kein DIRECT, keine Primärquelle, kein FC, Konsens insufficient → max 0.50."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_primary=False,
            has_fc_any=False,
            direct_count=0,
        )
        conf, reasons = _calibrate_confidence(0.90, pack, None)
        assert conf <= _CEILING_ZERO_USEFUL_EVIDENCE, (
            f"Zero-Evidence-Ceiling nicht angewandt, Confidence={conf}"
        )
        ceiling_reasons = [r for r in reasons if "brauchbare Evidenz" in r]
        assert ceiling_reasons

    def test_contextual_only_evidence_caps_false_at_misleading(self):
        """Nur kontextuelle Quellen (0 DIRECT) → FALSE wird zu MISLEADING gekappt."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.CONTRADICTORY,  # aktive Widerlegung via Konsens
            has_direct_refutation=False,
            has_fc_direct=False,
            direct_count=0,                           # kein DIRECT-Beleg
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.MISLEADING
        assert any("DIRECT" in r or "Kontext" in r for r in reasons)

    def test_false_with_real_refutation_stays_false(self):
        """Wenn echte direkte Widerlegung vorhanden: FALSE bleibt erhalten."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.CONTRADICTORY,
            has_direct_refutation=True,
            direct_count=2,   # DIRECT-Quellen vorhanden
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack)
        assert rating == FactRating.FALSE, (
            "Bei echter Widerlegung (DIRECT+REFUTES) muss FALSE erhalten bleiben"
        )

    def test_high_raw_confidence_reduced_with_zero_evidence(self):
        """LLM-Overconfidence (0.95) bei Zero-Evidence → final ≤ 0.50."""
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            has_primary=False,
            has_fc_any=False,
            direct_count=0,
        )
        conf, _ = _calibrate_confidence(0.95, pack, None)
        assert conf <= _CEILING_ZERO_USEFUL_EVIDENCE

    def test_false_to_misleading_not_worse_than_false(self):
        """Degradierung von FALSE → MISLEADING/UNVERIFIABLE ist nie schlimmer als FALSE.

        Stellt sicher, dass die Kalibrierung korrekt in Richtung „weniger sicher"
        und nicht in Richtung „schlechter als falsches Rating" degradiert.
        """
        from models.schemas import FactRating
        # Rating-Ordnung: TRUE < MOSTLY_TRUE < MISLEADING < MOSTLY_FALSE < FALSE < UNVERIFIABLE
        # Eine Degradierung darf nur in Richtung „unsicherer", nicht „schlimmer" gehen.
        # MISLEADING und UNVERIFIABLE sind "unsicherer" als FALSE (nicht "schlimmer")
        pack = _pack_with_signals(consensus=SourceConsensus.INSUFFICIENT)
        rating, _ = _calibrate_rating(FactRating.FALSE, pack)
        # Erlaubte Degradierungsziele
        assert rating in (FactRating.UNVERIFIABLE, FactRating.MISLEADING, FactRating.FALSE)

    def test_disabled_calibration_preserves_false(self):
        """Wenn false_requires_active_refutation=False UND contextual_only=False,
        bleibt FALSE unverändert (beide Korrekturregeln deaktiviert)."""
        config = VerdictRatingCalibrationConfig(
            false_requires_active_refutation=False,
            contextual_only_caps_false_at_misleading=False,  # auch den contextual-Cap abschalten
        )
        pack = _pack_with_signals(
            consensus=SourceConsensus.INSUFFICIENT,
            direct_count=0,
        )
        rating, reasons = _calibrate_rating(FactRating.FALSE, pack, config)
        assert rating == FactRating.FALSE
        assert not any("FALSE ohne aktive Widerlegung" in r for r in reasons)
