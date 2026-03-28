"""Fact Checker – Fassade über EvidenceBuilderAgent + CoVeProcessor + VerdictAgent.

Diese Klasse behält die bisherige öffentliche API für Abwärtskompatibilität:
    execute(claim, context) -> FactCheckResult

Intern delegiert sie an:
    1. EvidenceBuilderAgent  → EvidencePack
    2. CoVeProcessor         → CoVeTrace (optional)
    3. VerdictAgent          → FactCheckResult

Der Rückgabewert ist ein erweitertes FactCheckResult mit den neuen optionalen
Feldern evidence_pack, cove_trace, verdict_meta.

Bestehende Hilfsfunktionen (_build_search_queries, _optimize_queries_with_llm etc.)
bleiben erhalten, da sie von EvidenceBuilderAgent und ClaimProcessorAgent genutzt werden.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("fng.fact_checker")

from agents.base import BaseAgent
from i18n import t
from models.schemas import FACT_CHECK_SCHEMA, Claim, FactCheckResult, FactRating
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_scraper import ScrapedSource, scrape_sources

# ── Re-exports from agents.query_builder for backward compatibility ──────────
# All query-building functions and constants have been moved to agents/query_builder.py.
# They are re-exported here so that existing imports from agents.fact_checker continue to work.
from agents.query_builder import (  # noqa: F401
    SYSTEM_PROMPT,
    _ARTIFACT_TERMS,
    _QUERY_OPTIMIZER_PROMPT,
    _adaptive_max_results,
    _bind_number_to_context,
    _build_context_query,
    _build_enriched_context,
    _build_fallback_queries,
    _build_queries_for_underspecified_claim,
    _build_search_queries,
    _build_search_queries_from_profile,
    _categories_for_claim,
    _count_strong_anchors,
    _evaluate_scrape_quality,
    _is_current_state_claim,
    _optimize_queries_with_llm,
)


class FactCheckerAgent(BaseAgent):
    """Fassade über EvidenceBuilderAgent + CoVeProcessor + VerdictAgent.

    Öffentliche API unverändert: execute(claim) -> FactCheckResult.

    Intern:
        1. EvidenceBuilderAgent baut ein strukturiertes EvidencePack
        2. CoVeProcessor (wenn aktiviert) führt Chain-of-Verification durch
        3. VerdictAgent fällt das Urteil auf Basis des EvidencePack

    Trust Boundary:
        Rohe Webseiteninhalte verlassen den EvidenceBuilderAgent nie.
        Der VerdictAgent sieht nur das strukturierte EvidencePack.
    """

    name = "Fact Checker"
    emoji = "✅"

    def __init__(self, *args, **kwargs) -> None:
        llm_small = kwargs.pop("llm_small", None)
        super().__init__(*args, **kwargs)
        # Lazy-Imports um Zirkelimporte zu vermeiden
        from agents.evidence_builder import EvidenceBuilderAgent
        from agents.verdict_agent import VerdictAgent
        from agents.cove_processor import CoVeProcessor

        _llm_small = llm_small or self.llm
        # EvidenceBuilderAgent: Gemma 4B (reine Query-Optimierung, kein tiefes Reasoning)
        self._evidence_builder = EvidenceBuilderAgent(self.config, _llm_small, self.search)
        # VerdictAgent: bleibt auf 27B (Herzstück des Faktenchecks)
        self._verdict_agent = VerdictAgent(self.config, self.llm, self.search, self.cache)
        # CoVeProcessor: llm_small für Fragen/Antworten (RAG), llm für Baseline/Reconciliation
        self._cove_processor = CoVeProcessor(
            llm=self.llm,
            llm_small=_llm_small,
            config=self.config.cove,
        )

    def _check_cache(self, claim: Claim, context: str) -> FactCheckResult | None:
        """Cache-Lookup mit Kontext für kollisionsfreie Keys."""
        cached = self._cache_get(claim.text, context)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass
        return None

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Synchrone Fact-Check-Pipeline: EvidenceBuilder → CoVe → VerdictAgent."""
        claim: Claim = input_data

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        # ── 1. Evidence Builder ───────────────────────────────────────────────
        pack, pack_error = self._evidence_builder.run_safe(claim, context=context)
        if pack_error or pack is None:
            self._log(f"EvidenceBuilder fehlgeschlagen: {pack_error}")
            # Fallback: Legacy-Pfad
            return self._legacy_fact_check(claim, context)

        # ── 2. CoVe (optional) ────────────────────────────────────────────────
        cove_trace = None
        if self.config.cove.enabled:
            try:
                cove_trace = self._cove_processor.process(claim, pack)
            except Exception as e:
                self._log(f"CoVe fehlgeschlagen: {type(e).__name__}: {e}")

        # ── 3. VerdictAgent ───────────────────────────────────────────────────
        result, verdict_error = self._verdict_agent.run_safe(
            {"claim": claim, "evidence_pack": pack, "cove_trace": cove_trace},
            context=context,
        )
        if verdict_error or result is None:
            self._log(f"VerdictAgent fehlgeschlagen: {verdict_error}")
            return self._legacy_fact_check(claim, context)

        self._cache_set(claim.text, result.model_dump(exclude={"evidence_pack", "cove_trace", "verdict_meta"}), context)
        return result

    async def execute_async(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Async Fact-Check-Pipeline."""
        import asyncio
        claim: Claim = input_data

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        # ── 1. Evidence Builder (async) ───────────────────────────────────────
        try:
            pack = await self._evidence_builder.execute_async(claim, context=context)
        except Exception as e:
            self._log(f"EvidenceBuilder async fehlgeschlagen: {type(e).__name__}: {e}")
            return await self._legacy_fact_check_async(claim, context)

        # ── 2. CoVe im Thread-Pool (blockiert nicht den Event Loop) ──────────
        cove_trace = None
        if self.config.cove.enabled:
            try:
                loop = asyncio.get_event_loop()
                cove_trace = await loop.run_in_executor(
                    None, self._cove_processor.process, claim, pack
                )
            except Exception as e:
                self._log(f"CoVe fehlgeschlagen: {type(e).__name__}: {e}")

        # ── 3. VerdictAgent im Thread-Pool ────────────────────────────────────
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._verdict_agent.execute,
                {"claim": claim, "evidence_pack": pack, "cove_trace": cove_trace},
                context,
            )
        except Exception as e:
            self._log(f"VerdictAgent async fehlgeschlagen: {type(e).__name__}: {e}")
            return await self._legacy_fact_check_async(claim, context)

        self._cache_set(claim.text, result.model_dump(exclude={"evidence_pack", "cove_trace", "verdict_meta"}), context)
        return result

    # ── Legacy-Pfad (Fallback wenn neue Pipeline fehlschlägt) ─────────────────

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
            from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
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
            from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
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
