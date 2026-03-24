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
# Maximale Confidence bei hohem Off-topic Anteil
_CEILING_OFFTOPIC_CONTAMINATION = 0.75
# Maximale Confidence bei schwacher Evidenzqualität
_CEILING_WEAK_EVIDENCE = 0.70
# Maximale Confidence bei insufficient consensus
_CEILING_INSUFFICIENT_CONSENSUS = 0.65
# Minimale Anzahl guter Quellen für hohe Confidence
_MIN_GOOD_SOURCES_FOR_HIGH_CONF = 2


def _calibrate_confidence(
    raw_confidence: float,
    pack: "EvidencePack",
    cove_trace: "CoVeTrace | None",
) -> tuple[float, list[str]]:
    """Regelbasierter Confidence-Postprocessor.

    Senkt die LLM-Confidence basierend auf objektiven Signalen.
    Gibt (kalibrierte_confidence, gründe) zurück.

    Ceiling-Regeln:
        - Ohne Primärquelle: max 0.82
        - Bei off-topic contamination: max 0.75
        - Bei schwacher Evidenz: max 0.70
        - Bei insufficient consensus: max 0.65

    Penalty-Regeln:
        - Zu wenige gute Quellen: -0.10
        - Fehlende Primärquelle: -0.05
        - Hoher off-topic Anteil: -0.10
        - Unbeantwortete CoVe-Kernfragen: -0.05 pro Frage
        - Schwache Claim-Validität: -0.10
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

    # Ceiling: off-topic contamination (mehr als 50% der Top-Quellen irrelevant)
    if pack.web_results:
        top_results = pack.web_results[:5]
        low_relevance_count = sum(1 for r in top_results if r.relevance_score < 0.2)
        if low_relevance_count > len(top_results) / 2:
            if confidence > _CEILING_OFFTOPIC_CONTAMINATION:
                reasons.append(f"Off-topic Contamination ({low_relevance_count}/{len(top_results)} schwach) → Ceiling {_CEILING_OFFTOPIC_CONTAMINATION}")
                confidence = min(confidence, _CEILING_OFFTOPIC_CONTAMINATION)

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
        # objektive Signale (Quellenlage, CoVe, Off-topic) korrigiert.
        raw_confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.75))))
        calibrated_confidence, calibration_reasons = _calibrate_confidence(
            raw_confidence, pack, cove_trace
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
