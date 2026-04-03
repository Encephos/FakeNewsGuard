"""Shared state, singletons, Pydantic models, and helper functions for the API package."""

from __future__ import annotations

import contextvars
import os
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from config import AppConfig, RateLimitConfig, ScoutTier
from config.infrastructure import AuthConfig, JobConfig
from i18n import t
from tools.db.factory import create_archive, create_graph, create_user_db
from tools.logger import get_logger, record_auth_attempt
from tools.rate_limiter import RateLimiter
from tools.user_db import create_access_token, create_refresh_token, decode_token

# ── Logging ──────────────────────────────────────────────────────
logger = get_logger("fng-api")

# True only when explicitly enabled (e.g. behind an HTTPS reverse proxy)
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"

# ── Correlation-ID Context-Variable ──────────────────────────────
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

# ── Job Store (Redis-backed, replaces former in-memory dict) ─────
from worker.job_store import get_job_store, reset_job_store  # noqa: E402

# ── Job-Konfiguration (aus config/infrastructure.py, Env-Var-überschreibbar) ──
_job_config = JobConfig()
JOB_TTL_SECONDS = _job_config.ttl_seconds
JOB_TIMEOUT_SECONDS = _job_config.timeout_seconds
JOB_INACTIVITY_TIMEOUT = _job_config.inactivity_timeout


# ── Rate-Limiter (singleton) ────────────────────────────────────
_rate_limiter: RateLimiter | None = None


def _get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        config = AppConfig()
        _rate_limiter = RateLimiter(config.rate_limit)
    return _rate_limiter


# ── Archive (singleton, erstellt bei erstem Zugriff) ─────────────
_archive = None


def get_archive():
    global _archive
    if _archive is None:
        config = AppConfig()
        _archive = create_archive(config)
    return _archive


# ── Cross-Reference Graph (singleton) ───────────────────────────
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        config = AppConfig()
        _graph = create_graph(config)
    return _graph


# ── User Database (singleton) ──────────────────────────────────
_user_db = None


def get_user_db():
    global _user_db
    if _user_db is None:
        config = AppConfig()
        _user_db = create_user_db(config)
        # Auto-migrate from old users.json on first access (SQLite backend only)
        if hasattr(_user_db, "migrate_from_json"):
            import pathlib
            json_path = pathlib.Path(__file__).resolve().parent.parent / "users.json"
            if json_path.exists():
                imported = _user_db.migrate_from_json(str(json_path))
                if imported > 0:
                    import logging
                    logging.getLogger("fng-api").info("Migrated %d users from users.json", imported)
    return _user_db


# ── Auth Rate-Limiter (konfigurierbar via RateLimitConfig) ──────
_auth_rate_limiter: RateLimiter | None = None


def _get_auth_rate_limiter() -> RateLimiter:
    global _auth_rate_limiter
    if _auth_rate_limiter is None:
        config = AppConfig()
        rl = config.rate_limit
        _auth_rate_limiter = RateLimiter(
            RateLimitConfig(
                enabled=True,
                requests_per_minute=rl.auth_requests_per_minute,
                burst=rl.auth_burst,
            )
        )
    return _auth_rate_limiter


def check_auth_rate_limit(request: Request) -> None:
    """Pruefe Auth-Rate-Limit (5 req/min). Schuetzt vor Brute-Force."""
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


# ── Auth helpers ────────────────────────────────────────────────

def get_current_user_optional(request: Request) -> dict[str, Any] | None:
    """Extract user from JWT Bearer token. Returns None if no token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_db = get_user_db()
        return user_db.get_by_id(payload["sub"])
    except Exception:
        return None


def get_current_user(request: Request) -> dict[str, Any]:
    """Extract user from JWT Bearer token. Raises 401 if missing/invalid."""
    user = get_current_user_optional(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht authentifiziert.")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    """Require the current user to be an admin. Raises 403 otherwise."""
    user = get_current_user(request)
    if not user.get("admin"):
        raise HTTPException(status_code=403, detail="Nur Admins haben Zugriff.")
    return user


def check_rate_limit(request: Request) -> None:
    """Pruefe Rate-Limit fuer den aktuellen Request. Wirft HTTPException bei Ueberschreitung."""
    limiter = _get_rate_limiter()
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.check(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=t("api.errors.rate_limit").format(seconds=f"{retry_after:.0f}"),
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


# ── Rating mappings ──────────────────────────────────────────────

def get_rating_map() -> dict[str, str]:
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


def transform_result(result: Any, claims_map: dict[str, Any]) -> dict:
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
            "confidence": round(fc.confidence * 100) if fc.confidence >= 0.0 else None,
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
        "overall_rating": get_rating_map().get(
            result.overall_rating.value, t("api.ratings.MIXED")
        ),
        "overall_rating_key": result.overall_rating.value,
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
        "cost_summary": result.cost_summary.model_dump() if result.cost_summary else None,
    }


def format_image_analysis(result: Any) -> str:
    """Konvertiert ImageAnalysisResult in einen lesbaren Text-Block fuer LLM-Kontext."""
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
        parts.append(f"**Gesamteinschaetzung:** {result.overall_assessment}")

    return "\n".join(parts).strip()


# ── Pydantic request/response models ────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""
    invite_code: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class AnalyzeRequest(BaseModel):
    text: str
    url: str | None = None
    agent: str | None = None  # "Scout Lite" | "Scout Pro" | "Scout Max" | "Commander Pro" | "Commander Max"
    tier: str | None = None   # "lite" | "pro" | "max" | "commander-pro" | "commander-max"


class ExtractRequest(BaseModel):
    url: str


class UpdateProfileRequest(BaseModel):
    display_name: str


class UpdateTierRequest(BaseModel):
    tier: str  # "lite" | "pro" | "max"


class TelegramVerifyRequest(BaseModel):
    code: str
    telegram_id: str


class SetupCredentialsRequest(BaseModel):
    telegram_id: str
    email: EmailStr
    password: str
    setup_secret: str


class CreateRegistrationCodeRequest(BaseModel):
    label: str = ""
    max_uses: int = 1
    expires_days: int | None = None
