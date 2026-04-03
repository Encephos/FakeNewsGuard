"""Number Auditor – Prüft mathematische und statistische Konsistenz."""

from __future__ import annotations

import importlib
import logging
import re
from typing import Any

from agents.base import BaseAgent
from i18n import t
from models.schemas import NUMBER_AUDIT_SCHEMA, Claim, ClaimType, ManipulationType, NumberAuditResult
from tools.claim_router import RouteResult
from tools.sources.registry import SourceRegistry
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

_PERCENT_CURRENCY_RE = re.compile(r"\d+\s*%|€|\$|£|EUR|USD")


class NumberAuditorAgent(BaseAgent):
    name = "Number Auditor"
    emoji = "🔢"

    def _parse_input(self, input_data: Any) -> tuple[Claim, RouteResult | None]:
        """Akzeptiert Claim direkt oder dict mit 'claim' und optionalem 'route_result'."""
        if isinstance(input_data, dict):
            return input_data["claim"], input_data.get("route_result")
        return input_data, None

    def _compute_search_depth(self, claim: Claim, route_result: RouteResult | None) -> int:
        """Berechnet die Suchtiefe anhand von Claim-Eigenschaften und Routing-Konfidenz."""
        depth = 5
        if claim.type == ClaimType.STATISTICAL:
            depth += 3
        if _PERCENT_CURRENCY_RE.search(claim.text):
            depth += 2
        if route_result is not None and route_result.confidence > 0.7:
            depth += 2
        return min(depth, 12)

    def _fetch_institutional_context(self, claim: Claim, route_result: RouteResult) -> str:
        """Fragt institutionelle Source-Clients ab und gibt formatierten Kontext-String zurück."""
        parts: list[str] = []
        for src_cfg in route_result.sources[:3]:
            try:
                reg = SourceRegistry.get(src_cfg.source_id)
                if reg is None:
                    continue
                mod_path, cls_name = reg.source_class.rsplit(".", 1)
                client_cls = getattr(importlib.import_module(mod_path), cls_name)
                items = client_cls().search(claim.text, max_results=3)
                for item in items:
                    parts.append(
                        f"[{reg.source_id}] {item.title}\n"
                        f"URL: {item.url}\n"
                        f"{item.abstract or ''}"
                    )
            except Exception as exc:
                logging.warning(
                    "NumberAuditor: Source-Client '%s' fehlgeschlagen: %s",
                    src_cfg.source_id,
                    exc,
                )
        return "\n\n".join(parts)

    def execute(self, input_data: Any, context: str = "") -> NumberAuditResult:
        claim, route_result = self._parse_input(input_data)

        cached = self._cache_get(claim.text, context)
        if cached is not None:
            try:
                return NumberAuditResult(**cached)
            except Exception:
                pass

        suffix = t("agents.number_auditor.search_suffix")
        search_query = f"{claim.text} {suffix}"
        depth = self._compute_search_depth(claim, route_result)
        search_results = self._web_search(search_query, max_results=depth)

        if route_result is not None:
            inst = self._fetch_institutional_context(claim, route_result)
            if inst:
                search_results += f"\n\n## Institutionelle Quellen\n\n{inst}"

        return self._audit_with_context(claim, search_results, context)

    async def execute_async(self, input_data: Any, context: str = "") -> NumberAuditResult:
        """Async-Version – Suche läuft non-blocking."""
        claim, route_result = self._parse_input(input_data)

        cached = self._cache_get(claim.text, context)
        if cached is not None:
            try:
                return NumberAuditResult(**cached)
            except Exception:
                pass

        suffix = t("agents.number_auditor.search_suffix")
        search_query = f"{claim.text} {suffix}"
        depth = self._compute_search_depth(claim, route_result)
        results = await self.async_search.search_async(search_query, max_results=depth)
        search_results = WebSearchClient.format_results_for_llm(results)

        if route_result is not None:
            inst = self._fetch_institutional_context(claim, route_result)
            if inst:
                search_results += f"\n\n## Institutionelle Quellen\n\n{inst}"

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

        prompt = t("agents.number_auditor.system_prompt")
        raw = self._llm_structured(
            prompt, user_msg, NUMBER_AUDIT_SCHEMA,
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

        self._cache_set(claim.text, result.model_dump(), context)
        self._log(f"Claim {claim.id}: Manipulation = {result.manipulation_type.value}")
        return result
