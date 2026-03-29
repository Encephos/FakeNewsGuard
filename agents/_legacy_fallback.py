"""Legacy-Fallback-Pfad für FactCheckerAgent.

Enthält den monolithischen Pre-v2-Fact-Check-Pfad:
direkte Websuche → Scraping → LLM-Verdict in einem Durchlauf.

Dieser Pfad wird ausgelöst, wenn die v2-Pipeline
(EvidenceBuilderAgent → CoVeProcessor → VerdictAgent) fehlschlägt.
Er ist als Mixin gekapselt, damit FactCheckerAgent schlank und lesbar bleibt.

Kann entfernt werden, sobald die v2-Pipeline produktionsstabil ist.
"""

from __future__ import annotations

from i18n import t
from models.schemas import FACT_CHECK_SCHEMA, Claim, FactCheckResult, FactRating
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_scraper import ScrapedSource, scrape_sources
from agents.query_builder import (
    _adaptive_max_results,
    _build_enriched_context,
    _build_search_queries,
    _categories_for_claim,
    _optimize_queries_with_llm,
)


# FALLBACK – Compatibility Layer: Wird nur aufgerufen wenn die v2-Pipeline
# (EvidenceBuilderAgent → CoVeProcessor → VerdictAgent) fehlschlägt.
# Entfernen sobald v2-Pipeline produktionsstabil ist.
class _LegacyFallbackMixin:
    """Legacy-Fallback-Methoden für FactCheckerAgent.

    Nicht direkt instanziieren – wird von FactCheckerAgent geerbt.
    Alle Methoden setzen die BaseAgent-Attribute (llm, config, search, …) voraus.
    """

    def _legacy_fact_check(self, claim: Claim, context: str) -> FactCheckResult:
        """Legacy-Fact-Check-Pfad ohne EvidenceBuilder."""
        self._log(f"Legacy-Pfad für {claim.id}")
        queries = self._resolve_queries(claim, context)
        max_results = _adaptive_max_results(claim)
        search_context = self._web_multi_search(queries, max_results=max_results)

        # Externe Faktencheck-DBs
        external_context = self._query_factcheck_databases_legacy(claim)

        return self._fact_check_with_context(
            claim, search_context,
            original_text=context,
            external_factchecks=external_context,
        )

    async def _legacy_fact_check_async(self, claim: Claim, context: str) -> FactCheckResult:
        """Async Legacy-Pfad."""
        import asyncio
        self._log(f"Legacy-Pfad (async) für {claim.id}")

        loop = asyncio.get_event_loop()
        queries = await loop.run_in_executor(None, self._resolve_queries, claim, context)
        max_results = _adaptive_max_results(claim)
        categories = _categories_for_claim(claim)

        web_task = self.async_search.multi_search_async(
            queries, max_results=max_results, categories=categories,
        )
        db_task = self._query_factcheck_databases_async_legacy(claim)
        results_by_query, external_context = await asyncio.gather(
            web_task, db_task, return_exceptions=False
        )

        ranked, scraped = await self._rank_and_scrape(results_by_query, claim)
        search_context = _build_enriched_context(ranked, scraped)

        return self._fact_check_with_context(
            claim, search_context,
            original_text=context,
            external_factchecks=external_context,
        )

    def _resolve_queries(self, claim: Claim, context: str) -> list[str]:
        optimized = _optimize_queries_with_llm(claim, self.llm, original_text=context)
        if optimized:
            self._log(f"LLM-optimierte Queries: {optimized}")
            return optimized
        return _build_search_queries(claim, original_text=context)

    def _query_factcheck_databases_legacy(self, claim: Claim) -> str:
        try:
            client = FactCheckDatabaseClient(
                config=FactCheckDatabaseConfig(
                    google_factcheck_api_key=self.config.google_fact_check.api_key,
                    enabled=self.config.google_fact_check.enabled,
                ),
                retry=self.config.retry,
            )
            results = client.search(claim.text)
            if results:
                self._log(f"{len(results)} externe Faktenchecks gefunden")
            return FactCheckDatabaseClient.format_for_llm(results)
        except Exception as e:
            self._log(f"Faktencheck-DB Fehler: {type(e).__name__}: {e}")
            return ""

    async def _query_factcheck_databases_async_legacy(self, claim: Claim) -> str:
        try:
            client = FactCheckDatabaseClient(
                config=FactCheckDatabaseConfig(
                    google_factcheck_api_key=self.config.google_fact_check.api_key,
                    enabled=self.config.google_fact_check.enabled,
                ),
                retry=self.config.retry,
            )
            results = await client.search_async(claim.text)
            return FactCheckDatabaseClient.format_for_llm(results)
        except Exception as e:
            self._log(f"Faktencheck-DB Fehler: {type(e).__name__}: {e}")
            return ""

    async def _rank_and_scrape(
        self,
        results_by_query: dict[str, list],
        claim: Claim,
    ) -> tuple[list[RankedSource], list[ScrapedSource]]:
        ranked = rank_sources(
            results_by_query, claim.text,
            max_scrape=self.config.search.scrape_top_n,
        )
        scraped = await scrape_sources(
            ranked, claim.text,
            max_concurrent=self.config.search.max_concurrent_searches,
            timeout=self.config.search.scrape_timeout,
        )
        return ranked, scraped

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
        if external_factchecks:
            user_msg += f"\n{external_factchecks}\n"
        if original_text:
            truncated = original_text[:800]
            if len(original_text) > 800:
                truncated += "…"
            user_msg += f"\n## Originaltext (Gesamtkontext)\n\n{truncated}\n"
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
