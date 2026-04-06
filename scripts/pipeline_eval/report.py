"""Report generation – JSON and human-readable output."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.pipeline_eval.metrics import CaseReport, PipelineReport, StepResult


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def generate_json_report(report: PipelineReport) -> dict:
    """Convert PipelineReport to a JSON-serializable dict."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tier": report.tier,
        "total_latency_s": round(report.total_latency, 2),
        "total_tokens": report.total_tokens,
        "case_count": len(report.cases),
        "cases": [_case_to_dict(c) for c in report.cases],
        "analysis": _analyze(report),
    }


def _case_to_dict(case: CaseReport) -> dict:
    return {
        "case_id": case.case_id,
        "category": case.category,
        "total_latency_s": round(case.total_latency, 2),
        "total_tokens": case.total_tokens,
        "total_llm_calls": case.total_llm_calls,
        "error_count": case.error_count,
        "steps": [_step_to_dict(s) for s in case.steps],
    }


def _step_to_dict(step: StepResult) -> dict:
    d = {
        "name": step.name,
        "latency_s": round(step.latency_s, 3),
        "tokens": step.total_tokens,
        "llm_calls": step.llm_calls,
        "search_count": step.search_count,
    }
    if step.skipped:
        d["skipped"] = True
    if step.error:
        d["error"] = step.error
    if step.output_summary:
        d["output"] = _make_serializable(step.output_summary)
    return d


def _make_serializable(obj):
    """Recursively convert non-serializable objects."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(i) for i in obj]
    if hasattr(obj, "value"):
        return obj.value
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


# ---------------------------------------------------------------------------
# Analysis / bottleneck detection
# ---------------------------------------------------------------------------

def _analyze(report: PipelineReport) -> dict:
    """Generate bottleneck analysis from the report."""
    if not report.cases:
        return {"recommendations": ["No cases evaluated."]}

    avgs = report.step_averages()
    total_avg_latency = sum(v["avg_latency_s"] for v in avgs.values())

    bottlenecks = []
    recommendations = []

    # Sort steps by average latency descending
    sorted_steps = sorted(avgs.items(), key=lambda x: -x[1]["avg_latency_s"])

    for name, vals in sorted_steps:
        pct = (vals["avg_latency_s"] / total_avg_latency * 100) if total_avg_latency > 0 else 0

        bottlenecks.append({
            "step": name,
            "avg_latency_s": round(vals["avg_latency_s"], 2),
            "pct_of_total": round(pct, 1),
            "avg_tokens": round(vals["avg_tokens"]),
            "avg_llm_calls": round(vals["avg_llm_calls"], 1),
            "error_rate": round(vals["error_rate"], 2),
        })

        # Recommendations
        if pct > 40:
            recommendations.append(
                f"{name}: {pct:.0f}% der Gesamtlatenz (avg {vals['avg_latency_s']:.1f}s) "
                f"-> primaerer Bottleneck"
            )
        if vals["error_rate"] > 0.2:
            recommendations.append(
                f"{name}: Error-Rate {vals['error_rate']:.0%} -> Zuverlaessigkeitsproblem"
            )
        if vals["avg_llm_calls"] > 5:
            recommendations.append(
                f"{name}: {vals['avg_llm_calls']:.0f} LLM-Calls pro Case -> Stage-Merging pruefen"
            )

    # Quality checks from output summaries
    _check_verdict_quality(report, recommendations)
    _check_evidence_quality(report, recommendations)
    _check_rhetoric_quality(report, recommendations)

    return {
        "bottlenecks": bottlenecks,
        "recommendations": recommendations if recommendations else ["Keine auffaelligen Bottlenecks."],
    }


def _check_verdict_quality(report: PipelineReport, recs: list[str]) -> None:
    confidences = []
    for case in report.cases:
        for step in case.steps:
            if step.name == "verdict" and not step.skipped and not step.error:
                conf = step.output_summary.get("confidence", -1)
                if conf >= 0:
                    confidences.append(conf)
    if confidences:
        avg = sum(confidences) / len(confidences)
        if avg < 0.4:
            recs.append(
                f"Verdict Confidence avg {avg:.2f} -> Evidence-Qualitaet verbessern"
            )


def _check_evidence_quality(report: PipelineReport, recs: list[str]) -> None:
    relevances = []
    for case in report.cases:
        for step in case.steps:
            if step.name == "evidence_building" and not step.skipped and not step.error:
                rel = step.output_summary.get("avg_relevance", -1)
                if rel >= 0:
                    relevances.append(rel)
    if relevances:
        avg = sum(relevances) / len(relevances)
        if avg < 0.5:
            recs.append(
                f"Evidence avg Relevanz {avg:.2f} -> Query-Tuning noetig"
            )


def _check_rhetoric_quality(report: PipelineReport, recs: list[str]) -> None:
    for case in report.cases:
        for step in case.steps:
            if step.name == "rhetoric" and not step.skipped and not step.error:
                count = step.output_summary.get("technique_count", 0)
                if case.category == "rhetorik" and count == 0:
                    recs.append(
                        f"Case {case.case_id}: Rhetorik-lastiger Text, aber 0 Techniken erkannt"
                    )


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------

def print_human_report(report: PipelineReport) -> None:
    """Print a formatted report to stdout."""
    total_tokens = report.total_tokens
    print()
    print("=" * 65)
    print(f"  Pipeline Evaluation Report")
    print(f"  Tier: {report.tier} | Cases: {len(report.cases)} | "
          f"Total: {report.total_latency:.1f}s | Tokens: {total_tokens:,}")
    print("=" * 65)

    for case in report.cases:
        print()
        print(f"-- Case {case.case_id} ({case.category}) " + "-" * 35)
        print(f"{'Step':<25} {'Latency':>8} {'Tokens':>8} {'LLM':>5} {'Search':>7} {'Err':>5}")
        print("-" * 65)

        for step in case.steps:
            if step.skipped:
                print(f"{step.name:<25} {'skipped':>8}")
                continue
            err_mark = "ERR" if step.error else "-"
            print(
                f"{step.name:<25} "
                f"{step.latency_s:>7.1f}s "
                f"{step.total_tokens:>8,} "
                f"{step.llm_calls:>5} "
                f"{step.search_count:>7} "
                f"{err_mark:>5}"
            )

            # Print key output details
            _print_output_summary(step)

        print("-" * 65)
        print(
            f"{'Total':<25} "
            f"{case.total_latency:>7.1f}s "
            f"{case.total_tokens:>8,} "
            f"{case.total_llm_calls:>5}"
        )

    # Bottleneck analysis
    analysis = _analyze(report)
    print()
    print("=" * 65)
    print("  Bottleneck Analysis")
    print("=" * 65)
    for rec in analysis.get("recommendations", []):
        print(f"  -> {rec}")
    print()


def _print_output_summary(step: StepResult) -> None:
    """Print condensed output details for a step."""
    s = step.output_summary
    if not s:
        return

    if step.name == "claim_processing":
        print(f"  {'':>24} claims={s.get('claim_count',0)}, types={s.get('types',[])}")
    elif step.name == "claim_routing":
        print(f"  {'':>24} conf={s.get('confidence','?')}, domains={s.get('domains',[])}")
    elif step.name == "evidence_building":
        print(f"  {'':>24} results={s.get('web_results',0)}, "
              f"avg_rel={s.get('avg_relevance','?')}, tiers={s.get('source_tiers',{})}")
    elif step.name == "verdict":
        print(f"  {'':>24} rating={s.get('rating','?')}, conf={s.get('confidence','?')}")
    elif step.name == "number_audit":
        print(f"  {'':>24} rating={s.get('rating','?')}, "
              f"manipulation={s.get('manipulation_type','?')}")
    elif step.name == "rhetoric":
        print(f"  {'':>24} techniques={s.get('technique_count',0)}: "
              f"{s.get('techniques',[])} | narratives={s.get('narrative_count',0)}")
    elif step.name == "synthesis":
        print(f"  {'':>24} rating={s.get('overall_rating','?')}, "
              f"conf={s.get('confidence','?')}, "
              f"corrections={s.get('key_corrections_count',0)}")
