"""Celery task for admin-triggered retrieval evaluation runs.

Runs a random subset of eval cases through the LiveRunner,
storing per-case results and aggregated metrics in Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import redis

from celery.exceptions import SoftTimeLimitExceeded

from worker.celery_app import celery_app

logger = logging.getLogger("fng-eval")

_EVAL_PREFIX = "fng:eval:"
_HISTORY_KEY = "fng:eval:history"
_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _get_redis() -> redis.Redis:
    from config.infrastructure import CeleryConfig
    cfg = CeleryConfig()
    return redis.Redis.from_url(cfg.broker_url, decode_responses=True)


def _hash_key(eval_id: str) -> str:
    return f"{_EVAL_PREFIX}{eval_id}"


def _results_key(eval_id: str) -> str:
    return f"{_EVAL_PREFIX}{eval_id}:results"


def create_eval_run(
    case_ids: list[str],
    backends: str = "searxng",
    archive_results: bool = False,
) -> str:
    """Create eval run metadata in Redis and dispatch the Celery task."""
    eval_id = uuid.uuid4().hex[:12]
    r = _get_redis()

    r.hset(_hash_key(eval_id), mapping={
        "status": "pending",
        "total": str(len(case_ids)),
        "completed": "0",
        "started_at": str(time.time()),
        "backends": backends,
        "case_ids": json.dumps(case_ids),
        "archive_results": "1" if archive_results else "0",
        "error": "",
    })
    r.expire(_hash_key(eval_id), _TTL_SECONDS)

    run_evaluation.delay(eval_id, case_ids, backends, archive_results)
    return eval_id


def get_eval_status(eval_id: str) -> dict[str, Any] | None:
    """Read current eval run status from Redis."""
    r = _get_redis()
    data = r.hgetall(_hash_key(eval_id))
    if not data:
        return None
    started_at = float(data.get("started_at", "0"))
    return {
        "eval_id": eval_id,
        "status": data.get("status", "unknown"),
        "total": int(data.get("total", "0")),
        "completed": int(data.get("completed", "0")),
        "started_at": started_at,
        "elapsed_seconds": round(time.time() - started_at, 1) if started_at else 0,
        "backends": data.get("backends", "searxng"),
        "error": data.get("error", "") or None,
    }


def get_eval_results(eval_id: str) -> dict[str, Any] | None:
    """Read full eval results (aggregated metrics + per-case details)."""
    r = _get_redis()
    data = r.hgetall(_hash_key(eval_id))
    if not data:
        return None
    if data.get("status") != "done":
        return {"eval_id": eval_id, "status": data.get("status", "unknown")}

    # Load per-case results
    raw_results = r.lrange(_results_key(eval_id), 0, -1)
    cases = [json.loads(item) for item in raw_results]

    global_metrics = json.loads(data.get("global_metrics", "{}"))
    per_category = json.loads(data.get("per_category", "{}"))

    return {
        "eval_id": eval_id,
        "status": "done",
        "total": int(data.get("total", "0")),
        "completed": int(data.get("completed", "0")),
        "started_at": float(data.get("started_at", "0")),
        "elapsed_seconds": float(data.get("elapsed_total", "0")),
        "backends": data.get("backends", "searxng"),
        "global_metrics": global_metrics,
        "per_category": per_category,
        "cases": cases,
    }


def get_eval_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent evaluation runs from the history sorted set."""
    r = _get_redis()
    entries = r.zrevrange(_HISTORY_KEY, 0, limit - 1, withscores=True)
    history = []
    for eval_id, ts in entries:
        status = get_eval_status(eval_id)
        if status:
            # Add summary metrics if available
            data = r.hgetall(_hash_key(eval_id))
            global_metrics = {}
            if data.get("global_metrics"):
                global_metrics = json.loads(data["global_metrics"])
            history.append({
                **status,
                "global_metrics_summary": {
                    k: round(v, 4) for k, v in global_metrics.items()
                    if k in (
                        "preferred_domain_hit_rate", "low_trust_rate",
                        "direct_evidence_rate", "source_diversity",
                    )
                },
            })
    return history


def _build_snapshot_summary(snapshot_dict: dict) -> dict[str, Any]:
    """Extract the most relevant fields from a RetrievalSnapshot for the API."""
    evidence_items = snapshot_dict.get("evidence_items", [])
    top_items = []
    for item in evidence_items[:10]:
        source = item.get("source", {})
        top_items.append({
            "url": source.get("url", ""),
            "domain": source.get("domain", ""),
            "tier": source.get("domain_tier", 5),
            "title": source.get("title", ""),
            "relevance_score": round(item.get("relevance_score", 0), 3),
            "evidence_type": item.get("evidence_type", ""),
            "source_direction": item.get("source_direction", ""),
            "excerpt": (item.get("excerpt", "") or "")[:300],
        })
    return {
        "queries_used": snapshot_dict.get("generated_queries", []),
        "deduped_queries": snapshot_dict.get("deduped_queries", []),
        "num_results": len(snapshot_dict.get("merged_results", [])),
        "num_evidence_items": len(evidence_items),
        "evidence_items": top_items,
        "quality_signals": snapshot_dict.get("quality_signals", {}),
        "debug_notes": snapshot_dict.get("debug_notes", []),
        "backends_used": snapshot_dict.get("backends_used", []),
    }


@celery_app.task(
    bind=True,
    name="fakenewsguard.run_evaluation",
    time_limit=14400,
    soft_time_limit=14000,
)
def run_evaluation(
    self,
    eval_id: str,
    case_ids: list[str],
    backends: str = "searxng",
    archive_results: bool = False,
) -> None:
    """Run retrieval evaluation + full analysis for the given cases.

    Each case goes through:
    1. Retrieval evaluation (LiveRunner) → CaseResult + metrics
    2. Full analysis pipeline (Orchestrator) → SynthesisResult
    3. Optional archiving (Archive + CrossReferenceGraph)
    """
    r = _get_redis()
    hk = _hash_key(eval_id)
    rk = _results_key(eval_id)

    r.hset(hk, "status", "running")

    try:
        from config import AppConfig
        from eval.dataset import load_cases, filter_cases
        from eval.metrics import aggregate_by_category, aggregate_global
        from eval.runner_live import LiveRunner
        from orchestrator import Orchestrator

        from config.commander import CommanderConfig
        # Eval runs on PRO tier without Commander to keep costs low and
        # avoid the iterative search-refinement loop (retrieval is already
        # handled by LiveRunner in step 1).
        config = AppConfig()
        config.commander = CommanderConfig(enabled=False)
        all_cases = load_cases()
        cases = filter_cases(all_cases, ids=case_ids)

        runner = LiveRunner(config=config)
        orchestrator = Orchestrator(config)
        backend_tuple = tuple(backends.split(","))

        # Lazy-load archive/graph only if needed
        archive = None
        graph = None
        if archive_results:
            from api.dependencies import get_archive, get_graph
            archive = get_archive()
            graph = get_graph()

        results = []
        archived_count = 0

        for case in cases:
            # --- Step 1: Retrieval evaluation ---
            try:
                case_result = asyncio.run(
                    runner._evaluate_case(case, backend_tuple, save=False)
                )
            except Exception as exc:
                logger.error("Case %s retrieval failed: %s", case.id, exc)
                from eval.models import CaseMetrics, CaseResult, Violation
                case_result = CaseResult(
                    case_id=case.id,
                    category=case.category,
                    metrics=CaseMetrics(),
                    violations=[Violation(
                        metric="execution",
                        expected="success",
                        actual=str(exc)[:200],
                        severity="error",
                    )],
                    passed=False,
                )

            results.append(case_result)

            # Build snapshot summary
            snapshot_summary = {}
            try:
                from eval.snapshot import load_snapshot
                snap = load_snapshot(case.id, runner.snapshots_dir)
                snapshot_summary = _build_snapshot_summary(snap.model_dump())
            except (FileNotFoundError, Exception):
                pass

            # --- Step 2: Full analysis via Orchestrator ---
            analysis_summary = {}
            archive_id = None
            try:
                synthesis_result = orchestrator.analyze(case.claim_text)

                # Build claims_map from extraction (run it to get claim texts/types)
                extraction = orchestrator.claim_extractor.run(case.claim_text)
                claims_map = {
                    c.id: {"text": c.text, "type": c.type.value}
                    for c in extraction.claims
                }

                from api.dependencies import transform_result
                transformed = transform_result(synthesis_result, claims_map)

                analysis_summary = {
                    "overall_rating": transformed.get("overall_rating_key", ""),
                    "confidence": transformed.get("confidence"),
                    "summary": transformed.get("summary", ""),
                    "claims_count": len(transformed.get("claims", [])),
                    "techniques_count": len(transformed.get("rhetoric", [])),
                }

                # --- Step 3: Optional archiving ---
                if archive_results and archive is not None:
                    try:
                        archive_id = archive.save(
                            result=transformed,
                            input_text=case.claim_text,
                            platform="eval",
                            title=f"Eval: {case.id}",
                        )
                        archived_count += 1

                        if graph is not None:
                            graph.populate_from_result(
                                analysis_id=archive_id,
                                claims_analysis=transformed.get("claims", []),
                                original_text=case.claim_text,
                            )
                    except Exception as exc:
                        logger.warning(
                            "Case %s archive failed: %s", case.id, exc,
                        )

            except Exception as exc:
                logger.error("Case %s analysis failed: %s", case.id, exc)
                analysis_summary = {"error": str(exc)[:300]}

            # Store per-case result
            case_data = {
                "case_id": case.id,
                "claim_text": case.claim_text,
                "category": case.category.value,
                "language": case.language,
                "passed": case_result.passed,
                "metrics": case_result.metrics.model_dump(),
                "violations": [v.model_dump() for v in case_result.violations],
                "snapshot_summary": snapshot_summary,
                "analysis_result": analysis_summary,
                "archived": archive_id is not None,
                "archive_id": archive_id,
            }
            r.rpush(rk, json.dumps(case_data, ensure_ascii=False))
            r.hincrby(hk, "completed", 1)
            r.expire(rk, _TTL_SECONDS)

        # Aggregate metrics
        global_metrics = aggregate_global(results)
        per_category = aggregate_by_category(results)

        elapsed = round(time.time() - float(r.hget(hk, "started_at") or 0), 1)
        passed = sum(1 for res in results if res.passed)

        r.hset(hk, mapping={
            "status": "done",
            "global_metrics": json.dumps(global_metrics),
            "per_category": json.dumps(per_category),
            "elapsed_total": str(elapsed),
            "passed_count": str(passed),
            "archived_count": str(archived_count),
        })
        r.expire(hk, _TTL_SECONDS)

        # Add to history
        r.zadd(_HISTORY_KEY, {eval_id: time.time()})
        r.zremrangebyrank(_HISTORY_KEY, 0, -51)

        logger.info(
            "Evaluation %s complete: %d/%d passed, %d archived in %.1fs",
            eval_id, passed, len(results), archived_count, elapsed,
        )

    except SoftTimeLimitExceeded:
        r.hset(hk, mapping={"status": "error", "error": "Zeitlimit überschritten"})
        logger.error("Evaluation %s timed out", eval_id)
    except Exception as exc:
        r.hset(hk, mapping={"status": "error", "error": str(exc)[:500]})
        logger.error("Evaluation %s failed: %s", eval_id, exc, exc_info=True)
