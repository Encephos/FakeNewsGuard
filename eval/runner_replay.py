"""Deterministic replay runner.

Re-runs scoring, ranking, and routing functions on stored snapshots
without network access. Catches regressions in deterministic pipeline logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from eval.dataset import build_processed_claim, filter_cases, load_cases
from eval.metrics import check_expectations, compute_case_metrics
from eval.models import CaseResult, EvalCase
from eval.snapshot import (
    RetrievalSnapshot,
    dict_to_search_result,
    load_snapshot,
    snapshot_exists,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"


class ReplayRunner:
    """Run deterministic evaluation on stored retrieval snapshots."""

    def __init__(
        self,
        cases_path: Optional[Path] = None,
        snapshots_dir: Optional[Path] = None,
    ):
        self.cases_path = cases_path or _DATA_DIR / "cases.jsonl"
        self.snapshots_dir = snapshots_dir or _SNAPSHOTS_DIR

    def run(
        self,
        categories: Optional[list[str]] = None,
        case_ids: Optional[list[str]] = None,
    ) -> list[CaseResult]:
        """Run replay evaluation on all cases with available snapshots.

        Returns a CaseResult per evaluated case.
        Skips cases without snapshots (logs a warning).
        """
        cases = load_cases(self.cases_path)
        cases = filter_cases(cases, categories=categories, ids=case_ids)

        results: list[CaseResult] = []
        for case in cases:
            if not snapshot_exists(case.id, self.snapshots_dir):
                logger.warning("No snapshot for case %s — skipping", case.id)
                continue
            result = self._evaluate_case(case)
            results.append(result)

        return results

    def _evaluate_case(self, case: EvalCase) -> CaseResult:
        """Evaluate a single case against its stored snapshot."""
        snapshot = load_snapshot(case.id, self.snapshots_dir)

        # Re-run deterministic pipeline functions on stored data
        snapshot = self._recompute_deterministic(snapshot, case)

        # Compute metrics
        metrics = compute_case_metrics(snapshot, case)

        # Check expectations
        violations = check_expectations(metrics, case.expectations)

        return CaseResult(
            case_id=case.id,
            category=case.category,
            metrics=metrics,
            violations=violations,
            passed=len([v for v in violations if v.severity == "error"]) == 0,
        )

    def _recompute_deterministic(
        self,
        snapshot: RetrievalSnapshot,
        case: EvalCase,
    ) -> RetrievalSnapshot:
        """Re-run deterministic functions on snapshot data.

        This catches regressions in:
        - Query deduplication logic
        - Claim routing / hint generation
        - Source ranking
        - Quality signal computation
        """
        # 1. Re-run query dedup
        if snapshot.generated_queries:
            from agents.evidence_builder import _dedup_queries
            recomputed_deduped = _dedup_queries(snapshot.generated_queries)
            if recomputed_deduped != snapshot.deduped_queries:
                logger.info(
                    "Case %s: query dedup changed (%d → %d queries)",
                    case.id, len(snapshot.deduped_queries), len(recomputed_deduped),
                )
                snapshot = snapshot.model_copy(
                    update={"deduped_queries": recomputed_deduped}
                )

        # 2. Re-run claim routing
        try:
            claim = build_processed_claim(case)
            from tools.claim_router import ClaimRouter
            router = ClaimRouter()
            route_result = router.route(claim)
            snapshot = snapshot.model_copy(
                update={"route_result": route_result.model_dump()
                        if hasattr(route_result, "model_dump")
                        else vars(route_result)}
            )
        except Exception as exc:
            logger.warning("Case %s: routing failed: %s", case.id, exc)

        # 3. Re-run ranking on merged results
        if snapshot.merged_results:
            try:
                from tools.scrape_ranker import rank_sources
                from eval.snapshot import ranked_source_to_dict

                # Reconstruct results_by_query from merged
                results_by_query: dict[str, list] = {"merged": [
                    dict_to_search_result(r) for r in snapshot.merged_results
                ]}
                profile = claim.search_profile if 'claim' in dir() else None
                ranked = rank_sources(
                    results_by_query,
                    case.claim_text,
                    max_scrape=10,
                    profile=profile,
                )
                snapshot = snapshot.model_copy(
                    update={"ranked_sources": [ranked_source_to_dict(r) for r in ranked]}
                )
            except Exception as exc:
                logger.warning("Case %s: re-ranking failed: %s", case.id, exc)

        # 3b. Re-compute evidence items from re-ranked sources
        if snapshot.ranked_sources:
            try:
                from eval.retrieval import build_lite_evidence_items
                profile = claim.search_profile if 'claim' in dir() else None
                evidence_items = build_lite_evidence_items(
                    snapshot.ranked_sources, case.claim_text, profile,
                )
                if evidence_items:
                    snapshot = snapshot.model_copy(
                        update={"evidence_items": evidence_items}
                    )
            except Exception as exc:
                logger.warning("Case %s: evidence item recompute failed: %s", case.id, exc)

        # 4. Re-run quality signal computation on evidence items
        if snapshot.evidence_items:
            try:
                from agents.evidence_scoring import _compute_quality_signals
                from models.evidence_models import EvidenceItem, GoogleFactCheckMatch

                items = [EvidenceItem.model_validate(d) for d in snapshot.evidence_items]
                gfc = [GoogleFactCheckMatch.model_validate(d) for d in snapshot.gfc_results]
                is_current = case.expectations.requires_recency
                signals = _compute_quality_signals(
                    items, gfc, is_current_state=is_current,
                )
                snapshot = snapshot.model_copy(
                    update={"quality_signals": signals.model_dump()}
                )
            except Exception as exc:
                logger.warning("Case %s: quality signal recompute failed: %s", case.id, exc)

        return snapshot
