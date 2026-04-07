"""Data models for the retrieval evaluation framework."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Evaluation Categories ────────────────────────────────────────────────────


class EvalCategory(str, Enum):
    CURRENT_STATE = "current_state"
    REGULATORY = "regulatory"
    STATISTICAL = "statistical"
    CORPORATE = "corporate"
    MEDICAL_PHARMA = "medical_pharma"
    LEGAL_EU = "legal_eu"
    NOISY_OR_UNDERSPECIFIED = "noisy_or_underspecified"
    OFF_TOPIC_TRAPS = "off_topic_traps"
    MULTILINGUAL = "multilingual"
    HEALTH = "health"
    CLIMATE = "climate"
    MIGRATION = "migration"
    FINANCIAL = "financial"
    CONSPIRACY = "conspiracy"


# ── Retrieval Expectations ───────────────────────────────────────────────────


class RetrievalExpectations(BaseModel):
    """Expected retrieval behaviour for an evaluation case."""

    must_have_entities: list[str] = Field(
        default_factory=list,
        description="Entities that must appear in queries or results",
    )
    preferred_domains: list[str] = Field(
        default_factory=list,
        description="Domains expected among top results (e.g. destatis.de)",
    )
    disallowed_domains: list[str] = Field(
        default_factory=list,
        description="Domains that should NOT appear in results",
    )
    requires_recency: bool = Field(
        default=False,
        description="Whether results must be recent (current_state claims)",
    )
    expected_source_classes: list[str] = Field(
        default_factory=list,
        description="Source client classes expected (e.g. eurostat, openfda)",
    )
    expected_verdict_class: Optional[str] = Field(
        default=None,
        description="Optional expected verdict for full-pipeline eval",
    )
    max_low_trust_rate: float = Field(
        default=0.3,
        description="Maximum acceptable low-trust rate in top-K",
    )
    min_official_source_hits: int = Field(
        default=0,
        description="Minimum Tier-1/2 sources expected in results",
    )
    min_direct_evidence_count: int = Field(
        default=0,
        description="Minimum DIRECT evidence items expected",
    )


# ── Evaluation Case ─────────────────────────────────────────────────────────


class EvalCase(BaseModel):
    """A single evaluation case from the dataset."""

    id: str
    claim_text: str
    category: EvalCategory
    language: str = "de"
    context: str = ""
    expectations: RetrievalExpectations = Field(default_factory=RetrievalExpectations)


# ── Metrics ──────────────────────────────────────────────────────────────────


class CaseMetrics(BaseModel):
    """Computed retrieval metrics for a single case."""

    # Retrieval metrics
    official_source_recall_at_k: float = 0.0
    preferred_domain_hit_rate: float = 0.0
    low_trust_rate: float = 0.0
    offtopic_rate: float = 0.0
    freshness_hit_rate: float = 0.0
    direct_evidence_rate: float = 0.0
    contextual_only_rate: float = 0.0
    scrape_waste_rate: float = 0.0
    structured_source_hit_rate: float = 0.0
    retrieval_precision_proxy_at_k: float = 0.0
    query_duplication_rate: float = 0.0
    source_diversity: float = 0.0
    cache_hit_rate: Optional[float] = None

    # Verdict accuracy metrics
    verdict_accuracy: Optional[float] = None
    verdict_within_one_step: Optional[bool] = None
    verdict_distance: Optional[int] = None
    topic_relevance_avg: Optional[float] = None


class Violation(BaseModel):
    """A single expectation violation."""

    metric: str
    expected: str
    actual: str
    severity: str = "warning"  # "warning" | "error"


class CaseResult(BaseModel):
    """Evaluation result for a single case."""

    case_id: str
    category: EvalCategory
    metrics: CaseMetrics
    violations: list[Violation] = Field(default_factory=list)
    passed: bool = True


class Regression(BaseModel):
    """A detected regression vs baseline."""

    metric: str
    category: Optional[str] = None
    case_id: Optional[str] = None
    baseline_value: float
    current_value: float
    delta: float


class VerdictAccuracyReport(BaseModel):
    """Aggregated verdict accuracy metrics across all cases."""

    total_cases: int = 0
    cases_with_expected_verdict: int = 0
    exact_match_count: int = 0
    exact_match_rate: float = 0.0
    within_one_step_count: int = 0
    within_one_step_rate: float = 0.0
    avg_verdict_distance: float = 0.0
    avg_topic_relevance: Optional[float] = None
    avg_offtopic_rate: float = 0.0
    confusion_matrix: dict[str, dict[str, int]] = Field(default_factory=dict)


class MetricsReport(BaseModel):
    """Aggregated evaluation report."""

    timestamp: str
    baseline_id: Optional[str] = None
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    global_metrics: dict[str, float] = Field(default_factory=dict)
    per_category: dict[str, dict[str, float]] = Field(default_factory=dict)
    worst_cases: list[CaseResult] = Field(default_factory=list)
    regressions: list[Regression] = Field(default_factory=list)
    verdict_accuracy: Optional[VerdictAccuracyReport] = None
