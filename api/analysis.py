"""Analysis endpoints: /api/analyze, /api/extract, /api/jobs/{job_id}.

Job execution is delegated to a Celery worker (see worker/tasks.py).
The API layer only submits jobs and polls their status from Redis.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from config import AppConfig, ScoutTier
from i18n import t
from tools.content_extractor import ContentExtractor, extract_urls, is_url
from worker.tasks import run_analysis

from .dependencies import (
    AnalyzeRequest,
    ExtractRequest,
    check_rate_limit,
    get_archive,
    get_current_user_optional,
    get_job_store,
    logger,
)

router = APIRouter()


# ── API endpoints ──────────────────────────────────────────────────

@router.post("/api/extract")
async def extract_content(req: ExtractRequest, request: Request) -> dict:
    """Extract content from a URL without running analysis. Returns extracted text and metadata."""
    check_rate_limit(request)
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail=t("api.errors.no_url"))

    try:
        extract_config = AppConfig()
        extractor = ContentExtractor(media_config=extract_config.media)
        content = await extractor.extract_async(url)
        return {
            "url": content.url,
            "platform": content.platform,
            "title": content.title,
            "text": content.text,
            "author": content.author,
            "images": content.images,
            "timestamp": content.timestamp,
            "metadata": content.metadata,
        }
    except Exception as e:
        raise HTTPException(status_code=422, detail=t("api.errors.extraction_failed").format(error=e))


@router.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> dict:
    """Submit an analysis job. Returns a job_id for polling.

    Accepts either plain text or a URL (or both). If a URL is provided,
    content is first extracted and then analyzed.
    """
    check_rate_limit(request)
    store = get_job_store()

    text = req.text.strip()
    url = (req.url or "").strip()

    # If no explicit URL field but the text itself is a URL, treat it as URL
    if not url and text and is_url(text):
        urls = extract_urls(text)
        if urls and len(text) - len(urls[0]) < 20:
            url = urls[0]
            text = ""

    if not text and not url:
        raise HTTPException(status_code=400, detail=t("api.errors.no_text"))

    # ── Tier-Bestimmung ───────────────────────────────────────────
    PLAN_ACCESS: dict[ScoutTier, set[ScoutTier]] = {
        ScoutTier.LITE: {ScoutTier.LITE},
        ScoutTier.PRO: {ScoutTier.LITE, ScoutTier.PRO},
        ScoutTier.MAX: {ScoutTier.LITE, ScoutTier.PRO, ScoutTier.MAX},
    }

    user = get_current_user_optional(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentifizierung erforderlich.")

    if not user.get("consent", 0):
        raise HTTPException(
            status_code=403,
            detail="Bitte stimme der Datenverarbeitung zu, bevor du eine Analyse startest.",
        )
    plan = ScoutTier(user["tier"])

    tier = plan
    commander_requested = False
    if req.tier:
        raw_tier = req.tier.strip().lower()
        if raw_tier in ("commander-pro", "commander-max"):
            commander_requested = True
            base_tier = raw_tier.replace("commander-", "")
            try:
                tier = ScoutTier(base_tier)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Ungueltiger Tier: {req.tier}")
        else:
            try:
                tier = ScoutTier(raw_tier)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Ungueltiger Tier: {req.tier}. Erlaubt: lite, pro, max, commander-pro, commander-max",
                )

    if tier not in PLAN_ACCESS[plan]:
        raise HTTPException(
            status_code=403,
            detail=f"Dein Plan ({plan.value.upper()}) erlaubt keinen Zugriff auf Tier '{tier.value}'. "
                   f"Erlaubt: {', '.join(t_val.value for t_val in sorted(PLAN_ACCESS[plan], key=lambda x: x.value))}",
        )

    if commander_requested and tier == ScoutTier.LITE:
        raise HTTPException(status_code=400, detail="Commander ist nur mit PRO oder MAX verfuegbar.")

    # ── Archiv-Duplikat-Pruefung ────────────────────────────────────
    archive = get_archive()
    cached = archive.find_duplicate(text=text, url=url)
    if cached is not None:
        job_id = str(uuid.uuid4())
        now = time.time()
        store.create(
            job_id,
            status="done",
            result=cached["result"],
            error=None,
            created_at=now,
            last_activity=now,
            source_url=url or None,
            archive_id=cached["id"],
            from_cache=True,
        )
        store.push_step(job_id, {
            "id": "step-cache-1",
            "phase": "Archiv",
            "agent": "Archiv",
            "emoji": "",
            "message": t("api.steps.from_archive"),
            "status": "done",
            "timestamp": int(now * 1000),
        })
        return {"job_id": job_id}

    job_id = str(uuid.uuid4())
    now = time.time()
    user_id = user["id"] if user else None
    store.create(
        job_id,
        status="pending",
        result=None,
        error=None,
        created_at=now,
        last_activity=now,
        source_url=url or None,
        agent=f"Scout {plan.value.capitalize()}",
        tier=tier.value,
        user_id=user_id,
    )

    # Dispatch to Celery worker
    run_analysis.delay(job_id, text, url, tier=tier.value)

    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Poll job status. Frontend calls this every ~1.5 s."""
    store = get_job_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=t("api.errors.job_not_found"))

    steps = store.get_steps(job_id)

    return {
        "status": job.get("status", "pending"),
        "steps": steps,
        "result": job.get("result"),
        "error": job.get("error"),
        "extracted_content": job.get("extracted_content"),
        "archive_id": job.get("archive_id"),
        "from_cache": job.get("from_cache", False),
        "agent": job.get("agent", "Scout Max"),
        "tier": job.get("tier", "max"),
    }
