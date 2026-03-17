"""Rhetoric Analyzer – Erkennt manipulative Sprachmuster und Framing."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from models.schemas import RhetoricAnalysisResult, RhetoricTechnique, Severity

SYSTEM_PROMPT = """\
Du bist ein Rhetoric Analyzer.  Deine EINZIGE Aufgabe: Analysiere den Text
auf manipulative Rhetorik und Framing-Techniken.

## Erkennungsmuster

1. **Loaded Language**: Emotional aufgeladene Begriffe, die eine Wertung implizieren
   - "Asylflut" statt "Asylanträge", "Messermänner" statt "Tatverdächtige"
   - "Willkommenswahn", "Überfremdung", "Sozialtourismus"

2. **Cherry-Picking**: Nur Daten zeigen, die die eigene These stützen

3. **False Equivalence**: Unvergleichbares gleichsetzen

4. **Strohmann**: Gegnerposition absichtlich verzerrt darstellen

5. **Appeal to Fear**: Angst als Hauptargument
   - Verallgemeinerung von Einzelfällen, Katastrophenszenarien

6. **Whataboutism**: Ablenkung durch Gegenvorwurf ("Aber die anderen...")

7. **Dog Whistles**: Codierte Sprache, die Eingeweihte erkennen
   - "besorgte Bürger", "Umvolkung", "Great Replacement" Rhetorik

8. **Implizite Kausalität**: Dinge nebeneinander stellen, um Zusammenhang zu suggerieren
   - "Seit 2015 steigt die Kriminalität" (impliziert: wegen Migration)

9. **Anekdotische Verallgemeinerung**: Einzelfall → allgemeines Problem
   - Ein Vorfall wird zum Beweis für ein systematisches Problem

10. **Zahlen-Framing**: Korrekte Zahlen in irreführendem Rahmen präsentieren

## Wichtig

- Nicht alles ist Manipulation.  Starke Sprache ist in politischen Debatten normal.
- Nur wenn Sprache SYSTEMATISCH dazu dient, Fakten zu VERZERREN, ist es relevant.
- Sei fair: Manipulationstechniken werden von allen politischen Seiten verwendet.
- Bewerte die SCHWERE realistisch: LOW / MEDIUM / HIGH

## Output-Format (JSON)

{
  "techniques": [
    {
      "technique": "Loaded Language",
      "example": "Zitat aus dem Text",
      "explanation": "Wie die Technik hier wirkt",
      "severity": "MEDIUM"
    }
  ],
  "overall_framing": "Gesamteinschätzung des Framings in 2-3 Sätzen"
}
"""


class RhetoricAnalyzerAgent(BaseAgent):
    name = "Rhetoric Analyzer"
    emoji = "🎭"

    def execute(self, input_data: Any, context: str = "") -> RhetoricAnalysisResult:
        # input_data kann ein Claim (für Einzel-Analyse) oder der Gesamttext sein
        if hasattr(input_data, "text"):
            text = input_data.text
        else:
            text = str(input_data)

        user_msg = f"Analysiere folgenden Text auf manipulative Rhetorik:\n\n{text}"
        if context:
            user_msg += f"\n\n## Zusätzlicher Kontext\n\n{context}"

        raw = self._llm_json(SYSTEM_PROMPT, user_msg)

        techniques = []
        for t in raw.get("techniques", []):
            try:
                techniques.append(
                    RhetoricTechnique(
                        technique=t["technique"],
                        example=t.get("example", ""),
                        explanation=t.get("explanation", ""),
                        severity=Severity(t.get("severity", "MEDIUM")),
                    )
                )
            except (KeyError, ValueError) as e:
                self._log(f"Überspringe ungültige Technik: {e}")

        result = RhetoricAnalysisResult(
            techniques=techniques,
            overall_framing=raw.get("overall_framing", ""),
        )

        self._log(f"{len(result.techniques)} Techniken erkannt")
        return result
