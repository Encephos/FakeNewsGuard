#!/usr/bin/env python3
"""CLI entry point: Pipeline Step-by-Step Evaluation.

Usage:
    .venv/bin/python3 scripts/evaluate_pipeline.py
    .venv/bin/python3 scripts/evaluate_pipeline.py --tier lite --cases eval-1,eval-2
    .venv/bin/python3 scripts/evaluate_pipeline.py --json --output reports/pipeline_eval.json
    .venv/bin/python3 scripts/evaluate_pipeline.py --skip-evidence
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()


def _check_searxng(config) -> bool:
    """Pre-flight check: is SearXNG reachable?"""
    import httpx
    url = getattr(config.searxng, "url", "") or getattr(config.searxng, "base_url", "")
    if not url:
        return False
    try:
        resp = httpx.get(url, timeout=5.0)
        return resp.status_code < 400
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline Step-by-Step Evaluation")
    parser.add_argument("--tier", choices=["lite", "pro", "max"], default=None,
                        help="Override Scout tier (default: from config)")
    parser.add_argument("--cases", type=str, default=None,
                        help="Comma-separated case IDs (default: all)")
    parser.add_argument("--skip-evidence", action="store_true",
                        help="Skip evidence building + verdict + number audit")
    parser.add_argument("--cove", action="store_true",
                        help="Enable Chain-of-Verification (default: off)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output as JSON instead of formatted table")
    parser.add_argument("--output", type=str, default=None,
                        help="Save report to file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    from config import AppConfig, ScoutTier
    config = AppConfig()

    if args.tier:
        tier_map = {"lite": ScoutTier.LITE, "pro": ScoutTier.PRO, "max": ScoutTier.MAX}
        config.tier = tier_map[args.tier]

    # Pre-flight checks
    if not args.skip_evidence:
        if not _check_searxng(config):
            logging.warning(
                "SearXNG nicht erreichbar. Evidence-Steps werden wahrscheinlich fehlschlagen. "
                "Nutze --skip-evidence um nur LLM-Steps zu testen."
            )

    # Load cases
    from scripts.pipeline_eval.cases import get_cases
    case_ids = args.cases.split(",") if args.cases else None
    cases = get_cases(case_ids)

    if not cases:
        print("Keine Testfaelle ausgewaehlt.", file=sys.stderr)
        return 1

    print(f"Starte Evaluation: {len(cases)} Cases, Tier={config.tier.value}")
    print()

    # Run evaluation
    from scripts.pipeline_eval.runner import evaluate_all
    report = evaluate_all(
        config, cases,
        skip_evidence=args.skip_evidence,
        enable_cove=args.cove,
    )

    # Output
    from scripts.pipeline_eval.report import generate_json_report, print_human_report

    if args.json_output:
        json_report = generate_json_report(report)
        output = json.dumps(json_report, indent=2, ensure_ascii=False)
        print(output)
    else:
        print_human_report(report)

    # Save to file if requested
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_report = generate_json_report(report)
        output_path.write_text(json.dumps(json_report, indent=2, ensure_ascii=False))
        print(f"Report gespeichert: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
