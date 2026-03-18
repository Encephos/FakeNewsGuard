"""Fact Checker – Verifiziert faktische Behauptungen via Websuche + externe Faktencheck-DBs."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from models.schemas import FACT_CHECK_SCHEMA, Claim, FactCheckResult, FactRating
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.web_search import WebSearchClient

SYSTEM_PROMPT = """\
Du bist ein Fact-Checker.  Deine EINZIGE Aufgabe: Überprüfe die gegebene Behauptung
anhand der bereitgestellten Suchergebnisse.

## Quellen-Hierarchie (in dieser Reihenfolge vertrauen)

1. Offizielle Statistikämter (Destatis, Eurostat)
2. Offizielle Behörden (BAMF, BKA, BMI)
3. Qualitätsjournalismus (Reuters, dpa, Tagesschau, Zeit, SZ)
4. Fact-Checking-Organisationen (Correctiv, dpa Faktencheck, Mimikama)
5. Akademische Quellen

NIEMALS Blogs, Telegram, X/Twitter oder Parteiseiten als Primärquelle verwenden.

## Bewertungsskala

- TRUE: Faktenkonform, korrekt kontextualisiert
- MOSTLY_TRUE: Kern stimmt, Details ungenau
- MISLEADING: Technisch korrekt, aber irreführend präsentiert
- MOSTLY_FALSE: Kernaussage falsch, enthält wahre Elemente
- FALSE: Nachweislich falsch
- UNVERIFIABLE: Kann mit verfügbaren Quellen nicht geprüft werden

## Regeln

- Wenn etwas stimmt, sag es KLAR.  Sei fair und objektiv.
- Wenn ein Claim teilweise stimmt, erkläre EXAKT was stimmt und was nicht.
- Prüfe auch den KONTEXT: Stimmt der Zeitraum? Die Bezugsgröße? Die Kategorie?
- Gib die URLs der verwendeten Quellen an.
- Wenn professionelle Faktenchecks (z.B. von Correctiv, dpa, Snopes, AFP) vorliegen,
  beziehe deren Einschätzung STARK in deine Bewertung ein. Diese Organisationen haben
  oft tiefere Recherche betrieben als aus Suchergebnissen ersichtlich.

## Output-Format (JSON)

{
  "claim_id": "C1",
  "rating": "MISLEADING",
  "evidence": "Zusammenfassung der gefundenen Fakten",
  "correction": "Was an der Behauptung falsch oder irreführend ist",
  "missing_context": "Welcher Kontext absichtlich weggelassen wird",
  "sources": ["url1", "url2"]
}
"""


def _build_search_queries(claim: Claim, original_text: str = "") -> list[str]:
    """Generiere Suchbegriffe adaptiv basierend auf Claim-Typ und Kontext.

    Die Anzahl und Art der Queries passt sich an die Komplexität an:
      - OPINION:      0 Queries (wird nie aufgerufen)
      - FACTUAL:      1-2 Queries (einfache Faktenlage)
      - CONTEXTUAL:   2-3 Queries (Kontext-Suche wichtig)
      - CAUSAL:       2-3 Queries (Kausalität + Faktencheck)
      - STATISTICAL:  3-5 Queries (Daten + Faktencheck + Quellen + Kontext)
    """
    text = claim.text
    claim_type = claim.type.value

    queries = [text]  # Direktsuche mit vollem Claim-Text – immer dabei

    # ── Adaptive Strategie nach Claim-Typ ──────────────────────────

    if claim_type == "FACTUAL":
        # Einfache Fakten: Direktsuche reicht oft, Faktencheck als Ergänzung
        if len(text) > 60:
            # Längere Claims profitieren von einer Faktencheck-Suche
            queries.append(f"{text} faktencheck")

    elif claim_type == "STATISTICAL":
        # Statistische Claims: Aggressive Suche nach Primärdaten
        queries.append(f"{text} faktencheck")
        queries.append(f"{text} statistik daten")
        queries.append(f"{text} destatis eurostat studie")
        # Kontext-Suche ist hier besonders wichtig
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    elif claim_type == "CAUSAL":
        # Kausalbehauptungen: Faktencheck + Korrelation vs. Kausalität
        queries.append(f"{text} faktencheck")
        queries.append(f"{text} ursache wirkung zusammenhang")

    elif claim_type == "CONTEXTUAL":
        # Kontextuelle Claims: Faktencheck + Kontext-Suche
        queries.append(f"{text} faktencheck")
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    else:
        # Fallback für unbekannte Typen
        queries.append(f"{text} faktencheck")

    return queries


def _build_context_query(claim: Claim, original_text: str) -> str:
    """Baue eine kontextualisierte Suchanfrage aus Claim + Originaltext.

    Strategie: Nimm den Claim-Text und ergänze die wichtigsten
    thematischen Begriffe aus dem Originaltext, die im Claim fehlen.
    """
    import re

    claim_lower = claim.text.lower()

    # Extrahiere substantielle Wörter aus dem Originaltext (>4 Zeichen,
    # keine Stoppwörter), die NICHT bereits im Claim vorkommen
    stopwords = {
        "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
        "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
        "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
        "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
        "gegen", "damit", "dabei", "dafür", "darin", "darauf", "davon",
        "denen", "deren", "zeigen", "zeigt", "laut", "mehr", "sehr",
        "andere", "anderen", "anderer", "wieder", "bereits", "dabei",
        "beweist", "beweisen", "endgültig", "menschen", "daten",
    }

    # Alle "interessanten" Wörter aus dem Originaltext
    words = re.findall(r"[A-ZÄÖÜa-zäöüß]{4,}", original_text.lower())
    context_words = []
    seen: set[str] = set()
    for w in words:
        if w in seen or w in stopwords or w in claim_lower:
            continue
        seen.add(w)
        context_words.append(w)

    if not context_words:
        return ""

    # Nimm die ersten 3-4 Kontextbegriffe und kombiniere mit dem Claim-Kern
    # Kürze den Claim auf die ersten ~60 Zeichen für eine brauchbare Query
    claim_short = claim.text[:80].rsplit(" ", 1)[0] if len(claim.text) > 80 else claim.text
    extras = " ".join(context_words[:4])

    return f"{claim_short} {extras}"


def _adaptive_max_results(claim: Claim) -> int:
    """Bestimme die Anzahl der Suchergebnisse pro Query adaptiv.

    Einfache Claims brauchen weniger Ergebnisse, komplexe mehr.
    """
    if claim.type.value == "STATISTICAL":
        return 5  # Brauche mehr Quellen für Zahlenverifikation
    if claim.type.value in ("CAUSAL", "CONTEXTUAL"):
        return 4  # Kontext-Suche braucht etwas mehr
    return 3  # FACTUAL: weniger Ergebnisse reichen meist


class FactCheckerAgent(BaseAgent):
    name = "Fact Checker"
    emoji = "✅"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Externe Faktencheck-Datenbanken (Google Fact Check Tools, ClaimBuster)
        self._factcheck_db = FactCheckDatabaseClient(
            config=FactCheckDatabaseConfig(),
            retry=self.config.retry,
        )

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        claim: Claim = input_data

        # Cache-Lookup
        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass  # Ungültiger Cache – neu berechnen

        # Websuche
        queries = _build_search_queries(claim, original_text=context)
        max_results = _adaptive_max_results(claim)
        self._log(f"Suche nach: {queries} (max_results={max_results})")
        search_context = self._web_multi_search(queries, max_results=max_results)

        # Externe Faktencheck-DBs abfragen
        external_context = self._query_factcheck_databases(claim)

        return self._fact_check_with_context(
            claim, search_context,
            original_text=context,
            external_factchecks=external_context,
        )

    async def execute_async(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Async-Version – Websuche und Faktencheck-DB-Abfrage laufen parallel."""
        import asyncio
        claim: Claim = input_data

        # Cache-Lookup (sync, schnell)
        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass

        queries = _build_search_queries(claim, original_text=context)
        max_results = _adaptive_max_results(claim)
        self._log(f"Suche (async) nach: {queries} (max_results={max_results})")

        # Websuche + Faktencheck-DB parallel
        web_task = self.async_search.multi_search_async(queries, max_results=max_results)
        db_task = self._query_factcheck_databases_async(claim)

        results_by_query, external_context = await asyncio.gather(
            web_task, db_task, return_exceptions=False
        )

        parts: list[str] = []
        for query, results in results_by_query.items():
            parts.append(f"=== Suche: '{query}' ===")
            parts.append(WebSearchClient.format_results_for_llm(results))
        search_context = "\n\n".join(parts)

        return self._fact_check_with_context(
            claim, search_context,
            original_text=context,
            external_factchecks=external_context,
        )

    def _query_factcheck_databases(self, claim: Claim) -> str:
        """Frage externe Faktencheck-Datenbanken synchron ab."""
        try:
            results = self._factcheck_db.search(claim.text)
            if results:
                self._log(f"{len(results)} externe Faktenchecks gefunden")
            return FactCheckDatabaseClient.format_for_llm(results)
        except Exception as e:
            self._log(f"Faktencheck-DB Fehler: {type(e).__name__}: {e}")
            return ""

    async def _query_factcheck_databases_async(self, claim: Claim) -> str:
        """Frage externe Faktencheck-Datenbanken async ab."""
        try:
            results = await self._factcheck_db.search_async(claim.text)
            if results:
                self._log(f"{len(results)} externe Faktenchecks gefunden")
            return FactCheckDatabaseClient.format_for_llm(results)
        except Exception as e:
            self._log(f"Faktencheck-DB Fehler: {type(e).__name__}: {e}")
            return ""

    def _fact_check_with_context(
        self,
        claim: Claim,
        search_context: str,
        original_text: str = "",
        external_factchecks: str = "",
    ) -> FactCheckResult:
        user_msg = (
            f"## Zu prüfende Behauptung\n\n"
            f"Claim ID: {claim.id}\n"
            f"Text: {claim.text}\n"
            f"Typ: {claim.type.value}\n"
            f"Kontext-Hinweis: {claim.context}\n"
        )

        # Externe Faktenchecks VOR den Suchergebnissen – höchste Priorität
        if external_factchecks:
            user_msg += f"\n{external_factchecks}\n"

        # Originaltext als Kontext mitliefern (gekürzt), damit der Fact-Checker
        # das Gesamtthema versteht und Claims nicht isoliert betrachtet
        if original_text:
            truncated = original_text[:800]
            if len(original_text) > 800:
                truncated += "…"
            user_msg += (
                f"\n## Originaltext (Gesamtkontext)\n\n"
                f"{truncated}\n"
            )

        user_msg += f"\n## Suchergebnisse\n\n{search_context}"

        raw = self._llm_structured(
            SYSTEM_PROMPT, user_msg, FACT_CHECK_SCHEMA,
            tool_name="fact_check", tool_description="Fact-Check Ergebnis"
        )

        try:
            rating = FactRating(raw.get("rating", "UNVERIFIABLE"))
        except ValueError:
            rating = FactRating.UNVERIFIABLE

        result = FactCheckResult(
            claim_id=claim.id,
            rating=rating,
            evidence=raw.get("evidence", ""),
            correction=raw.get("correction", ""),
            missing_context=raw.get("missing_context", ""),
            sources=raw.get("sources", []),
        )

        self._cache_set(claim.text, result.model_dump())
        self._log(f"Claim {claim.id}: {result.rating.value}")
        return result
