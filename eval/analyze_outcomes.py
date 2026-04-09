"""Offline analysis of outcome logs for retrieval quality insights.

Reads the outcome log database and computes aggregate statistics to
identify systematic retrieval weaknesses, routing errors, and
source quality issues.

Usage:
    .venv/bin/python3 -m eval.analyze_outcomes [--db .outcome_log.db] [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(".outcome_log.db")


def _load_outcomes(db_path: Path) -> list[dict[str, Any]]:
    """Load all outcome records from the database."""
    if not db_path.exists():
        logger.warning("No outcome database found at %s", db_path)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM outcomes ORDER BY timestamp").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _parse_json_field(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def analyze_routing_quality(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze which routing domains/sources correlate with good evidence."""
    domain_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_quality": 0.0, "total_confidence": 0.0,
                 "high_quality_count": 0, "low_quality_count": 0}
    )

    for o in outcomes:
        domains = _parse_json_field(o.get("route_domains"))
        quality = o.get("evidence_quality", 0.0) or 0.0
        confidence = o.get("verdict_confidence", 0.0) or 0.0

        for domain in domains:
            s = domain_stats[domain]
            s["count"] += 1
            s["total_quality"] += quality
            s["total_confidence"] += confidence
            if quality >= 0.5:
                s["high_quality_count"] += 1
            elif quality < 0.3:
                s["low_quality_count"] += 1

    # Compute averages
    result = {}
    for domain, s in sorted(domain_stats.items(), key=lambda x: -x[1]["count"]):
        n = s["count"]
        result[domain] = {
            "count": n,
            "avg_evidence_quality": round(s["total_quality"] / n, 3) if n else 0,
            "avg_confidence": round(s["total_confidence"] / n, 3) if n else 0,
            "high_quality_rate": round(s["high_quality_count"] / n, 3) if n else 0,
            "low_quality_rate": round(s["low_quality_count"] / n, 3) if n else 0,
        }

    return result


def analyze_source_effectiveness(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze which routed sources deliver useful evidence."""
    source_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_quality": 0.0, "offtopic_sum": 0.0,
                 "low_trust_sum": 0.0}
    )

    for o in outcomes:
        sources = _parse_json_field(o.get("route_sources"))
        quality = o.get("evidence_quality", 0.0) or 0.0
        offtopic = o.get("off_topic_rate", 0.0) or 0.0
        low_trust = o.get("low_trust_rate", 0.0) or 0.0

        for source in sources:
            s = source_stats[source]
            s["count"] += 1
            s["total_quality"] += quality
            s["offtopic_sum"] += offtopic
            s["low_trust_sum"] += low_trust

    result = {}
    for source, s in sorted(source_stats.items(), key=lambda x: -x[1]["count"]):
        n = s["count"]
        result[source] = {
            "count": n,
            "avg_evidence_quality": round(s["total_quality"] / n, 3) if n else 0,
            "avg_offtopic_rate": round(s["offtopic_sum"] / n, 3) if n else 0,
            "avg_low_trust_rate": round(s["low_trust_sum"] / n, 3) if n else 0,
        }

    return result


def analyze_verdict_distribution(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze verdict rating distribution and confidence calibration."""
    rating_counts: Counter = Counter()
    confidence_by_rating: dict[str, list[float]] = defaultdict(list)
    consensus_by_rating: dict[str, list[float]] = defaultdict(list)

    for o in outcomes:
        rating = o.get("verdict_rating", "")
        confidence = o.get("verdict_confidence", 0.0) or 0.0
        consensus_score = o.get("consensus_score", 0.0) or 0.0

        if rating:
            rating_counts[rating] += 1
            confidence_by_rating[rating].append(confidence)
            consensus_by_rating[rating].append(consensus_score)

    result = {
        "total_verdicts": sum(rating_counts.values()),
        "rating_distribution": dict(rating_counts.most_common()),
        "avg_confidence_by_rating": {
            r: round(sum(vals) / len(vals), 3)
            for r, vals in sorted(confidence_by_rating.items())
            if vals
        },
        "avg_consensus_by_rating": {
            r: round(sum(vals) / len(vals), 3)
            for r, vals in sorted(consensus_by_rating.items())
            if vals
        },
    }

    # Overconfidence analysis: high confidence + negative ratings
    negative_ratings = {"FALSE", "MOSTLY_FALSE", "UNVERIFIABLE"}
    overconfident = sum(
        1 for o in outcomes
        if o.get("verdict_rating") in negative_ratings
        and (o.get("verdict_confidence") or 0) >= 0.70
    )
    result["overconfident_negative_count"] = overconfident

    return result


def analyze_claim_type_performance(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze how different claim types perform in the pipeline."""
    type_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_quality": 0.0, "total_confidence": 0.0,
                 "unverifiable_count": 0}
    )

    for o in outcomes:
        claim_type = o.get("claim_type", "UNKNOWN") or "UNKNOWN"
        quality = o.get("evidence_quality", 0.0) or 0.0
        confidence = o.get("verdict_confidence", 0.0) or 0.0
        rating = o.get("verdict_rating", "")

        s = type_stats[claim_type]
        s["count"] += 1
        s["total_quality"] += quality
        s["total_confidence"] += confidence
        if rating == "UNVERIFIABLE":
            s["unverifiable_count"] += 1

    result = {}
    for ctype, s in sorted(type_stats.items(), key=lambda x: -x[1]["count"]):
        n = s["count"]
        result[ctype] = {
            "count": n,
            "avg_evidence_quality": round(s["total_quality"] / n, 3) if n else 0,
            "avg_confidence": round(s["total_confidence"] / n, 3) if n else 0,
            "unverifiable_rate": round(s["unverifiable_count"] / n, 3) if n else 0,
        }

    return result


def generate_recommendations(
    routing: dict[str, Any],
    sources: dict[str, Any],
    verdicts: dict[str, Any],
    claim_types: dict[str, Any],
) -> list[str]:
    """Generate actionable recommendations from the analysis."""
    recs: list[str] = []

    # Flag sources with high off-topic rates
    for source, stats in sources.items():
        if stats["count"] >= 5 and stats["avg_offtopic_rate"] > 0.40:
            recs.append(
                f"SOURCE_QUALITY: '{source}' has {stats['avg_offtopic_rate']:.0%} "
                f"avg off-topic rate over {stats['count']} uses — consider "
                f"restricting or re-prioritizing"
            )

    # Flag domains with low evidence quality
    for domain, stats in routing.items():
        if stats["count"] >= 5 and stats["low_quality_rate"] > 0.50:
            recs.append(
                f"ROUTING: Domain '{domain}' produces low-quality evidence "
                f"in {stats['low_quality_rate']:.0%} of cases — review routing rules"
            )

    # Flag claim types with high unverifiable rates
    for ctype, stats in claim_types.items():
        if stats["count"] >= 5 and stats["unverifiable_rate"] > 0.50:
            recs.append(
                f"CLAIM_TYPE: '{ctype}' claims are UNVERIFIABLE {stats['unverifiable_rate']:.0%} "
                f"of the time — may need specialized sources or adjusted routing"
            )

    # Overconfidence warning
    total = verdicts.get("total_verdicts", 0)
    overconf = verdicts.get("overconfident_negative_count", 0)
    if total > 10 and overconf / total > 0.10:
        recs.append(
            f"CALIBRATION: {overconf}/{total} verdicts ({overconf/total:.0%}) are "
            f"overconfident (>=0.70 confidence on FALSE/MOSTLY_FALSE/UNVERIFIABLE) — "
            f"consider lowering confidence ceilings"
        )

    return recs


def run_analysis(db_path: Path | None = None) -> dict[str, Any]:
    """Run full outcome analysis and return structured report."""
    db_path = db_path or _DEFAULT_DB
    outcomes = _load_outcomes(db_path)

    if not outcomes:
        return {"error": "no_data", "n_outcomes": 0}

    routing = analyze_routing_quality(outcomes)
    sources = analyze_source_effectiveness(outcomes)
    verdicts = analyze_verdict_distribution(outcomes)
    claim_types = analyze_claim_type_performance(outcomes)
    recommendations = generate_recommendations(routing, sources, verdicts, claim_types)

    return {
        "n_outcomes": len(outcomes),
        "routing_quality": routing,
        "source_effectiveness": sources,
        "verdict_distribution": verdicts,
        "claim_type_performance": claim_types,
        "recommendations": recommendations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze outcome logs")
    parser.add_argument("--db", type=str, default=None, help="Outcome DB path")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = run_analysis(db_path=Path(args.db) if args.db else None)

    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        logger.info("Report written to %s", args.output)
    else:
        print(output)


if __name__ == "__main__":
    main()
