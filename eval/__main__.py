"""CLI entry point: python -m eval <command> [options]"""

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


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_replay(args: argparse.Namespace) -> int:
    """Run deterministic replay evaluation."""
    from eval.reports import generate_markdown_report, load_baseline, save_baseline
    from eval.runner_replay import ReplayRunner

    runner = ReplayRunner(
        cases_path=Path(args.cases) if args.cases else None,
        snapshots_dir=Path(args.snapshots) if args.snapshots else None,
    )

    categories = args.categories.split(",") if args.categories else None
    case_ids = args.case_ids.split(",") if args.case_ids else None

    results = runner.run(categories=categories, case_ids=case_ids)

    if not results:
        print("No cases evaluated (no snapshots found?).")
        return 0

    # Load baseline for comparison
    baseline = None
    if args.baseline:
        baseline = load_baseline(args.baseline)

    # Generate report
    md = generate_markdown_report(results, baseline)
    print(md)

    # Save baseline if requested
    if args.save_baseline:
        path = save_baseline(results)
        print(f"\nBaseline saved: {path}")

    # Fail on regression
    if args.fail_on_regression and baseline:
        from eval.metrics import aggregate_global, detect_regressions
        regressions = detect_regressions(
            aggregate_global(results), baseline.get("global", {}),
        )
        if regressions:
            print(f"\n{len(regressions)} regression(s) detected!")
            return 1

    # Fail on any error-level violations
    failed = [r for r in results if not r.passed]
    if failed and args.fail_on_regression:
        print(f"\n{len(failed)} case(s) failed expectations.")
        return 1

    return 0


def cmd_live(args: argparse.Namespace) -> int:
    """Run live retrieval evaluation."""
    from config import AppConfig
    from eval.reports import generate_markdown_report, save_baseline
    from eval.runner_live import LiveRunner
    from tools.data_loader import load_config

    config = load_config()

    runner = LiveRunner(
        config=config,
        cases_path=Path(args.cases) if args.cases else None,
        snapshots_dir=Path(args.snapshots) if args.snapshots else None,
    )

    categories = args.categories.split(",") if args.categories else None
    case_ids = args.case_ids.split(",") if args.case_ids else None
    backends = tuple(args.backends.split(","))

    results = asyncio.run(runner.run(
        categories=categories,
        case_ids=case_ids,
        backends=backends,
    ))

    if not results:
        print("No cases evaluated.")
        return 0

    md = generate_markdown_report(results)
    print(md)

    if args.save_baseline:
        path = save_baseline(results)
        print(f"\nBaseline saved: {path}")

    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two baselines."""
    from eval.metrics import detect_regressions
    from eval.reports import load_baseline

    b1 = load_baseline(args.baseline1)
    b2 = load_baseline(args.baseline2)

    if not b1 or not b2:
        print("Could not load one or both baselines.")
        return 1

    print(f"Comparing: {b1.get('timestamp', '?')} → {b2.get('timestamp', '?')}")
    print()

    regressions = detect_regressions(
        b2.get("global", {}), b1.get("global", {}),
    )
    improvements = detect_regressions(
        b1.get("global", {}), b2.get("global", {}),
    )

    if regressions:
        print("## Regressions")
        for r in regressions:
            print(f"  {r.metric}: {r.baseline_value:.4f} → {r.current_value:.4f} (delta={r.delta:+.4f})")

    if improvements:
        print("\n## Improvements")
        for r in improvements:
            print(f"  {r.metric}: {r.baseline_value:.4f} → {r.current_value:.4f} (delta={r.delta:+.4f})")

    if not regressions and not improvements:
        print("No significant changes detected.")

    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Run a minimal end-to-end smoke test."""
    print("Smoke test mode: running 2-3 cases through full pipeline...")

    from config import AppConfig
    from eval.dataset import load_cases, filter_cases
    from tools.data_loader import load_config

    config = load_config()
    cases = load_cases()
    # Pick one case per category (max 3)
    seen_cats: set[str] = set()
    smoke_cases = []
    for c in cases:
        if c.category.value not in seen_cats and len(smoke_cases) < 3:
            smoke_cases.append(c)
            seen_cats.add(c.category.value)

    if not smoke_cases:
        print("No cases found.")
        return 1

    from orchestrator import Orchestrator
    orch = Orchestrator(config)

    for case in smoke_cases:
        print(f"\n--- Smoke: {case.id} ({case.category.value}) ---")
        try:
            result = asyncio.run(orch.analyze_async(case.claim_text))
            print(f"  Rating: {result.overall_rating}")
            print(f"  Confidence: {result.confidence:.2f}")
            print(f"  Claims: {len(result.claims_analysis)}")
        except Exception as exc:
            print(f"  FAILED: {exc}")

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="FakeNewsGuard retrieval evaluation framework",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # replay
    p_replay = sub.add_parser("replay", help="Deterministic replay evaluation")
    p_replay.add_argument("--cases", help="Path to cases.jsonl")
    p_replay.add_argument("--snapshots", help="Path to snapshots directory")
    p_replay.add_argument("--categories", help="Comma-separated category filter")
    p_replay.add_argument("--case-ids", help="Comma-separated case ID filter")
    p_replay.add_argument("--baseline", default=None, help="Baseline ID for comparison")
    p_replay.add_argument("--save-baseline", action="store_true")
    p_replay.add_argument("--fail-on-regression", action="store_true")

    # live
    p_live = sub.add_parser("live", help="Live retrieval evaluation")
    p_live.add_argument("--cases", help="Path to cases.jsonl")
    p_live.add_argument("--snapshots", help="Path to snapshots directory")
    p_live.add_argument("--categories", help="Comma-separated category filter")
    p_live.add_argument("--case-ids", help="Comma-separated case ID filter")
    p_live.add_argument("--backends", default="searxng", help="Comma-separated backends")
    p_live.add_argument("--save-baseline", action="store_true")

    # compare
    p_compare = sub.add_parser("compare", help="Compare two baselines")
    p_compare.add_argument("--baseline1", required=True)
    p_compare.add_argument("--baseline2", default="latest")

    # smoke
    p_smoke = sub.add_parser("smoke", help="End-to-end smoke test")
    p_smoke.add_argument("--tier", default="lite", choices=["lite", "pro", "max"])

    args = parser.parse_args()
    _setup_logging(args.verbose)

    commands = {
        "replay": cmd_replay,
        "live": cmd_live,
        "compare": cmd_compare,
        "smoke": cmd_smoke,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
