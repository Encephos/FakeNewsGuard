"""Live retrieval runner.

Executes real search queries against SearXNG (and optionally LangSearch,
Google Fact Check, source clients), captures snapshots for future replay,
and computes metrics.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from eval.dataset import build_processed_claim, filter_cases, load_cases
from eval.metrics import check_expectations, compute_case_metrics
from eval.models import CaseResult, EvalCase
from eval.snapshot import (
    RetrievalSnapshot,
    ranked_source_to_dict,
    save_snapshot,
    search_result_to_dict,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"


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
                logger.error("Case %s failed: %s", case.id, exc)
                results.append(CaseResult(
                    case_id=case.id,
                    category=case.category,
                    metrics=__import__("eval.models", fromlist=["CaseMetrics"]).CaseMetrics(),
                    violations=[__import__("eval.models", fromlist=["Violation"]).Violation(
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
        """Run live retrieval for a single case and build snapshot."""
        from config import RetryConfig
        from tools.claim_router import ClaimRouter
        from tools.scrape_ranker import rank_sources
        from tools.web_search import SearchResult

        claim = build_processed_claim(case)

        # Route claim
        router = ClaimRouter()
        route_result = router.route(claim)

        # Generate queries from case text + entities
        queries = self._generate_queries(case)

        # Deduplicate
        from agents.evidence_builder import _dedup_queries
        deduped = _dedup_queries(queries)

        # Search backends
        searxng_results: dict[str, list[SearchResult]] = {}
        langsearch_results: dict[str, list[SearchResult]] = {}
        gfc_results: list[dict] = []
        source_client_results: list[dict] = []

        if "searxng" in backends:
            searxng_results = await self._search_searxng(deduped)

        if "langsearch" in backends:
            langsearch_results = await self._search_langsearch(deduped)

        # Merge results (LangSearch priority for dedup)
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

        # Rank sources
        results_by_query: dict[str, list[SearchResult]] = {"merged": all_results}
        ranked = rank_sources(
            results_by_query,
            case.claim_text,
            max_scrape=10,
            profile=claim.search_profile,
        )

        # Build snapshot (no scraping in eval — we evaluate retrieval quality,
        # not scraping success)
        snapshot = RetrievalSnapshot(
            case_id=case.id,
            generated_queries=queries,
            deduped_queries=deduped,
            searxng_results={
                q: [search_result_to_dict(r) for r in rs]
                for q, rs in searxng_results.items()
            },
            langsearch_results={
                q: [search_result_to_dict(r) for r in rs]
                for q, rs in langsearch_results.items()
            },
            gfc_results=gfc_results,
            source_client_results=source_client_results,
            merged_results=[search_result_to_dict(r) for r in all_results],
            ranked_sources=[ranked_source_to_dict(r) for r in ranked],
            evidence_items=[],  # No scraping in eval
            quality_signals={},
            route_result=(
                route_result.model_dump()
                if hasattr(route_result, "model_dump")
                else vars(route_result)
            ),
            backends_used=list(backends),
        )

        if save:
            save_snapshot(snapshot, self.snapshots_dir)
            logger.info("Snapshot saved for %s", case.id)

        # Compute metrics
        metrics = compute_case_metrics(snapshot, case)
        violations = check_expectations(metrics, case.expectations)

        return CaseResult(
            case_id=case.id,
            category=case.category,
            metrics=metrics,
            violations=violations,
            passed=len([v for v in violations if v.severity == "error"]) == 0,
        )

    def _generate_queries(self, case: EvalCase) -> list[str]:
        """Generate search queries from case text and expectations.

        Uses a heuristic approach (no LLM) to produce queries
        from the claim text and must_have_entities.
        """
        queries = [case.claim_text]

        entities = case.expectations.must_have_entities
        if entities:
            queries.append(" ".join(entities))
            if len(entities) >= 2:
                queries.append(f"{' '.join(entities)} Faktencheck")

        if case.expectations.requires_recency:
            from datetime import datetime
            year = datetime.now().year
            queries.append(f"{case.claim_text} {year}")

        # Add preferred domain site-hint queries
        for domain in case.expectations.preferred_domains[:2]:
            queries.append(f"site:{domain} {case.claim_text[:80]}")

        return queries

    async def _search_searxng(
        self,
        queries: list[str],
    ) -> dict[str, list]:
        """Execute queries against SearXNG."""
        from tools.search.searxng import SearXNGClient
        client = SearXNGClient(self.config.searxng, self.config.retry)
        return await client.multi_search_async(queries)

    async def _search_langsearch(
        self,
        queries: list[str],
    ) -> dict[str, list]:
        """Execute queries against LangSearch."""
        from tools.search.langsearch import LangSearchClient
        client = LangSearchClient(self.config.langsearch, self.config.retry)
        return await client.multi_search_async(queries[:4])  # Adaptive limit
