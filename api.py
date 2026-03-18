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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import AppConfig
from orchestrator import Orchestrator
from tools.archive import AnalysisArchive
from tools.content_extractor import ContentExtractor, ExtractedContent, detect_platform, extract_urls, is_url

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
# If no progress (new step) for this long, the job is considered stale
_JOB_INACTIVITY_TIMEOUT = 300  # 5 minutes without progress


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

        if len(text) > config.max_input_chars:
            text = text[: config.max_input_chars]

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

        # ── Phase 2 + 3: Fact-check claims (parallel) + Rhetoric ──
        from models.schemas import ClaimType

        fact_checks = []
        number_audits = []
        analysis_errors = []
        checkable = [c for c in extraction.claims if c.type != ClaimType.OPINION]

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

        # Run all claim checks + rhetoric analysis in parallel
        async def _run_rhetoric():
            push_step("Phase 3", "Rhetoric Analyzer", "Rhetorische Analyse gestartet…", "running")
            rhet_result, rhet_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda: orchestrator.rhetoric_analyzer.run_safe(text)
            )
            return rhet_result, rhet_error

        claim_tasks = [_check_claim(c) for c in checkable]
        rhetoric_task = _run_rhetoric()
        results = await asyncio.gather(*claim_tasks, rhetoric_task, return_exceptions=True)

        # Process rhetoric result (last item in gathered results)
        rhetoric_gathered = results[-1]
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

        # Check if any claim task raised an exception
        for i, res in enumerate(results[:-1]):
            if isinstance(res, Exception):
                analysis_errors.append(f"ClaimCheck: {res}")

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


@app.post("/api/extract")
async def extract_content(req: ExtractRequest) -> dict:
    """Extract content from a URL without running analysis. Returns extracted text and metadata."""
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
async def analyze(req: AnalyzeRequest) -> dict:
    """Submit an analysis job. Returns a job_id for polling.

    Accepts either plain text or a URL (or both). If a URL is provided,
    content is first extracted and then analyzed.
    """
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
            if idle > _JOB_INACTIVITY_TIMEOUT:
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


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
