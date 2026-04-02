"""Async benchmark runner – feeds items through the Orchestrator pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.models import BenchmarkItem, BenchmarkRunResult

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Run benchmark items through the FakeNewsGuard pipeline."""

    def __init__(
        self,
        results_dir: Path,
        tier: str = "pro",
        max_parallel: int = 1,
        delay: float = 2.0,
        verbose: bool = False,
    ) -> None:
        self.results_dir = results_dir
        self.tier = tier
        self.max_parallel = max_parallel
        self.delay = delay
        self.verbose = verbose
        self._orchestrator = None

    def _build_orchestrator(self):
        """Lazy-init the Orchestrator (avoids import at module level)."""
        if self._orchestrator is not None:
            return self._orchestrator

        from config import AppConfig, ScoutTier
        from orchestrator import Orchestrator

        tier_map = {"lite": ScoutTier.LITE, "pro": ScoutTier.PRO, "max": ScoutTier.MAX}
        scout_tier = tier_map.get(self.tier, ScoutTier.PRO)

        config = AppConfig(tier=scout_tier, verbose=self.verbose)
        self._orchestrator = Orchestrator(config)
        return self._orchestrator

    async def run_all(
        self,
        items: list[BenchmarkItem],
        resume: bool = True,
    ) -> list[BenchmarkRunResult]:
        """Run all benchmark items, optionally resuming from previous run."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        orchestrator = self._build_orchestrator()

        # Determine which items to run
        pending = []
        skipped = 0
        for item in items:
            result_path = self.results_dir / f"{item.id}.json"
            if resume and result_path.exists():
                skipped += 1
                continue
            pending.append(item)

        if skipped:
            logger.info("Resuming: skipped %d already-completed items", skipped)

        total = len(pending)
        if total == 0:
            logger.info("All items already completed.")
            return self._load_existing_results(items)

        logger.info(
            "Running %d items (tier=%s, parallel=%d, delay=%.1fs)",
            total, self.tier, self.max_parallel, self.delay,
        )

        semaphore = asyncio.Semaphore(self.max_parallel)
        completed = 0

        async def _run_with_semaphore(item: BenchmarkItem) -> BenchmarkRunResult:
            nonlocal completed
            async with semaphore:
                result = await self._run_single(orchestrator, item)
                self._save_result(result)
                completed += 1
                self._print_progress(completed, total, item, result)
                if self.delay > 0:
                    await asyncio.sleep(self.delay)
                return result

        results = await asyncio.gather(
            *[_run_with_semaphore(item) for item in pending],
            return_exceptions=True,
        )

        # Collect valid results
        final: list[BenchmarkRunResult] = []
        for r in results:
            if isinstance(r, BenchmarkRunResult):
                final.append(r)
            elif isinstance(r, Exception):
                logger.error("Unexpected error: %s", r)

        return final

    async def _run_single(
        self,
        orchestrator,
        item: BenchmarkItem,
    ) -> BenchmarkRunResult:
        """Run a single benchmark item through the pipeline."""
        t0 = time.monotonic()
        try:
            synthesis = await orchestrator.analyze_async(item.text)
            duration = time.monotonic() - t0
            return BenchmarkRunResult(
                item_id=item.id,
                tier=self.tier,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=round(duration, 2),
                synthesis_result=synthesis.model_dump(mode="json"),
                overall_rating=synthesis.overall_rating.value,
                confidence=synthesis.confidence,
                num_claims=len(synthesis.claims_analysis),
                num_errors=len(synthesis.analysis_errors),
            )
        except Exception as exc:
            duration = time.monotonic() - t0
            logger.error("Item %s failed: %s", item.id, exc)
            return BenchmarkRunResult(
                item_id=item.id,
                tier=self.tier,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=round(duration, 2),
                error=str(exc),
            )

    def _save_result(self, result: BenchmarkRunResult) -> None:
        """Persist a single result to disk."""
        path = self.results_dir / f"{result.item_id}.json"
        path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _load_existing_results(self, items: list[BenchmarkItem]) -> list[BenchmarkRunResult]:
        """Load all existing results from disk."""
        results = []
        for item in items:
            path = self.results_dir / f"{item.id}.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append(BenchmarkRunResult(**data))
        return results

    def _print_progress(
        self,
        completed: int,
        total: int,
        item: BenchmarkItem,
        result: BenchmarkRunResult,
    ) -> None:
        """Print progress line to stderr."""
        if result.error:
            status = f"ERROR: {result.error[:60]}"
        else:
            status = f"{result.overall_rating} ({result.confidence:.2f})"
        print(
            f"  [{completed:>3}/{total}] {item.id} "
            f"({item.difficulty.value}/{item.category.value}) "
            f"→ {status} "
            f"{result.duration_seconds:.1f}s",
            file=sys.stderr,
        )


def load_results_from_dir(results_dir: Path) -> list[BenchmarkRunResult]:
    """Load all result JSON files from a directory."""
    results = []
    for path in sorted(results_dir.glob("de-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        results.append(BenchmarkRunResult(**data))
    return results
