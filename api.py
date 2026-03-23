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
import contextvars
import os
import time
import uuid
from typing import Any

# True only when explicitly enabled (e.g. behind an HTTPS reverse proxy)
_SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr

from config import AppConfig, RateLimitConfig, ScoutTier
from i18n import set_default_locale, t
from orchestrator import InputValidationError, Orchestrator
from tools.archive import AnalysisArchive
from tools.content_extractor import ContentExtractor, ExtractedContent, detect_platform, extract_urls, is_url
from tools.cross_reference import CrossReferenceGraph
from tools.logger import (
    get_logger,
    get_metrics_snapshot,
    get_recent_logs,
    record_auth_attempt,
    record_request,
    setup_logging,
)
from tools.rate_limiter import RateLimiter
from tools.user_db import UserDB, create_access_token, create_refresh_token, decode_token

# ── Logging einrichten ─────────────────────────────────────────────
setup_logging()
logger = get_logger("fng-api")

# ── Correlation-ID Context-Variable ───────────────────────────────
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

app = FastAPI(title="FakeNewsGuard API")

# i18n auf konfigurierte Sprache setzen
set_default_locale(AppConfig().language)

# CORS – konfigurierbar via CORS_ORIGINS Umgebungsvariable.
# Standard: "*" (alle Origins), für Produktion explizit setzen.
_cors_origins = AppConfig().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ── HTTP Middleware: Request-Logging + Metriken ────────────────────
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any) -> Any:
    correlation_id = str(uuid.uuid4())[:8]
    _correlation_id.set(correlation_id)
    start = time.monotonic()
    path = request.url.path
    method = request.method
    client_ip = request.client.host if request.client else "unknown"

    logger.info("→ %s %s [%s] rid=%s", method, path, client_ip, correlation_id)
    try:
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, response.status_code, duration_ms)
        logger.info(
            "← %s %s %d %.1fms rid=%s",
            method, path, response.status_code, duration_ms, correlation_id,
        )
        response.headers["X-Request-ID"] = correlation_id
        # Periodisch abgelaufene Jobs bereinigen (günstig, läuft nur bei Requests)
        _cleanup_old_jobs()
        return response
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        record_request(path, 500, duration_ms)
        logger.exception("✗ %s %s ERROR %.1fms rid=%s: %s", method, path, duration_ms, correlation_id, exc)
        raise

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

# ── Cross-Reference Graph (singleton) ────────────────────────────
_graph: CrossReferenceGraph | None = None


def _get_graph() -> CrossReferenceGraph:
    global _graph
    if _graph is None:
        config = AppConfig()
        _graph = CrossReferenceGraph(config.graph.db_path)
    return _graph


# ── User Database (singleton) ───────────────────────────────────
_user_db: UserDB | None = None


def _get_user_db() -> UserDB:
    global _user_db
    if _user_db is None:
        config = AppConfig()
        _user_db = UserDB(config.user_db)
        # Auto-migrate from old users.json on first access
        import pathlib
        json_path = pathlib.Path(__file__).parent / "users.json"
        if json_path.exists():
            imported = _user_db.migrate_from_json(str(json_path))
            if imported > 0:
                import logging
                logging.getLogger("fng-api").info("Migrated %d users from users.json", imported)
    return _user_db


# ── Auth Rate-Limiter (strenger: 5 req/min, burst 2) ─────────────
_auth_rate_limiter: RateLimiter | None = None


def _get_auth_rate_limiter() -> RateLimiter:
    global _auth_rate_limiter
    if _auth_rate_limiter is None:
        _auth_rate_limiter = RateLimiter(
            RateLimitConfig(enabled=True, requests_per_minute=5, burst=2)
        )
    return _auth_rate_limiter


def _check_auth_rate_limit(request: Request) -> None:
    """Prüfe Auth-Rate-Limit (5 req/min). Schützt vor Brute-Force."""
    limiter = _get_auth_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        logger.warning("Auth rate limit erreicht: %s", client_ip)
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Anmeldeversuche. Bitte warte {retry_after:.0f} Sekunden.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


# ── Auth helpers ─────────────────────────────────────────────────

def _get_current_user_optional(request: Request) -> dict[str, Any] | None:
    """Extract user from JWT Bearer token. Returns None if no token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_db = _get_user_db()
        return user_db.get_by_id(payload["sub"])
    except Exception:
        return None


def _get_current_user(request: Request) -> dict[str, Any]:
    """Extract user from JWT Bearer token. Raises 401 if missing/invalid."""
    user = _get_current_user_optional(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert.")
    return user


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

def _get_rating_map() -> dict[str, str]:
    """Rating-Map dynamisch aus i18n laden."""
    return {
        k: t(f"api.ratings.{k}")
        for k in ("RELIABLE", "MOSTLY_RELIABLE", "MIXED", "MISLEADING",
                   "HIGHLY_MISLEADING", "FABRICATED")
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
        "overall_rating": _get_rating_map().get(
            result.overall_rating.value, t("api.ratings.MIXED")
        ),
        "confidence": round(result.confidence * 100),
        "summary": result.summary,
        "claims": frontend_claims,
        "rhetoric": [
            {
                "name": tech.technique,
                "severity": tech.severity.value,
                "description": tech.explanation,
                "example": tech.example or "",
            }
            for tech in result.manipulation_techniques
        ],
        "corrections": result.key_corrections or [],
        "fairness": result.fairness_notes or [],
        "sources": result.sources or [],
    }


def _format_image_analysis(result: Any) -> str:
    """Konvertiert ImageAnalysisResult in einen lesbaren Text-Block für LLM-Kontext."""
    parts: list[str] = []
    for item in result.items:
        idx = item.image_index + 1
        parts.append(f"### Bild {idx}")
        if item.ocr_text:
            parts.append(f"**Sichtbarer Text:** {item.ocr_text}")
        if item.visible_elements:
            parts.append(f"**Erkannte Elemente:** {', '.join(item.visible_elements)}")
        if item.manipulation_signs:
            parts.append(f"**Manipulationsanzeichen:** {', '.join(item.manipulation_signs)}")
        if item.emotional_framing:
            parts.append(f"**Emotionales Framing:** {item.emotional_framing}")
        if item.infographic_data:
            parts.append(f"**Infografik-Daten:** {item.infographic_data}")
        if item.context_clues:
            parts.append(f"**Kontexthinweise:** {', '.join(item.context_clues)}")
        parts.append("")

    if result.cross_image_observations:
        parts.append(f"**Zusammenspiel der Bilder:** {result.cross_image_observations}")
    if result.overall_assessment:
        parts.append(f"**Gesamteinschätzung:** {result.overall_assessment}")

    return "\n".join(parts).strip()


# ── Background worker ──────────────────────────────────────────────

async def _run_job(job_id: str, text: str, url: str = "", tier: ScoutTier = ScoutTier.MAX) -> None:
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
        config = AppConfig(verbose=True, tier=tier)
        orchestrator = Orchestrator(config)
        claims_map: dict[str, Any] = {}

        # ── Phase 0: URL Content Extraction (if URL provided) ──────
        content = None  # wird in Phase 0 gesetzt, für Phase 0.5 benötigt
        if url:
            platform = detect_platform(url)
            push_step("Phase 0", "Content Extractor", t("api.steps.extracting_content").format(platform=platform.capitalize()), "running")
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
                image_analysis_text = _format_image_analysis(img_result)
                push_step(
                    "Phase 0.5", "Image Analyzer",
                    t("api.steps.images_analyzed").format(count=len(img_result.items)),
                )
                # Bildanalyse als Kontext in den Text einfügen → ClaimExtractor sieht ihn
                text += f"\n\n--- Bildanalyse ---\n\n{image_analysis_text}"
                # Für das Frontend speichern
                if "extracted_content" in job:
                    job["extracted_content"]["image_analysis"] = {
                        "items": [item.model_dump() for item in img_result.items],
                        "overall_assessment": img_result.overall_assessment,
                        "cross_image_observations": img_result.cross_image_observations,
                    }

        # Input-Validierung und -Kürzung wird zentral im Orchestrator erledigt

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

            # ── Cross-Reference Graph: Beziehungen erfassen ────────
            try:
                graph = _get_graph()
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
                user_db = _get_user_db()
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

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class AnalyzeRequest(BaseModel):
    text: str
    url: str | None = None
    agent: str | None = None  # "Scout Lite" | "Scout Pro" | "Scout Max"
    tier: str | None = None   # "lite" | "pro" | "max" (default: max)


class ExtractRequest(BaseModel):
    url: str


# ── Auth endpoints ────────────────────────────────────────────────

@app.post("/api/auth/register")
async def auth_register(req: RegisterRequest, request: Request) -> dict:
    """Register a new user account."""
    _check_auth_rate_limit(request)
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen lang sein.")

    user_db = _get_user_db()
    user = user_db.create_user(
        email=req.email,
        password=req.password,
        display_name=req.display_name or req.email.split("@")[0],
    )
    if user is None:
        record_auth_attempt(False)
        raise HTTPException(status_code=409, detail="Ein Konto mit dieser E-Mail existiert bereits.")

    record_auth_attempt(True)
    logger.info("Neuer Nutzer registriert: %s", req.email)
    access_token = create_access_token(user["id"], user["tier"], bool(user["admin"]))
    refresh_token = create_refresh_token(user["id"])

    response = JSONResponse(content={
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "tier": user["tier"],
            "admin": bool(user["admin"]),
            "consent": bool(user.get("consent", 0)),
        },
        "access_token": access_token,
    })
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=_SECURE_COOKIES,
        samesite="lax",
        max_age=7 * 86400,
        path="/api/auth",
    )
    return response


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request) -> dict:
    """Login with email and password."""
    _check_auth_rate_limit(request)
    user_db = _get_user_db()
    user = user_db.authenticate(req.email, req.password)
    if user is None:
        record_auth_attempt(False)
        logger.warning("Fehlgeschlagener Login-Versuch: %s", req.email)
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort ungültig.")

    record_auth_attempt(True)
    access_token = create_access_token(user["id"], user["tier"], bool(user["admin"]))
    remember_days = 30 if req.remember_me else 7
    refresh_token = create_refresh_token(user["id"], expire_days=remember_days)

    response = JSONResponse(content={
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "tier": user["tier"],
            "admin": bool(user["admin"]),
            "consent": bool(user.get("consent", 0)),
        },
        "access_token": access_token,
    })
    # remember_me=False → session cookie (no max_age, cleared when browser closes)
    # remember_me=True  → persistent 30-day cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=_SECURE_COOKIES,
        samesite="lax",
        max_age=30 * 86400 if req.remember_me else None,
        path="/api/auth",
    )
    return response


@app.post("/api/auth/refresh")
async def auth_refresh(request: Request) -> dict:
    """Refresh the access token using the refresh cookie."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="Kein Refresh-Token.")

    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Ungültiger Token-Typ.")
    except Exception:
        raise HTTPException(status_code=401, detail="Refresh-Token ungültig oder abgelaufen.")

    user_db = _get_user_db()
    user = user_db.get_by_id(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Nutzer nicht gefunden.")

    access_token = create_access_token(user["id"], user["tier"], bool(user["admin"]))
    return {"access_token": access_token}


@app.get("/api/auth/me")
async def auth_me(request: Request) -> dict:
    """Get current user info from JWT."""
    user = _get_current_user(request)
    return {
        "id": user["id"],
        "email": user.get("email"),
        "display_name": user.get("display_name", ""),
        "tier": user["tier"],
        "admin": bool(user.get("admin", 0)),
        "telegram_id": user.get("telegram_id"),
        "consent": bool(user.get("consent", 0)),
    }


@app.post("/api/auth/logout")
async def auth_logout() -> dict:
    """Clear the refresh token cookie."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="refresh_token", path="/api/auth")
    return response


class UpdateProfileRequest(BaseModel):
    display_name: str


@app.patch("/api/auth/profile")
async def auth_update_profile(req: UpdateProfileRequest, request: Request) -> dict:
    """Update current user's display name."""
    user = _get_current_user(request)
    name = req.display_name.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Anzeigename muss 1–50 Zeichen lang sein.")

    user_db = _get_user_db()
    user_db.update_display_name(user["id"], name)
    updated = user_db.get_by_id(user["id"])
    return {
        "id": updated["id"],
        "email": updated.get("email"),
        "display_name": updated["display_name"],
        "tier": updated["tier"],
        "admin": bool(updated.get("admin", 0)),
        "telegram_id": updated.get("telegram_id"),
    }


@app.post("/api/auth/consent")
async def auth_consent(request: Request) -> dict:
    """Set the logging consent flag for the current user."""
    user = _get_current_user(request)
    user_db = _get_user_db()
    user_db.set_consent(user["id"], True)
    return {"ok": True}


@app.post("/api/auth/telegram/request-link")
async def auth_telegram_request_link(request: Request) -> dict:
    """Generate a 6-char code for the user to send to the Telegram bot."""
    user = _get_current_user(request)
    if user.get("telegram_id"):
        raise HTTPException(status_code=409, detail="Telegram ist bereits verknüpft.")

    user_db = _get_user_db()
    code = user_db.create_link_code(user["id"])
    return {"code": code, "expires_in": 600}


class TelegramVerifyRequest(BaseModel):
    code: str
    telegram_id: str


@app.post("/api/auth/telegram/verify-link")
async def auth_telegram_verify_link(req: TelegramVerifyRequest) -> dict:
    """Called by the Telegram bot to verify a link code and bind the account."""
    user_db = _get_user_db()
    user = user_db.verify_link_code(req.code.strip().upper(), str(req.telegram_id))
    if user is None:
        raise HTTPException(status_code=400, detail="Code ungültig, abgelaufen oder Telegram-ID bereits verknüpft.")
    return {
        "ok": True,
        "user_id": user["id"],
        "display_name": user.get("display_name", ""),
    }


@app.delete("/api/auth/telegram/unlink")
async def auth_telegram_unlink(request: Request) -> dict:
    """Remove the Telegram link from the current user's account."""
    user = _get_current_user(request)
    if not user.get("telegram_id"):
        raise HTTPException(status_code=400, detail="Kein Telegram-Konto verknüpft.")

    user_db = _get_user_db()
    user_db.unlink_telegram(user["id"])
    return {"ok": True}


class SetupCredentialsRequest(BaseModel):
    telegram_id: str
    email: EmailStr
    password: str
    setup_secret: str


@app.post("/api/auth/setup-credentials")
async def auth_setup_credentials(req: SetupCredentialsRequest) -> dict:
    """One-time endpoint to add email+password to a Telegram-only account.

    Requires SETUP_SECRET env var to be set. Only works if the account
    has no email/password yet (prevents credential override attacks).
    """
    import os
    expected_secret = os.getenv("SETUP_SECRET", "")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="SETUP_SECRET nicht konfiguriert.")
    if req.setup_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Falsches Setup-Secret.")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen lang sein.")

    user_db = _get_user_db()
    user = user_db.get_by_telegram_id(req.telegram_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"Telegram-Nutzer {req.telegram_id} nicht gefunden.")
    if user.get("email"):
        raise HTTPException(status_code=409, detail="Dieser Account hat bereits E-Mail-Zugangsdaten.")

    ok = user_db.set_credentials(user["id"], req.email, req.password)
    if not ok:
        raise HTTPException(status_code=409, detail="E-Mail bereits von einem anderen Account verwendet.")

    return {"ok": True, "message": f"Credentials für Telegram-ID {req.telegram_id} gesetzt."}


# ── Admin endpoints ───────────────────────────────────────────────

def _require_admin(request: Request) -> dict[str, Any]:
    """Require the current user to be an admin. Raises 403 otherwise."""
    user = _get_current_user(request)
    if not user.get("admin"):
        raise HTTPException(status_code=403, detail="Nur Admins haben Zugriff.")
    return user


class UpdateTierRequest(BaseModel):
    tier: str  # "lite" | "pro" | "max"


@app.get("/api/admin/users")
async def admin_list_users(request: Request) -> dict:
    """List all users with usage stats. Admin only."""
    _require_admin(request)
    user_db = _get_user_db()
    users = user_db.list_users()
    return {"users": users}


@app.patch("/api/admin/users/{user_id}/tier")
async def admin_update_tier(user_id: str, req: UpdateTierRequest, request: Request) -> dict:
    """Change a user's tier. Admin only."""
    _require_admin(request)
    if req.tier not in ("lite", "pro", "max"):
        raise HTTPException(status_code=400, detail="Ungültiger Tier. Erlaubt: lite, pro, max")
    user_db = _get_user_db()
    if not user_db.update_tier(user_id, req.tier):
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    return {"ok": True, "tier": req.tier}


@app.get("/api/admin/users/{user_id}/usage")
async def admin_user_usage(user_id: str, request: Request) -> dict:
    """Get usage log for a specific user. Admin only."""
    _require_admin(request)
    user_db = _get_user_db()
    user = user_db.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
    usage = user_db.get_user_usage(user_id, days=30)
    return {"user_id": user_id, "usage": usage}


@app.get("/api/admin/stats")
async def admin_stats(request: Request) -> dict:
    """Get overall platform stats. Admin only."""
    _require_admin(request)
    user_db = _get_user_db()
    users = user_db.list_users()
    # Analyse-Zahlen aus dem Archiv (erfasst auch unauthentifizierte Analysen)
    archive = _get_archive()
    archive_counts = archive.count_analyses()
    total_analyses = archive_counts["total"]
    month_analyses = archive_counts["last_30_days"]
    tier_counts = {}
    for u in users:
        t = u.get("tier", "lite")
        tier_counts[t] = tier_counts.get(t, 0) + 1
    return {
        "total_users": len(users),
        "total_analyses": total_analyses,
        "month_analyses": month_analyses,
        "tier_distribution": tier_counts,
    }


@app.get("/api/admin/metrics")
async def admin_metrics(request: Request) -> dict:
    """Echtzeit-Systemmetriken (Requests, Latenzen, Auth-Stats). Admin only."""
    _require_admin(request)
    snapshot = get_metrics_snapshot()
    snapshot["active_jobs"] = sum(
        1 for j in _jobs.values() if j["status"] in ("pending", "running")
    )
    return snapshot


@app.get("/api/admin/logs")
async def admin_logs(
    request: Request, limit: int = 100, level: str | None = None
) -> dict:
    """Letzte Log-Einträge aus dem In-Memory-Puffer. Admin only."""
    _require_admin(request)
    return {"logs": get_recent_logs(limit=limit, level=level)}


def _check_rate_limit(request: Request) -> None:
    """Prüfe Rate-Limit für den aktuellen Request. Wirft HTTPException bei Überschreitung."""
    limiter = _get_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=t("api.errors.rate_limit").format(seconds=f"{retry_after:.0f}"),
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


@app.post("/api/extract")
async def extract_content(req: ExtractRequest, request: Request) -> dict:
    """Extract content from a URL without running analysis. Returns extracted text and metadata."""
    _check_rate_limit(request)
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
        raise HTTPException(status_code=400, detail=t("api.errors.no_text"))

    # ── Tier-Bestimmung ───────────────────────────────────────────
    # Wenn der Nutzer authentifiziert ist, wird sein Plan-Tier aus der DB
    # verwendet und begrenzt den gewünschten Tier.
    # Ohne Auth: Fallback auf Request-basierte Logik (Telegram-Bot, etc.)
    PLAN_ACCESS: dict[ScoutTier, set[ScoutTier]] = {
        ScoutTier.LITE: {ScoutTier.LITE},
        ScoutTier.PRO: {ScoutTier.LITE, ScoutTier.PRO},
        ScoutTier.MAX: {ScoutTier.LITE, ScoutTier.PRO, ScoutTier.MAX},
    }

    user = _get_current_user_optional(request)
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

    # Bestimme gewünschten Tier (default: Plan-Maximum)
    tier = plan
    if req.tier:
        try:
            tier = ScoutTier(req.tier.strip().lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiger Tier: {req.tier}. Erlaubt: lite, pro, max")

    # Prüfe ob der Plan Zugriff auf den Tier hat
    if tier not in PLAN_ACCESS[plan]:
        raise HTTPException(
            status_code=403,
            detail=f"Dein Plan ({plan.value.upper()}) erlaubt keinen Zugriff auf Tier '{tier.value}'. "
                   f"Erlaubt: {', '.join(t.value for t in sorted(PLAN_ACCESS[plan], key=lambda x: x.value))}",
        )

    config = AppConfig(tier=tier)

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
    _jobs[job_id] = {
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
                job["error"] = t("api.errors.timeout_inactivity").format(seconds=int(idle))
                job["status"] = "error"
                return
            if total > _JOB_TIMEOUT_SECONDS:
                job["error"] = t("api.errors.timeout_hard")
                job["status"] = "error"
                return

    async def _run_job_watched(jid: str, txt: str, link: str, t: ScoutTier = ScoutTier.MAX) -> None:
        watchdog_task = asyncio.create_task(_watchdog(jid))
        try:
            await _run_job(jid, txt, link, tier=t)
        finally:
            watchdog_task.cancel()

    asyncio.create_task(_run_job_watched(job_id, text, url, tier))

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Poll job status. Frontend calls this every ~1.5 s."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=t("api.errors.job_not_found"))
    job = _jobs[job_id]

    # Detect stale jobs: no activity for too long OR total time exceeded
    if job["status"] in ("pending", "running"):
        now = time.time()
        idle = now - job.get("last_activity", job["created_at"])
        total = now - job["created_at"]
        if idle > _JOB_INACTIVITY_TIMEOUT + 30:
            job["status"] = "error"
            job["error"] = t("api.errors.timeout_stale")
        elif total > _JOB_TIMEOUT_SECONDS + 30:
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
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))
    return entry


@app.delete("/api/archive/{archive_id}")
async def delete_archive_entry(archive_id: str) -> dict:
    """Lösche einen einzelnen Archiv-Eintrag."""
    archive = _get_archive()
    deleted = archive.delete(archive_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))
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
        raise HTTPException(status_code=404, detail=t("api.errors.archive_not_found"))

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
        raise HTTPException(status_code=400, detail=t("api.errors.no_result"))

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


# ── Cross-Reference Graph endpoints ───────────────────────────────


@app.get("/api/graph/stats")
async def graph_stats() -> dict:
    """Statistiken des Cross-Reference Graphen."""
    graph = _get_graph()
    return graph.stats()


@app.get("/api/graph/actor/{actor_name}")
async def graph_actor(actor_name: str) -> dict:
    """Alle Claims, in denen ein Akteur erwähnt wird."""
    graph = _get_graph()
    claims = graph.get_actor_claims(actor_name)
    return {
        "actor": actor_name,
        "claims": [
            {"id": c.id, "text": c.label, "rating": c.properties.get("rating", "")}
            for c in claims
        ],
    }


@app.get("/api/graph/source/{domain}")
async def graph_source(domain: str) -> dict:
    """Wie oft und in welchem Kontext wurde eine Quelle verwendet?"""
    graph = _get_graph()
    return graph.get_source_history(domain)


@app.get("/api/graph/search")
async def graph_search(
    type: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict:
    """Suche im Graphen nach Knoten."""
    graph = _get_graph()
    nodes = graph.find_nodes(node_type=type, label_search=q, limit=limit)
    return {
        "nodes": [
            {"id": n.id, "type": n.type, "label": n.label, "properties": n.properties}
            for n in nodes
        ],
    }


@app.get("/api/graph/node/{node_id:path}")
async def graph_node(node_id: str) -> dict:
    """Knoten mit allen Kanten."""
    graph = _get_graph()
    node = graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")
    edges = graph.get_edges(node_id)
    neighbors = graph.get_neighbors(node_id)
    return {
        "node": {"id": node.id, "type": node.type, "label": node.label, "properties": node.properties},
        "edges": [
            {"source": e.source_id, "target": e.target_id, "relation": e.relation}
            for e in edges
        ],
        "neighbors": [
            {"id": n.id, "type": n.type, "label": n.label}
            for n in neighbors
        ],
    }


@app.get("/api/locales")
async def list_locales() -> dict:
    """Verfügbare Sprachen und aktuelle Einstellung."""
    from i18n import available_locales, get_default_locale
    return {
        "current": get_default_locale(),
        "available": available_locales(),
    }


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
