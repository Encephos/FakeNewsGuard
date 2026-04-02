"""CLI entry point: python -m benchmarks <command> [options]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── Commands ────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    """Run benchmark items through the pipeline."""
    from benchmarks.dataset import load_dataset, filter_items
    from benchmarks.models import DisinfoCategory, Difficulty
    from benchmarks.runner import BenchmarkRunner

    _setup_logging(args.verbose)

    items = load_dataset(Path(args.dataset) if args.dataset else None)

    # Optional filtering
    if args.category:
        items = filter_items(items, category=DisinfoCategory(args.category))
    if args.difficulty:
        items = filter_items(items, difficulty=Difficulty(args.difficulty))
    if args.limit:
        items = items[: args.limit]

    if not items:
        print("No items to run.", file=sys.stderr)
        return 1

    results_dir = Path(args.results_dir)
    runner = BenchmarkRunner(
        results_dir=results_dir,
        tier=args.tier,
        max_parallel=args.parallel,
        delay=args.delay,
        verbose=args.verbose,
    )

    print(f"\n  Benchmark: {len(items)} items, tier={args.tier}", file=sys.stderr)
    print(f"  Results:   {results_dir}\n", file=sys.stderr)

    results = asyncio.run(runner.run_all(items, resume=args.resume))

    succeeded = sum(1 for r in results if r.error is None)
    failed = sum(1 for r in results if r.error is not None)
    print(
        f"\n  Done: {succeeded} succeeded, {failed} failed",
        file=sys.stderr,
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Score results against ground truth."""
    from benchmarks.dataset import load_dataset
    from benchmarks.runner import load_results_from_dir
    from benchmarks.scorer import score_all

    _setup_logging(args.verbose)

    items = load_dataset(Path(args.dataset) if args.dataset else None)
    results_dir = Path(args.results_dir)

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}", file=sys.stderr)
        return 1

    results = load_results_from_dir(results_dir)
    if not results:
        print("No results found.", file=sys.stderr)
        return 1

    tier = results[0].tier if results else "unknown"
    report = score_all(items, results, tier=tier)

    # Print summary to stdout
    print(f"\n  === Benchmark Score (tier={report.tier}) ===\n")
    print(f"  Items:      {report.evaluated_items}/{report.total_items} "
          f"(coverage {report.coverage_rate:.1%})")
    print(f"  Accuracy:   {report.binary_accuracy:.1%}")
    print(f"  Precision:  {report.binary_precision:.1%}")
    print(f"  Recall:     {report.binary_recall:.1%}")
    print(f"  F1:         {report.binary_f1:.1%}")
    print(f"  ECE:        {report.confidence_calibration.expected_calibration_error:.4f}")
    print()

    if report.false_negatives:
        print(f"  False Negatives: {len(report.false_negatives)} "
              f"(fake missed: {', '.join(report.false_negatives[:5])})")
    if report.false_positives:
        print(f"  False Positives: {len(report.false_positives)} "
              f"(real flagged: {', '.join(report.false_positives[:5])})")
    if report.unverifiable_items:
        print(f"  Unverifiable:    {len(report.unverifiable_items)}")
    print()

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate detailed report files."""
    from benchmarks.dataset import load_dataset
    from benchmarks.reporter import write_json_report, write_markdown_report
    from benchmarks.runner import load_results_from_dir
    from benchmarks.scorer import score_all

    _setup_logging(args.verbose)

    items = load_dataset(Path(args.dataset) if args.dataset else None)
    results_dir = Path(args.results_dir)

    if not results_dir.exists():
        print(f"Results directory not found: {results_dir}", file=sys.stderr)
        return 1

    results = load_results_from_dir(results_dir)
    if not results:
        print("No results found.", file=sys.stderr)
        return 1

    tier = results[0].tier if results else "unknown"
    report = score_all(items, results, tier=tier)

    fmt = args.format
    if fmt in ("json", "both"):
        json_path = results_dir / "report.json"
        write_json_report(report, json_path)
        print(f"  JSON report: {json_path}", file=sys.stderr)

    if fmt in ("markdown", "both"):
        md_path = results_dir / "report.md"
        write_markdown_report(report, items, md_path)
        print(f"  Markdown report: {md_path}", file=sys.stderr)

    return 0


# ── Argument Parser ─────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks",
        description="German disinformation benchmark for FakeNewsGuard",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- run --
    p_run = sub.add_parser("run", help="Run benchmark items through the pipeline")
    p_run.add_argument("--tier", default="pro", choices=["lite", "pro", "max"])
    p_run.add_argument("--results-dir", default="benchmarks/results/default",
                       help="Directory to store per-item results")
    p_run.add_argument("--dataset", default=None, help="Path to benchmark JSON")
    p_run.add_argument("--resume", action="store_true", default=True,
                       help="Skip already-completed items (default: True)")
    p_run.add_argument("--no-resume", dest="resume", action="store_false")
    p_run.add_argument("--parallel", type=int, default=1,
                       help="Max concurrent items (default: 1)")
    p_run.add_argument("--delay", type=float, default=2.0,
                       help="Delay between items in seconds (default: 2.0)")
    p_run.add_argument("--category", default=None, help="Filter by category")
    p_run.add_argument("--difficulty", default=None, help="Filter by difficulty")
    p_run.add_argument("--limit", type=int, default=None, help="Max items to run")
    p_run.add_argument("--verbose", "-v", action="store_true")
    p_run.set_defaults(func=cmd_run)

    # -- score --
    p_score = sub.add_parser("score", help="Score results against ground truth")
    p_score.add_argument("--results-dir", required=True,
                         help="Directory with per-item result JSON files")
    p_score.add_argument("--dataset", default=None, help="Path to benchmark JSON")
    p_score.add_argument("--verbose", "-v", action="store_true")
    p_score.set_defaults(func=cmd_score)

    # -- report --
    p_report = sub.add_parser("report", help="Generate detailed report files")
    p_report.add_argument("--results-dir", required=True,
                          help="Directory with per-item result JSON files")
    p_report.add_argument("--dataset", default=None, help="Path to benchmark JSON")
    p_report.add_argument("--format", default="both",
                          choices=["json", "markdown", "both"])
    p_report.add_argument("--verbose", "-v", action="store_true")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
