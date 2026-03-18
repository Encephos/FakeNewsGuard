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

# Clean up jobs older than 1 hour to avoid memory leaks
_JOB_TTL_SECONDS = 3600


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

async def _run_job(job_id: str, text: str) -> None:
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

    try:
        job["status"] = "running"
        config = AppConfig(verbose=True)
        orchestrator = Orchestrator(config)
        claims_map: dict[str, Any] = {}

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

        # ── Phase 2: Fact-check each claim ────────────────────────
        from models.schemas import ClaimType

        fact_checks = []
        number_audits = []
        analysis_errors = []
        checkable = [c for c in extraction.claims if c.type != ClaimType.OPINION]

        for claim in checkable:
            push_step("Phase 2", "Fact Checker", f"Prüfe: {claim.text[:80]}…", "running")

            fc_result, fc_error = await asyncio.get_event_loop().run_in_executor(
                None, lambda c=claim: orchestrator.fact_checker.run_safe(c)
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

        # ── Phase 3: Rhetoric ─────────────────────────────────────
        push_step("Phase 3", "Rhetoric Analyzer", "Rhetorische Analyse gestartet…", "running")
        rhetoric_result, rhetoric_error = await asyncio.get_event_loop().run_in_executor(
            None, lambda: orchestrator.rhetoric_analyzer.run_safe(text)
        )
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

    except Exception as exc:
        traceback.print_exc()
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["status"] = "error"


# ── API endpoints ──────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    text: str


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Submit an analysis job. Returns a job_id for polling."""
    _cleanup_old_jobs()

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text angegeben.")

    config = AppConfig()
    if len(text) > config.max_input_chars:
        text = text[: config.max_input_chars]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "steps": [],
        "result": None,
        "error": None,
        "created_at": time.time(),
    }

    # Fire-and-forget background task
    asyncio.create_task(_run_job(job_id, text))

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Poll job status. Frontend calls this every ~1.5 s."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job nicht gefunden.")
    job = _jobs[job_id]
    return {
        "status": job["status"],
        "steps": job["steps"],
        "result": job["result"],
        "error": job["error"],
    }


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
