"""Analysis endpoints: /api/analyze, /api/extract, /api/jobs/{job_id}, /api/jobs/{job_id}/stream.

Job execution is delegated to a Celery worker (see worker/tasks.py).
The API layer only submits jobs and polls their status from Redis.
The ``/stream`` endpoint provides Server-Sent Events for real-time updates.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

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

# ── SSE configuration ────────────────────────────────────────────────
_SSE_POLL_INTERVAL_S: float = 0.5
_SSE_MAX_DURATION_S: int = 2100  # 35 minutes
_SSE_KEEPALIVE_INTERVAL_S: int = 15

router = APIRouter()


# ── API endpoints ──────────────────────────────────────────────────

@router.post("/extract")
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


@router.post("/analyze")
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


@router.get("/jobs/{job_id}")
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


# ── Server-Sent Events stream ────────────────────────────────────


def _sse_event(event: str, data: dict | str, event_id: str | int = "") -> str:
    """Format a single SSE event."""
    payload = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    lines = f"event: {event}\n"
    if event_id != "":
        lines += f"id: {event_id}\n"
    lines += f"data: {payload}\n\n"
    return lines


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Stream job progress via Server-Sent Events.

    The generator polls Redis every 500ms and pushes new events.
    Supports reconnection via the ``Last-Event-ID`` header.
    """
    store = get_job_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=t("api.errors.job_not_found"))

    # Reconnection support: resume from last seen step index
    last_event_id = request.headers.get("last-event-id", "")
    start_index = 0
    if last_event_id.isdigit():
        start_index = int(last_event_id) + 1

    async def _event_generator() -> AsyncGenerator[str, None]:
        seen_step_count = start_index
        extracted_content_sent = start_index > 0  # assume sent on reconnect
        deadline = time.monotonic() + _SSE_MAX_DURATION_S
        last_keepalive = time.monotonic()

        try:
            while time.monotonic() < deadline:
                # Check if client disconnected
                if await request.is_disconnected():
                    return

                job_data = store.get(job_id)
                if job_data is None:
                    yield _sse_event("error", {"error": "Job nicht mehr verfuegbar."}, "error")
                    return

                # Emit new steps
                new_steps = store.get_steps_from(job_id, seen_step_count)
                for i, step in enumerate(new_steps):
                    step_index = seen_step_count + i
                    yield _sse_event("step", step, step_index)
                    last_keepalive = time.monotonic()
                seen_step_count += len(new_steps)

                # Emit extracted_content (once)
                if not extracted_content_sent:
                    ec = job_data.get("extracted_content")
                    if ec:
                        yield _sse_event("extracted_content", ec, "ec")
                        extracted_content_sent = True
                        last_keepalive = time.monotonic()

                # Terminal states
                status = job_data.get("status", "pending")
                if status == "done":
                    result_data = {
                        "result": job_data.get("result"),
                        "archive_id": job_data.get("archive_id"),
                        "from_cache": job_data.get("from_cache", False),
                        "agent": job_data.get("agent", "Scout Max"),
                        "tier": job_data.get("tier", "max"),
                    }
                    yield _sse_event("done", result_data, "done")
                    return

                if status == "error":
                    yield _sse_event("error", {"error": job_data.get("error", "Unbekannter Fehler")}, "error")
                    return

                # Keepalive comment to prevent connection timeout
                if time.monotonic() - last_keepalive >= _SSE_KEEPALIVE_INTERVAL_S:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()

                await asyncio.sleep(_SSE_POLL_INTERVAL_S)

            # Stream duration exceeded
            yield _sse_event("timeout", {"error": "Stream-Timeout erreicht."}, "timeout")

        except asyncio.CancelledError:
            # Client disconnected — exit cleanly
            return

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if present
        },
    )
