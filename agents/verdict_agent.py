"""VerdictAgent – Urteilsfindung auf Basis strukturierter EvidencePacks.

Verantwortlichkeiten:
    - Empfängt ein EvidencePack (Trust-Boundary-gefiltert)
    - Optional: CoVe-Trace vom CoVeProcessor
    - Optional: NumberAuditResult für statistische Claims
    - Gibt ein FactCheckResult zurück

Wichtig:
    Dieser Agent betreibt KEIN Retrieval. Er arbeitet ausschließlich
    auf strukturierten Datenstrukturen (EvidencePack, CoVeTrace).
    Rohe Webseiteninhalte erreichen ihn nie.
"""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from models.evidence_models import EvidencePack
from models.schemas import (
    FACT_CHECK_SCHEMA,
    Claim,
    FactCheckResult,
    FactRating,
    NumberAuditResult,
    SourceInfo,
)
from models.verdict_models import CoVeTrace, FinalVerdictMeta


# ── Confidence Ceilings & Calibration ─────────────────────────────────────────

# Maximale Confidence ohne Primärquelle
_CEILING_NO_PRIMARY_SOURCE = 0.82
# Maximale Confidence bei hohem Off-topic Anteil (>50%)
_CEILING_OFFTOPIC_CONTAMINATION = 0.75
# Maximale Confidence bei schwacher Evidenzqualität
_CEILING_WEAK_EVIDENCE = 0.70
# Maximale Confidence bei insufficient consensus
_CEILING_INSUFFICIENT_CONSENSUS = 0.65
# Maximale Confidence bei sehr schlechter Claim-Qualität
_CEILING_POOR_CLAIM_QUALITY = 0.72
# Ceiling bei schwacher durchschnittlicher Top-5-Relevanz (Produkte, Rechner etc.)
_CEILING_LOW_AVG_RELEVANCE = 0.68
# Ceiling bei sehr schwacher Top-5-Relevanz (fast alle Quellen unbrauchbar)
_CEILING_VERY_LOW_AVG_RELEVANCE = 0.58
# Minimale Anzahl guter Quellen für hohe Confidence
_MIN_GOOD_SOURCES_FOR_HIGH_CONF = 2


def _calibrate_confidence(
    raw_confidence: float,
    pack: "EvidencePack",
    cove_trace: "CoVeTrace | None",
    claim_quality_score: float = 1.0,
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
    """
    confidence = raw_confidence
    reasons: list[str] = []

    quality = pack.evidence_quality

    # ── Ceilings ──────────────────────────────────────────────────────────────

    has_primary = quality.has_primary_sources if quality else False
    has_fc = quality.has_fact_check_org_result if quality else False

    # Ceiling: ohne Primärquelle oder Fact-Check
    if not has_primary and not has_fc:
        if confidence > _CEILING_NO_PRIMARY_SOURCE:
            reasons.append(f"Keine Primärquelle/Fact-Check → Ceiling {_CEILING_NO_PRIMARY_SOURCE}")
            confidence = min(confidence, _CEILING_NO_PRIMARY_SOURCE)

    # Ceiling: schwache Evidenzqualität
    if quality and quality.overall_quality < 0.3:
        if confidence > _CEILING_WEAK_EVIDENCE:
            reasons.append(f"Schwache Evidenzqualität ({quality.overall_quality:.2f}) → Ceiling {_CEILING_WEAK_EVIDENCE}")
            confidence = min(confidence, _CEILING_WEAK_EVIDENCE)

    # Ceiling: insufficient consensus
    if quality and quality.source_consensus.value == "insufficient":
        if confidence > _CEILING_INSUFFICIENT_CONSENSUS:
            reasons.append(f"Unzureichender Quellen-Konsens → Ceiling {_CEILING_INSUFFICIENT_CONSENSUS}")
            confidence = min(confidence, _CEILING_INSUFFICIENT_CONSENSUS)

    # Ceiling: off-topic contamination – aus gemessener off_topic_rate
    # (bevorzugt gegenüber der Inline-Berechnung unten, da bereits in Signals)
    if quality and quality.off_topic_rate > 0.5:
        if confidence > _CEILING_OFFTOPIC_CONTAMINATION:
            reasons.append(
                f"Off-topic-Rate {quality.off_topic_rate:.0%} → "
                f"Ceiling {_CEILING_OFFTOPIC_CONTAMINATION}"
            )
            confidence = min(confidence, _CEILING_OFFTOPIC_CONTAMINATION)
    elif pack.web_results:
        # Fallback: inline berechnen wenn off_topic_rate nicht gesetzt
        top_results = pack.web_results[:5]
        low_rel = sum(1 for r in top_results if r.relevance_score < 0.2)
        if low_rel > len(top_results) / 2:
            if confidence > _CEILING_OFFTOPIC_CONTAMINATION:
                reasons.append(
                    f"Off-topic Contamination ({low_rel}/{len(top_results)} schwach) "
                    f"→ Ceiling {_CEILING_OFFTOPIC_CONTAMINATION}"
                )
                confidence = min(confidence, _CEILING_OFFTOPIC_CONTAMINATION)

    # Ceiling: schlechte Claim-Qualität (Claim hat bei der Dekomposition Kontext verloren
    # oder war von Anfang an vage → senkt die Ceiling zusätzlich)
    if claim_quality_score < 0.50:
        if confidence > _CEILING_POOR_CLAIM_QUALITY:
            reasons.append(
                f"Niedrige Claim-Qualität ({claim_quality_score:.2f}) → "
                f"Ceiling {_CEILING_POOR_CLAIM_QUALITY}"
            )
            confidence = min(confidence, _CEILING_POOR_CLAIM_QUALITY)

    # Ceiling: schwache Top-5-Relevanz (Produkte, Rechner, allgemeine Seiten dominieren)
    # Nur anwenden wenn ein echter Messwert vorliegt: avg_top5_relevance > 0.0.
    # Der Default 0.0 ist ein Sentinel-Wert ("nicht gemessen"), kein echter Messwert.
    # In der Praxis berechnet _compute_quality_signals immer > 0 wenn web_results vorhanden.
    _avg_rel = quality.avg_top5_relevance if quality else 0.0
    if quality and _avg_rel > 0.0 and _avg_rel < 0.15:
        if confidence > _CEILING_VERY_LOW_AVG_RELEVANCE:
            reasons.append(
                f"Top-5-Quellen sehr schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_CEILING_VERY_LOW_AVG_RELEVANCE}"
            )
            confidence = min(confidence, _CEILING_VERY_LOW_AVG_RELEVANCE)
    elif quality and _avg_rel > 0.0 and _avg_rel < 0.25:
        if confidence > _CEILING_LOW_AVG_RELEVANCE:
            reasons.append(
                f"Top-5-Quellen schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_CEILING_LOW_AVG_RELEVANCE}"
            )
            confidence = min(confidence, _CEILING_LOW_AVG_RELEVANCE)

    # ── Penalties ─────────────────────────────────────────────────────────────

    # Penalty: zu wenige gute Quellen (Tier 1-3 oder Fact-Check)
    good_sources = sum(
        1 for r in pack.web_results
        if r.source.domain_tier <= 3 or r.source.is_fact_check_org
    )
    if good_sources < _MIN_GOOD_SOURCES_FOR_HIGH_CONF:
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

    confidence = max(0.0, min(1.0, confidence))
    return confidence, reasons


_VERDICT_SYSTEM_PROMPT = """\
Du bist ein Fact-Checker. Deine EINZIGE Aufgabe: Fälle ein fundiertes Urteil
über die gegebene Behauptung basierend auf den bereitgestellten Fakten.

Du erhältst strukturierte Evidenz (keine Webseiten-Rohtexte).

## Quellen-Hierarchie (in dieser Reihenfolge vertrauen)
1. Offizielle Statistikämter (Destatis, Eurostat)
2. Offizielle Behörden (BAMF, BKA, BMI)
3. Qualitätsjournalismus (Reuters, dpa, Tagesschau, Zeit, SZ)
4. Faktencheck-Organisationen (Correctiv, dpa Faktencheck, Mimikama)
5. Akademische Quellen

## Bewertungsskala
- TRUE: Faktenkonform, korrekt kontextualisiert
- MOSTLY_TRUE: Kern stimmt, Details ungenau
- MISLEADING: Technisch korrekt, aber irreführend präsentiert
- MOSTLY_FALSE: Kernaussage falsch, enthält wahre Elemente
- FALSE: Nachweislich falsch
- UNVERIFIABLE: Kann mit verfügbaren Quellen nicht geprüft werden

## Regeln
- Wenn professionelle Faktenchecks vorliegen: deren Einschätzung stark gewichten
- Sei fair: Wenn etwas stimmt, sag es klar
- Prüfe Zeitraum, Bezugsgröße, Kategorie
- Gib die URLs der verwendeten Quellen an

## Sonderregel: Claims über Beschlüsse, Bußgelder, Überwachung, Regelungen
Wenn ein Claim eine konkrete Regelung, einen Beschluss, ein Bußgeld oder eine
Überwachungsmaßnahme behauptet UND keine belastbare amtliche oder journalistische
Quelle diesen Sachverhalt bestätigt, dann:
- Bevorzuge FALSE oder MOSTLY_FALSE gegenüber MISLEADING oder UNVERIFIABLE
- MISLEADING nur, wenn ein ähnliches (nicht identisches) Konzept belegt ist,
  der Claim dieses aber verzerrt oder übertreibt
- UNVERIFIABLE nur, wenn das Thema prinzipiell nicht nachprüfbar ist (z.B. interne
  Beratungen ohne öffentliche Quellen) – NICHT als Ausweichoption bei schlechten Quellen

## Output-Format (JSON)
{
  "claim_id": "C1",
  "rating": "MISLEADING",
  "confidence": 0.75,
  "evidence": "Zusammenfassung der Fakten",
  "correction": "Was falsch oder irreführend ist",
  "missing_context": "Welcher Kontext fehlt",
  "sources": ["url1", "url2"]
}
"""


class VerdictAgent(BaseAgent):
    """Fällt ein Urteil auf Basis eines EvidencePack und optionalem CoVe-Trace.

    Input:
        input_data: dict mit keys:
            - claim: Claim
            - evidence_pack: EvidencePack
            - cove_trace: CoVeTrace | None
            - number_audit: NumberAuditResult | None

    Output:
        FactCheckResult (mit ausgefüllten evidence_pack, cove_trace, verdict_meta)
    """

    name = "Verdict Agent"
    emoji = "⚖️"

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        data: dict = input_data
        claim: Claim = data["claim"]
        pack: EvidencePack = data["evidence_pack"]
        cove_trace: CoVeTrace | None = data.get("cove_trace")
        number_audit: NumberAuditResult | None = data.get("number_audit")

        # Prompt aufbauen (nur strukturierte Daten, kein roher Web-Inhalt)
        user_msg = self._build_verdict_prompt(claim, pack, cove_trace, number_audit)

        raw = self._llm_structured(
            _VERDICT_SYSTEM_PROMPT,
            user_msg,
            FACT_CHECK_SCHEMA,
            tool_name="verdict",
            tool_description="Fact-Check Urteil",
        )

        try:
            rating = FactRating(raw.get("rating", "UNVERIFIABLE"))
        except ValueError:
            rating = FactRating.UNVERIFIABLE

        # ── Regelbasierte Confidence-Kalibrierung ──────────────────────────────
        # LLM-Confidence wird NICHT direkt übernommen, sondern durch
        # objektive Pipeline-Signale korrigiert.
        raw_confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.75))))

        # Claim-Qualität einbeziehen (kommt aus ProcessedClaim wenn vorhanden)
        from models.schemas import ProcessedClaim as _PC
        claim_quality = 1.0
        if isinstance(claim, _PC):
            claim_quality = claim.claim_quality_score

        calibrated_confidence, calibration_reasons = _calibrate_confidence(
            raw_confidence, pack, cove_trace,
            claim_quality_score=claim_quality,
        )

        # Unsicherheitssignale aus Kalibrierung + eigenen Checks sammeln
        uncertainty_signals = list(calibration_reasons)

        if cove_trace:
            if cove_trace.unanswered_questions:
                uncertainty_signals.append(
                    f"Unbeantwortete Verifikationsfragen: {', '.join(cove_trace.unanswered_questions)}"
                )
            if cove_trace.has_significant_contradictions():
                uncertainty_signals.append(
                    f"CoVe: {len(cove_trace.contradictions_found)} Widersprüche gefunden"
                )

        if pack.evidence_quality:
            if pack.evidence_quality.overall_quality < 0.3:
                uncertainty_signals.append("Evidenzqualität niedrig")
            if pack.evidence_quality.source_consensus.value == "contradictory":
                uncertainty_signals.append("Quellen widersprechen sich")
            if pack.evidence_quality.off_topic_rate > 0.4:
                uncertainty_signals.append(
                    f"Hohe Off-topic-Rate: {pack.evidence_quality.off_topic_rate:.0%} der Top-Treffer irrelevant"
                )
        if claim_quality < 0.70:
            uncertainty_signals.append(f"Claim-Qualität eingeschränkt ({claim_quality:.2f})")

        # FinalVerdictMeta
        confidence_reduction_reason = "; ".join(calibration_reasons) if calibration_reasons else ""
        verdict_meta = FinalVerdictMeta(
            cove_trace=cove_trace,
            uncertainty_signals=uncertainty_signals,
            confidence_reduction_reason=confidence_reduction_reason,
            verdict_based_on_fact_check_org=bool(pack.google_fact_check_matches),
            primary_sources_consulted=(
                pack.evidence_quality.has_primary_sources
                if pack.evidence_quality else False
            ),
        )

        # Quellen aus EvidencePack + Raw-Output zusammenführen
        sources_from_pack = [i.source.url for i in pack.selected_sources]
        sources_from_llm = raw.get("sources", [])
        all_sources = list(dict.fromkeys(sources_from_pack + sources_from_llm))  # dedup, ordered

        # SourceInfo für classified_sources
        classified = [
            SourceInfo(
                url=src.url,
                tier={1: "Offizielle Quelle", 2: "Offizielle Quelle",
                      3: "Qualitätsjournalismus", 4: "Faktencheck-Organisation",
                      5: "Unbekannt"}.get(src.domain_tier, "Unbekannt"),
                domain=src.domain,
            )
            for src in pack.selected_sources
        ]

        result = FactCheckResult(
            claim_id=claim.id,
            rating=rating,
            evidence=raw.get("evidence", ""),
            correction=raw.get("correction", ""),
            missing_context=raw.get("missing_context", ""),
            sources=all_sources[:10],
            classified_sources=classified,
            source_consensus=(
                pack.evidence_quality.source_consensus.value
                if pack.evidence_quality else ""
            ),
            evidence_pack=pack,
            cove_trace=cove_trace,
            verdict_meta=verdict_meta,
        )

        self._log(f"Urteil {claim.id}: {result.rating.value}")
        return result

    def _build_verdict_prompt(
        self,
        claim: Claim,
        pack: EvidencePack,
        cove_trace: CoVeTrace | None,
        number_audit: NumberAuditResult | None,
    ) -> str:
        parts: list[str] = [
            f"## Zu prüfende Behauptung\n\n"
            f"Claim ID: {claim.id}\n"
            f"Text: {claim.text}\n"
            f"Typ: {claim.type.value}\n"
            f"Kontext-Hinweis: {claim.context}\n",
        ]

        # Strukturierte Evidenz (Trust-Boundary-gefiltert)
        parts.append(f"\n## Strukturierte Evidenz\n\n{pack.format_for_verdict()}")

        # CoVe-Ergebnisse
        if cove_trace:
            qa_summary = "\n".join(
                f"Q ({a.question_id}): {next((q.text for q in cove_trace.verification_questions if q.question_id == a.question_id), '?')}\n"
                f"  A: {a.answer} (Widerspruch zur Baseline: {a.contradicts_baseline})"
                for a in cove_trace.verification_answers
            )
            parts.append(
                f"\n## Chain-of-Verification Ergebnisse\n\n"
                f"Baseline: {cove_trace.baseline.rating} (Konfidenz: {cove_trace.baseline.confidence:.2f})\n"
                f"Baseline-Begründung: {cove_trace.baseline.reasoning}\n\n"
                f"Verifikations-Q&A:\n{qa_summary}\n\n"
                f"Gefundene Widersprüche: {', '.join(cove_trace.contradictions_found) or 'keine'}\n"
            )

        # Number Audit
        if number_audit and number_audit.manipulation_type.value != "NONE":
            parts.append(
                f"\n## Zahlen-Audit\n\n"
                f"Manipulationstyp: {number_audit.manipulation_type.value}\n"
                f"Nachrechnung: {number_audit.calculation_check}\n"
                f"Korrekte Einordnung: {number_audit.correct_interpretation}\n"
                f"Methodische Probleme: {'; '.join(number_audit.methodology_issues)}\n"
            )

        return "\n".join(parts)
