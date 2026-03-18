"""Fact Checker – Verifiziert faktische Behauptungen via Websuche."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from models.schemas import FACT_CHECK_SCHEMA, Claim, FactCheckResult, FactRating
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
    """Generiere Suchbegriffe basierend auf Claim-Text UND Originalkontext.

    Der Originaltext liefert thematischen Kontext, der in isolierten Claims
    oft fehlt.  Beispiel: Claim "Studie mit 50.000 Probanden" ist allein
    nicht suchbar — mit Kontext wird daraus "Studie 50.000 Probanden
    Light-Getränke BMI".
    """
    text = claim.text
    queries = [text]  # Direktsuche mit vollem Claim-Text

    # Faktencheck-Suffix
    queries.append(f"{text} faktencheck")

    # Für statistische Claims: fachspezifisches Suffix
    if claim.type.value == "STATISTICAL":
        queries.append(f"{text} statistik daten")

    # Kontext-angereicherte Suche: Wenn der Originaltext vorhanden ist und
    # der Claim-Text das Kernthema möglicherweise nicht vollständig abdeckt,
    # extrahiere Schlüsselbegriffe aus dem Originaltext für eine zusätzliche
    # kontextualisierte Suche.
    if original_text and len(original_text) > len(text) + 30:
        context_query = _build_context_query(claim, original_text)
        if context_query and context_query not in queries:
            queries.append(context_query)

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


class FactCheckerAgent(BaseAgent):
    name = "Fact Checker"
    emoji = "✅"

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        claim: Claim = input_data

        # Cache-Lookup
        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass  # Ungültiger Cache – neu berechnen

        queries = _build_search_queries(claim, original_text=context)
        self._log(f"Suche nach: {queries}")
        search_context = self._web_multi_search(queries, max_results=4)
        return self._fact_check_with_context(claim, search_context, original_text=context)

    async def execute_async(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Async-Version – Suchen laufen parallel."""
        claim: Claim = input_data

        # Cache-Lookup (sync, schnell)
        cached = self._cache_get(claim.text)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass

        queries = _build_search_queries(claim, original_text=context)
        self._log(f"Suche (async) nach: {queries}")

        results_by_query = await self.async_search.multi_search_async(queries, max_results=4)

        parts: list[str] = []
        for query, results in results_by_query.items():
            parts.append(f"=== Suche: '{query}' ===")
            parts.append(WebSearchClient.format_results_for_llm(results))
        search_context = "\n\n".join(parts)

        return self._fact_check_with_context(claim, search_context, original_text=context)

    def _fact_check_with_context(
        self, claim: Claim, search_context: str, original_text: str = ""
    ) -> FactCheckResult:
        user_msg = (
            f"## Zu prüfende Behauptung\n\n"
            f"Claim ID: {claim.id}\n"
            f"Text: {claim.text}\n"
            f"Typ: {claim.type.value}\n"
            f"Kontext-Hinweis: {claim.context}\n"
        )

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
