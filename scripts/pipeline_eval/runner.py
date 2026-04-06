"""Core evaluation logic – runs each pipeline step in isolation."""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from typing import Any

from scripts.pipeline_eval.cases import EvalCase
from scripts.pipeline_eval.metrics import CaseReport, PipelineReport, StepResult

logger = logging.getLogger("pipeline_eval")


# ---------------------------------------------------------------------------
# Agent instantiation (replicates orchestrator.py lines 277-322)
# ---------------------------------------------------------------------------

def build_agents(config: "AppConfig") -> dict[str, Any]:
    """Build all pipeline agents with the same wiring as Orchestrator."""
    import os
    from config import ScoutTier
    from tools.llm import LLMClient
    from tools.web_search import WebSearchClient
    from tools.db.factory import create_cache

    # Disable Valkey if not reachable (use SQLite fallback)
    if config.valkey.enabled:
        try:
            import redis
            r = redis.from_url(config.valkey.url, socket_connect_timeout=2)
            r.ping()
        except Exception:
            logger.warning("Valkey nicht erreichbar, Fallback auf SQLite-Cache")
            config.valkey.enabled = False

    tier = config.tier
    tm = config.tier_models

    if tier == ScoutTier.LITE:
        llm_fast = LLMClient(replace(config.llm, model=tm.model_free), config.retry)
        llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
        llm_powerful = llm_fast
    elif tier == ScoutTier.PRO:
        llm_fast = LLMClient(replace(config.llm, model=tm.model_medium), config.retry)
        llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
        llm_powerful = llm_fast
    else:
        llm_fast = LLMClient(replace(config.llm, model=tm.model_medium), config.retry)
        llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
        llm_powerful = LLMClient(config.llm, config.retry)

    search = WebSearchClient(config.search, config.retry)
    cache = create_cache(config)

    from agents.claim_extractor import ClaimExtractorAgent
    from agents.fact_checker import FactCheckerAgent
    from agents.number_auditor import NumberAuditorAgent
    from agents.rhetoric_analyzer import RhetoricAnalyzerAgent
    from agents.synthesizer import SynthesizerAgent
    from tools.claim_router import ClaimRouter

    return {
        "claim_extractor": ClaimExtractorAgent(config, llm_fast, search, llm_small=llm_small),
        "fact_checker": FactCheckerAgent(config, llm_fast, search, cache, llm_small=llm_small),
        "number_auditor": NumberAuditorAgent(config, llm_powerful, search, cache),
        "rhetoric_analyzer": RhetoricAnalyzerAgent(config, llm_powerful, search),
        "synthesizer": SynthesizerAgent(config, llm_powerful, search),
        "router": ClaimRouter(),
        "config": config,
    }


# ---------------------------------------------------------------------------
# Token snapshot helpers (uses existing cost_tracker ContextVar)
# ---------------------------------------------------------------------------

def _get_accumulator() -> list:
    from tools.cost_tracker import _accumulator_ref
    return _accumulator_ref.get() or []


def _get_search_count() -> int:
    from tools.cost_tracker import _search_count_ref
    counter = _search_count_ref.get()
    return counter[0] if counter else 0


def _snapshot_tokens(acc: list, start_idx: int) -> tuple[int, int, int]:
    """Return (input_tokens, output_tokens, llm_calls) since start_idx."""
    entries = acc[start_idx:]
    inp = sum(e.input_tokens for e in entries)
    out = sum(e.output_tokens for e in entries)
    return inp, out, len(entries)


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

def run_step(name: str, fn, *args, **kwargs) -> tuple[StepResult, Any]:
    """Run a single pipeline step, capturing metrics.

    Returns (StepResult, output_value_or_None).
    """
    acc = _get_accumulator()
    idx_before = len(acc)
    search_before = _get_search_count()

    t0 = time.perf_counter()
    output = None
    error = None
    try:
        output = fn(*args, **kwargs)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error("Step %s failed: %s", name, error)
    elapsed = time.perf_counter() - t0

    acc = _get_accumulator()
    inp, out, calls = _snapshot_tokens(acc, idx_before)
    searches = _get_search_count() - search_before

    step = StepResult(
        name=name,
        latency_s=round(elapsed, 3),
        input_tokens=inp,
        output_tokens=out,
        llm_calls=calls,
        search_count=searches,
        error=error,
    )
    return step, output


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------

def evaluate_case(
    agents: dict[str, Any],
    case: EvalCase,
    *,
    skip_evidence: bool = False,
    enable_cove: bool = False,
) -> CaseReport:
    """Run all pipeline steps for a single test case."""
    from tools.cost_tracker import reset_accumulator
    reset_accumulator()

    report = CaseReport(case_id=case.id, category=case.category)
    text = case.text

    # -- Step 1: Claim Processing ------------------------------------------
    logger.info("[%s] Step 1: Claim Processing", case.id)
    step1, extraction = run_step(
        "claim_processing",
        agents["claim_extractor"].run_safe,
        text,
    )
    if extraction is not None:
        result, err = extraction
        if err:
            step1.error = err
            extraction = None
        else:
            extraction = result
    if extraction is not None:
        claims = extraction.claims
        step1.output_summary = {
            "claim_count": len(claims),
            "types": [c.type.value for c in claims],
            "priority_scores": [round(c.priority_score, 2) for c in claims],
        }
    else:
        claims = []
    report.steps.append(step1)

    if not claims:
        logger.warning("[%s] No claims extracted, skipping remaining steps", case.id)
        return report

    # Select top claim for remaining steps (cost efficiency)
    from models.schemas import ClaimType
    checkable = [c for c in claims if c.type != ClaimType.OPINION and c.is_checkworthy and c.is_valid_claim]
    if not checkable:
        logger.warning("[%s] No checkable claims after filtering", case.id)
        return report
    claim = checkable[0]

    # -- Step 2: Claim Routing (heuristic, no LLM) -------------------------
    logger.info("[%s] Step 2: Claim Routing", case.id)
    step2, route_output = run_step(
        "claim_routing",
        agents["router"].route_and_apply,
        claim,
    )
    route_result = None
    routed_claim = claim
    if route_output is not None:
        route_result, routed_claim = route_output
        step2.output_summary = {
            "confidence": round(route_result.confidence, 3),
            "domains": [d.value if hasattr(d, "value") else str(d) for d in route_result.domains],
            "jurisdiction": route_result.jurisdiction,
            "source_count": len(route_result.sources),
            "rationale": route_result.rationale[:120],
        }
    report.steps.append(step2)

    # -- Step 3: Evidence Building -----------------------------------------
    evidence_pack = None
    if skip_evidence:
        step3 = StepResult(name="evidence_building", skipped=True)
        report.steps.append(step3)
    else:
        logger.info("[%s] Step 3: Evidence Building", case.id)
        # Set topic_model on evidence builder (like orchestrator line 466-467)
        topic_model = getattr(extraction, "topic_model", None)
        eb = agents["fact_checker"]._evidence_builder
        if topic_model:
            eb.topic_model = topic_model

        step3, evidence_pack = run_step(
            "evidence_building",
            eb.run_safe,
            routed_claim,
            context=text,
        )
        if evidence_pack is not None:
            result, err = evidence_pack
            if err:
                step3.error = err
                evidence_pack = None
            else:
                evidence_pack = result
        if evidence_pack is not None:
            step3.output_summary = {
                "web_results": len(evidence_pack.web_results),
                "fact_check_results": len(getattr(evidence_pack, "fact_check_results", []) or []),
                "consensus": (
                    evidence_pack.evidence_quality.source_consensus.value
                    if evidence_pack.evidence_quality
                    else "unknown"
                ),
                "avg_relevance": round(
                    sum(i.relevance_score for i in evidence_pack.web_results)
                    / max(len(evidence_pack.web_results), 1),
                    3,
                ),
                "source_tiers": _count_source_tiers(evidence_pack),
            }
        report.steps.append(step3)

    # -- Step 4: Chain-of-Verification (optional) --------------------------
    cove_trace = None
    if enable_cove and evidence_pack is not None:
        logger.info("[%s] Step 4: CoVe", case.id)
        cove_proc = agents["fact_checker"]._cove_processor
        step4, cove_trace = run_step(
            "cove",
            cove_proc.process,
            claim,
            evidence_pack,
        )
        if cove_trace is not None:
            step4.output_summary = {
                "questions": len(getattr(cove_trace, "verification_questions", []) or []),
                "contradictions_found": getattr(cove_trace, "contradictions_found", False),
                "confidence_delta": getattr(cove_trace, "confidence_delta", 0.0),
            }
        report.steps.append(step4)
    else:
        step4 = StepResult(name="cove", skipped=True)
        report.steps.append(step4)

    # -- Steps 5+6 (Verdict + NumberAudit) parallel with Step 7 (Rhetoric) --
    # Rhetoric depends only on the raw text, not on evidence/verdict.
    # NumberAudit depends only on routed_claim + route_result, not on verdict.
    # → Run all three in parallel via thread pool.

    from orchestrator import _should_run_number_auditor
    _run_na = _should_run_number_auditor(routed_claim) and not skip_evidence

    def _run_verdict():
        """Run verdict (depends on evidence_pack)."""
        if evidence_pack is None:
            return StepResult(name="verdict", skipped=True), None
        logger.info("[%s] Step 5: Verdict", case.id)
        verdict_agent = agents["fact_checker"]._verdict_agent
        _topic_model = getattr(extraction, "topic_model", None)
        verdict_input = {
            "claim": routed_claim,
            "evidence_pack": evidence_pack,
            "cove_trace": cove_trace,
            "topic_model": _topic_model,
        }
        _step5, verdict_output = run_step(
            "verdict",
            verdict_agent.run_safe,
            verdict_input,
            context=text,
        )
        _verdict_result = None
        if verdict_output is not None:
            res, err = verdict_output
            if err:
                _step5.error = err
            else:
                _verdict_result = res
        if _verdict_result is not None:
            _step5.output_summary = {
                "rating": _verdict_result.rating.value,
                "confidence": round(_verdict_result.confidence, 3),
                "sources_count": len(_verdict_result.sources),
                "evidence_excerpt": (_verdict_result.evidence or "")[:200],
            }
        return _step5, _verdict_result

    def _run_number_audit():
        """Run number audit (depends only on routed_claim + route_result)."""
        if not _run_na:
            return StepResult(name="number_audit", skipped=True), None
        logger.info("[%s] Step 6: Number Audit", case.id)
        na_input = {"claim": routed_claim, "route_result": route_result}
        _step6, na_output = run_step(
            "number_audit",
            agents["number_auditor"].run_safe,
            na_input,
        )
        _na_result = None
        if na_output is not None:
            res, err = na_output
            if err:
                _step6.error = err
            else:
                _na_result = res
        if _na_result is not None:
            _step6.output_summary = {
                "rating": _na_result.rating.value if hasattr(_na_result, "rating") else "N/A",
                "manipulation_type": getattr(_na_result, "manipulation_type", "N/A"),
            }
        return _step6, _na_result

    def _run_rhetoric():
        """Run rhetoric analysis (independent of evidence/verdict)."""
        logger.info("[%s] Step 7: Rhetoric Analysis", case.id)
        _step7, rhet_output = run_step(
            "rhetoric",
            agents["rhetoric_analyzer"].run_safe,
            text,
        )
        _rhetoric_result = None
        if rhet_output is not None:
            res, err = rhet_output
            if err:
                _step7.error = err
            else:
                _rhetoric_result = res
        if _rhetoric_result is not None:
            _step7.output_summary = {
                "technique_count": len(_rhetoric_result.techniques),
                "techniques": [t.technique for t in _rhetoric_result.techniques],
                "severities": [t.severity.value for t in _rhetoric_result.techniques],
                "narrative_count": len(getattr(_rhetoric_result, "narrative_patterns", []) or []),
                "overall_framing_excerpt": (_rhetoric_result.overall_framing or "")[:200],
            }
        return _step7, _rhetoric_result

    # Run verdict, number_audit, and rhetoric all in parallel.
    # Each thread needs its own copy_context() so ContextVars
    # (cost_tracker accumulator) propagate correctly.
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_verdict = pool.submit(copy_context().run, _run_verdict)
        fut_na = pool.submit(copy_context().run, _run_number_audit)
        fut_rhetoric = pool.submit(copy_context().run, _run_rhetoric)
        step5, fact_check_result = fut_verdict.result()
        step6, number_audit_result = fut_na.result()
        step7, rhetoric_result = fut_rhetoric.result()

    report.steps.append(step5)
    report.steps.append(step6)
    report.steps.append(step7)

    # -- Step 8: Synthesis -------------------------------------------------
    fact_checks = [fact_check_result] if fact_check_result is not None else []
    number_audits = [number_audit_result] if number_audit_result is not None else []

    if fact_checks or rhetoric_result:
        logger.info("[%s] Step 8: Synthesis", case.id)
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
            "cross_claim_evidence_map": {},
            "consistency_warnings": [],
        }
        step8, synth_output = run_step(
            "synthesis",
            agents["synthesizer"].run_safe,
            synthesis_input,
        )
        synth_result = None
        if synth_output is not None:
            result, err = synth_output
            if err:
                step8.error = err
            else:
                synth_result = result
        if synth_result is not None:
            step8.output_summary = {
                "overall_rating": synth_result.overall_rating.value,
                "confidence": round(synth_result.confidence, 3),
                "key_corrections_count": len(synth_result.key_corrections),
                "manipulation_techniques_count": len(synth_result.manipulation_techniques),
                "summary_excerpt": (synth_result.summary or "")[:200],
            }
        report.steps.append(step8)
    else:
        step8 = StepResult(name="synthesis", skipped=True)
        report.steps.append(step8)

    return report


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------

def evaluate_all(
    config: "AppConfig",
    cases: list[EvalCase],
    *,
    skip_evidence: bool = False,
    enable_cove: bool = False,
) -> PipelineReport:
    """Run evaluation for all cases."""
    agents = build_agents(config)
    report = PipelineReport(tier=config.tier.value)

    for i, case in enumerate(cases, 1):
        logger.info("=" * 60)
        logger.info("Case %d/%d: %s (%s)", i, len(cases), case.id, case.category)
        logger.info("=" * 60)
        try:
            case_report = evaluate_case(
                agents, case,
                skip_evidence=skip_evidence,
                enable_cove=enable_cove,
            )
        except Exception as exc:
            logger.error("Case %s failed completely: %s", case.id, exc, exc_info=True)
            case_report = CaseReport(case_id=case.id, category=case.category)
            case_report.steps.append(StepResult(
                name="FATAL",
                error=f"{type(exc).__name__}: {exc}",
            ))
        report.cases.append(case_report)

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_source_tiers(evidence_pack) -> dict[str, int]:
    """Count evidence items per domain tier."""
    tiers: dict[str, int] = {}
    for item in evidence_pack.web_results:
        source = getattr(item, "source", None)
        tier = getattr(source, "domain_tier", "unknown") if source else "unknown"
        key = str(tier)
        tiers[key] = tiers.get(key, 0) + 1
    return tiers
