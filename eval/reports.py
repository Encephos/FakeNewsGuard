"""Report generation: JSON and Markdown output with baseline comparison."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from eval.metrics import aggregate_by_category, aggregate_global, detect_regressions
from eval.models import CaseResult, MetricsReport, Regression

_BASELINES_DIR = Path(__file__).parent / "data" / "baselines"


# ── Baseline Management ─────────────────────────────────────────────────────


def save_baseline(
    results: list[CaseResult],
    baselines_dir: Optional[Path] = None,
) -> Path:
    """Save current metrics as a baseline snapshot."""
    bdir = baselines_dir or _BASELINES_DIR
    bdir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = bdir / f"baseline_{ts}.json"
    data = {
        "timestamp": ts,
        "global": aggregate_global(results),
        "per_category": aggregate_by_category(results),
        "cases": {r.case_id: r.metrics.model_dump() for r in results},
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also update "latest" symlink / file
    latest = bdir / "latest.json"
    latest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return path


def load_baseline(
    baseline_id: str = "latest",
    baselines_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Load a baseline by ID or 'latest'."""
    bdir = baselines_dir or _BASELINES_DIR
    if baseline_id == "latest":
        path = bdir / "latest.json"
    else:
        path = bdir / f"baseline_{baseline_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Report Generation ────────────────────────────────────────────────────────


def generate_report(
    results: list[CaseResult],
    baseline: Optional[dict] = None,
) -> MetricsReport:
    """Build a MetricsReport from evaluation results."""
    global_metrics = aggregate_global(results)
    per_category = aggregate_by_category(results)

    # Detect regressions
    regressions: list[Regression] = []
    if baseline and "global" in baseline:
        regressions = detect_regressions(global_metrics, baseline["global"])

    # Worst cases: sorted by violation count descending
    worst = sorted(results, key=lambda r: len(r.violations), reverse=True)
    worst_cases = [r for r in worst if r.violations][:10]

    return MetricsReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        baseline_id=baseline.get("timestamp") if baseline else None,
        total_cases=len(results),
        passed_cases=sum(1 for r in results if r.passed),
        failed_cases=sum(1 for r in results if not r.passed),
        global_metrics=global_metrics,
        per_category=per_category,
        worst_cases=worst_cases,
        regressions=regressions,
    )


def generate_json_report(
    results: list[CaseResult],
    baseline: Optional[dict] = None,
) -> str:
    """Generate a JSON report string."""
    report = generate_report(results, baseline)
    return report.model_dump_json(indent=2)


def generate_markdown_report(
    results: list[CaseResult],
    baseline: Optional[dict] = None,
) -> str:
    """Generate a human-readable Markdown report."""
    report = generate_report(results, baseline)
    lines: list[str] = []

    # Header
    lines.append("# Retrieval Evaluation Report")
    lines.append(f"\n**Timestamp:** {report.timestamp}")
    if report.baseline_id:
        lines.append(f"**Baseline:** {report.baseline_id}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(f"- Total cases: {report.total_cases}")
    lines.append(f"- Passed: {report.passed_cases}")
    lines.append(f"- Failed: {report.failed_cases}")
    lines.append("")

    # Global Metrics
    lines.append("## Global Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for metric, value in sorted(report.global_metrics.items()):
        lines.append(f"| {metric} | {value:.4f} |")
    lines.append("")

    # Per-Category
    if report.per_category:
        lines.append("## Per-Category Metrics")
        lines.append("")
        cats = sorted(report.per_category.keys())
        key_metrics = [
            "preferred_domain_hit_rate", "low_trust_rate", "offtopic_rate",
            "direct_evidence_rate", "freshness_hit_rate", "source_diversity",
        ]
        header = "| Category | " + " | ".join(key_metrics) + " |"
        sep = "|" + "---|" * (len(key_metrics) + 1)
        lines.append(header)
        lines.append(sep)
        for cat in cats:
            vals = report.per_category[cat]
            row = f"| {cat} | "
            row += " | ".join(f"{vals.get(m, 0):.3f}" for m in key_metrics)
            row += " |"
            lines.append(row)
        lines.append("")

    # Regressions
    if report.regressions:
        lines.append("## Regressions")
        lines.append("")
        lines.append("| Metric | Baseline | Current | Delta |")
        lines.append("|--------|----------|---------|-------|")
        for reg in report.regressions:
            lines.append(
                f"| {reg.metric} | {reg.baseline_value:.4f} | "
                f"{reg.current_value:.4f} | {reg.delta:+.4f} |"
            )
        lines.append("")

    # Worst Cases
    if report.worst_cases:
        lines.append("## Worst Cases")
        lines.append("")
        for case_r in report.worst_cases[:10]:
            lines.append(f"### {case_r.case_id} ({case_r.category.value})")
            for v in case_r.violations:
                lines.append(f"- **{v.metric}** [{v.severity}]: expected {v.expected}, got {v.actual}")
            lines.append("")

    # High low-trust / off-topic cases
    high_lt = [r for r in results if r.metrics.low_trust_rate > 0.3]
    if high_lt:
        lines.append("## High Low-Trust Cases")
        lines.append("")
        for r in high_lt:
            lines.append(f"- {r.case_id}: low_trust_rate={r.metrics.low_trust_rate:.3f}")
        lines.append("")

    high_ot = [r for r in results if r.metrics.offtopic_rate > 0.3]
    if high_ot:
        lines.append("## High Off-Topic Cases")
        lines.append("")
        for r in high_ot:
            lines.append(f"- {r.case_id}: offtopic_rate={r.metrics.offtopic_rate:.3f}")
        lines.append("")

    # Current-state freshness failures
    fresh_fail = [
        r for r in results
        if r.metrics.freshness_hit_rate < 0.3
        and any(v.metric == "freshness_hit_rate" for v in r.violations)
    ]
    if fresh_fail:
        lines.append("## Current-State Freshness Failures")
        lines.append("")
        for r in fresh_fail:
            lines.append(
                f"- {r.case_id}: freshness_hit_rate={r.metrics.freshness_hit_rate:.3f}"
            )
        lines.append("")

    # Scrape waste outliers
    high_waste = [r for r in results if r.metrics.scrape_waste_rate > 0.5]
    if high_waste:
        lines.append("## Scrape Waste Outliers")
        lines.append("")
        for r in high_waste:
            lines.append(
                f"- {r.case_id}: scrape_waste_rate={r.metrics.scrape_waste_rate:.3f}"
            )
        lines.append("")

    return "\n".join(lines)
