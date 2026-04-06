"""Verdict calibration logic – extracted from verdict_agent.py.

Contains confidence ceilings, rating calibration, and confidence calibration
functions used by VerdictAgent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.processing import VerdictCalibrationConfig
from models.evidence_models import EvidencePack, EvidenceType, SourceConsensus, SourceDirection
from models.schemas import FactRating
from models.verdict_models import CoVeTrace


# ── Default-Instanz (Modul-Level) – wird verwendet wenn kein Config übergeben wird ──
_DEFAULT_VCAL = VerdictCalibrationConfig()

# ── Abwärtskompatible Modul-Konstanten (für Re-Export in verdict_agent.py und Tests) ──
_CEILING_NO_PRIMARY_SOURCE = _DEFAULT_VCAL.ceiling_no_primary_source
_CEILING_OFFTOPIC_CONTAMINATION = _DEFAULT_VCAL.ceiling_offtopic_contamination
_CEILING_WEAK_EVIDENCE = _DEFAULT_VCAL.ceiling_weak_evidence
_CEILING_INSUFFICIENT_CONSENSUS = _DEFAULT_VCAL.ceiling_insufficient_consensus
_CEILING_POOR_CLAIM_QUALITY = _DEFAULT_VCAL.ceiling_poor_claim_quality
_CEILING_LOW_AVG_RELEVANCE = _DEFAULT_VCAL.ceiling_low_avg_relevance
_CEILING_VERY_LOW_AVG_RELEVANCE = _DEFAULT_VCAL.ceiling_very_low_avg_relevance
_CEILING_HIGH_LOW_TRUST = _DEFAULT_VCAL.ceiling_high_low_trust
_CEILING_REGULATORY_NO_OFFICIAL = _DEFAULT_VCAL.ceiling_regulatory_no_official
_CEILING_CONTEXTUAL_ONLY = _DEFAULT_VCAL.ceiling_contextual_only
_CEILING_HIGH_WEAK_RATE = _DEFAULT_VCAL.ceiling_high_weak_rate
_CEILING_CONTEXTUAL_AND_LOW_TRUST = _DEFAULT_VCAL.ceiling_contextual_and_low_trust
_CEILING_REGULATORY_NO_DIRECT_EVIDENCE = _DEFAULT_VCAL.ceiling_regulatory_no_direct_evidence
_CEILING_STALE_SOURCES = _DEFAULT_VCAL.ceiling_stale_sources
_CEILING_CURRENT_STATE_NO_FRESH = _DEFAULT_VCAL.ceiling_current_state_no_fresh
_CEILING_ZERO_USEFUL_EVIDENCE = _DEFAULT_VCAL.ceiling_zero_useful_evidence
_CEILING_REGULATORY_NOISY_CONTEXTUAL = _DEFAULT_VCAL.ceiling_regulatory_noisy_contextual
_MIN_GOOD_SOURCES_FOR_HIGH_CONF = _DEFAULT_VCAL.min_good_sources_for_high_conf

# Textuelles Muster für Regulatory-Claims (Fallback wenn claim.frame fehlt)
_REGULATORY_TEXT_PATTERN = re.compile(
    r"\b("
    r"bu[sß]geld|geldbu[sß]e|strafe|straf(?:zahlung|gebühr)|"
    r"beschluss|beschlossen|verordnung|gesetz(?:lich|gebung)?|regelung|"
    r"[üu]berwachung|kontrolliert|[üu]berwacht|kamera(?:system)?|"
    r"pflicht|verpflichtend|verboten|verbiet|untersagt|erlaubt\s+nicht|"
    r"beh[öo]rde|amt|amtlich|offiziell\s+vorgeschrieben|"
    r"sanktion|sanktioniert|zwang|zwangsgeld|bußgeldkatalog"
    r")\b",
    re.IGNORECASE,
)


def _is_regulatory_from_text(claim_text: str) -> bool:
    """Textueller Fallback zur Regulatory-Erkennung wenn claim.frame fehlt."""
    return bool(_REGULATORY_TEXT_PATTERN.search(claim_text))


# ── Rating-Kalibrierung ────────────────────────────────────────────────────────


@dataclass
class VerdictRatingCalibrationConfig:
    """Konfigurierbare Regeln für die regelbasierte Rating-Nachkorrektur.

    Kerntrennung:
        - UNVERIFIABLE : Claim kann mit vorliegender Evidenz nicht geprüft werden
                         (fehlende direkte Belege, kein Konsens)
        - MISLEADING   : Kontext-Quellen vorhanden, aber kein direkter Beweis/Widerruf;
                         oder ähnliches Konzept belegt, aber Details verzerrt
        - MOSTLY_FALSE : Kernaussage falsch, aber nur durch schwache/kontextuelle
                         Widerlegungssignale (kein aktives DIRECT-REFUTES-Signal nötig)
        - FALSE        : Direkt widerlegt (aktive direkte Quelle, Faktenchecker-Match
                         oder überwiegend widerlegender Konsens)
    """

    # --- FALSE-Schwellen -------------------------------------------------------
    # FALSE erfordert mindestens eines dieser Signale:
    #   a) eine DIRECT+REFUTES Quelle in Top-5 (has_direct_refutation)
    #   b) CONTRADICTORY source_consensus (gewichteter Widerlegungs-Konsens)
    #   c) einen direkten Faktenchecker-Match (has_fact_check_direct_match)
    false_requires_active_refutation: bool = True

    # Wohin FALSE degradiert wird, wenn kein aktives Widerlegungssignal vorliegt:
    #   "UNVERIFIABLE" wenn Konsens INSUFFICIENT (keine Richtungssignale)
    #   "MISLEADING"   wenn Konsens MIXED oder AGREEING (Kontext vorhanden,
    #                  aber kein direkter Widerruf)
    false_no_refutation_downgrade_insufficient: str = "UNVERIFIABLE"
    false_no_refutation_downgrade_mixed_or_agreeing: str = "MISLEADING"

    # --- MOSTLY_FALSE-Schwellen -----------------------------------------------
    # MOSTLY_FALSE erfordert mindestens eines dieser Signale:
    #   a) irgendein REFUTES-Signal in Top-5 (auch CONTEXTUAL, nicht nur DIRECT)
    #   b) CONTRADICTORY oder MIXED source_consensus
    #   c) irgendein Faktenchecker-Ergebnis (has_fact_check_any)
    mostly_false_requires_refutation_signal: bool = True

    # Wohin MOSTLY_FALSE degradiert wenn kein Widerlegungssignal vorliegt:
    mostly_false_no_signal_downgrade: str = "UNVERIFIABLE"

    # --- Kontextueller Cap -------------------------------------------------------
    # Wenn 0 DIRECT-Evidence in Top-5 (nur CONTEXTUAL/WEAK): FALSE → MISLEADING
    # Kontextquellen simulieren keine aktive Widerlegung.
    contextual_only_caps_false_at_misleading: bool = True

    # Mindestanzahl DIRECT-Evidence-Items damit FALSE als aktiv belegt gilt.
    # Bei direct_count < Schwelle greift contextual_only_caps_false_at_misleading.
    direct_evidence_min_for_strong_false: int = 1

    # --- Konsens-Widerspruch-Korrektur -------------------------------------------
    # Wenn SourceConsensus = AGREEING aber LLM-Rating = FALSE/MOSTLY_FALSE:
    # Das LLM hat sein Vorwissen über die Evidenz gestellt.
    consensus_contradiction_override: bool = True
    consensus_contradiction_current_state_downgrade: str = "MOSTLY_TRUE"
    consensus_contradiction_general_downgrade: str = "MISLEADING"

    # --- Inverse Konsens-Korrektur -----------------------------------------------
    # Wenn SourceConsensus = CONTRADICTORY aber LLM-Rating = TRUE/MOSTLY_TRUE:
    # Die Quellen widerlegen den Claim, aber das LLM ignoriert das.
    inverse_consensus_override: bool = True
    inverse_consensus_downgrade: str = "MISLEADING"


def _is_negated_claim(claim_text: str) -> bool:
    """Erkennt ob ein Claim eine Negation enthält (z.B. 'ist kein', 'ist nicht')."""
    _NEGATION_PATTERN = re.compile(
        r"\b(?:kein(?:e|er|em|en|es)?|nicht|nie(?:mals)?|weder|"
        r"(?:ist|sind|war|were|has|have)\s+(?:not|kein|keine|nich))\b",
        re.IGNORECASE,
    )
    return bool(_NEGATION_PATTERN.search(claim_text))


def _calibrate_rating(
    raw_rating: "FactRating",
    pack: "EvidencePack",
    config: VerdictRatingCalibrationConfig | None = None,
    is_regulatory_claim: bool = False,
    is_current_state_claim: bool = False,
    claim_text: str = "",
) -> tuple["FactRating", list[str]]:
    """Regelbasierter Rating-Postprocessor.

    Korrigiert LLM-Ratings die aus fehlendem Beweis fälschlich auf FALSE schließen.

    Kernregel: Fehlende direkte Evidenz ≠ Widerlegung.
        - Keine Belege + kein Konsens         → UNVERIFIABLE
        - Kontextquellen ohne direkten Beweis → höchstens MISLEADING
        - Aktive Widerlegung (DIRECT+REFUTES) → FALSE bleibt FALSE

    Args:
        raw_rating: Vom LLM geliefertes Rating (FactRating-Enum)
        pack: EvidencePack mit berechneten Qualitätssignalen
        config: Optionale Konfiguration (Default: VerdictRatingCalibrationConfig())

    Returns:
        (korrigiertes_rating, gründe_als_liste)
    """
    if config is None:
        config = VerdictRatingCalibrationConfig()

    rating = raw_rating
    reasons: list[str] = []

    # Negierte Claims ("ist kein", "ist nicht") dürfen nicht von den
    # current-state Overrides profitieren – bei negierten Claims ist
    # ein FALSE-Rating wahrscheinlich korrekt, wenn Quellen die positive
    # Version bestätigen.
    claim_is_negated = _is_negated_claim(claim_text) if claim_text else False

    quality = pack.evidence_quality
    if quality is None:
        # Ohne Qualitätssignale kann keine regelbasierte Korrektur erfolgen
        return rating, reasons

    consensus = quality.source_consensus
    has_direct_refutation = quality.has_direct_refutation
    has_fc_direct = quality.has_fact_check_direct_match
    has_fc_any = quality.has_fact_check_any
    direct_count = quality.direct_evidence_count

    # Aktuell-Zustand-Claim: veraltete direkte Widerlegungsquellen ignorieren.
    # Alte Artikel (z.B. 2024: "Scholz ist Kanzler") dürfen einen aktuellen
    # Zustandsclaim nicht als FALSE halten, wenn die Quellen selbst veraltet sind.
    _STALE_REFUTATION_THRESHOLD = 0.40
    if (
        is_current_state_claim
        and not claim_is_negated
        and has_direct_refutation
        and quality.direct_refutation_freshness < _STALE_REFUTATION_THRESHOLD
    ):
        has_direct_refutation = False
        reasons.append(
            f"Aktuell-Zustand-Claim: direkte Widerlegungsquellen veraltet "
            f"(Freshness={quality.direct_refutation_freshness:.2f} < "
            f"{_STALE_REFUTATION_THRESHOLD}) → has_direct_refutation ignoriert"
        )

    # Gibt es überhaupt irgendein Widerlegungs-Richtungssignal (auch CONTEXTUAL)?
    has_any_refutation_signal = (
        has_direct_refutation
        or has_fc_any
        or consensus in (SourceConsensus.CONTRADICTORY, SourceConsensus.MIXED)
        or any(
            getattr(i, "source_direction", None) is not None
            and i.source_direction.value == "refutes"
            for i in pack.web_results[:5]
        )
    )

    # ── Konsens-Rating-Widerspruch: AGREEING + FALSE/MOSTLY_FALSE ───────────
    # Wenn die Quellen den Claim überwiegend STÜTZEN (AGREEING) aber das LLM
    # FALSE oder MOSTLY_FALSE urteilt, hat das LLM sein Vorwissen über die
    # Evidenz gestellt. Bei current-state-Claims → MOSTLY_TRUE (die Quellen
    # bestätigen den aktuellen Zustand, das LLM nutzt veraltetes Wissen).
    _consensus_contradiction_applied = False
    if (
        config.consensus_contradiction_override
        and rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE)
        and consensus == SourceConsensus.AGREEING
        and not has_fc_direct
        and not has_direct_refutation
    ):
        if is_current_state_claim and not claim_is_negated:
            new_rating = FactRating(config.consensus_contradiction_current_state_downgrade)
            reasons.append(
                f"Konsens-Widerspruch: Evidenz AGREEING aber Rating {rating.value} "
                f"bei current-state → {new_rating.value}"
            )
            rating = new_rating
            _consensus_contradiction_applied = True
        elif not claim_is_negated:
            new_rating = FactRating(config.consensus_contradiction_general_downgrade)
            reasons.append(
                f"Konsens-Widerspruch: Evidenz AGREEING aber Rating {rating.value} "
                f"→ {new_rating.value}"
            )
            rating = new_rating
            _consensus_contradiction_applied = True

    # ── Inverse Konsens-Korrektur: CONTRADICTORY + TRUE/MOSTLY_TRUE ─────────
    # Wenn die Quellen den Claim überwiegend WIDERLEGEN (CONTRADICTORY) aber
    # das LLM TRUE oder MOSTLY_TRUE urteilt, ignoriert es die Widerlegungssignale.
    if (
        config.inverse_consensus_override
        and not _consensus_contradiction_applied
        and rating in (FactRating.TRUE, FactRating.MOSTLY_TRUE)
        and consensus == SourceConsensus.CONTRADICTORY
        and not has_fc_direct  # Kein Faktenchecker bestätigt den Claim
    ):
        new_rating = FactRating(config.inverse_consensus_downgrade)
        reasons.append(
            f"Inverser Konsens-Widerspruch: Evidenz CONTRADICTORY aber Rating {rating.value} "
            f"→ {new_rating.value}"
        )
        rating = new_rating
        _consensus_contradiction_applied = True

    # ── FALSE-Korrektur ────────────────────────────────────────────────────────
    # Nur anwenden wenn Konsens-Widerspruch nicht bereits gegriffen hat.
    if (
        rating == FactRating.FALSE
        and config.false_requires_active_refutation
        and not _consensus_contradiction_applied
    ):
        has_active_refutation = (
            has_direct_refutation
            or consensus == SourceConsensus.CONTRADICTORY
            or has_fc_direct
        )
        if not has_active_refutation:
            # Kein aktives Widerlegungssignal → FALSE nicht gerechtfertigt
            if consensus == SourceConsensus.INSUFFICIENT:
                new_rating = FactRating(config.false_no_refutation_downgrade_insufficient)
                reasons.append(
                    f"FALSE ohne aktive Widerlegung + Konsens INSUFFICIENT → {new_rating.value}"
                )
            else:
                # MIXED oder AGREEING: Kontext vorhanden, aber kein direkter Widerruf
                new_rating = FactRating(config.false_no_refutation_downgrade_mixed_or_agreeing)
                reasons.append(
                    f"FALSE ohne aktive Widerlegung (Konsens={consensus.value}) → {new_rating.value}"
                )
            rating = new_rating

    # Zusätzlicher Kontextueller Cap: Wenn nur CONTEXTUAL/WEAK-Quellen vorhanden,
    # kann FALSE nicht gerechtfertigt sein.
    # Ausnahmen: has_direct_refutation (DIRECT+REFUTES-Quelle vorhanden – zählt als
    #            direkte Evidenz), has_fc_direct (Faktenchecker hat direkten Match).
    if (
        rating == FactRating.FALSE
        and config.contextual_only_caps_false_at_misleading
        and direct_count < config.direct_evidence_min_for_strong_false
        and not has_fc_direct
        and not has_direct_refutation
    ):
        reasons.append(
            "FALSE bei ausschließlich Kontext-Evidenz (0 DIRECT in Top-5, kein FC-Match) → MISLEADING"
        )
        rating = FactRating.MISLEADING

    # ── MOSTLY_FALSE-Korrektur ────────────────────────────────────────────────
    if rating == FactRating.MOSTLY_FALSE and config.mostly_false_requires_refutation_signal:
        if not has_any_refutation_signal:
            new_rating = FactRating(config.mostly_false_no_signal_downgrade)
            reasons.append(
                f"MOSTLY_FALSE ohne jegliches Widerlegungssignal → {new_rating.value}"
            )
            rating = new_rating

    # ── Regelungsclaim: MISLEADING ohne jedes Evidenz-Signal → UNVERIFIABLE ────
    # Bei konkreten Sanktions-/Überwachungs-/Beschlussclaims darf allgemeiner
    # thematischer Kontext keine MISLEADING-Einstufung erzeugen, wenn:
    #   a) kein Widerlegungssignal vorliegt (kein REFUTES, kein Faktenchecker, kein Konsens)
    #   b) keine direkten Belege vorhanden sind (0 DIRECT in Top-5)
    # In diesem Fall ist die Datenlage zu dünn für eine qualitative Einschätzung.
    if (
        is_regulatory_claim
        and rating == FactRating.MISLEADING
        and not has_any_refutation_signal
        and direct_count == 0
    ):
        reasons.append(
            "Regelungsclaim: MISLEADING ohne Widerlegungssignal + 0 direkte Belege → UNVERIFIABLE"
        )
        rating = FactRating.UNVERIFIABLE

    # ── Aktuell-Zustand-Claim + direkte Belege + frische Quellen → TRUE ────────
    # Wenn ein current-state Claim (z.B. "X ist Bundeskanzler") fälschlich als
    # FALSE/MOSTLY_FALSE/MISLEADING bewertet wird, ABER direkte Belege mit
    # ausreichender Freshness vorliegen, ist das LLM-Urteil vermutlich durch
    # fehlerhafte temporale Logik entstanden (z.B. "Quelle sagt Amtsantritt
    # Mai 2025" wird als "veraltet" behandelt statt als Beleg für aktuellen
    # Zustand). In diesem Fall auf TRUE korrigieren.
    try:
        _freshness = float(quality.freshness_score) if quality else 0.0
    except (TypeError, ValueError):
        _freshness = 0.0
    if (
        is_current_state_claim
        and not claim_is_negated
        and rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE, FactRating.MISLEADING)
        and direct_count > 0
        and _freshness >= 0.30
        and not has_direct_refutation
    ):
        reasons.append(
            f"Aktuell-Zustand-Claim: {rating.value} mit {direct_count} direkten "
            f"Belegen und Freshness={_freshness:.2f} → TRUE "
            "(Transitions-Quellen belegen aktuellen Zustand)"
        )
        rating = FactRating.TRUE

    # ── Aktuell-Zustand-Claim: keine frischen direkten Belege → UNVERIFIABLE ──
    # Veraltete Quellen (z.B. Artikel 2022 über Amtsinhaber) dürfen einen
    # aktuellen Zustandsclaim nicht widerlegen.  Wenn kein direkter Beweis
    # (DIRECT-Evidence, Faktenchecker) vorliegt, ist UNVERIFIABLE das korrekte
    # Urteil – nicht FALSE oder MISLEADING.
    if (
        is_current_state_claim
        and not claim_is_negated
        and rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE, FactRating.MISLEADING)
        and direct_count == 0
        and not has_fc_direct
        and not has_direct_refutation
    ):
        reasons.append(
            f"Aktuell-Zustand-Claim: {rating.value} ohne direkte Belege "
            "(0 DIRECT, kein FC-Match, kein aktiver Widerruf) → UNVERIFIABLE"
        )
        rating = FactRating.UNVERIFIABLE

    # ── Aktuell-Zustand-Claim + frische Quellen: LLM-Vorwissen-Override ──────
    # Wenn ein current-state Claim als FALSE/MOSTLY_FALSE bewertet wird, aber
    # die Quellen überwiegend frisch sind (freshness >= 0.50), hat das LLM
    # vermutlich veraltetes Trainingswissen über die Quellen gestellt.
    # Bei Meta-Disinfo-Artikeln können has_direct_refutation / Konsens
    # fehlklassifiziert sein, deshalb prüft diese Regel unabhängig davon.
    # NICHT bei negierten Claims – dort ist FALSE wahrscheinlich korrekt.
    if (
        is_current_state_claim
        and not claim_is_negated
        and rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE)
        and _freshness >= 0.50
        and not has_fc_direct
    ):
        reasons.append(
            f"Aktuell-Zustand-Claim: {rating.value} trotz frischer Quellen "
            f"(Freshness={_freshness:.2f}) → UNVERIFIABLE "
            "(LLM-Vorwissen-Override bei current-state vermutet)"
        )
        rating = FactRating.UNVERIFIABLE

    return rating, reasons


def _calibrate_confidence(
    raw_confidence: float,
    pack: "EvidencePack",
    cove_trace: "CoVeTrace | None",
    claim_quality_score: float = 1.0,
    is_regulatory_claim: bool = False,
    is_current_state_claim: bool = False,
    stale_freshness_threshold: float = 0.40,
    vcal: VerdictCalibrationConfig | None = None,
    rating: "FactRating | None" = None,
    topical_centrality: float = -1.0,
) -> tuple[float, list[str]]:
    """Regelbasierter Confidence-Postprocessor.

    Senkt die LLM-Confidence basierend auf objektiven Pipeline-Signalen.
    LLM-Confidence wird nie direkt übernommen.

    Ceiling-Regeln:
        - Ohne Primärquelle UND ohne Fact-Check: max 0.82
        - Off-topic-Rate > 50% (aus EvidenceQualitySignals): max 0.75
        - Schwache Evidenzqualität (overall < 0.30): max 0.70
        - Schlechte Claim-Qualität (score < 0.50): max 0.72
        - Insufficient source consensus: max 0.65
        - Hoher Low-Trust-Anteil in Top-5: max 0.62
        - Regelungsclaim ohne offizielle Quelle: max 0.72
        - Veraltete Quellen (avg_freshness < Schwellwert): max 0.72
        - Aktuell-Zustand-Claim ohne frische Quellen: max 0.62

    Penalty-Regeln:
        - Zu wenige gute Quellen (Tier 1-3 oder Fact-Check): -0.10
        - CoVe-Widersprüche: dynamisch (bis -0.15)
        - Unbeantwortete CoVe-Fragen: -0.05 pro Frage (max -0.15)
        - Quellen widersprechen sich: -0.10
        - Schlechte Claim-Qualität (score < 0.70): -0.05 bis -0.10

    Args:
        raw_confidence: Rohkonfidenz des LLM (0.0–1.0)
        pack: EvidencePack mit Qualitätssignalen inkl. off_topic_rate
        cove_trace: Optionaler Chain-of-Verification Trace
        claim_quality_score: Qualität des ursprünglichen Claims (0.0–1.0)
                             aus ProcessedClaim.claim_quality_score
        is_regulatory_claim: True wenn der Claim Beschlüsse, Bußgelder,
                             Überwachung oder rechtlich bindende Regeln behauptet
        is_current_state_claim: True wenn der Claim einen aktuellen Amtsinhaber oder
                                Rolleninhaber beschreibt (zeitkritisch)
        stale_freshness_threshold: Schwellwert für avg_freshness unterhalb dessen
                                   Quellen als veraltet gelten (Default: 0.40)
    """
    if vcal is None:
        vcal = _DEFAULT_VCAL

    confidence = raw_confidence
    reasons: list[str] = []

    quality = pack.evidence_quality

    # ── Ceilings ──────────────────────────────────────────────────────────────

    has_primary = quality.has_primary_source_any if quality else False
    has_fc = quality.has_fact_check_any if quality else False

    # Ceiling: ohne Primärquelle oder Fact-Check
    if not has_primary and not has_fc:
        _ceil = vcal.ceiling_no_primary_source
        if confidence > _ceil:
            reasons.append(f"Keine Primärquelle/Fact-Check → Ceiling {_ceil}")
            confidence = min(confidence, _ceil)

    # Ceiling: schwache Evidenzqualität
    if quality and quality.overall_quality < 0.3:
        _ceil = vcal.ceiling_weak_evidence
        if confidence > _ceil:
            reasons.append(f"Schwache Evidenzqualität ({quality.overall_quality:.2f}) → Ceiling {_ceil}")
            confidence = min(confidence, _ceil)

    # Ceiling: insufficient consensus
    if quality and quality.source_consensus.value == "insufficient":
        _ceil = vcal.ceiling_insufficient_consensus
        if confidence > _ceil:
            reasons.append(f"Unzureichender Quellen-Konsens → Ceiling {_ceil}")
            confidence = min(confidence, _ceil)

    # Ceiling: off-topic contamination – aus gemessener off_topic_rate
    _ceil_ot = vcal.ceiling_offtopic_contamination
    if quality and quality.off_topic_rate > 0.5:
        if confidence > _ceil_ot:
            reasons.append(
                f"Off-topic-Rate {quality.off_topic_rate:.0%} → "
                f"Ceiling {_ceil_ot}"
            )
            confidence = min(confidence, _ceil_ot)
    elif pack.web_results:
        top_results = pack.web_results[:5]
        low_rel = sum(1 for r in top_results if r.relevance_score < 0.3)
        if low_rel > len(top_results) / 2:
            if confidence > _ceil_ot:
                reasons.append(
                    f"Off-topic Contamination ({low_rel}/{len(top_results)} schwach) "
                    f"→ Ceiling {_ceil_ot}"
                )
                confidence = min(confidence, _ceil_ot)

    # Ceiling: Topic-Disconnected Evidence
    # Wenn die Top-5 Evidence-Items durchschnittlich geringe topic_relevance haben,
    # ist die Evidenz thematisch vom Artikel entfernt → Confidence beschränken.
    if pack.web_results:
        top5 = pack.web_results[:5]
        avg_topic_rel = sum(
            getattr(r, "topic_relevance_score", 1.0) for r in top5
        ) / len(top5)
        if avg_topic_rel < 0.25:
            _ceil_topic = 0.45
            if confidence > _ceil_topic:
                reasons.append(
                    f"Topic-disconnected Evidence (avg topic_rel={avg_topic_rel:.2f}) "
                    f"→ Ceiling {_ceil_topic}"
                )
                confidence = min(confidence, _ceil_topic)

    # Ceiling: peripherer Claim mit nur kontextueller Evidenz
    # Claims die tangential zum Artikelthema sind und nur KONTEXT-Evidenz haben,
    # verdienen niedrigere Confidence.
    if topical_centrality >= 0 and topical_centrality < 0.3:
        _contextual_rate_tc = quality.contextual_only_rate if quality else 0.0
        _direct_count_tc = quality.direct_evidence_count if quality else 0
        if _contextual_rate_tc > 0.5 and _direct_count_tc == 0:
            _ceil_peripheral = 0.55
            if confidence > _ceil_peripheral:
                reasons.append(
                    f"Peripherer Claim (centrality={topical_centrality:.2f}) mit nur "
                    f"Kontext-Evidenz → Ceiling {_ceil_peripheral}"
                )
                confidence = min(confidence, _ceil_peripheral)

    # Ceiling: schlechte Claim-Qualität
    if claim_quality_score < 0.50:
        _ceil = vcal.ceiling_poor_claim_quality
        if confidence > _ceil:
            reasons.append(
                f"Niedrige Claim-Qualität ({claim_quality_score:.2f}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: schwache Top-5-Relevanz (Produkte, Rechner, allgemeine Seiten dominieren)
    # Nur anwenden wenn ein echter Messwert vorliegt: avg_top5_relevance > 0.0.
    # Der Default 0.0 ist ein Sentinel-Wert ("nicht gemessen"), kein echter Messwert.
    # In der Praxis berechnet _compute_quality_signals immer > 0 wenn web_results vorhanden.
    _avg_rel = quality.avg_top5_relevance if quality else 0.0
    if quality and _avg_rel > 0.0 and _avg_rel < 0.15:
        _ceil = vcal.ceiling_very_low_avg_relevance
        if confidence > _ceil:
            reasons.append(
                f"Top-5-Quellen sehr schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)
    elif quality and _avg_rel > 0.0 and _avg_rel < 0.25:
        _ceil = vcal.ceiling_low_avg_relevance
        if confidence > _ceil:
            reasons.append(
                f"Top-5-Quellen schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: hoher Low-Trust-Anteil
    _low_trust = quality.low_trust_rate if quality else 0.0
    if quality and _low_trust > 0.2:
        effective_ceiling = vcal.ceiling_high_low_trust if _low_trust > 0.3 else 0.70
        if confidence > effective_ceiling:
            reasons.append(
                f"Low-Trust-Quellen dominieren (Rate={_low_trust:.0%}) → "
                f"Ceiling {effective_ceiling}"
            )
            confidence = min(confidence, effective_ceiling)

    # Ceiling: Regelungsclaim ohne offizielle Quelle (Tier 1-2)
    if is_regulatory_claim and not has_primary and not has_fc:
        _ceil = vcal.ceiling_regulatory_no_official
        if confidence > _ceil:
            reasons.append(
                f"Regelungsclaim ohne offizielle Quelle/Fact-Check → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: überwiegend contextual evidence
    _contextual_rate = quality.contextual_only_rate if quality else 0.0
    _direct_count = quality.direct_evidence_count if quality else 0
    if quality and _contextual_rate > 0.6 and _direct_count == 0:
        _ceil = vcal.ceiling_contextual_only
        if confidence > _ceil:
            reasons.append(
                f"Überwiegend Kontext-Evidenz ({_contextual_rate:.0%}, "
                f"0 direkte Belege) → Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: Regelungsclaim ohne direkte Regelungsgrundlage (strenger)
    if is_regulatory_claim and _direct_count == 0:
        _ceil = vcal.ceiling_regulatory_no_direct_evidence
        if confidence > _ceil:
            reasons.append(
                f"Regelungsclaim ohne direkte Evidenz (0 DIRECT in Top-5) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: Regelungsclaim + überwiegend Kontext-Evidenz + keine Primärquelle/FC
    if (
        is_regulatory_claim
        and _direct_count == 0
        and _contextual_rate > 0.5
        and not has_primary
        and not has_fc
    ):
        _ceil = vcal.ceiling_regulatory_noisy_contextual
        if confidence > _ceil:
            reasons.append(
                f"Regelungsclaim: überwiegend Kontext-Evidenz ({_contextual_rate:.0%}), "
                f"keine Primärquelle → Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: hohe weak evidence rate (>60% WEAK-Evidenz in Top-5)
    if pack.web_results:
        _top5 = pack.web_results[:5]
        _weak_count = sum(1 for i in _top5 if i.evidence_type == EvidenceType.WEAK)
        if _weak_count / max(1, len(_top5)) > 0.6:
            _ceil = vcal.ceiling_high_weak_rate
            if confidence > _ceil:
                reasons.append(
                    f"Hohe Weak-Evidence-Rate ({_weak_count}/{len(_top5)} WEAK) → "
                    f"Ceiling {_ceil}"
                )
                confidence = min(confidence, _ceil)

    # Ceiling: contextual evidence + low-trust kombiniert (verschärft)
    if quality and _contextual_rate > 0.5 and _low_trust > 0.2:
        _ceil = vcal.ceiling_contextual_and_low_trust
        if confidence > _ceil:
            reasons.append(
                f"Kontext-Evidenz ({_contextual_rate:.0%}) + Low-Trust ({_low_trust:.0%}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: veraltete Quellen (avg_freshness unter Schwellwert)
    _freshness = quality.freshness_score if quality else 1.0
    if quality and _freshness < stale_freshness_threshold:
        _ceil = vcal.ceiling_stale_sources
        if confidence > _ceil:
            reasons.append(
                f"Veraltete Quellen (Freshness Ø={_freshness:.2f} < {stale_freshness_threshold}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: Aktuell-Zustand-Claim ohne frische Quellen (zeitkritisch)
    if is_current_state_claim and quality and _freshness < stale_freshness_threshold:
        _ceil = vcal.ceiling_current_state_no_fresh
        if confidence > _ceil:
            reasons.append(
                f"Aktuell-Zustand-Claim mit veralteten Quellen (Freshness Ø={_freshness:.2f}) → "
                f"Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # Ceiling: keinerlei brauchbare Evidenz
    if (not has_primary and not has_fc
            and _direct_count == 0
            and quality
            and quality.source_consensus.value == "insufficient"):
        _ceil = vcal.ceiling_zero_useful_evidence
        if confidence > _ceil:
            reasons.append(
                f"Keine brauchbare Evidenz (0 DIRECT, keine Primärquelle, "
                f"kein Fact-Check, Konsens insufficient) → Ceiling {_ceil}"
            )
            confidence = min(confidence, _ceil)

    # ── Anti-Stacking ────────────────────────────────────────────────────────
    # Wenn ≥2 Ceilings gefeuert haben, darf die Confidence nicht unter den
    # combined_ceiling_floor fallen – verhindert pathologisches Stacking.
    n_ceilings = sum(1 for r in reasons if "Ceiling" in r)
    if n_ceilings >= 2 and confidence < vcal.combined_ceiling_floor:
        reasons.append(
            f"Anti-Stacking ({n_ceilings} Ceilings) → Floor {vcal.combined_ceiling_floor}"
        )
        confidence = max(confidence, vcal.combined_ceiling_floor)

    # ── Penalties ─────────────────────────────────────────────────────────────

    # Penalty: zu wenige gute Quellen (Tier 1-3 oder Fact-Check)
    good_sources = sum(
        1 for r in pack.web_results
        if r.source.domain_tier <= 3 or r.source.is_fact_check_org
    )
    if good_sources < vcal.min_good_sources_for_high_conf:
        penalty = 0.10
        reasons.append(f"Nur {good_sources} gute Quellen → -{penalty}")
        confidence -= penalty

    # Penalty: CoVe-Widersprüche
    if cove_trace and cove_trace.has_significant_contradictions():
        delta = abs(min(0.0, cove_trace.confidence_delta))
        if delta > 0:
            reasons.append(f"CoVe-Widersprüche (delta={cove_trace.confidence_delta:.2f}) → -{delta:.2f}")
            confidence -= delta

    # Penalty: Unbeantwortete CoVe-Kernfragen
    if cove_trace and cove_trace.unanswered_questions:
        n_unanswered = len(cove_trace.unanswered_questions)
        penalty = min(0.15, n_unanswered * 0.05)
        reasons.append(f"{n_unanswered} unbeantwortete CoVe-Fragen → -{penalty:.2f}")
        confidence -= penalty

    # Penalty: Quellen widersprechen sich
    if quality and quality.source_consensus.value == "contradictory":
        penalty = 0.10
        reasons.append(f"Quellen widersprechen sich → -{penalty}")
        confidence -= penalty

    # Penalty: Claim-Qualität unter Schwellwert
    if claim_quality_score < 0.70:
        # Sanfter gradueller Abzug: 0.05 bei 0.5–0.7, 0.10 darunter
        penalty = 0.10 if claim_quality_score < 0.50 else 0.05
        reasons.append(f"Claim-Qualität niedrig ({claim_quality_score:.2f}) → -{penalty}")
        confidence -= penalty

    # Floor: AGREEING-Konsens + positives Rating → Mindest-Confidence
    if rating and quality and quality.source_consensus.value == "agreeing":
        if rating in (FactRating.TRUE, FactRating.MOSTLY_TRUE):
            # Stärkerer Floor wenn viele Quellen übereinstimmen
            _n_supporting = sum(
                1 for i in pack.web_results[:8]
                if i.source_direction == SourceDirection.SUPPORTS
            )
            floor = 0.70 if _n_supporting >= 4 else 0.60
            if confidence < floor:
                reasons.append(
                    f"AGREEING-Konsens + {rating.value} "
                    f"({_n_supporting} supporting) → Floor {floor}"
                )
                confidence = max(confidence, floor)

    # Floor: Primärquelle + AGREEING-Konsens
    if has_primary and quality and quality.source_consensus.value == "agreeing":
        _floor = vcal.floor_primary_and_agreeing
        if confidence < _floor:
            reasons.append(f"Primärquelle + AGREEING → Floor {_floor}")
            confidence = max(confidence, _floor)

    # Floor: Direkter Fact-Check-Match
    if has_fc and quality and quality.has_fact_check_direct_match:
        _floor = vcal.floor_fact_check_direct_match
        if confidence < _floor:
            reasons.append(f"FC-Direct-Match → Floor {_floor}")
            confidence = max(confidence, _floor)

    # Floor: Mehrere High-Trust-Quellen + AGREEING
    if (quality and quality.source_consensus.value == "agreeing"
            and quality.direct_evidence_count >= 2
            and quality.low_trust_rate < 0.2):
        _floor = vcal.floor_multi_high_trust_agreeing
        if confidence < _floor:
            reasons.append(f"Multi-High-Trust + AGREEING → Floor {_floor}")
            confidence = max(confidence, _floor)

    confidence = max(0.0, min(1.0, confidence))
    return confidence, reasons
