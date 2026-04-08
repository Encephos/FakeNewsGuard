"""Admin evaluation endpoints: start, poll, and view retrieval evaluation runs."""

from __future__ import annotations

import random

from fastapi import APIRouter, HTTPException, Request

from .dependencies import StartEvaluationRequest, require_admin

router = APIRouter()


@router.post("/admin/evaluation/start")
async def start_evaluation(req: StartEvaluationRequest, request: Request) -> dict:
    """Start a new retrieval evaluation run with a random sample of German cases."""
    require_admin(request)

    from eval.dataset import load_cases
    from worker.eval_tasks import create_eval_run

    all_cases = load_cases()
    de_cases = [c for c in all_cases if c.language == "de"]

    if not de_cases:
        raise HTTPException(status_code=500, detail="Keine deutschen Evaluationsfälle vorhanden.")

    sample_size = min(req.sample_size, len(de_cases))
    if sample_size < 1:
        raise HTTPException(status_code=400, detail="sample_size muss mindestens 1 sein.")

    sampled = random.sample(de_cases, sample_size)
    case_ids = [c.id for c in sampled]

    eval_id = create_eval_run(case_ids, req.backends, req.archive_results)

    return {
        "eval_id": eval_id,
        "total": sample_size,
        "case_ids": case_ids,
    }


@router.get("/admin/evaluation/{eval_id}/status")
async def evaluation_status(eval_id: str, request: Request) -> dict:
    """Poll progress of an evaluation run."""
    require_admin(request)

    from worker.eval_tasks import get_eval_status

    status = get_eval_status(eval_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Evaluation nicht gefunden.")
    return status


@router.get("/admin/evaluation/{eval_id}/results")
async def evaluation_results(eval_id: str, request: Request) -> dict:
    """Get full results of a completed evaluation run."""
    require_admin(request)

    from worker.eval_tasks import get_eval_results

    results = get_eval_results(eval_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Evaluation nicht gefunden.")
    return results


@router.get("/admin/evaluation/history")
async def evaluation_history(request: Request) -> dict:
    """List recent evaluation runs."""
    require_admin(request)

    from worker.eval_tasks import get_eval_history

    history = get_eval_history(limit=20)
    return {"history": history}
