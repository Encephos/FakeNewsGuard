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

        # Konfidenz aus CoVe-Trace ableiten (falls vorhanden)
        if cove_trace and cove_trace.has_significant_contradictions():
            confidence_reduction = abs(min(0.0, cove_trace.confidence_delta))
        else:
            confidence_reduction = 0.0

        # Unsicherheitssignale sammeln
        uncertainty_signals = []
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
        verdict_meta = FinalVerdictMeta(
            cove_trace=cove_trace,
            uncertainty_signals=uncertainty_signals,
            confidence_reduction_reason=(
                f"CoVe-Widersprüche (delta={cove_trace.confidence_delta:.2f})"
                if cove_trace and cove_trace.has_significant_contradictions()
                else ""
            ),
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
