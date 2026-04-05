"""Automatic error categorization for evaluation results.

Classifies fact-check errors into actionable categories to guide
pipeline improvements. Each category maps to a specific pipeline
weakness (off-topic evidence, lost context, over/under-confidence, etc.).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorCategory(str, Enum):
    """Classification of fact-check errors."""

    EVIDENCE_OFF_TOPIC = "EVIDENCE_OFF_TOPIC"
    CLAIM_ORPHANED = "CLAIM_ORPHANED"
    VERDICT_OVERCAUTIOUS = "VERDICT_OVERCAUTIOUS"
    VERDICT_OVERCONFIDENT = "VERDICT_OVERCONFIDENT"
    CROSS_CLAIM_INCONSISTENT = "CROSS_CLAIM_INCONSISTENT"


class ErrorEntry(BaseModel):
    """A single categorized error from evaluation."""

    case_id: str
    claim_id: str = ""
    category: ErrorCategory
    severity: str = "warning"  # "warning" | "error"
    detail: str = ""
    predicted_verdict: str = ""
    expected_verdict: str = ""
    confidence: float = 0.0
    topic_relevance_avg: Optional[float] = None


class ErrorReport(BaseModel):
    """Aggregated error analysis across all evaluated cases."""

    total_errors: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    entries: list[ErrorEntry] = Field(default_factory=list)


# ── Detection Functions ─────────────────────────────────────────────────────


def detect_evidence_off_topic(
    case_id: str,
    evidence_items: list[dict[str, Any]],
    k: int = 5,
    threshold: float = 0.25,
) -> Optional[ErrorEntry]:
    """Flag when top-K evidence has low average topic_relevance_score.

    Indicates the pipeline found sources that match the claim keywords
    but not the article's overarching topic.
    """
    top_k = evidence_items[:k]
    if not top_k:
        return None
    scores = [item.get("topic_relevance_score", 1.0) for item in top_k]
    avg = sum(scores) / len(scores)
    if avg < threshold:
        return ErrorEntry(
            case_id=case_id,
            category=ErrorCategory.EVIDENCE_OFF_TOPIC,
            severity="error" if avg < 0.15 else "warning",
            detail=f"Top-{k} evidence avg topic_relevance={avg:.3f} < {threshold}",
            topic_relevance_avg=avg,
        )
    return None


def detect_claim_orphaned(
    case_id: str,
    claim_id: str,
    claim_text: str,
    topic_keywords: list[str],
    topic_entities: list[str],
) -> Optional[ErrorEntry]:
    """Flag when a claim shares no keywords/entities with the article topic.

    Indicates decomposition may have produced a sub-claim that lost
    the article context.
    """
    if not topic_keywords and not topic_entities:
        return None
    text_lower = claim_text.lower()
    anchors = topic_keywords + topic_entities
    hits = sum(1 for a in anchors if a.lower() in text_lower)
    if hits == 0:
        return ErrorEntry(
            case_id=case_id,
            claim_id=claim_id,
            category=ErrorCategory.CLAIM_ORPHANED,
            severity="warning",
            detail=f"Claim shares 0/{len(anchors)} anchors with article topic",
        )
    return None


def detect_verdict_overcautious(
    case_id: str,
    predicted: str,
    expected: str,
    confidence: float,
    evidence_count: int,
) -> Optional[ErrorEntry]:
    """Flag UNVERIFIABLE verdict when evidence was available and expected verdict differs.

    Indicates the pipeline had enough evidence to render a verdict
    but chose UNVERIFIABLE — typically due to overly conservative
    confidence thresholds or scoring.
    """
    if predicted.upper() != "UNVERIFIABLE":
        return None
    if expected.upper() == "UNVERIFIABLE":
        return None
    if evidence_count < 2:
        return None  # genuinely thin evidence, not overcautious
    return ErrorEntry(
        case_id=case_id,
        category=ErrorCategory.VERDICT_OVERCAUTIOUS,
        severity="warning",
        detail=(
            f"Predicted UNVERIFIABLE (confidence={confidence:.2f}, "
            f"{evidence_count} evidence items) but expected {expected}"
        ),
        predicted_verdict=predicted,
        expected_verdict=expected,
        confidence=confidence,
    )


def detect_verdict_overconfident(
    case_id: str,
    predicted: str,
    expected: str,
    confidence: float,
    verdict_distance: int,
) -> Optional[ErrorEntry]:
    """Flag high-confidence verdict that is far from expected.

    Indicates the pipeline rendered a confident but wrong verdict —
    typically due to off-topic evidence being scored too highly.
    """
    if verdict_distance <= 1:
        return None  # close enough
    if confidence < 0.5:
        return None  # already low confidence
    return ErrorEntry(
        case_id=case_id,
        category=ErrorCategory.VERDICT_OVERCONFIDENT,
        severity="error" if verdict_distance >= 3 else "warning",
        detail=(
            f"Predicted {predicted} (confidence={confidence:.2f}) "
            f"but expected {expected} (distance={verdict_distance})"
        ),
        predicted_verdict=predicted,
        expected_verdict=expected,
        confidence=confidence,
    )


def detect_cross_claim_inconsistent(
    case_id: str,
    claim_a_id: str,
    claim_a_verdict: str,
    claim_b_id: str,
    claim_b_verdict: str,
    dependency_type: str,
) -> Optional[ErrorEntry]:
    """Flag logical contradictions between dependent claims.

    E.g. parent claim (policy exists) = FALSE but child claim
    (sanction for violating policy) = TRUE/MOSTLY_TRUE.
    """
    a = claim_a_verdict.upper()
    b = claim_b_verdict.upper()
    # Parent is FALSE/UNVERIFIABLE but dependent is TRUE/MOSTLY_TRUE
    parent_negative = a in ("FALSE", "MOSTLY_FALSE", "UNVERIFIABLE")
    child_positive = b in ("TRUE", "MOSTLY_TRUE")
    if not (parent_negative and child_positive):
        return None
    return ErrorEntry(
        case_id=case_id,
        claim_id=claim_b_id,
        category=ErrorCategory.CROSS_CLAIM_INCONSISTENT,
        severity="error",
        detail=(
            f"Parent claim {claim_a_id} ({a}) contradicts "
            f"dependent claim {claim_b_id} ({b}), "
            f"dependency_type={dependency_type}"
        ),
        predicted_verdict=b,
        expected_verdict="",
    )


# ── Aggregate Analysis ──────────────────────────────────────────────────────


def analyze_errors(entries: list[ErrorEntry]) -> ErrorReport:
    """Build an aggregated error report from individual entries."""
    by_cat: dict[str, int] = {}
    for e in entries:
        by_cat[e.category.value] = by_cat.get(e.category.value, 0) + 1
    return ErrorReport(
        total_errors=len(entries),
        by_category=by_cat,
        entries=entries,
    )
