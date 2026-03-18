"""FakeNewsGuard – FastAPI Server with job-queue + polling.

Instead of SSE (which drops on mobile / screen lock), analysis runs as a
background job.  The client:
  1. POST /api/analyze          → { job_id }
  2. GET  /api/jobs/{job_id}    → { status, steps, result?, error? }
     (poll every ~1.5 s until status == "done" | "error")

Usage:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from config import AppConfig
from orchestrator import InputValidationError, Orchestrator
from tools.archive import AnalysisArchive
from tools.content_extractor import ContentExtractor, ExtractedContent, detect_platform, extract_urls, is_url
from tools.rate_limiter import RateLimiter

app = FastAPI(title="FakeNewsGuard API")

# CORS – allow all origins so the deployed server works regardless of domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── In-memory job store ────────────────────────────────────────────
# { job_id: { status, steps, result, error, created_at } }
_jobs: dict[str, dict[str, Any]] = {}

# ── Rate-Limiter (singleton) ─────────────────────────────────────
_rate_limiter: RateLimiter | None = None


def _get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        config = AppConfig()
        _rate_limiter = RateLimiter(config.rate_limit)
    return _rate_limiter


# ── Archive (singleton, erstellt bei erstem Zugriff) ──────────────
_archive: AnalysisArchive | None = None


def _get_archive() -> AnalysisArchive:
    global _archive
    if _archive is None:
        config = AppConfig()
        _archive = AnalysisArchive(config.archive)
    return _archive

# Clean up jobs older than 1 hour to avoid memory leaks
_JOB_TTL_SECONDS = 3600
# Maximum time a single analysis job may run before being killed (total hard cap)
_JOB_TIMEOUT_SECONDS = 1800  # 30 minutes hard cap
# If no progress (new step) for this long, the job is considered stale.
# Per-job override possible via job["inactivity_timeout"].
_JOB_INACTIVITY_TIMEOUT = 300  # 5 minutes without progress (default)


def _cleanup_old_jobs() -> None:
    now = time.time()
    stale = [jid for jid, j in _jobs.items() if now - j["created_at"] > _JOB_TTL_SECONDS]
    for jid in stale:
        del _jobs[jid]


# ── Rating mappings ────────────────────────────────────────────────

OVERALL_RATING_MAP: dict[str, str] = {
    "RELIABLE": "Wahr",
    "MOSTLY_RELIABLE": "Größtenteils wahr",
    "MIXED": "Irreführend",
    "MISLEADING": "Irreführend",
    "HIGHLY_MISLEADING": "Größtenteils falsch",
    "FABRICATED": "Falsch",
}

MANIPULATION_TYPE_MAP: dict[str, str] = {
    "BASE_EFFECT": "CHERRY_PICKING",
    "ABSOLUTE_VS_RELATIVE": "MISLEADING_COMPARISON",
    "CATEGORY_ERROR": "CATEGORY_ERROR",
    "CHERRY_PICKED_TIMEFRAME": "CHERRY_PICKING",
    "CUMULATION_TRICK": "MISLEADING_COMPARISON",
    "TREND_VS_NOISE": "CHERRY_PICKING",
    "PER_CAPITA_MISSING": "SCALE_DISTORTION",
    "CALCULATION_ERROR": "FALSE_PRECISION",
    "NONE": "NONE",
}


def _transform_result(result: Any, claims_map: dict[str, Any]) -> dict:
    """Transform backend SynthesisResult into frontend AnalysisResult shape."""
    frontend_claims = []
    for fc in result.claims_analysis:
        claim_info = claims_map.get(fc.claim_id, {})
        na = next(
            (a for a in result.number_audits if a.claim_id == fc.claim_id), None
        )
        claim_dict: dict[str, Any] = {
            "id": fc.claim_id,
            "text": claim_info.get("text", fc.claim_id),
            "type": claim_info.get("type", "FACTUAL"),
            "rating": fc.rating.value,
            "evidence": fc.evidence or "",
            "correction": fc.correction or "",
            "missing_context": fc.missing_context or "",
            "sources": fc.sources or [],
        }
        if na and na.manipulation_type.value != "NONE":
            claim_dict["number_audit"] = {
                "manipulation": MANIPULATION_TYPE_MAP.get(
                    na.manipulation_type.value, na.manipulation_type.value
                ),
                "calculation": na.calculation_check or "",
                "correct_value": na.correct_interpretation or "",
            }
        frontend_claims.append(claim_dict)

    return {
        "overall_rating": OVERALL_RATING_MAP.get(
            result.overall_rating.value, "Irreführend"
        ),
        "confidence": round(result.confidence * 100),
        "summary": result.summary,
        "claims": frontend_claims,
        "rhetoric": [
            {
                "name": t.technique,
                "severity": t.severity.value,
                "description": t.explanation,
                "example": t.example or "",
            }
            for t in result.manipulation_techniques
        ],
        "corrections": result.key_corrections or [],
        "fairness": result.fairness_notes or [],
        "sources": result.sources or [],
    }


# ── Background worker ──────────────────────────────────────────────

async def _run_job(job_id: str, text: str, url: str = "") -> None:
    """Run the full analysis pipeline, updating _jobs[job_id] in-place."""
    job = _jobs[job_id]
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
        config = AppConfig(verbose=True)
        orchestrator = Orchestrator(config)
        claims_map: dict[str, Any] = {}

        # ── Phase 0: URL Content Extraction (if URL provided) ──────
        if url:
            platform = detect_platform(url)
            push_step("Phase 0", "Content Extractor", f"Extrahiere Inhalt von {platform.capitalize()}…", "running")
            try:
                extractor = ContentExtractor()
                content = await extractor.extract_async(url)
                extracted_text = extractor.format_for_analysis(content)
                push_step(
                    "Phase 0", "Content Extractor",
                    f"Inhalt extrahiert: {content.title[:80] or content.text[:80]}…"
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

        # Input-Validierung und -Kürzung wird zentral im Orchestrator erledigt

        # ── Phase 1: Claim Extraction ──────────────────────────────
        push_step("Phase 1", "Claim Extractor", "Claims werden extrahiert…", "running")
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
                summary="Es wurden keine überprüfbaren Tatsachenbehauptungen gefunden.",
                sources=[],
            )
            job["result"] = _transform_result(result, claims_map)
            job["status"] = "done"
            return

        for claim in extraction.claims:
            push_step(
                "Phase 1",
                "Claim Extractor",
                f"{claim.id} [{claim.type.value}]: {claim.text}",
            )

        # ── Phase 2 + 3: Fact-check claims (parallel, batched) + Rhetoric ──
        from models.schemas import ClaimType

        _CLAIM_BATCH_SIZE = 4  # Max parallel claim checks to avoid API overload

        fact_checks = []
        number_audits = []
        analysis_errors = []
        checkable = [c for c in extraction.claims if c.type != ClaimType.OPINION]

        # Scale inactivity timeout for large inputs: base 300s + 30s per claim
        if len(checkable) > 4:
            job["inactivity_timeout"] = _JOB_INACTIVITY_TIMEOUT + len(checkable) * 30

        async def _check_claim(claim):
            """Fact-check a single claim, then optionally number-audit it."""
            push_step("Phase 2", "Fact Checker", f"Prüfe: {claim.text[:80]}…", "running")

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
                push_step("Phase 2", "Number Auditor", f"Zahlenprüfung {claim.id}…", "running")
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
            push_step("Phase 3", "Rhetoric Analyzer", "Rhetorische Analyse gestartet…", "running")
            rhet_result, rhet_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda: orchestrator.rhetoric_analyzer.run_safe(text)
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
                    f"Batch {batch_num}/{total_batches} ({len(batch)} Claims)…", "running",
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
        push_step("Phase 4", "Synthesizer", "Erstelle Gesamtbewertung…", "running")
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
        }
        final_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: orchestrator.synthesizer.run(synthesis_input)
        )
        if analysis_errors:
            final_result.analysis_errors.extend(analysis_errors)

        push_step("Phase 4", "Synthesizer", "Analyse abgeschlossen ✓")

        job["result"] = _transform_result(final_result, claims_map)
        job["status"] = "done"

        # ── Auto-Archive: Ergebnis persistent speichern ────────
        try:
            archive = _get_archive()
            extracted = job.get("extracted_content", {})
            archive_id = archive.save(
                result=job["result"],
                input_text=text[:500],
                source_url=url or extracted.get("url"),
                platform=extracted.get("platform"),
                title=extracted.get("title"),
            )
            job["archive_id"] = archive_id
        except Exception:
            pass  # Archivierung darf Analyse nicht brechen

    except Exception as exc:
        traceback.print_exc()
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["status"] = "error"


# ── API endpoints ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    text: str
    url: str | None = None


class ExtractRequest(BaseModel):
    url: str


def _check_rate_limit(request: Request) -> None:
    """Prüfe Rate-Limit für den aktuellen Request. Wirft HTTPException bei Überschreitung."""
    limiter = _get_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Anfragen. Bitte warte {retry_after:.0f} Sekunden.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


@app.post("/api/extract")
async def extract_content(req: ExtractRequest, request: Request) -> dict:
    """Extract content from a URL without running analysis. Returns extracted text and metadata."""
    _check_rate_limit(request)
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Keine URL angegeben.")

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
        raise HTTPException(status_code=422, detail=f"Inhalt konnte nicht extrahiert werden: {e}")


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> dict:
    """Submit an analysis job. Returns a job_id for polling.

    Accepts either plain text or a URL (or both). If a URL is provided,
    content is first extracted and then analyzed.
    """
    _check_rate_limit(request)
    _cleanup_old_jobs()

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
        raise HTTPException(status_code=400, detail="Kein Text oder URL angegeben.")

    config = AppConfig()

    # ── Archiv-Duplikat-Prüfung ────────────────────────────────────
    # Wenn derselbe URL oder Text schon mal analysiert wurde, sofort
    # das gecachte Ergebnis zurückgeben – ohne alle Agenten neu zu starten.
    archive = _get_archive()
    cached = archive.find_duplicate(text=text, url=url)
    if cached is not None:
        job_id = str(uuid.uuid4())
        now = time.time()
        _jobs[job_id] = {
            "status": "done",
            "steps": [
                {
                    "id": "step-cache-1",
                    "phase": "Archiv",
                    "agent": "Archiv",
                    "emoji": "",
                    "message": "Identischer Input bereits analysiert – Ergebnis aus Archiv geladen.",
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
    _jobs[job_id] = {
        "status": "pending",
        "steps": [],
        "result": None,
        "error": None,
        "created_at": now,
        "last_activity": now,
        "source_url": url or None,
    }

    # Fire-and-forget background task with activity-based timeout watchdog
    async def _watchdog(jid: str) -> None:
        """Kill the job if no progress for _JOB_INACTIVITY_TIMEOUT seconds,
        or if the total runtime exceeds _JOB_TIMEOUT_SECONDS."""
        while True:
            await asyncio.sleep(15)
            job = _jobs.get(jid)
            if not job or job["status"] in ("done", "error"):
                return
            now = time.time()
            idle = now - job.get("last_activity", job["created_at"])
            total = now - job["created_at"]
            inactivity_limit = job.get("inactivity_timeout", _JOB_INACTIVITY_TIMEOUT)
            if idle > inactivity_limit:
                job["error"] = (
                    f"Zeitüberschreitung: Kein Fortschritt seit {int(idle)}s. "
                    "Möglicherweise hängt ein externer API-Aufruf."
                )
                job["status"] = "error"
                return
            if total > _JOB_TIMEOUT_SECONDS:
                job["error"] = "Zeitüberschreitung: Gesamtlimit von 30 Minuten überschritten."
                job["status"] = "error"
                return

    async def _run_job_watched(jid: str, txt: str, link: str) -> None:
        watchdog_task = asyncio.create_task(_watchdog(jid))
        try:
            await _run_job(jid, txt, link)
        finally:
            watchdog_task.cancel()

    asyncio.create_task(_run_job_watched(job_id, text, url))

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Poll job status. Frontend calls this every ~1.5 s."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job nicht gefunden.")
    job = _jobs[job_id]

    # Detect stale jobs: no activity for too long OR total time exceeded
    if job["status"] in ("pending", "running"):
        now = time.time()
        idle = now - job.get("last_activity", job["created_at"])
        total = now - job["created_at"]
        if idle > _JOB_INACTIVITY_TIMEOUT + 30:
            job["status"] = "error"
            job["error"] = "Zeitüberschreitung: Kein Fortschritt – Job hängt."
        elif total > _JOB_TIMEOUT_SECONDS + 30:
            job["status"] = "error"
            job["error"] = "Zeitüberschreitung: Gesamtlimit überschritten."

    return {
        "status": job["status"],
        "steps": job["steps"],
        "result": job["result"],
        "error": job["error"],
        "extracted_content": job.get("extracted_content"),
        "archive_id": job.get("archive_id"),
        "from_cache": job.get("from_cache", False),
    }


# ── Archive endpoints ──────────────────────────────────────────────


@app.get("/api/archive")
async def list_archive(
    limit: int = 50,
    offset: int = 0,
    rating: str | None = None,
    search: str | None = None,
) -> dict:
    """Liste vergangene Analysen auf (neueste zuerst).

    Query-Parameter:
        limit:  Max. Einträge pro Seite (1-100, default 50)
        offset: Überspringe N Einträge (Pagination)
        rating: Filter nach Bewertung ("Wahr", "Irreführend", etc.)
        search: Volltextsuche in Titel, Zusammenfassung, URL
    """
    archive = _get_archive()
    return archive.list(
        limit=limit,
        offset=offset,
        rating_filter=rating,
        search=search,
    )


@app.get("/api/archive/{archive_id}")
async def get_archive_entry(archive_id: str) -> dict:
    """Hole einen vollständigen Archiv-Eintrag mit allen Details."""
    archive = _get_archive()
    entry = archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht gefunden.")
    return entry


@app.delete("/api/archive/{archive_id}")
async def delete_archive_entry(archive_id: str) -> dict:
    """Lösche einen einzelnen Archiv-Eintrag."""
    archive = _get_archive()
    deleted = archive.delete(archive_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht gefunden.")
    return {"deleted": True}


@app.get("/api/archive-stats")
async def archive_stats() -> dict:
    """Statistiken über das Archiv (Anzahl, Verteilung, etc.)."""
    archive = _get_archive()
    return archive.stats()


# ── PDF Export endpoint ────────────────────────────────────────────


@app.get("/api/export/pdf/{archive_id}")
async def export_pdf(archive_id: str) -> Response:
    """Exportiere einen Archiv-Eintrag als PDF-Report.

    Gibt das PDF als Download zurück (Content-Disposition: attachment).
    """
    archive = _get_archive()
    entry = archive.get(archive_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Archiv-Eintrag nicht gefunden.")

    from tools.pdf_export import generate_pdf

    result = entry.get("result", {})
    title = entry.get("title", "Faktencheck-Report")
    source_url = entry.get("source_url", "")

    pdf_bytes = generate_pdf(result, title=title, source_url=source_url or "")

    # Dateiname aus Titel ableiten (sanitized)
    import re
    safe_title = re.sub(r"[^\w\s-]", "", title or "report")[:50].strip().replace(" ", "_")
    filename = f"faktencheck_{safe_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/export/pdf")
async def export_pdf_from_result(req: dict) -> Response:
    """Exportiere ein Analyse-Ergebnis direkt als PDF (ohne Archiv).

    Erwartet im Body: { result: {...}, title?: str, source_url?: str }
    Nützlich für den Export direkt aus einem laufenden Job.
    """
    result = req.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="Kein Analyse-Ergebnis angegeben.")

    from tools.pdf_export import generate_pdf

    title = req.get("title", "Faktencheck-Report")
    source_url = req.get("source_url", "")

    pdf_bytes = generate_pdf(result, title=title, source_url=source_url)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="faktencheck_report.pdf"',
        },
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
