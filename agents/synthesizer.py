"""Synthesizer – Aggregiert alle Teilergebnisse zum Gesamtverdikt."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from i18n import t
from models.schemas import (
    SYNTHESIS_SCHEMA,
    FactCheckResult,
    NumberAuditResult,
    OverallRating,
    RhetoricAnalysisResult,
    SynthesisResult,
)

SYSTEM_PROMPT = """\
Du bist der Synthesizer.  Deine EINZIGE Aufgabe: Fasse alle Teilergebnisse
der anderen Agenten zu einem kohärenten, nützlichen Gesamtbild zusammen.

## Input

Du erhältst:
- Fact-Check-Ergebnisse (pro Claim)
- Number-Audit-Ergebnisse (für statistische Claims)
- Rhetoric-Analyse (für den Gesamttext)

## Gesamtbewertung

Wähle eine Stufe:
- RELIABLE: Fakten stimmen und sind fair dargestellt
- MOSTLY_RELIABLE: Kleine Ungenauigkeiten, Gesamtbild stimmt
- MIXED: Teils richtig, teils irreführend
- MISLEADING: Systematisch irreführend, auch wenn einzelne Fakten stimmen
- HIGHLY_MISLEADING: Stark verzerrend, wichtige Fakten werden verdreht
- FABRICATED: Frei erfunden

## Confidence Score

0.0 bis 1.0 – wie sicher bist du in der Bewertung?
- Hohe Confidence (>0.8): Klare Quellenlage, eindeutige Fakten
- Mittlere Confidence (0.5-0.8): Manche Aspekte unklar
- Niedrige Confidence (<0.5): Wenig verlässliche Quellen gefunden

## WICHTIG: Fairness-Check

Du MUSST explizit angeben, was am Text KORREKT ist.
Dies ist entscheidend für die Glaubwürdigkeit der Analyse.

## Output-Format (JSON)

{
  "overall_rating": "MISLEADING",
  "confidence": 0.85,
  "summary": "3-5 Sätze Zusammenfassung für Nicht-Experten",
  "key_corrections": ["Korrektur 1", "Korrektur 2"],
  "fairness_notes": ["Was korrekt dargestellt wurde"],
  "sources": ["url1", "url2"]
}
"""


class SynthesizerAgent(BaseAgent):
    name = "Synthesizer"
    emoji = "📊"

    def execute(self, input_data: Any, context: str = "") -> SynthesisResult:
        """Input ist ein dict mit allen Teilergebnissen."""
        data: dict = input_data

        fact_checks: list[FactCheckResult] = data.get("fact_checks", [])
        number_audits: list[NumberAuditResult] = data.get("number_audits", [])
        rhetoric: RhetoricAnalysisResult | None = data.get("rhetoric")
        original_text: str = data.get("original_text", "")
        image_analysis: str = data.get("image_analysis", "")

        # Kontext für das LLM zusammenbauen
        parts: list[str] = [f"## Originaltext\n\n{original_text}\n"]

        if fact_checks:
            parts.append("## Fact-Check-Ergebnisse\n")
            for fc in fact_checks:
                parts.append(
                    f"- Claim {fc.claim_id}: **{fc.rating.value}**\n"
                    f"  Evidenz: {fc.evidence}\n"
                    f"  Korrektur: {fc.correction}\n"
                    f"  Fehlender Kontext: {fc.missing_context}\n"
                    f"  Quellen: {', '.join(fc.sources)}\n"
                )

        if number_audits:
            parts.append("## Number-Audit-Ergebnisse\n")
            for na in number_audits:
                parts.append(
                    f"- Claim {na.claim_id}: Manipulation = {na.manipulation_type.value}\n"
                    f"  Rechnung: {na.calculation_check}\n"
                    f"  Korrekte Einordnung: {na.correct_interpretation}\n"
                    f"  Methodische Probleme: {', '.join(na.methodology_issues)}\n"
                )

        if rhetoric:
            parts.append("## Rhetoric-Analyse\n")
            parts.append(f"Gesamtframing: {rhetoric.overall_framing}\n")
            for tech in rhetoric.techniques:
                parts.append(
                    f"- {tech.technique} ({tech.severity.value}): {tech.explanation}\n"
                    f"  Beispiel: \"{tech.example}\"\n"
                )

        if image_analysis:
            parts.append("## Bildanalyse\n")
            parts.append(image_analysis + "\n")

        user_msg = "\n".join(parts)

        prompt = t("agents.synthesizer.system_prompt")
        tool_desc = t("agents.synthesizer.tool_description")
        raw = self._llm_structured(
            prompt, user_msg, SYNTHESIS_SCHEMA,
            tool_name="synthesis", tool_description=tool_desc
        )

        # Alle Quellen sammeln und deduplizieren
        all_sources: list[str] = raw.get("sources", [])
        for fc in fact_checks:
            all_sources.extend(fc.sources)
        all_sources = list(dict.fromkeys(all_sources))

        try:
            rating = OverallRating(raw.get("overall_rating", "MIXED"))
        except ValueError:
            rating = OverallRating.MIXED

        # Confidence auf gültigen Bereich begrenzen (#5)
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.5))))

        return SynthesisResult(
            overall_rating=rating,
            confidence=confidence,
            summary=raw.get("summary", ""),
            claims_analysis=fact_checks,
            number_audits=number_audits,
            key_corrections=raw.get("key_corrections", []),
            manipulation_techniques=rhetoric.techniques if rhetoric else [],
            fairness_notes=raw.get("fairness_notes", []),
            sources=all_sources,
        )
