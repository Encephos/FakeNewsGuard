"""Auth endpoints: register, login, refresh, me, consent, telegram link, setup."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tools.logger import record_auth_attempt
from tools.user_db import create_access_token, create_refresh_token, decode_token

from config.infrastructure import AuthConfig

from .dependencies import (
    SECURE_COOKIES,
    RegisterRequest,
    LoginRequest,
    UpdateProfileRequest,
    TelegramVerifyRequest,
    SetupCredentialsRequest,
    check_auth_rate_limit,
    get_current_user,
    get_user_db,
    logger,
)

_auth_config = AuthConfig()

router = APIRouter()


@router.post("/auth/register")
async def auth_register(req: RegisterRequest, request: Request) -> dict:
    """Register a new user account."""
    check_auth_rate_limit(request)
    if len(req.password) < _auth_config.min_password_length:
        raise HTTPException(
            status_code=400,
            detail=f"Passwort muss mindestens {_auth_config.min_password_length} Zeichen lang sein.",
        )

    # Validate invite code
    user_db = get_user_db()
    code = req.invite_code.strip().upper()
    if not user_db.validate_and_consume_registration_code(code):
        record_auth_attempt(False)
        raise HTTPException(status_code=400, detail="Ungueltiger oder abgelaufener Einladungscode.")

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
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=_auth_config.refresh_token_max_age,
        path="/api/v1/auth",
    )
    return response


@router.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request) -> dict:
    """Login with email and password."""
    check_auth_rate_limit(request)
    user_db = get_user_db()
    user = user_db.authenticate(req.email, req.password)
    if user is None:
        record_auth_attempt(False)
        logger.warning("Fehlgeschlagener Login-Versuch: %s", req.email)
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort ungueltig.")

    record_auth_attempt(True)
    access_token = create_access_token(user["id"], user["tier"], bool(user["admin"]))
    remember_days = _auth_config.remember_me_max_age // 86400 if req.remember_me else _auth_config.refresh_token_max_age // 86400
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
    # remember_me=False -> session cookie (no max_age, cleared when browser closes)
    # remember_me=True  -> persistent 30-day cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=_auth_config.remember_me_max_age if req.remember_me else None,
        path="/api/v1/auth",
    )
    return response


@router.post("/auth/refresh")
async def auth_refresh(request: Request) -> dict:
    """Refresh the access token using the refresh cookie."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="Kein Refresh-Token.")

    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Ungueltiger Token-Typ.")
    except Exception:
        raise HTTPException(status_code=401, detail="Refresh-Token ungueltig oder abgelaufen.")

    user_db = get_user_db()
    user = user_db.get_by_id(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="Nutzer nicht gefunden.")

    access_token = create_access_token(user["id"], user["tier"], bool(user["admin"]))
    return {"access_token": access_token}


@router.get("/auth/me")
async def auth_me(request: Request) -> dict:
    """Get current user info from JWT."""
    user = get_current_user(request)
    return {
        "id": user["id"],
        "email": user.get("email"),
        "display_name": user.get("display_name", ""),
        "tier": user["tier"],
        "admin": bool(user.get("admin", 0)),
        "telegram_id": user.get("telegram_id"),
        "consent": bool(user.get("consent", 0)),
    }


@router.post("/auth/logout")
async def auth_logout() -> dict:
    """Clear the refresh token cookie."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="refresh_token", path="/api/v1/auth")
    return response


@router.patch("/auth/profile")
async def auth_update_profile(req: UpdateProfileRequest, request: Request) -> dict:
    """Update current user's display name."""
    user = get_current_user(request)
    name = req.display_name.strip()
    if not name or len(name) > _auth_config.max_display_name_length:
        raise HTTPException(
            status_code=400,
            detail=f"Anzeigename muss 1-{_auth_config.max_display_name_length} Zeichen lang sein.",
        )

    user_db = get_user_db()
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


@router.post("/auth/consent")
async def auth_consent(request: Request) -> dict:
    """Set the logging consent flag for the current user."""
    user = get_current_user(request)
    user_db = get_user_db()
    user_db.set_consent(user["id"], True)
    return {"ok": True}


@router.post("/auth/telegram/request-link")
async def auth_telegram_request_link(request: Request) -> dict:
    """Generate a 6-char code for the user to send to the Telegram bot."""
    user = get_current_user(request)
    if user.get("telegram_id"):
        raise HTTPException(status_code=409, detail="Telegram ist bereits verknuepft.")

    user_db = get_user_db()
    code = user_db.create_link_code(user["id"])
    return {"code": code, "expires_in": _auth_config.link_code_expiration}


@router.post("/auth/telegram/verify-link")
async def auth_telegram_verify_link(req: TelegramVerifyRequest) -> dict:
    """Called by the Telegram bot to verify a link code and bind the account."""
    user_db = get_user_db()
    user = user_db.verify_link_code(req.code.strip().upper(), str(req.telegram_id))
    if user is None:
        raise HTTPException(status_code=400, detail="Code ungueltig, abgelaufen oder Telegram-ID bereits verknuepft.")
    return {
        "ok": True,
        "user_id": user["id"],
        "display_name": user.get("display_name", ""),
    }


@router.delete("/auth/telegram/unlink")
async def auth_telegram_unlink(request: Request) -> dict:
    """Remove the Telegram link from the current user's account."""
    user = get_current_user(request)
    if not user.get("telegram_id"):
        raise HTTPException(status_code=400, detail="Kein Telegram-Konto verknuepft.")

    user_db = get_user_db()
    user_db.unlink_telegram(user["id"])
    return {"ok": True}


@router.post("/auth/setup-credentials")
async def auth_setup_credentials(req: SetupCredentialsRequest) -> dict:
    """One-time endpoint to add email+password to a Telegram-only account.

    Requires SETUP_SECRET env var to be set. Only works if the account
    has no email/password yet (prevents credential override attacks).
    """
    expected_secret = os.getenv("SETUP_SECRET", "")
    if not expected_secret:
        raise HTTPException(status_code=503, detail="SETUP_SECRET nicht konfiguriert.")
    if req.setup_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Falsches Setup-Secret.")
    if len(req.password) < _auth_config.min_password_length:
        raise HTTPException(
            status_code=400,
            detail=f"Passwort muss mindestens {_auth_config.min_password_length} Zeichen lang sein.",
        )

    user_db = get_user_db()
    user = user_db.get_by_telegram_id(req.telegram_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"Telegram-Nutzer {req.telegram_id} nicht gefunden.")
    if user.get("email"):
        raise HTTPException(status_code=409, detail="Dieser Account hat bereits E-Mail-Zugangsdaten.")

    ok = user_db.set_credentials(user["id"], req.email, req.password)
    if not ok:
        raise HTTPException(status_code=409, detail="E-Mail bereits von einem anderen Account verwendet.")

    return {"ok": True, "message": f"Credentials fuer Telegram-ID {req.telegram_id} gesetzt."}
