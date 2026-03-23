"""Fact Checker – Verifiziert faktische Behauptungen via Websuche + externe Faktencheck-DBs."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger("fng.fact_checker")

from agents.base import BaseAgent
from i18n import t
from models.schemas import FACT_CHECK_SCHEMA, Claim, FactCheckResult, FactRating
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.llm import LLMClient
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_scraper import ScrapedSource, scrape_sources
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


_QUERY_OPTIMIZER_PROMPT = """\
Du bist ein Suchquery-Optimierer für Faktenprüfung. Deine Aufgabe: Generiere 3 optimierte, \
kurze Suchqueries für eine Suchmaschine, um die gegebene Behauptung zu überprüfen.

## Regeln
- Jede Query maximal 6-8 Wörter (kurz und präzise!)
- Verwende Schlüsselbegriffe, KEINE ganzen Sätze
- Query 1: Direkte Suche nach dem Kernfakt (z.B. "Studie 2016 Frauen Haushalt Glück Deutschland")
- Query 2: Faktencheck-Suche (z.B. "Hausfrauen glücklicher Studie Faktencheck")
- Query 3: Quellensuche nach Primärdaten (z.B. "Studie Frauen Zufriedenheit Haushalt 2016 Ergebnis")
- Verwende die Sprache der Behauptung
- Entferne Füllwörter (der, die, das, und, ist, hat, etc.)
- Behalte spezifische Zahlen, Jahreszahlen und Eigennamen bei

Antworte NUR mit einem JSON-Array von 3 Strings. Beispiel:
["query eins", "query zwei", "query drei"]
"""


def _optimize_queries_with_llm(
    claim: Claim, llm: LLMClient, original_text: str = "",
) -> list[str] | None:
    """Nutze das LLM um optimierte Suchqueries aus dem Claim zu generieren.

    Returns:
        Liste von 3 optimierten Queries, oder None bei Fehler.
    """
    user_msg = f"Behauptung: {claim.text}\nTyp: {claim.type.value}"
    if original_text and len(original_text) > len(claim.text) + 30:
        user_msg += f"\nOriginaltext (Kontext): {original_text[:500]}"

    try:
        raw = llm.complete(_QUERY_OPTIMIZER_PROMPT, user_msg, response_format="json")
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        # Akzeptiere sowohl direkte Liste als auch {"items": [...]}
        if isinstance(parsed, list):
            queries = [str(q).strip() for q in parsed if isinstance(q, str) and q.strip()]
        elif isinstance(parsed, dict) and "items" in parsed:
            queries = [str(q).strip() for q in parsed["items"] if isinstance(q, str) and q.strip()]
        else:
            return None
        return queries[:4] if queries else None
    except Exception as e:
        logger.warning("Query-Optimierung fehlgeschlagen: %s: %s", type(e).__name__, e)
        return None


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

    suffix_fc = t("agents.fact_checker.search_suffix_factcheck")
    suffix_stats = t("agents.fact_checker.search_suffix_stats")
    suffix_official = t("agents.fact_checker.search_suffix_official")
    suffix_causal = t("agents.fact_checker.search_suffix_causal")

    if claim_type == "FACTUAL":
        # Einfache Fakten: Direktsuche reicht oft, Faktencheck als Ergänzung
        if len(text) > 60:
            queries.append(f"{text} {suffix_fc}")

    elif claim_type == "STATISTICAL":
        # Statistische Claims: Aggressive Suche nach Primärdaten
        queries.append(f"{text} {suffix_fc}")
        queries.append(f"{text} {suffix_stats}")
        queries.append(f"{text} {suffix_official}")
        # Kontext-Suche ist hier besonders wichtig
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    elif claim_type == "CAUSAL":
        # Kausalbehauptungen: Faktencheck + Korrelation vs. Kausalität
        queries.append(f"{text} {suffix_fc}")
        queries.append(f"{text} {suffix_causal}")

    elif claim_type == "CONTEXTUAL":
        # Kontextuelle Claims: Faktencheck + Kontext-Suche
        queries.append(f"{text} {suffix_fc}")
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    else:
        # Fallback für unbekannte Typen
        queries.append(f"{text} {suffix_fc}")

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


def _evaluate_scrape_quality(
    ranked: list[RankedSource],
    scraped: list[ScrapedSource],
) -> tuple[bool, str]:
    """Prüfe ob die Scraping-Ergebnisse ausreichend sind.

    Returns:
        (needs_retry, reason) — True wenn ein Retry sinnvoll ist.
    """
    scrapable = [rs for rs in ranked if rs.should_scrape]

    # Fall A: Keine Quelle war scrapbar
    if not scrapable:
        return True, "no_scrapable_sources"

    # Fall B: Alle Scrapes fehlgeschlagen
    if scraped and all(not s.fetch_success for s in scraped):
        return True, "all_scrapes_failed"

    # Fall C: Alle erfolgreichen Scrapes haben low_relevance
    successful = [s for s in scraped if s.fetch_success]
    if successful and all(s.low_relevance for s in successful):
        return True, "all_low_relevance"

    return False, ""


def _build_fallback_queries(
    claim: Claim,
    original_queries: list[str],
) -> list[str]:
    """Generiere alternative Suchqueries für den Retry-Durchlauf.

    Strategien:
      1. Keyword-basierte Kurzquery (ohne Stoppwörter/Füllwörter)
      2. Keyword-Query + "Faktencheck"
      3. Zahlen-fokussierte Query (falls Zahlen im Claim)
    """
    from tools.scrape_ranker import _extract_claim_keywords

    keywords = _extract_claim_keywords(claim.text)
    if not keywords:
        return []

    # Strategie 1: Nur Keywords, kompakt
    keyword_query = " ".join(sorted(keywords)[:6])

    # Strategie 2: Keywords + Faktencheck
    suffix_fc = t("agents.fact_checker.search_suffix_factcheck")
    keyword_fc_query = f"{keyword_query} {suffix_fc}"

    # Strategie 3: Zahlen + Kontext-Keywords (für statistische Claims)
    import re
    numbers = re.findall(r"\d+[\.,]?\d*%?", claim.text)
    number_query = ""
    if numbers:
        number_query = f"{' '.join(numbers)} {keyword_query}"

    # Nur Queries die nicht schon im Original waren
    original_set = set(original_queries)
    fallback = []
    for q in (keyword_query, keyword_fc_query, number_query):
        if q and q not in original_set:
            fallback.append(q)

    return fallback


def _categories_for_claim(claim: Claim) -> str:
    """Bestimme SearXNG-Kategorien basierend auf dem Claim-Typ."""
    mapping = {
        "STATISTICAL": "general,science,news",
        "CAUSAL": "general,science",
        "FACTUAL": "general,news",
        "CONTEXTUAL": "general,news",
    }
    return mapping.get(claim.type.value, "general")


def _build_enriched_context(
    ranked: list[RankedSource],
    scraped: list[ScrapedSource],
) -> str:
    """Baue den angereicherten LLM-Kontext aus gerankten und gescrapten Quellen.

    Quellen mit Volltext werden bevorzugt angezeigt, Quellen ohne Volltext
    erhalten den Original-Snippet mit einem Hinweis auf den Grund.
    """
    skip_reason_labels = {
        "paywall": "Paywall",
        "low_tier": "Niedriger Quellen-Tier",
        "irrelevant": "Kein thematischer Bezug (Snippet-Analyse)",
        "limit_reached": "Scrape-Limit erreicht",
    }

    scraped_by_url: dict[str, ScrapedSource] = {s.url: s for s in scraped}

    # Sortiere: should_scrape=True zuerst
    sorted_ranked = sorted(ranked, key=lambda rs: (not rs.should_scrape, -rs.tier, -rs.relevance_score))

    from tools.source_classifier import classify_source
    parts: list[str] = []
    for i, rs in enumerate(sorted_ranked, 1):
        classified = classify_source(rs.result)
        tier_label = classified.tier_label
        title = rs.result.title
        url = rs.result.url

        sc = scraped_by_url.get(url)

        if sc and sc.fetch_success:
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Volltext-Auszug:\n  {sc.passage}"
            )
        elif sc and not sc.fetch_success:
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Snippet: {rs.result.snippet}\n"
                f"[Kein Volltext: {sc.error}]"
            )
        else:
            reason = skip_reason_labels.get(rs.skip_reason or "", rs.skip_reason or "")
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Snippet: {rs.result.snippet}\n"
                f"[Kein Volltext: {reason}]"
            )

        parts.append(block)

    return "\n---\n".join(parts) if parts else "Keine Suchergebnisse gefunden."


def _adaptive_max_results(claim: Claim) -> int:
    """Bestimme die Anzahl der Suchergebnisse pro Query adaptiv.

    Einfache Claims brauchen weniger Ergebnisse, komplexe mehr.
    """
    if claim.type.value == "STATISTICAL":
        return 10  # Brauche mehr Quellen für Zahlenverifikation
    if claim.type.value in ("CAUSAL", "CONTEXTUAL"):
        return 8  # Kontext-Suche braucht etwas mehr
    return 5  # FACTUAL: weniger Ergebnisse reichen meist


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

    def _check_cache(self, claim: Claim, context: str) -> FactCheckResult | None:
        """Cache-Lookup mit Kontext für kollisionsfreie Keys."""
        cached = self._cache_get(claim.text, context)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass  # Ungültiger Cache – neu berechnen
        return None

    def _resolve_queries(self, claim: Claim, context: str) -> list[str]:
        """LLM-optimierte Queries versuchen, Fallback auf regelbasierte Queries."""
        optimized = _optimize_queries_with_llm(claim, self.llm, original_text=context)
        if optimized:
            self._log(f"LLM-optimierte Queries: {optimized}")
            return optimized
        self._log("Fallback auf regelbasierte Queries")
        return _build_search_queries(claim, original_text=context)

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        claim: Claim = input_data

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        queries = self._resolve_queries(claim, context)
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

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        # Query-Optimierung im Thread-Pool (blockiert nicht den Event Loop)
        loop = asyncio.get_event_loop()
        queries = await loop.run_in_executor(
            None, self._resolve_queries, claim, context,
        )

        max_results = _adaptive_max_results(claim)
        categories = _categories_for_claim(claim)
        self._log(f"Suche (async) nach: {queries} (max_results={max_results}, categories={categories})")

        # Websuche + Faktencheck-DB parallel
        web_task = self.async_search.multi_search_async(
            queries, max_results=max_results, categories=categories,
        )
        db_task = self._query_factcheck_databases_async(claim)

        results_by_query, external_context = await asyncio.gather(
            web_task, db_task, return_exceptions=False
        )

        # Source Scraping Pipeline: Ranking → Scraping → Enriched Context
        ranked, scraped = await self._rank_and_scrape(results_by_query, claim)

        # Retry bei unzureichenden Ergebnissen (max. 1x)
        needs_retry, retry_reason = _evaluate_scrape_quality(ranked, scraped)
        if needs_retry:
            fallback_queries = _build_fallback_queries(claim, queries)
            if fallback_queries:
                self._log(
                    f"Retry: {retry_reason} — Fallback-Suche mit {len(fallback_queries)} "
                    f"alternativen Queries"
                )
                fallback_categories = "general,news" if categories == "general" else categories
                retry_results = await self.async_search.multi_search_async(
                    fallback_queries,
                    max_results=max_results,
                    categories=fallback_categories,
                )
                # Ergebnisse mergen (Original + Retry)
                merged = dict(results_by_query)
                merged.update(retry_results)

                ranked, scraped = await self._rank_and_scrape(merged, claim)

                retry_scrape = sum(1 for rs in ranked if rs.should_scrape)
                retry_success = sum(1 for s in scraped if s.fetch_success)
                self._log(
                    f"Retry abgeschlossen: {retry_scrape} scrapbar, "
                    f"{retry_success}/{len(scraped)} erfolgreich"
                )
            else:
                self._log(f"Retry: {retry_reason} — keine Fallback-Queries generierbar")

        search_context = _build_enriched_context(ranked, scraped)

        return self._fact_check_with_context(
            claim, search_context,
            original_text=context,
            external_factchecks=external_context,
        )

    async def _rank_and_scrape(
        self,
        results_by_query: dict[str, list],
        claim: Claim,
    ) -> tuple[list[RankedSource], list[ScrapedSource]]:
        """Ranke Suchergebnisse und scrape die relevantesten Quellen."""
        ranked = rank_sources(
            results_by_query, claim.text,
            max_scrape=self.config.search.scrape_top_n,
        )
        scrape_count = sum(1 for rs in ranked if rs.should_scrape)
        self._log(f"Scraping {scrape_count} von {len(ranked)} Quellen...")

        scraped = await scrape_sources(
            ranked, claim.text,
            max_concurrent=self.config.search.max_concurrent_searches,
            timeout=self.config.search.scrape_timeout,
        )
        success_count = sum(1 for s in scraped if s.fetch_success)
        self._log(f"Scraping abgeschlossen: {success_count}/{len(scraped)} erfolgreich")
        return ranked, scraped

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

        prompt = t("agents.fact_checker.system_prompt")
        raw = self._llm_structured(
            prompt, user_msg, FACT_CHECK_SCHEMA,
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

        self._cache_set(claim.text, result.model_dump(), original_text)
        self._log(f"Claim {claim.id}: {result.rating.value}")
        return result
