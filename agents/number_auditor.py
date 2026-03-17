"""Number Auditor – Prüft mathematische und statistische Konsistenz."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from models.schemas import NUMBER_AUDIT_SCHEMA, Claim, ManipulationType, NumberAuditResult
from tools.web_search import WebSearchClient

SYSTEM_PROMPT = """\
Du bist ein Number Auditor.  Deine EINZIGE Aufgabe: Prüfe mathematische und
statistische Aussagen auf Korrektheit und Manipulationstechniken.

## Systematische Prüfungen

1. **Rechencheck**: Stimmen genannte Prozentzahlen rechnerisch?
   - "Verdopplung" = tatsächlich +100%?
   - Stimmen Auf-/Abrundungen?

2. **Basis-Trick**: Wird ein günstiger Vergleichszeitraum gewählt?
   - Vergleich mit Ausnahmejahren (2015 Flüchtlingskrise, 2020 COVID) statt normaler Baselines
   - Wird ein besonders niedriger/hoher Ausgangswert gewählt?

3. **Absolut vs. Relativ**: Wird zwischen absoluten und relativen Zahlen gewechselt?
   - "40% Anstieg" klingt dramatisch, wenn die Basis 5 Fälle waren (→ 7 Fälle)
   - Große absolute Zahlen bei großen Populationen können relativ winzig sein

4. **Per Capita**: Werden Gesamtzahlen statt Pro-Kopf-Raten verglichen?
   - Ländervergleiche ohne Bevölkerungsnormalisierung

5. **Kategorie-Fehler**: Werden verschiedene Messgrößen vermischt?
   - Tatverdächtige ≠ Verurteilte ≠ Anzeigen ≠ Vorfälle
   - Asylanträge ≠ Asylbewerber ≠ Geflüchtete ≠ Ausländer

6. **Trend vs. Schwankung**: Wird normaler statistischer Noise als Trend dargestellt?
   - Kleine Stichproben mit großer Varianz
   - Ein einzelner Datenpunkt als "Trend"

7. **Kumulation**: Werden kumulierte Zahlen statt Jahresraten verwendet?

## Manipulation-Typen

- BASE_EFFECT: Günstiger Vergleichszeitraum
- ABSOLUTE_VS_RELATIVE: Wechsel zwischen absolut/relativ
- CATEGORY_ERROR: Verschiedene Messgrößen vermischt
- CHERRY_PICKED_TIMEFRAME: Selektiver Zeitraum
- CUMULATION_TRICK: Kumuliert statt jährlich
- TREND_VS_NOISE: Schwankung als Trend
- PER_CAPITA_MISSING: Fehlende Bevölkerungsnormalisierung
- CALCULATION_ERROR: Rechenfehler
- NONE: Kein Problem gefunden

## Output-Format (JSON)

{
  "claim_id": "C1",
  "calculation_check": "Eigene Nachrechnung und Erklärung",
  "methodology_issues": ["Problem 1", "Problem 2"],
  "correct_interpretation": "Wie die Zahl korrekt einzuordnen wäre",
  "manipulation_type": "ABSOLUTE_VS_RELATIVE"
}
"""


class NumberAuditorAgent(BaseAgent):
    name = "Number Auditor"
    emoji = "🔢"

    def execute(self, input_data: Any, context: str = "") -> NumberAuditResult:
        claim: Claim = input_data

        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return NumberAuditResult(**cached)
            except Exception:
                pass

        search_query = f"{claim.text} Statistik Daten"
        search_results = self._web_search(search_query, max_results=3)
        return self._audit_with_context(claim, search_results, context)

    async def execute_async(self, input_data: Any, context: str = "") -> NumberAuditResult:
        """Async-Version – Suche läuft non-blocking."""
        claim: Claim = input_data

        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return NumberAuditResult(**cached)
            except Exception:
                pass

        search_query = f"{claim.text} Statistik Daten"
        results = await self.async_search.search_async(search_query, max_results=3)
        search_results = WebSearchClient.format_results_for_llm(results)
        return self._audit_with_context(claim, search_results, context)

    def _audit_with_context(self, claim: Claim, search_results: str, context: str) -> NumberAuditResult:
        user_msg = (
            f"## Zu prüfende Behauptung\n\n"
            f"Claim ID: {claim.id}\n"
            f"Text: {claim.text}\n"
            f"Kontext-Hinweis: {claim.context}\n"
        )
        if context:
            user_msg += f"\n## Zusätzlicher Kontext (aus Fact-Check)\n\n{context}\n"

        user_msg += f"\n## Suchergebnisse zu den Zahlen\n\n{search_results}"

        raw = self._llm_structured(
            SYSTEM_PROMPT, user_msg, NUMBER_AUDIT_SCHEMA,
            tool_name="number_audit", tool_description="Number Audit Ergebnis"
        )

        try:
            manip_type = ManipulationType(raw.get("manipulation_type", "NONE"))
        except ValueError:
            manip_type = ManipulationType.NONE

        result = NumberAuditResult(
            claim_id=claim.id,
            calculation_check=raw.get("calculation_check", ""),
            methodology_issues=raw.get("methodology_issues", []),
            correct_interpretation=raw.get("correct_interpretation", ""),
            manipulation_type=manip_type,
        )

        self._cache_set(claim.text, result.model_dump())
        self._log(f"Claim {claim.id}: Manipulation = {result.manipulation_type.value}")
        return result
