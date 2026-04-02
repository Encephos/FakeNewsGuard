"""Load and validate the German disinformation benchmark dataset."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from benchmarks.models import BenchmarkItem, DisinfoCategory, Difficulty, GroundTruthLabel

logger = logging.getLogger(__name__)

_DEFAULT_DATASET = Path(__file__).parent / "data" / "german_disinfo_benchmark.json"


def load_dataset(path: Path | None = None) -> list[BenchmarkItem]:
    """Load benchmark items from JSON, validate, and return."""
    path = path or _DEFAULT_DATASET
    if not path.exists():
        raise FileNotFoundError(f"Benchmark dataset not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    items: list[BenchmarkItem] = []
    for entry in raw["data"]:
        item = BenchmarkItem(**entry)
        items.append(item)

    _validate_dataset(items)
    logger.info("Loaded %d benchmark items from %s", len(items), path.name)
    return items


def _validate_dataset(items: list[BenchmarkItem]) -> None:
    """Run basic sanity checks on the dataset."""
    ids = [item.id for item in items]
    duplicates = [x for x in ids if ids.count(x) > 1]
    if duplicates:
        raise ValueError(f"Duplicate item IDs: {set(duplicates)}")

    # Check distribution
    fake_count = sum(1 for i in items if i.ground_truth == GroundTruthLabel.FAKE)
    real_count = sum(1 for i in items if i.ground_truth == GroundTruthLabel.REAL)
    logger.info(
        "Dataset distribution: %d fake, %d real (%.0f%% real)",
        fake_count,
        real_count,
        100 * real_count / len(items) if items else 0,
    )

    # Check category coverage
    categories_present = {i.category for i in items}
    missing = set(DisinfoCategory) - categories_present
    if missing:
        logger.warning("Missing categories in dataset: %s", missing)

    # Check difficulty coverage
    difficulties_present = {i.difficulty for i in items}
    missing_diff = set(Difficulty) - difficulties_present
    if missing_diff:
        logger.warning("Missing difficulty levels in dataset: %s", missing_diff)


def filter_items(
    items: list[BenchmarkItem],
    *,
    category: DisinfoCategory | None = None,
    difficulty: Difficulty | None = None,
    ground_truth: GroundTruthLabel | None = None,
    limit: int | None = None,
) -> list[BenchmarkItem]:
    """Filter benchmark items by criteria."""
    filtered = items
    if category is not None:
        filtered = [i for i in filtered if i.category == category]
    if difficulty is not None:
        filtered = [i for i in filtered if i.difficulty == difficulty]
    if ground_truth is not None:
        filtered = [i for i in filtered if i.ground_truth == ground_truth]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered
