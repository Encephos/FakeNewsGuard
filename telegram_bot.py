"""FakeNewsGuard – Telegram Bot.

Alternative interface to the web UI. Users send text or links to the bot,
the bot processes them through the analysis pipeline and returns
formatted results using Telegram's MarkdownV2 formatting.

Usage:
    TELEGRAM_BOT_TOKEN=... python telegram_bot.py

Environment variables:
    TELEGRAM_BOT_TOKEN  – Bot token from @BotFather (required)
    BACKEND_URL         – Backend API URL (default: http://localhost:8000)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time as _time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from telegram_formatting import (
    bold,
    code,
    escape_md,
    fact_rating_emoji,
    format_claim_detail,
    format_corrections_section,
    format_fairness_section,
    format_help_message,
    format_result_overview,
    format_rhetoric_section,
    format_sources_section,
    format_start_message,
    format_steps_progress,
    format_tier_selection,
)

load_dotenv()

# ── Configuration ────────────────────────────────────────────────

from config.infrastructure import TelegramConfig

_tg_cfg = TelegramConfig()

BOT_TOKEN = _tg_cfg.bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
BACKEND_URL = _tg_cfg.backend_url
POLL_INTERVAL = _tg_cfg.poll_interval
MAX_POLL_ATTEMPTS = _tg_cfg.max_poll_attempts
MSG_CHUNK_SIZE = _tg_cfg.message_chunk_size
HTTP_TIMEOUT = _tg_cfg.http_timeout
POLL_TIMEOUT = _tg_cfg.poll_timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fng-telegram")

# ── User Database (SQLite) ───────────────────────────────────────

from config import AppConfig as _AppConfig
from tools.db.factory import create_user_db as _create_user_db
from tools.user_db import create_access_token

_user_db = None


def _get_db():
    global _user_db
    if _user_db is None:
        _cfg = _AppConfig()
        _user_db = _create_user_db(_cfg)
        if hasattr(_user_db, "migrate_from_json"):
            json_path = str(Path(__file__).parent / "users.json")
            imported = _user_db.migrate_from_json(json_path)
            if imported > 0:
                log.info("Migrated %d users from users.json to SQLite", imported)
    return _user_db


def get_user(user_id: int | str) -> dict[str, Any] | None:
    """Find a user by Telegram ID. Returns None if not registered."""
    return _get_db().get_by_telegram_id(str(user_id))


def is_admin(user_id: int | str) -> bool:
    """Check if a user has admin rights."""
    user = get_user(user_id)
    return user is not None and user.get("admin", 0) == 1


def add_user(user_id: int | str, tier: str = "lite", admin: int = 0) -> bool:
    """Add a new Telegram user. Returns False if the user already exists."""
    db = _get_db()
    if db.get_by_telegram_id(str(user_id)) is not None:
        return False
    result = db.create_user(
        telegram_id=str(user_id),
        tier=tier,
        admin=admin,
        display_name=f"Telegram {user_id}",
    )
    return result is not None


# ── In-Memory Caches for Inline Keyboards ──────────────────────

PENDING_TEXT_TTL = 300       # 5 minutes
RESULT_CACHE_TTL = 1800      # 30 minutes
MAX_CACHE_ENTRIES = 200

_pending_texts: dict[str, dict[str, Any]] = {}
_result_cache: dict[str, dict[str, Any]] = {}


def _cleanup_caches() -> None:
    """Remove expired entries from both caches."""
    now = _time.time()
    for cache in (_pending_texts, _result_cache):
        expired = [k for k, v in cache.items() if now > v.get("expires", 0)]
        for k in expired:
            del cache[k]
        # Hard cap
        if len(cache) > MAX_CACHE_ENTRIES:
            by_age = sorted(cache.items(), key=lambda kv: kv[1].get("expires", 0))
            for k, _ in by_age[: len(cache) - MAX_CACHE_ENTRIES]:
                del cache[k]


# ── Telegram Bot API Client ─────────────────────────────────────

class TelegramBot:
    """Minimal Telegram Bot using the Bot API via httpx."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        self._offset = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, **params: Any) -> dict[str, Any]:
        resp = await self.client.post(f"{self.base_url}/{method}", json=params)
        data = resp.json()
        if not data.get("ok"):
            log.error("Telegram API error: %s", data)
        return data

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_updates(self) -> list[dict[str, Any]]:
        data = await self._call("getUpdates", offset=self._offset, timeout=30)
        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "MarkdownV2",
        reply_to_message_id: int | None = None,
        disable_web_page_preview: bool = True,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return await self._call("sendMessage", **params)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "MarkdownV2",
        disable_web_page_preview: bool = True,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return await self._call("editMessageText", **params)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            params["text"] = text
        if show_alert:
            params["show_alert"] = True
        return await self._call("answerCallbackQuery", **params)

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", chat_id=chat_id, action=action)


# ── Backend Communication ────────────────────────────────────────


async def poll_job(job_id: str) -> dict[str, Any]:
    """Poll backend until job is done or error."""
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT) as client:
        for _ in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL)
            resp = await client.get(f"{BACKEND_URL}/api/v1/jobs/{job_id}")
            if resp.status_code == 404:
                return {"status": "error", "error": "Job nicht gefunden."}
            data = resp.json()
            if data["status"] in ("done", "error"):
                return data
    return {"status": "error", "error": "Zeitüberschreitung."}


# ── Message Handler ──────────────────────────────────────────────

TIER_LEVELS = {"lite": 0, "pro": 1, "max": 2}


def _build_tier_keyboard(user_tier: str, msg_hash: str) -> dict[str, Any]:
    """Build an inline keyboard with available tier buttons."""
    user_level = TIER_LEVELS.get(user_tier, 0)
    row1 = []
    for tier in ("lite", "pro", "max"):
        if TIER_LEVELS[tier] <= user_level:
            row1.append({"text": tier.upper(), "callback_data": f"t:{tier}:{msg_hash}"})
    rows = [row1]
    # Commander tiers
    cmd_row = []
    if user_level >= TIER_LEVELS["pro"]:
        cmd_row.append({"text": "\U0001F9ED CMD PRO", "callback_data": f"t:commander-pro:{msg_hash}"})
    if user_level >= TIER_LEVELS["max"]:
        cmd_row.append({"text": "\U0001F9ED CMD MAX", "callback_data": f"t:commander-max:{msg_hash}"})
    if cmd_row:
        rows.append(cmd_row)
    return {"inline_keyboard": rows}


def _build_result_keyboard(result: dict[str, Any], job_short: str) -> dict[str, Any]:
    """Build an inline keyboard for navigating analysis results."""
    rows: list[list[dict[str, str]]] = []

    # One button per claim
    claims = result.get("claims", [])
    for i, claim in enumerate(claims):
        text = claim.get("text", "")
        if len(text) > 25:
            text = text[:22] + "..."
        emoji = fact_rating_emoji(claim.get("rating", ""))
        rows.append([{"text": f"{emoji} #{i + 1} {text}", "callback_data": f"c:{job_short}:{i}"}])

    # Section buttons (only for non-empty sections)
    section_row: list[dict[str, str]] = []
    if result.get("rhetoric"):
        section_row.append({"text": "\U0001F3AD Rhetorik", "callback_data": f"s:{job_short}:rhet"})
    if result.get("sources"):
        section_row.append({"text": "\U0001F517 Quellen", "callback_data": f"s:{job_short}:src"})
    if section_row:
        rows.append(section_row)

    section_row2: list[dict[str, str]] = []
    if result.get("corrections"):
        section_row2.append({"text": "\u270F\uFE0F Korrekturen", "callback_data": f"s:{job_short}:corr"})
    if result.get("fairness"):
        section_row2.append({"text": "\u2705 Korrekt", "callback_data": f"s:{job_short}:fair"})
    if section_row2:
        rows.append(section_row2)

    # New analysis button
    rows.append([{"text": "\U0001F504 Neue Analyse", "callback_data": "new"}])

    return {"inline_keyboard": rows}


async def _run_analysis(bot: TelegramBot, chat_id: int, msg_id: int, text: str, tier: str) -> None:
    """Submit text to backend with the given tier and show results."""
    await bot.send_chat_action(chat_id)

    # Show initial progress (empty checklist)
    initial_progress = format_steps_progress([], tier=tier)
    status_resp = await bot.send_message(
        chat_id,
        initial_progress,
        reply_to_message_id=msg_id,
    )
    status_msg_id = status_resp.get("result", {}).get("message_id")

    try:
        # Detect if text is a URL
        url = ""
        url_match = re.search(r"https?://[^\s]+", text)
        if url_match:
            url = url_match.group(0)
            remaining = text.replace(url, "").strip()
            if len(remaining) < 20:
                text = ""

        # Submit to backend with tier + auth
        body: dict[str, str] = {"text": text, "tier": tier}
        if url:
            body["url"] = url

        # Mint a JWT for the Telegram user so the backend accepts the request
        tg_user = get_user(chat_id)
        if tg_user is None:
            raise ValueError("Nutzer nicht registriert.")
        auth_token = create_access_token(tg_user["id"], tg_user["tier"], bool(tg_user.get("admin", 0)))
        auth_headers = {"Authorization": f"Bearer {auth_token}"}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(f"{BACKEND_URL}/api/v1/analyze", json=body, headers=auth_headers)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

        # Poll for result with time-based throttle
        last_step_count = 0
        last_update_time = 0.0
        MIN_UPDATE_INTERVAL = 3.0

        async with httpx.AsyncClient(timeout=POLL_TIMEOUT) as client:
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)

                if attempt % 5 == 0:
                    await bot.send_chat_action(chat_id)

                resp = await client.get(f"{BACKEND_URL}/api/v1/jobs/{job_id}")
                if resp.status_code == 404:
                    raise ValueError("Job nicht gefunden.")

                data = resp.json()

                # Update progress display (time-throttled)
                steps = data.get("steps", [])
                now = _time.time()
                if status_msg_id and len(steps) != last_step_count and (now - last_update_time) >= MIN_UPDATE_INTERVAL:
                    last_step_count = len(steps)
                    last_update_time = now
                    progress = format_steps_progress(steps, tier=tier)
                    try:
                        await bot.edit_message(chat_id, status_msg_id, progress)
                    except Exception:
                        pass

                if data["status"] == "done":
                    result = data.get("result")
                    if not result:
                        raise ValueError("Kein Ergebnis erhalten.")

                    # Delete progress message
                    if status_msg_id:
                        try:
                            await bot._call("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
                        except Exception:
                            pass

                    # Build overview + inline keyboard
                    job_short = job_id[:8]
                    overview = format_result_overview(result)
                    keyboard = _build_result_keyboard(result, job_short)

                    # Cache result for callback navigation
                    _result_cache[job_short] = {
                        "result": result,
                        "chat_id": chat_id,
                        "overview_msg": overview,
                        "keyboard": keyboard,
                        "user_id": tg_user["id"],
                        "expires": _time.time() + RESULT_CACHE_TTL,
                    }

                    # Send overview with keyboard (fall back to chunked if too long)
                    if len(overview) > MSG_CHUNK_SIZE:
                        chunks = _split_message(overview, MSG_CHUNK_SIZE)
                        for j, chunk in enumerate(chunks):
                            # Attach keyboard only to the last chunk
                            kb = keyboard if j == len(chunks) - 1 else None
                            await bot.send_message(chat_id, chunk, reply_to_message_id=msg_id, reply_markup=kb)
                            await asyncio.sleep(0.5)
                    else:
                        await bot.send_message(chat_id, overview, reply_to_message_id=msg_id, reply_markup=keyboard)
                    return

                if data["status"] == "error":
                    raise ValueError(data.get("error", "Analyse fehlgeschlagen."))

        raise ValueError("Zeitüberschreitung: Analyse dauert zu lange.")

    except Exception as e:
        error_text = f"\u274C {bold('Fehler')}\n\n{escape_md(str(e))}\n\n_{escape_md('Versuche es erneut oder sende /help')}_"
        if status_msg_id:
            try:
                await bot.edit_message(chat_id, status_msg_id, error_text)
            except Exception:
                await bot.send_message(chat_id, error_text)
        else:
            await bot.send_message(chat_id, error_text)


async def handle_message(bot: TelegramBot, message: dict[str, Any]) -> None:
    """Process an incoming Telegram message."""
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)
    msg_id = message.get("message_id")
    text = message.get("text", "").strip()

    if not text:
        return

    # ── /adduser (admin only) ──
    if text.startswith("/adduser"):
        if not is_admin(user_id):
            await bot.send_message(chat_id, f"\u274C {escape_md('Keine Berechtigung.')}")
            return
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(
                chat_id,
                f"\u2139\uFE0F {bold('/adduser')}\n\n"
                f"{escape_md('Verwendung:')} {code('/adduser <Telegram-ID>')}\n"
                f"_{escape_md('Fügt einen neuen Nutzer mit Tier LITE hinzu.')}_"
            )
            return
        new_uid = parts[1].strip()
        if add_user(new_uid):
            await bot.send_message(chat_id, f"\u2705 {escape_md(f'Nutzer {new_uid} hinzugefügt (Tier: lite).')}")
        else:
            await bot.send_message(chat_id, f"\u2139\uFE0F {escape_md(f'Nutzer {new_uid} existiert bereits.')}")
        return

    # ── /link <code> – link Telegram account to web account ──
    if text.startswith("/link"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(
                chat_id,
                f"\u2139\uFE0F {escape_md('Verwendung: /link <CODE>')}\n\n"
                f"_{escape_md('Den Code findest du in deinem Profil auf der Webseite.')}_"
            )
            return
        link_code = parts[1].strip().upper()
        # Check if this telegram_id is already linked
        existing = get_user(user_id)
        if existing is not None:
            await bot.send_message(
                chat_id,
                f"\u2139\uFE0F {escape_md('Dein Telegram-Konto ist bereits mit einem Account verknüpft.')}"
            )
            return
        # Call backend to verify code
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/api/v1/auth/telegram/verify-link",
                    json={"code": link_code, "telegram_id": str(user_id)},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    display_name = data.get("display_name", "")
                    await bot.send_message(
                        chat_id,
                        f"\u2705 {bold('Verknüpfung erfolgreich!')}\n\n"
                        f"{escape_md(f'Dein Telegram-Konto ist jetzt mit dem Account')}"
                        f" {bold(display_name or 'deinem Account')} {escape_md('verbunden.')}\n\n"
                        f"_{escape_md('Du kannst jetzt Analysen direkt hier starten.')}_"
                    )
                else:
                    detail = resp.json().get("detail", "Unbekannter Fehler.")
                    await bot.send_message(
                        chat_id,
                        f"\u274C {escape_md(detail)}"
                    )
        except Exception as e:
            log.error("Link verification failed: %s", e)
            await bot.send_message(
                chat_id,
                f"\u274C {escape_md('Verbindung zum Server fehlgeschlagen. Bitte versuche es erneut.')}"
            )
        return

    # ── /zustimmen – consent to logging ──
    if text == "/zustimmen":
        user = get_user(user_id)
        if user is None:
            await bot.send_message(
                chat_id,
                f"\U0001F6AB {escape_md('Bitte registriere dich zuerst. Sende /start für Infos.')}"
            )
            return
        db = _get_db()
        db.set_consent(user["id"], True)
        await bot.send_message(
            chat_id,
            f"\u2705 {bold('Zustimmung erteilt')}\n\n"
            f"{escape_md('Vielen Dank! Du kannst FakeNewsGuard jetzt nutzen.')}\n"
            f"{escape_md('Sende einfach einen Text oder Link zur Analyse.')}"
        )
        return

    # ── Access check: user must be registered ──
    user = get_user(user_id)
    if user is None:
        await bot.send_message(
            chat_id,
            f"\U0001F6AB {bold('Nicht registriert')}\n\n"
            f"{escape_md('Um FakeNewsGuard zu nutzen, verknüpfe dein Telegram-Konto:')}\n\n"
            f"  *1\\.* {escape_md('Erstelle ein Konto auf der Webseite')}\n"
            f"  *2\\.* {escape_md('Kopiere den Verknüpfungscode aus deinem Profil')}\n"
            f"  *3\\.* {escape_md('Sende hier:')} {code('/link <CODE>')}\n"
        )
        return

    user_tier = user.get("tier", "lite")

    # ── Consent check ──
    if not user.get("consent", 0) and text not in ("/start", "/help", "/zustimmen"):
        await bot.send_message(
            chat_id,
            f"\u2139\uFE0F {bold('Zustimmung erforderlich')}\n\n"
            f"{escape_md('Um FakeNewsGuard zu nutzen, musst du der Datenverarbeitung zustimmen.')}\n"
            f"{escape_md('Alle Anfragen werden protokolliert, um das Modell und die Architektur zu verbessern.')}\n\n"
            f"{escape_md('Sende')} {code('/zustimmen')}{escape_md(', um zuzustimmen und loszulegen.')}"
        )
        return

    # ── /lite, /pro, /max, /commander-pro, /commander-max – start analysis with specific tier ──
    # Commander-Befehle zuerst prüfen (längerer Prefix)
    for tier_cmd in ("commander-pro", "commander-max", "lite", "pro", "max"):
        if text.startswith(f"/{tier_cmd}"):
            # Check tier access: user can only use tiers up to their own level
            # Commander-Tiers erfordern mindestens den Basis-Tier
            tier_levels = {"lite": 0, "pro": 1, "max": 2}
            base_tier = tier_cmd.replace("commander-", "") if tier_cmd.startswith("commander-") else tier_cmd
            if tier_levels.get(base_tier, 0) > tier_levels.get(user_tier, 0):
                label = tier_cmd.upper().replace("-", " ")
                await bot.send_message(
                    chat_id,
                    f"\u274C {bold('Kein Zugriff')}\n\n"
                    f"{escape_md(f'Dein aktueller Plan ({user_tier.upper()}) erlaubt keinen Zugriff auf {label}.')}\n"
                    f"_{escape_md('Upgrade deinen Plan auf der Webseite.')}_"
                )
                return
            analysis_text = text[len(f"/{tier_cmd}"):].strip()
            if not analysis_text:
                await bot.send_message(
                    chat_id,
                    f"\u2139\uFE0F {bold(f'/{tier_cmd}')}\n\n"
                    f"{escape_md(f'Sende einen Text oder Link nach dem Befehl:')}\n"
                    f"  {code(f'/{tier_cmd}')} {escape_md('Dein Text oder Link hier')}"
                )
                return
            await _run_analysis(bot, chat_id, msg_id, analysis_text, tier_cmd)
            return

    # Handle /start command
    if text == "/start":
        await bot.send_message(chat_id, format_start_message())
        return

    # Handle /help command
    if text == "/help":
        await bot.send_message(chat_id, format_help_message())
        return

    # Default: show tier selection keyboard
    msg_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    _pending_texts[msg_hash] = {
        "chat_id": chat_id,
        "msg_id": msg_id,
        "text": text,
        "user_id": user_id,
        "expires": _time.time() + PENDING_TEXT_TTL,
    }
    keyboard = _build_tier_keyboard(user_tier, msg_hash)
    await bot.send_message(
        chat_id,
        format_tier_selection(),
        reply_to_message_id=msg_id,
        reply_markup=keyboard,
    )


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message at line breaks."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


# ── Callback Query Handlers ─────────────────────────────────────


async def handle_callback(bot: TelegramBot, cq: dict[str, Any]) -> None:
    """Route an incoming callback query by data prefix."""
    cq_id = cq.get("id", "")
    data = cq.get("data", "")
    user_id = cq.get("from", {}).get("id", 0)
    message = cq.get("message", {})
    chat_id = message.get("chat", {}).get("id", 0)
    message_id = message.get("message_id", 0)

    try:
        if data.startswith("t:"):
            await _handle_tier_callback(bot, cq_id, data, user_id, chat_id, message_id)
        elif data.startswith("c:"):
            await _handle_claim_callback(bot, cq_id, data, user_id, chat_id, message_id)
        elif data.startswith("s:"):
            await _handle_section_callback(bot, cq_id, data, user_id, chat_id, message_id)
        elif data.startswith("b:"):
            await _handle_back_callback(bot, cq_id, data, user_id, chat_id, message_id)
        elif data == "new":
            await bot.answer_callback_query(cq_id, text="Sende einen neuen Text oder Link.")
        else:
            await bot.answer_callback_query(cq_id)
    except Exception as e:
        log.error("Callback error: %s", e)
        try:
            await bot.answer_callback_query(cq_id, text="Fehler aufgetreten.", show_alert=True)
        except Exception:
            pass


async def _handle_tier_callback(
    bot: TelegramBot, cq_id: str, data: str, user_id: int, chat_id: int, message_id: int
) -> None:
    """Handle tier selection button press."""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await bot.answer_callback_query(cq_id)
        return

    tier = parts[1]
    msg_hash = parts[2]

    pending = _pending_texts.get(msg_hash)
    if pending is None or _time.time() > pending.get("expires", 0):
        _pending_texts.pop(msg_hash, None)
        await bot.answer_callback_query(cq_id, text="Auswahl abgelaufen. Sende den Text erneut.", show_alert=True)
        await bot.edit_message(
            chat_id, message_id,
            f"\u23F3 {escape_md('Auswahl abgelaufen. Sende den Text erneut.')}",
            reply_markup={"inline_keyboard": []},
        )
        return

    # Verify the user clicking matches the original requester
    if pending["user_id"] != user_id:
        await bot.answer_callback_query(cq_id, text="Du kannst nur deine eigene Analyse steuern.", show_alert=True)
        return

    # Check tier access
    base_tier = tier.replace("commander-", "") if tier.startswith("commander-") else tier
    user = get_user(user_id)
    user_tier = user.get("tier", "lite") if user else "lite"
    if TIER_LEVELS.get(base_tier, 0) > TIER_LEVELS.get(user_tier, 0):
        await bot.answer_callback_query(cq_id, text="Kein Zugriff auf diesen Tier.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    # Update the tier selection message
    tier_label = tier.upper().replace("-", " ")
    await bot.edit_message(
        chat_id, message_id,
        f"\U0001F9E0 {escape_md(f'Analyse mit {tier_label} gestartet...')}",
        reply_markup={"inline_keyboard": []},
    )

    # Start analysis
    original_text = pending["text"]
    original_msg_id = pending["msg_id"]
    _pending_texts.pop(msg_hash, None)
    await _run_analysis(bot, chat_id, original_msg_id, original_text, tier)


async def _handle_claim_callback(
    bot: TelegramBot, cq_id: str, data: str, user_id: int, chat_id: int, message_id: int
) -> None:
    """Handle claim detail button press."""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await bot.answer_callback_query(cq_id)
        return

    job_short = parts[1]
    try:
        idx = int(parts[2])
    except ValueError:
        await bot.answer_callback_query(cq_id)
        return

    cached = _result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        _result_cache.pop(job_short, None)
        await bot.answer_callback_query(cq_id, text="Ergebnis nicht mehr verfügbar. Starte eine neue Analyse.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    claims = cached["result"].get("claims", [])
    if idx < 0 or idx >= len(claims):
        return

    detail = format_claim_detail(claims[idx], idx)
    back_keyboard = {"inline_keyboard": [[{"text": "\u2190 Zurück zur Übersicht", "callback_data": f"b:{job_short}"}]]}

    try:
        await bot.edit_message(chat_id, message_id, detail, reply_markup=back_keyboard)
    except Exception:
        # Message too long for edit – send as new message
        await bot.send_message(chat_id, detail, reply_markup=back_keyboard)


async def _handle_section_callback(
    bot: TelegramBot, cq_id: str, data: str, user_id: int, chat_id: int, message_id: int
) -> None:
    """Handle section button press (rhetoric, sources, corrections, fairness)."""
    parts = data.split(":", 2)
    if len(parts) != 3:
        await bot.answer_callback_query(cq_id)
        return

    job_short = parts[1]
    section = parts[2]

    cached = _result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        _result_cache.pop(job_short, None)
        await bot.answer_callback_query(cq_id, text="Ergebnis nicht mehr verfügbar. Starte eine neue Analyse.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    result = cached["result"]
    section_formatters = {
        "rhet": lambda: format_rhetoric_section(result.get("rhetoric", [])),
        "src": lambda: format_sources_section(result.get("sources", [])),
        "corr": lambda: format_corrections_section(result.get("corrections", [])),
        "fair": lambda: format_fairness_section(result.get("fairness", [])),
    }

    formatter = section_formatters.get(section)
    if formatter is None:
        return

    content = formatter()
    if not content.strip():
        return

    back_keyboard = {"inline_keyboard": [[{"text": "\u2190 Zurück zur Übersicht", "callback_data": f"b:{job_short}"}]]}

    try:
        await bot.edit_message(chat_id, message_id, content, reply_markup=back_keyboard)
    except Exception:
        await bot.send_message(chat_id, content, reply_markup=back_keyboard)


async def _handle_back_callback(
    bot: TelegramBot, cq_id: str, data: str, user_id: int, chat_id: int, message_id: int
) -> None:
    """Handle back-to-overview button press."""
    parts = data.split(":", 1)
    if len(parts) != 2:
        await bot.answer_callback_query(cq_id)
        return

    job_short = parts[1]

    cached = _result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        _result_cache.pop(job_short, None)
        await bot.answer_callback_query(cq_id, text="Ergebnis nicht mehr verfügbar. Starte eine neue Analyse.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    try:
        await bot.edit_message(
            chat_id, message_id,
            cached["overview_msg"],
            reply_markup=cached["keyboard"],
        )
    except Exception:
        pass


# ── Main Loop ────────────────────────────────────────────────────

async def main() -> None:
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN nicht gesetzt. Bitte in .env konfigurieren.", file=sys.stderr)
        sys.exit(1)

    bot = TelegramBot(BOT_TOKEN)

    try:
        me = await bot.get_me()
        bot_name = me.get("result", {}).get("username", "?")
        log.info("Bot gestartet: @%s", bot_name)
        log.info("Backend: %s", BACKEND_URL)

        while True:
            try:
                _cleanup_caches()
                updates = await bot.get_updates()
                for update in updates:
                    message = update.get("message")
                    if message:
                        asyncio.create_task(handle_message(bot, message))
                    callback_query = update.get("callback_query")
                    if callback_query:
                        asyncio.create_task(handle_callback(bot, callback_query))
            except httpx.ConnectError:
                log.warning("Telegram API nicht erreichbar, versuche erneut in 5s…")
                await asyncio.sleep(5)
            except Exception as e:
                log.error("Fehler im Update-Loop: %s", e)
                await asyncio.sleep(2)

    except KeyboardInterrupt:
        log.info("Bot wird beendet…")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
