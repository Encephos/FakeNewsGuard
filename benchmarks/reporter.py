"""Generate JSON and Markdown reports from benchmark scores."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.models import BenchmarkItem, ScoreReport


def write_json_report(report: ScoreReport, output_path: Path) -> None:
    """Write the score report as JSON."""
    output_path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )


def write_markdown_report(
    report: ScoreReport,
    items: list[BenchmarkItem],
    output_path: Path,
) -> None:
    """Write a human-readable Markdown report."""
    item_map = {i.id: i for i in items}
    lines: list[str] = []

    def add(line: str = "") -> None:
        lines.append(line)

    # ── Header ──────────────────────────────────────────────────────
    add("# FakeNewsGuard Benchmark Report")
    add()
    add(f"**Tier:** {report.tier}")
    add(f"**Timestamp:** {report.timestamp}")
    add(f"**Items:** {report.evaluated_items}/{report.total_items} evaluated "
        f"(Coverage: {report.coverage_rate:.1%})")
    add()

    # ── Overall Metrics ─────────────────────────────────────────────
    add("## Overall Metrics")
    add()
    add("| Metric | Value |")
    add("|--------|-------|")
    add(f"| Binary Accuracy | {report.binary_accuracy:.1%} |")
    add(f"| Binary Precision | {report.binary_precision:.1%} |")
    add(f"| Binary Recall | {report.binary_recall:.1%} |")
    add(f"| Binary F1 | {report.binary_f1:.1%} |")
    add(f"| Multiclass Accuracy | {report.multiclass_accuracy:.1%} |")
    add(f"| Coverage Rate | {report.coverage_rate:.1%} |")
    add()

    # ── Per Category ────────────────────────────────────────────────
    add("## Per-Category Metrics")
    add()
    add("| Category | Total | Accuracy | Precision | Recall | F1 |")
    add("|----------|-------|----------|-----------|--------|------|")
    for cat, m in sorted(report.per_category_metrics.items()):
        if isinstance(m, dict):
            add(f"| {cat} | {m['total']} | {m['accuracy']:.1%} | "
                f"{m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.1%} |")
        else:
            add(f"| {cat} | {m.total} | {m.accuracy:.1%} | "
                f"{m.precision:.1%} | {m.recall:.1%} | {m.f1:.1%} |")
    add()

    # ── Per Difficulty ──────────────────────────────────────────────
    add("## Per-Difficulty Metrics")
    add()
    add("| Difficulty | Total | Accuracy | F1 |")
    add("|------------|-------|----------|----|")
    for diff, m in sorted(report.per_difficulty_metrics.items()):
        if isinstance(m, dict):
            add(f"| {diff} | {m['total']} | {m['accuracy']:.1%} | {m['f1']:.1%} |")
        else:
            add(f"| {diff} | {m.total} | {m.accuracy:.1%} | {m.f1:.1%} |")
    add()

    # ── Confusion Matrix ────────────────────────────────────────────
    if report.confusion_matrix:
        add("## Confusion Matrix (Predicted x Expected)")
        add()
        all_labels = sorted(
            set(report.confusion_matrix.keys())
            | {lbl for row in report.confusion_matrix.values() for lbl in row}
        )
        header = "| Predicted \\ Expected | " + " | ".join(all_labels) + " |"
        sep = "|" + "---|" * (len(all_labels) + 1)
        add(header)
        add(sep)
        for predicted in all_labels:
            row = report.confusion_matrix.get(predicted, {})
            if isinstance(row, dict):
                cells = " | ".join(str(row.get(exp, 0)) for exp in all_labels)
            else:
                cells = " | ".join("0" for _ in all_labels)
            add(f"| {predicted} | {cells} |")
        add()

    # ── Confidence Calibration ──────────────────────────────────────
    add("## Confidence Calibration")
    add()
    add(f"**Expected Calibration Error (ECE):** {report.confidence_calibration.expected_calibration_error:.4f}")
    add()
    add("| Confidence Range | Count | Accuracy | Avg Confidence | Gap |")
    add("|------------------|-------|----------|----------------|-----|")
    for b in report.confidence_calibration.bins:
        if isinstance(b, dict):
            gap = abs(b.get("accuracy", 0) - b.get("avg_confidence", 0))
            add(f"| {b['range']} | {b.get('count', 0)} | "
                f"{b.get('accuracy', 0):.1%} | {b.get('avg_confidence', 0):.1%} | {gap:.1%} |")
        else:
            gap = abs(b.accuracy - b.avg_confidence)
            add(f"| {b.range} | {b.count} | {b.accuracy:.1%} | "
                f"{b.avg_confidence:.1%} | {gap:.1%} |")
    add()

    # ── False Positives ─────────────────────────────────────────────
    if report.false_positives:
        add("## False Positives (Real classified as Fake)")
        add()
        for item_id in report.false_positives[:10]:
            item = item_map.get(item_id)
            if item:
                text_preview = item.text[:120].replace("\n", " ")
                add(f"- **{item_id}** ({item.category.value}/{item.difficulty.value}): "
                    f"_{text_preview}..._")
        if len(report.false_positives) > 10:
            add(f"- ... and {len(report.false_positives) - 10} more")
        add()

    # ── False Negatives ─────────────────────────────────────────────
    if report.false_negatives:
        add("## False Negatives (Fake classified as Real)")
        add()
        for item_id in report.false_negatives[:10]:
            item = item_map.get(item_id)
            if item:
                text_preview = item.text[:120].replace("\n", " ")
                add(f"- **{item_id}** ({item.category.value}/{item.difficulty.value}): "
                    f"_{text_preview}..._")
        if len(report.false_negatives) > 10:
            add(f"- ... and {len(report.false_negatives) - 10} more")
        add()

    # ── Unverifiable ────────────────────────────────────────────────
    if report.unverifiable_items:
        add("## Unverifiable Items")
        add()
        add(f"**{len(report.unverifiable_items)} items** could not be evaluated:")
        add()
        for item_id in report.unverifiable_items:
            item = item_map.get(item_id)
            if item:
                add(f"- {item_id} ({item.category.value})")
        add()

    output_path.write_text("\n".join(lines), encoding="utf-8")
