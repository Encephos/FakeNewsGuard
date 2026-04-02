"""Data models for the German disinformation benchmark."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────────


class DisinfoCategory(str, Enum):
    POLITICAL = "political"
    HEALTH = "health"
    STATISTICAL = "statistical"
    CONSPIRACY = "conspiracy"
    PROPAGANDA = "propaganda"
    REGULATORY = "regulatory"
    FINANCIAL = "financial"
    CLIMATE = "climate"
    MIGRATION = "migration"
    INSTITUTIONAL_MIMICRY = "institutional_mimicry"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"
    PROPAGANDA = "propaganda"


class GroundTruthLabel(str, Enum):
    FAKE = "fake"
    REAL = "real"


# ── Benchmark Item ──────────────────────────────────────────────────


class BenchmarkItem(BaseModel):
    """A single item in the benchmark dataset."""

    id: str
    text: str
    ground_truth: GroundTruthLabel
    difficulty: Difficulty
    category: DisinfoCategory
    topic: str
    trick: str = Field(description="Desinformationstechnik / warum real")
    source_dataset: str = Field(
        default="original",
        description="Herkunft: original | defakts | euvsDisinfo etc.",
    )
    expected_rating: str = Field(
        description="Erwartetes OverallRating (RELIABLE, FABRICATED, etc.)",
    )
    notes: str = ""


# ── Run Result ──────────────────────────────────────────────────────


class BenchmarkRunResult(BaseModel):
    """Persisted result of running one benchmark item."""

    item_id: str
    tier: str
    timestamp: str
    duration_seconds: float
    synthesis_result: dict = Field(default_factory=dict)
    overall_rating: str = ""
    confidence: float = 0.0
    num_claims: int = 0
    num_errors: int = 0
    error: Optional[str] = None


# ── Scoring Models ──────────────────────────────────────────────────


class CategoryMetrics(BaseModel):
    total: int = 0
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


class DifficultyMetrics(BaseModel):
    total: int = 0
    accuracy: float = 0.0
    f1: float = 0.0


class ConfidenceBin(BaseModel):
    range: str
    count: int = 0
    accuracy: float = 0.0
    avg_confidence: float = 0.0


class ConfidenceCalibration(BaseModel):
    bins: list[ConfidenceBin] = Field(default_factory=list)
    expected_calibration_error: float = 0.0


class ScoreReport(BaseModel):
    """Aggregated scoring report."""

    timestamp: str
    tier: str
    total_items: int = 0
    evaluated_items: int = 0
    coverage_rate: float = 0.0

    # Binary metrics (fake = positive class)
    binary_accuracy: float = 0.0
    binary_precision: float = 0.0
    binary_recall: float = 0.0
    binary_f1: float = 0.0

    # Multi-class (reliable / mixed / unreliable)
    multiclass_accuracy: float = 0.0

    per_category_metrics: dict[str, CategoryMetrics] = Field(default_factory=dict)
    per_difficulty_metrics: dict[str, DifficultyMetrics] = Field(default_factory=dict)

    # Confusion matrix: predicted → actual counts
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)

    confidence_calibration: ConfidenceCalibration = Field(
        default_factory=ConfidenceCalibration,
    )

    false_positives: list[str] = Field(default_factory=list)
    false_negatives: list[str] = Field(default_factory=list)
    unverifiable_items: list[str] = Field(default_factory=list)
