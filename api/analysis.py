"""Analysis endpoints: /api/analyze, /api/extract, /api/jobs/{job_id}, _run_job() background worker."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config import AppConfig, ScoutTier
from i18n import t
from orchestrator import Orchestrator
from tools.content_extractor import ContentExtractor, detect_platform, extract_urls, is_url

from .dependencies import (
    AnalyzeRequest,
    ExtractRequest,
    JOB_INACTIVITY_TIMEOUT,
    JOB_TIMEOUT_SECONDS,
    check_rate_limit,
    cleanup_old_jobs,
    format_image_analysis,
    get_archive,
    get_current_user_optional,
    get_graph,
    get_user_db,
    jobs,
    logger,
    transform_result,
)

router = APIRouter()


# ── Background worker ──────────────────────────────────────────────

async def _run_job(job_id: str, text: str, url: str = "", tier: ScoutTier = ScoutTier.MAX) -> None:
    """Run the full analysis pipeline, updating jobs[job_id] in-place."""
    job = jobs[job_id]
    step_counter = 0

    def push_step(phase: str, agent: str, message: str, status: str = "done") -> None:
        nonlocal step_counter
        step_counter += 1
        job["steps"].append(
            {
                "id": f"step-{step_counter}",
                "phase": phase,
                "agent": agent,
                "emoji": "",
                "message": message,
                "status": status,
                "timestamp": int(time.time() * 1000),
            }
        )
        # Reset activity watchdog on every step
        job["last_activity"] = time.time()

    try:
        job["status"] = "running"
        config = AppConfig(verbose=True, tier=tier)
        orchestrator = Orchestrator(config)
        claims_map: dict[str, Any] = {}

        # ── Phase 0: URL Content Extraction (if URL provided) ──────
        content = None  # wird in Phase 0 gesetzt, fuer Phase 0.5 benoetigt
        if url:
            platform = detect_platform(url)
            push_step("Phase 0", "Content Extractor", t("api.steps.extracting_content").format(platform=platform.capitalize()), "running")
            try:
                extractor = ContentExtractor()
                content = await extractor.extract_async(url)
                extracted_text = extractor.format_for_analysis(content)
                push_step(
                    "Phase 0", "Content Extractor",
                    f"Inhalt extrahiert: {content.title[:80] or content.text[:80]}..."
                    + (f" ({len(content.images)} Bilder)" if content.images else ""),
                )
                # Store extraction info in job for frontend
                job["extracted_content"] = {
                    "platform": content.platform,
                    "title": content.title,
                    "author": content.author,
                    "images": content.images[:5],
                    "url": content.url,
                }
                # Combine: user text + extracted content
                if text:
                    text = f"{text}\n\n--- Extrahierter Inhalt von {url} ---\n\n{extracted_text}"
                else:
                    text = extracted_text
            except Exception as e:
                push_step("Phase 0", "Content Extractor", f"Extraktion fehlgeschlagen: {e}", "error")
                if not text:
                    job["error"] = f"Inhalt konnte nicht extrahiert werden: {e}"
                    job["status"] = "error"
                    return

        # ── Phase 0.5: Bildanalyse (nur Social Media) ─────────────
        _VISION_PLATFORMS = {"twitter", "instagram", "threads"}
        image_analysis_text = ""

        if content is not None and content.images and content.platform in _VISION_PLATFORMS:
            img_urls = content.images[:5]
            push_step(
                "Phase 0.5", "Image Analyzer",
                t("api.steps.analyzing_images").format(count=len(img_urls)),
                "running",
            )
            img_input = {"image_urls": img_urls, "post_text": content.text}
            img_result, img_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda: orchestrator.image_analyzer.run_safe(img_input)
            )
            if img_error:
                push_step(
                    "Phase 0.5", "Image Analyzer",
                    t("api.steps.image_analysis_failed").format(error=img_error), "error",
                )
            elif img_result is not None:
                image_analysis_text = format_image_analysis(img_result)
                push_step(
                    "Phase 0.5", "Image Analyzer",
                    t("api.steps.images_analyzed").format(count=len(img_result.items)),
                )
                # Bildanalyse als Kontext in den Text einfuegen -> ClaimExtractor sieht ihn
                text += f"\n\n--- Bildanalyse ---\n\n{image_analysis_text}"
                # Fuer das Frontend speichern
                if "extracted_content" in job:
                    job["extracted_content"]["image_analysis"] = {
                        "items": [item.model_dump() for item in img_result.items],
                        "overall_assessment": img_result.overall_assessment,
                        "cross_image_observations": img_result.cross_image_observations,
                    }

        # Input-Validierung und -Kuerzung wird zentral im Orchestrator erledigt

        # ── Phase 1: Claim Extraction ──────────────────────────────
        push_step("Phase 1", "Claim Extractor", t("api.steps.extracting_claims"), "running")
        extraction = await asyncio.get_event_loop().run_in_executor(
            None, orchestrator.claim_extractor.run, text
        )
        claims_map = {
            c.id: {"text": c.text, "type": c.type.value}
            for c in extraction.claims
        }
        push_step(
            "Phase 1",
            "Claim Extractor",
            f"{len(extraction.claims)} Claims extrahiert, {len(extraction.implicit_claims)} implizite",
        )

        if not extraction.claims:
            from models.schemas import OverallRating, SynthesisResult
            result = SynthesisResult(
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary=t("api.steps.no_claims_found"),
                sources=[],
            )
            job["result"] = transform_result(result, claims_map)
            job["status"] = "done"
            return

        for claim in extraction.claims:
            prio_info = f" prio={claim.priority_score:.2f}" if hasattr(claim, "priority_score") else ""
            push_step(
                "Phase 1",
                "Claim Extractor",
                f"{claim.id} [{claim.type.value}]{prio_info}: {claim.text}",
            )

        # ── Phase 2 + 3: Fact-check claims (parallel, batched) + Rhetoric ──
        from models.schemas import ClaimType

        _CLAIM_BATCH_SIZE = 4  # Max parallel claim checks to avoid API overload

        fact_checks = []
        number_audits = []
        analysis_errors = []

        # Top-N Filterung via Orchestrator (beruecksichtigt priority_score + is_checkworthy)
        checkable = orchestrator._select_top_claims(extraction)
        if len(checkable) < len([c for c in extraction.claims if c.type != ClaimType.OPINION]):
            push_step(
                "Phase 1", "Claim Extractor",
                f"Top-{len(checkable)} von {len(extraction.claims)} Claims ausgewaehlt (konfigurierbar via CLAIM_TOP_N)",
            )

        # Scale inactivity timeout for large inputs: base 300s + 30s per claim
        if len(checkable) > 4:
            job["inactivity_timeout"] = JOB_INACTIVITY_TIMEOUT + len(checkable) * 30

        async def _check_claim(claim):
            """Fact-check a single claim, then optionally number-audit it."""
            push_step("Phase 2", "Fact Checker", t("api.steps.checking_claim").format(text=claim.text[:80]), "running")

            fc_result, fc_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda c=claim: orchestrator.fact_checker.run_safe(c, context=text)
            )
            if fc_error:
                analysis_errors.append(fc_error)
                push_step("Phase 2", "Fact Checker", f"Fehler bei {claim.id}: {fc_error}", "error")
            elif fc_result is not None:
                fact_checks.append(fc_result)
                push_step("Phase 2", "Fact Checker", f"Claim {claim.id}: {fc_result.rating.value}")

            if "number_auditor" in claim.requires_agents or claim.type == ClaimType.STATISTICAL:
                push_step("Phase 2", "Number Auditor", t("api.steps.number_audit").format(id=claim.id), "running")
                fc_context = (
                    f"Fact-Check Ergebnis: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                    if fc_result is not None
                    else ""
                )
                na_result, na_error = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda c=claim, ctx=fc_context: orchestrator.number_auditor.run_safe(c, context=ctx),
                )
                if na_error:
                    analysis_errors.append(na_error)
                elif na_result is not None:
                    number_audits.append(na_result)
                    push_step(
                        "Phase 2", "Number Auditor",
                        f"{claim.id}: {na_result.manipulation_type.value}",
                    )

        # Run rhetoric analysis in parallel with claim batches
        async def _run_rhetoric():
            push_step("Phase 3", "Rhetoric Analyzer", t("api.steps.rhetoric_started"), "running")
            rhet_result, rhet_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda: orchestrator.rhetoric_analyzer.run_safe(text, context=image_analysis_text)
            )
            return rhet_result, rhet_error

        # Process claims in batches to avoid API rate limits and overload
        rhetoric_task = asyncio.create_task(_run_rhetoric())

        for batch_start in range(0, len(checkable), _CLAIM_BATCH_SIZE):
            batch = checkable[batch_start : batch_start + _CLAIM_BATCH_SIZE]
            batch_num = batch_start // _CLAIM_BATCH_SIZE + 1
            total_batches = (len(checkable) + _CLAIM_BATCH_SIZE - 1) // _CLAIM_BATCH_SIZE
            if total_batches > 1:
                push_step(
                    "Phase 2", "Fact Checker",
                    f"Batch {batch_num}/{total_batches} ({len(batch)} Claims)...", "running",
                )
            batch_tasks = [_check_claim(c) for c in batch]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            for i, res in enumerate(batch_results):
                if isinstance(res, Exception):
                    analysis_errors.append(f"ClaimCheck: {res}")

        # Wait for rhetoric to finish
        results = [await rhetoric_task]

        # Process rhetoric result
        rhetoric_gathered = results[0]
        rhetoric_result = None
        if isinstance(rhetoric_gathered, Exception):
            analysis_errors.append(f"RhetoricAnalyzer: {rhetoric_gathered}")
            push_step("Phase 3", "Rhetoric Analyzer", f"Fehler: {rhetoric_gathered}", "error")
        else:
            rhetoric_result, rhetoric_error = rhetoric_gathered
            if rhetoric_error:
                analysis_errors.append(rhetoric_error)
                push_step("Phase 3", "Rhetoric Analyzer", f"Fehler: {rhetoric_error}", "error")
            elif rhetoric_result is not None:
                push_step(
                    "Phase 3", "Rhetoric Analyzer",
                    f"{len(rhetoric_result.techniques)} Techniken erkannt",
                )

        # ── Phase 4: Synthesis ────────────────────────────────────
        push_step("Phase 4", "Synthesizer", t("api.steps.synthesizing"), "running")
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
            "image_analysis": image_analysis_text,
        }
        final_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: orchestrator.synthesizer.run(synthesis_input)
        )
        if analysis_errors:
            final_result.analysis_errors.extend(analysis_errors)

        push_step("Phase 4", "Synthesizer", t("api.steps.analysis_done"))

        job["result"] = transform_result(final_result, claims_map)
        job["status"] = "done"

        # ── Auto-Archive: Ergebnis persistent speichern ────────
        try:
            archive = get_archive()
            extracted = job.get("extracted_content", {})
            archive_id = archive.save(
                result=job["result"],
                input_text=text[:500],
                source_url=url or extracted.get("url"),
                platform=extracted.get("platform"),
                title=extracted.get("title"),
            )
            job["archive_id"] = archive_id

            # ── Cross-Reference Graph: Beziehungen erfassen ────────
            try:
                graph = get_graph()
                graph.populate_from_result(
                    analysis_id=archive_id,
                    claims_analysis=job["result"].get("claims", []),
                    original_text=text,
                )
            except Exception:
                pass  # Graph-Fehler darf Analyse nicht brechen

        except Exception:
            pass  # Archivierung darf Analyse nicht brechen

        # ── Usage-Tracking ──────────────────────────────────────
        try:
            if job.get("user_id"):
                user_db = get_user_db()
                result_data = job.get("result", {})
                user_db.log_usage(
                    user_id=job["user_id"],
                    tier_used=job.get("tier", "max"),
                    claims=len(result_data.get("claims", [])),
                    rating=result_data.get("overall_rating"),
                    source="web",
                )
        except Exception:
            pass  # Usage-Tracking darf Analyse nicht brechen

    except Exception as exc:
        logger.exception("Job %s fehlgeschlagen: %s", job_id, exc)
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["status"] = "error"


# ── API endpoints ──────────────────────────────────────────────────

@router.post("/api/extract")
async def extract_content(req: ExtractRequest, request: Request) -> dict:
    """Extract content from a URL without running analysis. Returns extracted text and metadata."""
    check_rate_limit(request)
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail=t("api.errors.no_url"))

    try:
        extractor = ContentExtractor()
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
    cleanup_old_jobs()

    text = req.text.strip()
    url = (req.url or "").strip()

    # If no explicit URL field but the text itself is a URL, treat it as URL
    if not url and text and is_url(text):
        urls = extract_urls(text)
        if urls and len(text) - len(urls[0]) < 20:
            # Text is essentially just a URL (maybe some whitespace)
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
    if user is not None:
        # Consent check: user must have agreed to data logging
        if not user.get("consent", 0):
            raise HTTPException(
                status_code=403,
                detail="Bitte stimme der Datenverarbeitung zu, bevor du eine Analyse startest.",
            )
        # Authenticated: user's DB tier is the plan ceiling
        plan = ScoutTier(user["tier"])
    else:
        # Unauthenticated: derive plan from agent field (backwards compat)
        AGENT_TO_PLAN: dict[str, ScoutTier] = {
            "scout lite": ScoutTier.LITE,
            "scout pro": ScoutTier.PRO,
            "scout max": ScoutTier.MAX,
        }
        plan = ScoutTier.MAX
        if req.agent:
            plan = AGENT_TO_PLAN.get(req.agent.strip().lower(), ScoutTier.MAX)

    # Bestimme gewuenschten Tier (default: Plan-Maximum)
    tier = plan
    if req.tier:
        try:
            tier = ScoutTier(req.tier.strip().lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungueltiger Tier: {req.tier}. Erlaubt: lite, pro, max")

    # Pruefe ob der Plan Zugriff auf den Tier hat
    if tier not in PLAN_ACCESS[plan]:
        raise HTTPException(
            status_code=403,
            detail=f"Dein Plan ({plan.value.upper()}) erlaubt keinen Zugriff auf Tier '{tier.value}'. "
                   f"Erlaubt: {', '.join(t_val.value for t_val in sorted(PLAN_ACCESS[plan], key=lambda x: x.value))}",
        )

    config = AppConfig(tier=tier)

    # ── Archiv-Duplikat-Pruefung ────────────────────────────────────
    archive = get_archive()
    cached = archive.find_duplicate(text=text, url=url)
    if cached is not None:
        job_id = str(uuid.uuid4())
        now = time.time()
        jobs[job_id] = {
            "status": "done",
            "steps": [
                {
                    "id": "step-cache-1",
                    "phase": "Archiv",
                    "agent": "Archiv",
                    "emoji": "",
                    "message": t("api.steps.from_archive"),
                    "status": "done",
                    "timestamp": int(now * 1000),
                }
            ],
            "result": cached["result"],
            "error": None,
            "created_at": now,
            "last_activity": now,
            "source_url": url or None,
            "archive_id": cached["id"],
            "from_cache": True,
        }
        return {"job_id": job_id}

    job_id = str(uuid.uuid4())
    now = time.time()
    user_id = user["id"] if user else None
    jobs[job_id] = {
        "status": "pending",
        "steps": [],
        "result": None,
        "error": None,
        "created_at": now,
        "last_activity": now,
        "source_url": url or None,
        "agent": f"Scout {plan.value.capitalize()}",
        "tier": tier.value,
        "user_id": user_id,
    }

    # Fire-and-forget background task with activity-based timeout watchdog
    async def _watchdog(jid: str) -> None:
        """Kill the job if no progress for JOB_INACTIVITY_TIMEOUT seconds,
        or if the total runtime exceeds JOB_TIMEOUT_SECONDS."""
        while True:
            await asyncio.sleep(15)
            job = jobs.get(jid)
            if not job or job["status"] in ("done", "error"):
                return
            now = time.time()
            idle = now - job.get("last_activity", job["created_at"])
            total = now - job["created_at"]
            inactivity_limit = job.get("inactivity_timeout", JOB_INACTIVITY_TIMEOUT)
            if idle > inactivity_limit:
                job["error"] = t("api.errors.timeout_inactivity").format(seconds=int(idle))
                job["status"] = "error"
                return
            if total > JOB_TIMEOUT_SECONDS:
                job["error"] = t("api.errors.timeout_hard")
                job["status"] = "error"
                return

    async def _run_job_watched(jid: str, txt: str, link: str, t_tier: ScoutTier = ScoutTier.MAX) -> None:
        watchdog_task = asyncio.create_task(_watchdog(jid))
        try:
            await _run_job(jid, txt, link, tier=t_tier)
        finally:
            watchdog_task.cancel()

    asyncio.create_task(_run_job_watched(job_id, text, url, tier))

    return {"job_id": job_id}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Poll job status. Frontend calls this every ~1.5 s."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=t("api.errors.job_not_found"))
    job = jobs[job_id]

    # Detect stale jobs: no activity for too long OR total time exceeded
    if job["status"] in ("pending", "running"):
        now = time.time()
        idle = now - job.get("last_activity", job["created_at"])
        total = now - job["created_at"]
        if idle > JOB_INACTIVITY_TIMEOUT + 30:
            job["status"] = "error"
            job["error"] = t("api.errors.timeout_stale")
        elif total > JOB_TIMEOUT_SECONDS + 30:
            job["status"] = "error"
            job["error"] = t("api.errors.timeout_total")

    return {
        "status": job["status"],
        "steps": job["steps"],
        "result": job["result"],
        "error": job["error"],
        "extracted_content": job.get("extracted_content"),
        "archive_id": job.get("archive_id"),
        "from_cache": job.get("from_cache", False),
        "agent": job.get("agent", "Scout Max"),
        "tier": job.get("tier", "max"),
    }
