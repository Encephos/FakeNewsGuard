"""Live retrieval runner — production-path evaluation.

Executes real search queries against SearXNG (and optionally LangSearch,
Google Fact Check), using the **same** query-building, routing, ranking,
and evidence-scoring logic as the production pipeline.  Captures rich
snapshots (incl. evidence_items + quality_signals) for future replay
and computes all retrieval metrics.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from eval.dataset import build_live_claim, filter_cases, load_cases
from eval.metrics import check_expectations, compute_case_metrics
from eval.models import CaseMetrics, CaseResult, EvalCase, Violation
from eval.retrieval import (
    build_lite_evidence_items,
    build_production_queries,
    compute_quality_signals,
)
from eval.snapshot import (
    RetrievalSnapshot,
    ranked_source_to_dict,
    save_snapshot,
    search_result_to_dict,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_SNAPSHOTS_DIR = Path(__file__).parent / "snapshots" / "live"


class LiveRunner:
    """Run live retrieval against real search backends and capture snapshots."""

    def __init__(
        self,
        config: "AppConfig",  # noqa: F821
        cases_path: Optional[Path] = None,
        snapshots_dir: Optional[Path] = None,
    ):
        self.config = config
        self.cases_path = cases_path or _DATA_DIR / "cases.jsonl"
        self.snapshots_dir = snapshots_dir or _SNAPSHOTS_DIR

    async def run(
        self,
        categories: Optional[list[str]] = None,
        case_ids: Optional[list[str]] = None,
        backends: tuple[str, ...] = ("searxng",),
        save_snapshots: bool = True,
    ) -> list[CaseResult]:
        """Run live retrieval for all cases.

        Args:
            categories: Filter by category names.
            case_ids: Filter by case IDs.
            backends: Which backends to use ("searxng", "langsearch", "gfc").
            save_snapshots: Whether to save snapshots to disk.

        Returns:
            List of CaseResult with metrics and violations.
        """
        cases = load_cases(self.cases_path)
        cases = filter_cases(cases, categories=categories, ids=case_ids)

        results: list[CaseResult] = []
        for case in cases:
            logger.info("Live eval: %s (%s)", case.id, case.category.value)
            try:
                result = await self._evaluate_case(case, backends, save_snapshots)
                results.append(result)
            except Exception as exc:
                logger.error("Case %s failed: %s", case.id, exc, exc_info=True)
                results.append(CaseResult(
                    case_id=case.id,
                    category=case.category,
                    metrics=CaseMetrics(),
                    violations=[Violation(
                        metric="execution",
                        expected="success",
                        actual=str(exc),
                        severity="error",
                    )],
                    passed=False,
                ))

        return results

    async def _evaluate_case(
        self,
        case: EvalCase,
        backends: tuple[str, ...],
        save: bool,
    ) -> CaseResult:
        """Run live retrieval for a single case using the production path."""
        from agents.evidence_builder import _dedup_queries
        from agents.query_builder import _is_current_state_claim
        from tools.scrape_ranker import rank_sources
        from tools.web_search import SearchResult

        notes: list[str] = []

        # --- 1. Build claim via production routing -------------------------
        claim, route_result = build_live_claim(case)
        if route_result:
            notes.append(
                f"Routed: domains={[d.value for d in route_result.domains]}, "
                f"jurisdiction={route_result.jurisdiction}, "
                f"confidence={route_result.confidence:.2f}"
            )
        else:
            notes.append("Routing failed or skipped; using base claim")

        # --- 2. Generate queries via production path -----------------------
        queries, searxng_queries = build_production_queries(
            claim, route_result, self.config,
        )
        notes.append(f"Queries ({len(queries)}): {queries[:4]}")

        # Plain deduped queries for snapshot
        deduped = _dedup_queries(queries)

        # --- 3. Execute searches against real backends ---------------------
        searxng_results: dict[str, list[SearchResult]] = {}
        langsearch_results: dict[str, list[SearchResult]] = {}
        gfc_results: list[dict] = []

        if "searxng" in backends:
            searxng_results = await self._search_searxng(searxng_queries)
            notes.append(f"SearXNG: {sum(len(v) for v in searxng_results.values())} results")

        if "langsearch" in backends:
            langsearch_results = await self._search_langsearch(deduped)
            notes.append(f"LangSearch: {sum(len(v) for v in langsearch_results.values())} results")

        if "gfc" in backends:
            gfc_results = await self._search_gfc(case.claim_text)
            notes.append(f"GFC: {len(gfc_results)} matches")

        # --- 4. Merge results (LangSearch priority for dedup) --------------
        all_results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for query_results in langsearch_results.values():
            for r in query_results:
                norm_url = r.url.rstrip("/").lower()
                if norm_url not in seen_urls:
                    seen_urls.add(norm_url)
                    all_results.append(r)

        for query_results in searxng_results.values():
            for r in query_results:
                norm_url = r.url.rstrip("/").lower()
                if norm_url not in seen_urls:
                    seen_urls.add(norm_url)
                    all_results.append(r)

        notes.append(f"Merged: {len(all_results)} unique results")

        # --- 5. Rank sources -----------------------------------------------
        results_by_query: dict[str, list[SearchResult]] = {"merged": all_results}
        ranked = rank_sources(
            results_by_query,
            case.claim_text,
            max_scrape=10,
            profile=claim.search_profile,
        )
        ranked_dicts = [ranked_source_to_dict(r) for r in ranked]

        # --- 6. Build lite evidence items (snippet-based) ------------------
        is_current_state = _is_current_state_claim(case.claim_text)
        evidence_items = build_lite_evidence_items(
            ranked_dicts, case.claim_text, claim.search_profile,
        )
        notes.append(
            f"Evidence items: {len(evidence_items)} "
            f"(direct={sum(1 for i in evidence_items if i.get('evidence_type') == 'direct')}, "
            f"contextual={sum(1 for i in evidence_items if i.get('evidence_type') == 'contextual')}, "
            f"weak={sum(1 for i in evidence_items if i.get('evidence_type') == 'weak')})"
        )

        # --- 7. Compute quality signals ------------------------------------
        quality_signals = compute_quality_signals(
            evidence_items, gfc_results, is_current_state=is_current_state,
        )

        # --- 8. Build snapshot ---------------------------------------------
        snapshot = RetrievalSnapshot(
            case_id=case.id,
            generated_queries=queries,
            deduped_queries=deduped,
            searxng_queries=[
                {"query": sq.query, "pageno": sq.pageno,
                 "categories": sq.categories, "engines": sq.engines}
                for sq in searxng_queries
            ],
            searxng_results={
                q: [search_result_to_dict(r) for r in rs]
                for q, rs in searxng_results.items()
            },
            langsearch_results={
                q: [search_result_to_dict(r) for r in rs]
                for q, rs in langsearch_results.items()
            },
            gfc_results=gfc_results,
            source_client_results=[],
            merged_results=[search_result_to_dict(r) for r in all_results],
            ranked_sources=ranked_dicts,
            evidence_items=evidence_items,
            quality_signals=quality_signals,
            route_result=(
                route_result.model_dump()
                if route_result and hasattr(route_result, "model_dump")
                else vars(route_result) if route_result else None
            ),
            backends_used=list(backends),
            debug_notes=notes,
        )

        if save:
            save_snapshot(snapshot, self.snapshots_dir)
            logger.info("Snapshot saved for %s", case.id)

        # --- 9. Compute metrics and check expectations ---------------------
        metrics = compute_case_metrics(snapshot, case)
        violations = check_expectations(metrics, case.expectations)

        return CaseResult(
            case_id=case.id,
            category=case.category,
            metrics=metrics,
            violations=violations,
            passed=len([v for v in violations if v.severity == "error"]) == 0,
        )

    # -- Search backend helpers ---------------------------------------------

    async def _search_searxng(
        self,
        queries: list,
    ) -> dict[str, list]:
        """Execute SearXNGQuery objects against SearXNG."""
        from tools.search.searxng import SearXNGClient
        client = SearXNGClient(self.config.searxng, self.config.retry)
        return await client.multi_search_async(
            queries, max_results=self.config.searxng.max_results,
        )

    async def _search_langsearch(
        self,
        queries: list[str],
    ) -> dict[str, list]:
        """Execute queries against LangSearch."""
        from tools.search.langsearch import LangSearchClient
        client = LangSearchClient(self.config.langsearch, self.config.retry)
        return await client.multi_search_async(queries[:4])

    async def _search_gfc(
        self,
        claim_text: str,
    ) -> list[dict]:
        """Search Google Fact Check API."""
        from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
        client = FactCheckDatabaseClient(
            config=FactCheckDatabaseConfig(
                google_factcheck_api_key=self.config.google_fact_check.api_key,
                enabled=self.config.google_fact_check.enabled,
            ),
            retry=self.config.retry,
        )
        results = await client.search_async(claim_text)
        return [
            {
                "claim_reviewed": fc.claim_reviewed,
                "rating": fc.rating,
                "publisher": fc.publisher,
                "url": fc.url,
                "language": fc.language,
                "title": fc.title,
            }
            for fc in results
        ]
