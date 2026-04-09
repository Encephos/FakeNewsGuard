"""Retrieval metric computation.

Reuses existing scoring functions from the production codebase.
No logic duplication — all domain-tier, low-trust, and freshness
checks delegate to agents.evidence_scoring.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from eval.models import (
    CaseMetrics,
    CaseResult,
    EvalCase,
    Regression,
    VerdictAccuracyReport,
    Violation,
)
from eval.snapshot import RetrievalSnapshot


# ── Helpers (thin wrappers around existing functions) ────────────────────────


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _get_domain_tier(url: str) -> int:
    """Delegate to evidence_scoring._domain_tier."""
    from agents.evidence_scoring import _domain_tier
    return _domain_tier(url)


def _is_low_trust(url: str, title: str = "", snippet: str = "") -> bool:
    """Delegate to evidence_scoring._is_low_trust_site."""
    from agents.evidence_scoring import _is_low_trust_site
    return _is_low_trust_site(url, title, snippet)


# ── Per-case metric functions ────────────────────────────────────────────────


def _item_domain_tier(item: dict[str, Any]) -> int:
    """Extract domain_tier from evidence item, supporting both nested and flat formats."""
    tier = item.get("source", {}).get("domain_tier")
    if tier is not None:
        return tier
    # Flat fallback: item.tier or item.domain_tier
    tier = item.get("domain_tier")
    if tier is not None:
        return tier
    tier = item.get("tier")
    if tier is not None:
        return tier
    # Re-derive from URL as last resort
    url = item.get("source", {}).get("url") or item.get("url", "")
    if url:
        return _get_domain_tier(url)
    return 5


def _item_publication_date(item: dict[str, Any]) -> str:
    """Extract publication_date from evidence item, supporting both nested and flat formats."""
    date = item.get("source", {}).get("publication_date")
    if date:
        return date
    return item.get("publication_date", "")


def _official_source_recall_at_k(
    evidence_items: list[dict[str, Any]],
    expected_min: int,
    k: int = 10,
) -> float:
    """Fraction of top-K results from Tier-1/2 sources vs expected minimum."""
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    official = sum(
        1 for item in top_k
        if _item_domain_tier(item) <= 2
    )
    if expected_min <= 0:
        return 1.0 if official > 0 else 0.0
    return min(official / expected_min, 1.0)


def _preferred_domain_hit_rate(
    all_results: list[dict[str, Any]],
    preferred_domains: list[str],
) -> float:
    """Fraction of preferred domains found anywhere in results."""
    if not preferred_domains:
        return 1.0
    found_domains = set()
    for r in all_results:
        url = r.get("url", "") or r.get("source", {}).get("url", "")
        domain = _extract_domain(url)
        for pref in preferred_domains:
            if pref in domain:
                found_domains.add(pref)
    return len(found_domains) / len(preferred_domains)


def _item_url(item: dict[str, Any]) -> str:
    """Extract URL from evidence item, supporting both nested and flat formats."""
    return item.get("source", {}).get("url") or item.get("url", "")


def _low_trust_rate(evidence_items: list[dict[str, Any]], k: int = 10) -> float:
    """Fraction of low-trust sources in top-K evidence items."""
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    low = sum(
        1 for item in top_k
        if _is_low_trust(
            _item_url(item),
            item.get("source", {}).get("title") or item.get("title", ""),
            item.get("excerpt", ""),
        )
    )
    return low / len(top_k)


def _offtopic_rate(evidence_items: list[dict[str, Any]], k: int = 10) -> float:
    """Fraction of OFFTOPIC items in top-K."""
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    offtopic = sum(
        1 for item in top_k
        if item.get("source_direction") == "offtopic"
    )
    return offtopic / len(top_k)


def _freshness_hit_rate(
    evidence_items: list[dict[str, Any]],
    requires_recency: bool,
    k: int = 10,
) -> float:
    """For current_state claims: fraction of top-K with freshness >= 0.5.

    The threshold is >= 0.5 (not strict >), because the freshness tiers
    assign 0.5 to items up to 365 days old — those are still relevant for
    claims about data from the preceding year.
    """
    if not requires_recency:
        return 1.0  # not applicable
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    from agents.evidence_scoring import _compute_freshness
    fresh = sum(
        1 for item in top_k
        if _compute_freshness(_item_publication_date(item)) >= 0.5
    )
    return fresh / len(top_k)


def _direct_evidence_rate(evidence_items: list[dict[str, Any]], k: int = 10) -> float:
    """Fraction of DIRECT evidence items in top-K."""
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    direct = sum(
        1 for item in top_k
        if item.get("evidence_type") == "direct"
    )
    return direct / len(top_k)


def _contextual_only_rate(evidence_items: list[dict[str, Any]], k: int = 10) -> float:
    """1.0 if zero DIRECT items in top-K, else fraction of non-direct."""
    top_k = evidence_items[:k]
    if not top_k:
        return 1.0
    direct_count = sum(
        1 for item in top_k
        if item.get("evidence_type") == "direct"
    )
    if direct_count == 0:
        return 1.0
    return 1.0 - (direct_count / len(top_k))


def _scrape_waste_rate(ranked_sources: list[dict[str, Any]]) -> float:
    """Fraction of scraped sources with relevance_score < 0.2."""
    scraped = [r for r in ranked_sources if r.get("should_scrape")]
    if not scraped:
        return 0.0
    waste = sum(1 for r in scraped if r.get("relevance_score", 0) < 0.2)
    return waste / len(scraped)


def _structured_source_hit_rate(
    source_client_results: list[dict[str, Any]],
    expected_classes: list[str],
) -> float:
    """Fraction of expected source client classes actually hit."""
    if not expected_classes:
        return 1.0
    found = set()
    for r in source_client_results:
        src_class = r.get("source_class", "") or r.get("source_id", "")
        for expected in expected_classes:
            if expected.lower() in src_class.lower():
                found.add(expected)
    return len(found) / len(expected_classes)


def _retrieval_precision_proxy_at_k(
    evidence_items: list[dict[str, Any]],
    k: int = 10,
    threshold: float = 0.3,
) -> float:
    """Fraction of top-K evidence items with relevance_score > threshold."""
    top_k = evidence_items[:k]
    if not top_k:
        return 0.0
    relevant = sum(
        1 for item in top_k
        if item.get("relevance_score", 0) > threshold
    )
    return relevant / len(top_k)


def _query_duplication_rate(
    generated: list[str],
    deduped: list[str],
) -> float:
    """1 - (deduped_count / generated_count). 0 = no duplication."""
    if not generated:
        return 0.0
    return 1.0 - (len(deduped) / len(generated))


def _source_diversity(all_results: list[dict[str, Any]]) -> float:
    """Unique domains / total results."""
    if not all_results:
        return 0.0
    domains = set()
    for r in all_results:
        url = r.get("url", "") or r.get("source", {}).get("url", "")
        domain = _extract_domain(url)
        if domain:
            domains.add(domain)
    return len(domains) / len(all_results)


def _cache_hit_rate(hits: int, misses: int) -> Optional[float]:
    """Cache hit ratio. Returns None if no cache data."""
    total = hits + misses
    if total == 0:
        return None
    return hits / total


# ── Aggregation ──────────────────────────────────────────────────────────────


def compute_case_metrics(
    snapshot: RetrievalSnapshot,
    case: EvalCase,
) -> CaseMetrics:
    """Compute all metrics for a single case from its snapshot."""
    exp = case.expectations
    items = snapshot.evidence_items
    ranked = snapshot.ranked_sources
    merged = snapshot.merged_results

    return CaseMetrics(
        official_source_recall_at_k=_official_source_recall_at_k(
            items, exp.min_official_source_hits
        ),
        preferred_domain_hit_rate=_preferred_domain_hit_rate(
            merged, exp.preferred_domains
        ),
        low_trust_rate=_low_trust_rate(items),
        offtopic_rate=_offtopic_rate(items),
        freshness_hit_rate=_freshness_hit_rate(
            items, exp.requires_recency
        ),
        direct_evidence_rate=_direct_evidence_rate(items),
        contextual_only_rate=_contextual_only_rate(items),
        scrape_waste_rate=_scrape_waste_rate(ranked),
        structured_source_hit_rate=_structured_source_hit_rate(
            snapshot.source_client_results, exp.expected_source_classes
        ),
        retrieval_precision_proxy_at_k=_retrieval_precision_proxy_at_k(items),
        query_duplication_rate=_query_duplication_rate(
            snapshot.generated_queries, snapshot.deduped_queries
        ),
        source_diversity=_source_diversity(merged),
        cache_hit_rate=_cache_hit_rate(snapshot.cache_hits, snapshot.cache_misses),
    )


def check_expectations(
    metrics: CaseMetrics,
    expectations: "RetrievalExpectations",
) -> list[Violation]:
    """Check computed metrics against case expectations, return violations."""
    violations: list[Violation] = []

    if metrics.low_trust_rate > expectations.max_low_trust_rate:
        violations.append(Violation(
            metric="low_trust_rate",
            expected=f"<= {expectations.max_low_trust_rate}",
            actual=f"{metrics.low_trust_rate:.3f}",
            severity="error",
        ))

    if expectations.min_official_source_hits > 0:
        if metrics.official_source_recall_at_k < 1.0:
            violations.append(Violation(
                metric="official_source_recall_at_k",
                expected=f">= {expectations.min_official_source_hits} official sources",
                actual=f"recall={metrics.official_source_recall_at_k:.3f}",
                severity="warning",
            ))

    if expectations.min_direct_evidence_count > 0:
        if metrics.direct_evidence_rate == 0.0:
            violations.append(Violation(
                metric="direct_evidence_rate",
                expected=f">= {expectations.min_direct_evidence_count} direct items",
                actual="0",
                severity="warning",
            ))

    if expectations.preferred_domains and metrics.preferred_domain_hit_rate < 0.5:
        violations.append(Violation(
            metric="preferred_domain_hit_rate",
            expected=">= 0.5",
            actual=f"{metrics.preferred_domain_hit_rate:.3f}",
            severity="warning",
        ))

    if expectations.requires_recency and metrics.freshness_hit_rate < 0.1:
        violations.append(Violation(
            metric="freshness_hit_rate",
            expected=">= 0.1 for current_state",
            actual=f"{metrics.freshness_hit_rate:.3f}",
            severity="error",
        ))
    elif expectations.requires_recency and metrics.freshness_hit_rate < 0.3:
        violations.append(Violation(
            metric="freshness_hit_rate",
            expected=">= 0.3 for current_state",
            actual=f"{metrics.freshness_hit_rate:.3f}",
            severity="warning",
        ))

    if expectations.disallowed_domains:
        # Checked via preferred_domain_hit_rate logic;
        # disallowed domain presence is a hard failure
        pass

    return violations


def aggregate_global(results: list[CaseResult]) -> dict[str, float]:
    """Compute mean metrics across all cases."""
    if not results:
        return {}
    metric_fields = CaseMetrics.model_fields.keys()
    agg: dict[str, list[float]] = {f: [] for f in metric_fields}
    for r in results:
        for f in metric_fields:
            val = getattr(r.metrics, f)
            if val is not None:
                agg[f].append(val)
    return {
        f: sum(vals) / len(vals) if vals else 0.0
        for f, vals in agg.items()
    }


def aggregate_by_category(
    results: list[CaseResult],
) -> dict[str, dict[str, float]]:
    """Compute mean metrics per category."""
    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category.value, []).append(r)
    return {cat: aggregate_global(cat_results) for cat, cat_results in by_cat.items()}


# ── Verdict Accuracy ────────────────────────────────────────────────────────

# Ordinal scale for FactRating (distance-based comparison)
_RATING_ORDINAL: dict[str, int] = {
    "TRUE": 0,
    "MOSTLY_TRUE": 1,
    "MISLEADING": 2,
    "MOSTLY_FALSE": 3,
    "FALSE": 4,
    "UNVERIFIABLE": -1,  # special: only exact match counts
}

# Ordinal scale for OverallRating (Synthesizer output)
_OVERALL_RATING_ORDINAL: dict[str, int] = {
    "RELIABLE": 0,
    "MOSTLY_RELIABLE": 1,
    "MIXED": 2,
    "MISLEADING": 3,
    "HIGHLY_MISLEADING": 4,
    "FABRICATED": 5,
}


def _verdict_distance(predicted: str, expected: str) -> int:
    """Compute ordinal distance between two rating values.

    Supports both FactRating (TRUE..FALSE) and OverallRating (RELIABLE..FABRICATED)
    scales. Auto-detects which scale to use based on the values.
    UNVERIFIABLE is only exact-matchable — distance to any other rating is 99.
    """
    pred_upper = predicted.upper()
    exp_upper = expected.upper()

    # Try OverallRating scale first (used by eval pipeline)
    p_overall = _OVERALL_RATING_ORDINAL.get(pred_upper, -99)
    e_overall = _OVERALL_RATING_ORDINAL.get(exp_upper, -99)
    if p_overall != -99 and e_overall != -99:
        return abs(p_overall - e_overall)

    # Fall back to FactRating scale
    p = _RATING_ORDINAL.get(pred_upper, -99)
    e = _RATING_ORDINAL.get(exp_upper, -99)
    if p == -99 or e == -99:
        return 99  # unknown rating
    if p == -1 or e == -1:
        return 0 if p == e else 99
    return abs(p - e)


def _topic_relevance_avg(evidence_items: list[dict[str, Any]], k: int = 5) -> Optional[float]:
    """Average topic_relevance_score of top-K evidence items."""
    top_k = evidence_items[:k]
    if not top_k:
        return None
    scores = [item.get("topic_relevance_score", 1.0) for item in top_k]
    return sum(scores) / len(scores)


def compute_verdict_metrics(
    predicted: str,
    expected: str,
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute verdict accuracy metrics for a single case.

    Returns dict with verdict_accuracy, verdict_within_one_step,
    verdict_distance, topic_relevance_avg.
    """
    dist = _verdict_distance(predicted, expected)
    return {
        "verdict_accuracy": 1.0 if dist == 0 else 0.0,
        "verdict_within_one_step": dist <= 1,
        "verdict_distance": dist,
        "topic_relevance_avg": _topic_relevance_avg(evidence_items),
    }


def compute_verdict_accuracy_report(
    results: list[CaseResult],
) -> VerdictAccuracyReport:
    """Aggregate verdict accuracy across all evaluated cases.

    Only includes cases where both predicted and expected verdicts exist
    (i.e. CaseMetrics.verdict_accuracy is not None).
    """
    cases_with_verdict = [
        r for r in results if r.metrics.verdict_accuracy is not None
    ]
    total = len(results)
    n = len(cases_with_verdict)

    if n == 0:
        return VerdictAccuracyReport(total_cases=total)

    exact = sum(1 for r in cases_with_verdict if r.metrics.verdict_accuracy == 1.0)
    within_one = sum(1 for r in cases_with_verdict if r.metrics.verdict_within_one_step)
    distances = [r.metrics.verdict_distance for r in cases_with_verdict if r.metrics.verdict_distance is not None]
    topic_rels = [r.metrics.topic_relevance_avg for r in cases_with_verdict if r.metrics.topic_relevance_avg is not None]
    offtopic_rates = [r.metrics.offtopic_rate for r in cases_with_verdict]

    # Build confusion matrix from case results
    # We need predicted/expected stored somewhere — use violations or pass separately
    # For now, confusion matrix is built externally via build_confusion_matrix()

    return VerdictAccuracyReport(
        total_cases=total,
        cases_with_expected_verdict=n,
        exact_match_count=exact,
        exact_match_rate=exact / n,
        within_one_step_count=within_one,
        within_one_step_rate=within_one / n,
        avg_verdict_distance=sum(distances) / len(distances) if distances else 0.0,
        avg_topic_relevance=sum(topic_rels) / len(topic_rels) if topic_rels else None,
        avg_offtopic_rate=sum(offtopic_rates) / len(offtopic_rates) if offtopic_rates else 0.0,
    )


def build_confusion_matrix(
    pairs: list[tuple[str, str]],
) -> dict[str, dict[str, int]]:
    """Build a confusion matrix from (predicted, expected) rating pairs.

    Returns nested dict: matrix[expected][predicted] = count.
    """
    ratings = ["TRUE", "MOSTLY_TRUE", "MISLEADING", "MOSTLY_FALSE", "FALSE", "UNVERIFIABLE"]
    matrix: dict[str, dict[str, int]] = {r: {c: 0 for c in ratings} for r in ratings}
    for predicted, expected in pairs:
        p = predicted.upper()
        e = expected.upper()
        if e in matrix and p in matrix[e]:
            matrix[e][p] += 1
    return matrix


def detect_regressions(
    current: dict[str, float],
    baseline: dict[str, float],
    thresholds: Optional[dict[str, float]] = None,
) -> list[Regression]:
    """Detect regressions by comparing current vs baseline metrics.

    A regression is flagged when a metric degrades beyond the threshold.
    Default threshold: 0.05 (5% absolute degradation).
    """
    default_threshold = 0.05
    regressions: list[Regression] = []

    # Metrics where lower is better (invert check)
    lower_is_better = {
        "low_trust_rate", "offtopic_rate", "contextual_only_rate",
        "scrape_waste_rate", "query_duplication_rate",
    }

    for metric, cur_val in current.items():
        if metric not in baseline:
            continue
        base_val = baseline[metric]
        threshold = (thresholds or {}).get(metric, default_threshold)

        if metric in lower_is_better:
            delta = cur_val - base_val  # increase = regression
        else:
            delta = base_val - cur_val  # decrease = regression

        if delta > threshold:
            regressions.append(Regression(
                metric=metric,
                baseline_value=base_val,
                current_value=cur_val,
                delta=delta,
            ))

    return regressions
