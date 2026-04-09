"""Threshold calibration via grid search on eval snapshots.

Replays quality-signal computation and verdict calibration logic across
stored snapshots with varying threshold configurations. Reports which
parameter combinations optimize Brier score, verdict accuracy, and
overconfidence rate.

Usage:
    .venv/bin/python3 -m eval.calibrate_thresholds [--split 0.7] [--output calibration_report.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eval.dataset import build_processed_claim, load_cases
from eval.metrics import _verdict_distance
from eval.snapshot import RetrievalSnapshot, load_snapshot, snapshot_exists

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"


# ── Calibration parameter space ─────────────────────────────────────────────


@dataclass
class ThresholdGrid:
    """Defines the search space for threshold calibration.

    Each field is a list of candidate values. The calibrator performs
    grid search over the Cartesian product of all fields.
    """

    # VerdictCalibrationConfig thresholds
    ceiling_no_primary_source: list[float] = field(
        default_factory=lambda: [0.82, 0.85, 0.88, 0.92]
    )
    ceiling_weak_evidence: list[float] = field(
        default_factory=lambda: [0.65, 0.70, 0.75, 0.80]
    )
    ceiling_insufficient_consensus: list[float] = field(
        default_factory=lambda: [0.65, 0.70, 0.75, 0.80]
    )

    # SynthesizerConfig thresholds
    fabricated_min_refuted_ratio: list[float] = field(
        default_factory=lambda: [0.40, 0.50, 0.60]
    )
    rhetoric_floor_misleading: list[float] = field(
        default_factory=lambda: [0.50, 0.60, 0.70]
    )

    def total_combinations(self) -> int:
        sizes = [len(v) for v in asdict(self).values()]
        result = 1
        for s in sizes:
            result *= s
        return result


# ── Calibration result ──────────────────────────────────────────────────────


@dataclass
class CalibrationResult:
    """Metrics for a single threshold configuration."""

    params: dict[str, float]
    brier_score: float
    verdict_accuracy: float
    within_one_step_rate: float
    overconfidence_rate: float
    n_cases: int

    def score(self) -> float:
        """Composite score: lower is better.

        Weighted combination of Brier score (primary) and overconfidence.
        """
        return self.brier_score * 0.7 + self.overconfidence_rate * 0.3


# ── Core calibration logic ──────────────────────────────────────────────────


def _compute_brier_score(predicted_confidence: float, is_correct: bool) -> float:
    """Brier score for a single prediction."""
    outcome = 1.0 if is_correct else 0.0
    return (predicted_confidence - outcome) ** 2


def _simulate_verdict_calibration(
    quality_signals: dict[str, Any],
    params: dict[str, float],
) -> float:
    """Simulate confidence calibration with given thresholds.

    Applies ceiling rules to a base confidence of 0.80 (the median
    LLM-produced confidence) using the stored quality signals.
    Returns adjusted confidence.
    """
    confidence = 0.80  # typical LLM default

    has_primary = quality_signals.get("has_primary_source_any", False)
    has_fc = quality_signals.get("has_fact_check_any", False)
    overall_quality = quality_signals.get("overall_quality", 0.5)
    consensus = quality_signals.get("source_consensus", "insufficient")
    off_topic_rate = quality_signals.get("off_topic_rate", 0.0)
    low_trust_rate = quality_signals.get("low_trust_rate", 0.0)

    # Ceiling: no primary source
    if not has_primary and not has_fc:
        confidence = min(confidence, params.get("ceiling_no_primary_source", 0.88))

    # Ceiling: weak evidence
    if overall_quality < 0.3:
        confidence = min(confidence, params.get("ceiling_weak_evidence", 0.75))

    # Ceiling: insufficient consensus
    if consensus == "insufficient":
        confidence = min(confidence, params.get("ceiling_insufficient_consensus", 0.75))

    # Ceiling: off-topic contamination (fixed threshold)
    if off_topic_rate > 0.5:
        confidence = min(confidence, 0.75)

    # Ceiling: high low-trust rate
    if low_trust_rate > 0.3:
        confidence = min(confidence, 0.65)

    # Consensus strength bonus/penalty (from Concept 1)
    consensus_disagreement = quality_signals.get("consensus_disagreement", 0.0)
    consensus_n_signals = quality_signals.get("consensus_n_signals", 0)
    consensus_score = quality_signals.get("consensus_score", 0.0)
    if consensus_n_signals >= 3:
        if consensus_disagreement >= 0.60:
            confidence = min(confidence, 0.70)
        elif consensus_disagreement <= 0.15 and abs(consensus_score) >= 0.70:
            confidence += 0.05

    return max(0.0, min(1.0, confidence))


def _evaluate_config(
    cases_data: list[dict[str, Any]],
    params: dict[str, float],
) -> CalibrationResult:
    """Evaluate a single threshold configuration across all cases."""
    brier_scores: list[float] = []
    correct_count = 0
    within_one_count = 0
    overconfident_count = 0
    total = 0

    for case in cases_data:
        expected = case.get("expected_verdict")
        predicted = case.get("predicted_verdict")
        quality_signals = case.get("quality_signals", {})

        if not expected or not predicted:
            continue

        total += 1
        is_exact = _verdict_distance(predicted, expected) == 0
        is_within_one = _verdict_distance(predicted, expected) <= 1

        # Simulate confidence under these thresholds
        confidence = _simulate_verdict_calibration(quality_signals, params)

        # Brier score (using within-one-step as outcome for calibration)
        brier_scores.append(_compute_brier_score(confidence, is_within_one))

        if is_exact:
            correct_count += 1
        if is_within_one:
            within_one_count += 1

        # Overconfidence: high confidence but wrong verdict
        if confidence >= 0.70 and not is_within_one:
            overconfident_count += 1

    if total == 0:
        return CalibrationResult(
            params=params,
            brier_score=1.0,
            verdict_accuracy=0.0,
            within_one_step_rate=0.0,
            overconfidence_rate=1.0,
            n_cases=0,
        )

    return CalibrationResult(
        params=params,
        brier_score=sum(brier_scores) / len(brier_scores),
        verdict_accuracy=correct_count / total,
        within_one_step_rate=within_one_count / total,
        overconfidence_rate=overconfident_count / total if total > 0 else 0.0,
        n_cases=total,
    )


def _load_cases_with_snapshots(
    cases_path: Path,
    snapshots_dir: Path,
) -> list[dict[str, Any]]:
    """Load eval cases and their snapshots, returning merged dicts."""
    cases = load_cases(cases_path)
    result: list[dict[str, Any]] = []

    for case in cases:
        if not snapshot_exists(case.id, snapshots_dir):
            continue

        snapshot = load_snapshot(case.id, snapshots_dir)

        expected_verdict = case.expectations.expected_verdict_class
        if not expected_verdict:
            continue

        # Use stored quality signals from snapshot
        quality_signals = snapshot.quality_signals or {}

        result.append({
            "case_id": case.id,
            "category": case.category.value,
            "expected_verdict": expected_verdict,
            "predicted_verdict": expected_verdict,  # baseline: perfect prediction
            "quality_signals": quality_signals,
        })

    return result


def run_calibration(
    cases_path: Path | None = None,
    snapshots_dir: Path | None = None,
    grid: ThresholdGrid | None = None,
    train_split: float = 0.7,
    seed: int = 42,
) -> dict[str, Any]:
    """Run threshold calibration and return report."""
    cases_path = cases_path or _DATA_DIR / "cases.jsonl"
    snapshots_dir = snapshots_dir or _SNAPSHOTS_DIR
    grid = grid or ThresholdGrid()

    all_cases = _load_cases_with_snapshots(cases_path, snapshots_dir)
    if not all_cases:
        logger.warning("No cases with snapshots and expected verdicts found")
        return {"error": "no_data", "n_cases": 0}

    # Train/test split
    random.seed(seed)
    shuffled = list(all_cases)
    random.shuffle(shuffled)
    split_idx = max(1, int(len(shuffled) * train_split))
    train_cases = shuffled[:split_idx]
    test_cases = shuffled[split_idx:] if split_idx < len(shuffled) else shuffled

    logger.info(
        "Calibrating on %d train cases, %d test cases (%d total with verdicts)",
        len(train_cases), len(test_cases), len(all_cases),
    )

    # Grid search
    grid_dict = asdict(grid)
    param_names = list(grid_dict.keys())
    param_values = list(grid_dict.values())

    total_combos = grid.total_combinations()
    logger.info("Grid search over %d combinations", total_combos)

    results: list[CalibrationResult] = []
    for combo in itertools.product(*param_values):
        params = dict(zip(param_names, combo))
        result = _evaluate_config(train_cases, params)
        results.append(result)

    # Sort by composite score (lower is better)
    results.sort(key=lambda r: r.score())
    best = results[0]
    defaults = _evaluate_config(
        train_cases,
        {
            "ceiling_no_primary_source": 0.88,
            "ceiling_weak_evidence": 0.75,
            "ceiling_insufficient_consensus": 0.75,
            "fabricated_min_refuted_ratio": 0.50,
            "rhetoric_floor_misleading": 0.60,
        },
    )

    # Evaluate best config on test set
    test_result = _evaluate_config(test_cases, best.params)
    test_defaults = _evaluate_config(test_cases, defaults.params)

    # Sensitivity analysis: which parameters matter most
    sensitivity: dict[str, float] = {}
    for param_name in param_names:
        scores_by_value: dict[float, list[float]] = {}
        for r in results:
            val = r.params[param_name]
            scores_by_value.setdefault(val, []).append(r.score())
        if scores_by_value:
            means = [sum(s) / len(s) for s in scores_by_value.values()]
            sensitivity[param_name] = max(means) - min(means) if len(means) > 1 else 0.0

    report = {
        "n_total_cases": len(all_cases),
        "n_train": len(train_cases),
        "n_test": len(test_cases),
        "n_combinations_searched": total_combos,
        "best_config": {
            "params": best.params,
            "train_brier_score": round(best.brier_score, 4),
            "train_overconfidence_rate": round(best.overconfidence_rate, 4),
            "train_composite_score": round(best.score(), 4),
            "test_brier_score": round(test_result.brier_score, 4),
            "test_overconfidence_rate": round(test_result.overconfidence_rate, 4),
        },
        "default_config": {
            "params": defaults.params,
            "train_brier_score": round(defaults.brier_score, 4),
            "train_overconfidence_rate": round(defaults.overconfidence_rate, 4),
            "test_brier_score": round(test_defaults.brier_score, 4),
            "test_overconfidence_rate": round(test_defaults.overconfidence_rate, 4),
        },
        "improvement": {
            "brier_delta": round(defaults.brier_score - best.brier_score, 4),
            "overconfidence_delta": round(
                defaults.overconfidence_rate - best.overconfidence_rate, 4
            ),
        },
        "sensitivity_analysis": {
            k: round(v, 4) for k, v in sorted(
                sensitivity.items(), key=lambda x: -x[1]
            )
        },
        "top_5_configs": [
            {
                "params": r.params,
                "brier_score": round(r.brier_score, 4),
                "composite_score": round(r.score(), 4),
            }
            for r in results[:5]
        ],
    }

    return report


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Threshold calibration via grid search")
    parser.add_argument("--split", type=float, default=0.7, help="Train/test split ratio")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--cases", type=str, default=None, help="Cases JSONL path")
    parser.add_argument("--snapshots", type=str, default=None, help="Snapshots directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = run_calibration(
        cases_path=Path(args.cases) if args.cases else None,
        snapshots_dir=Path(args.snapshots) if args.snapshots else None,
        train_split=args.split,
    )

    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        logger.info("Report written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
