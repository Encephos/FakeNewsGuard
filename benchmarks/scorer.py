"""Score benchmark results against ground truth."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from benchmarks.models import (
    BenchmarkItem,
    BenchmarkRunResult,
    CategoryMetrics,
    ConfidenceBin,
    ConfidenceCalibration,
    DifficultyMetrics,
    GroundTruthLabel,
    ScoreReport,
)


# ── Rating Mappings ─────────────────────────────────────────────────

_BINARY_REAL = {"RELIABLE", "MOSTLY_RELIABLE"}
_BINARY_FAKE = {"MIXED", "MISLEADING", "HIGHLY_MISLEADING", "FABRICATED"}

_MULTICLASS_RELIABLE = {"RELIABLE", "MOSTLY_RELIABLE"}
_MULTICLASS_MIXED = {"MIXED"}
_MULTICLASS_UNRELIABLE = {"MISLEADING", "HIGHLY_MISLEADING", "FABRICATED"}


def map_rating_to_binary(rating: str) -> str:
    """Map OverallRating to binary 'real' or 'fake'."""
    return "real" if rating in _BINARY_REAL else "fake"


def map_rating_to_multiclass(rating: str) -> str:
    """Map OverallRating to 3-class: reliable/mixed/unreliable."""
    if rating in _MULTICLASS_RELIABLE:
        return "reliable"
    if rating in _MULTICLASS_MIXED:
        return "mixed"
    return "unreliable"


def is_unverifiable(result: BenchmarkRunResult) -> bool:
    """Check if a result is effectively unverifiable."""
    if result.error:
        return True
    if not result.overall_rating:
        return True
    # MIXED with very low confidence and no claims = system couldn't determine
    if (
        result.overall_rating == "MIXED"
        and result.confidence < 0.3
        and result.num_claims == 0
    ):
        return True
    return False


# ── Metric Computation ──────────────────────────────────────────────


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Compute precision, recall, F1 from counts."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def score_all(
    items: list[BenchmarkItem],
    results: list[BenchmarkRunResult],
    tier: str = "pro",
) -> ScoreReport:
    """Compute all metrics from items and their run results."""

    # Build lookup
    result_map = {r.item_id: r for r in results}

    # Pair items with results, separate unverifiable
    pairs: list[tuple[BenchmarkItem, BenchmarkRunResult]] = []
    unverifiable_ids: list[str] = []
    missing_ids: list[str] = []

    for item in items:
        result = result_map.get(item.id)
        if result is None:
            missing_ids.append(item.id)
            continue
        if is_unverifiable(result):
            unverifiable_ids.append(item.id)
            continue
        pairs.append((item, result))

    total = len(items)
    evaluated = len(pairs)
    coverage = evaluated / total if total > 0 else 0.0

    # ── Binary metrics (fake = positive class) ──────────────────────
    tp = fp = fn = tn = 0
    false_pos_ids: list[str] = []
    false_neg_ids: list[str] = []

    for item, result in pairs:
        predicted = map_rating_to_binary(result.overall_rating)
        actual = item.ground_truth.value

        if actual == "fake" and predicted == "fake":
            tp += 1
        elif actual == "real" and predicted == "fake":
            fp += 1
            false_pos_ids.append(item.id)
        elif actual == "fake" and predicted == "real":
            fn += 1
            false_neg_ids.append(item.id)
        else:
            tn += 1

    binary_accuracy = (tp + tn) / evaluated if evaluated > 0 else 0.0
    binary_precision, binary_recall, binary_f1 = _precision_recall_f1(tp, fp, fn)

    # ── Multiclass accuracy ─────────────────────────────────────────
    multiclass_correct = 0
    for item, result in pairs:
        predicted_mc = map_rating_to_multiclass(result.overall_rating)
        if item.ground_truth == GroundTruthLabel.REAL:
            expected_mc = "reliable"
        else:
            expected_mc = "unreliable"
        if predicted_mc == expected_mc:
            multiclass_correct += 1
    multiclass_accuracy = multiclass_correct / evaluated if evaluated > 0 else 0.0

    # ── Per-category metrics ────────────────────────────────────────
    per_category = _compute_grouped_metrics(pairs, key_fn=lambda item: item.category.value)

    # ── Per-difficulty metrics ──────────────────────────────────────
    per_difficulty_raw = _compute_grouped_metrics(pairs, key_fn=lambda item: item.difficulty.value)
    per_difficulty = {
        k: DifficultyMetrics(total=v.total, accuracy=v.accuracy, f1=v.f1)
        for k, v in per_difficulty_raw.items()
    }

    # ── Confusion matrix ────────────────────────────────────────────
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item, result in pairs:
        predicted = result.overall_rating
        expected = item.expected_rating
        confusion[predicted][expected] += 1
    # Convert to regular dicts
    confusion_dict = {k: dict(v) for k, v in confusion.items()}

    # ── Confidence calibration ──────────────────────────────────────
    calibration = _compute_confidence_calibration(pairs)

    return ScoreReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        tier=tier,
        total_items=total,
        evaluated_items=evaluated,
        coverage_rate=round(coverage, 4),
        binary_accuracy=round(binary_accuracy, 4),
        binary_precision=binary_precision,
        binary_recall=binary_recall,
        binary_f1=binary_f1,
        multiclass_accuracy=round(multiclass_accuracy, 4),
        per_category_metrics=per_category,
        per_difficulty_metrics=per_difficulty,
        confusion_matrix=confusion_dict,
        confidence_calibration=calibration,
        false_positives=false_pos_ids,
        false_negatives=false_neg_ids,
        unverifiable_items=unverifiable_ids,
    )


def _compute_grouped_metrics(
    pairs: list[tuple[BenchmarkItem, BenchmarkRunResult]],
    key_fn,
) -> dict[str, CategoryMetrics]:
    """Compute per-group binary metrics."""
    groups: dict[str, list[tuple[BenchmarkItem, BenchmarkRunResult]]] = defaultdict(list)
    for item, result in pairs:
        groups[key_fn(item)].append((item, result))

    metrics = {}
    for key, group_pairs in sorted(groups.items()):
        tp = fp = fn = tn = 0
        for item, result in group_pairs:
            predicted = map_rating_to_binary(result.overall_rating)
            actual = item.ground_truth.value
            if actual == "fake" and predicted == "fake":
                tp += 1
            elif actual == "real" and predicted == "fake":
                fp += 1
            elif actual == "fake" and predicted == "real":
                fn += 1
            else:
                tn += 1

        total = len(group_pairs)
        accuracy = (tp + tn) / total if total > 0 else 0.0
        precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
        metrics[key] = CategoryMetrics(
            total=total,
            accuracy=round(accuracy, 4),
            precision=precision,
            recall=recall,
            f1=f1,
        )
    return metrics


def _compute_confidence_calibration(
    pairs: list[tuple[BenchmarkItem, BenchmarkRunResult]],
) -> ConfidenceCalibration:
    """Bin items by confidence and compute accuracy per bin."""
    bin_ranges = [
        ("0.0–0.3", 0.0, 0.3),
        ("0.3–0.6", 0.3, 0.6),
        ("0.6–0.8", 0.6, 0.8),
        ("0.8–1.0", 0.8, 1.01),
    ]

    bins: list[ConfidenceBin] = []
    total_ece = 0.0
    total_items = len(pairs)

    for label, lo, hi in bin_ranges:
        bin_pairs = [
            (item, result) for item, result in pairs
            if lo <= result.confidence < hi
        ]
        if not bin_pairs:
            bins.append(ConfidenceBin(range=label))
            continue

        correct = sum(
            1 for item, result in bin_pairs
            if map_rating_to_binary(result.overall_rating) == item.ground_truth.value
        )
        count = len(bin_pairs)
        accuracy = correct / count
        avg_conf = sum(r.confidence for _, r in bin_pairs) / count

        bins.append(ConfidenceBin(
            range=label,
            count=count,
            accuracy=round(accuracy, 4),
            avg_confidence=round(avg_conf, 4),
        ))

        # ECE contribution
        if total_items > 0:
            total_ece += (count / total_items) * abs(accuracy - avg_conf)

    return ConfidenceCalibration(
        bins=bins,
        expected_calibration_error=round(total_ece, 4),
    )
