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
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from telegram_formatting import (
    bold,
    code,
    escape_md,
    format_help_message,
    format_result,
    format_start_message,
    format_steps_progress,
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
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        return await self._call("sendMessage", **params)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "MarkdownV2",
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        return await self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", chat_id=chat_id, action=action)


# ── Backend Communication ────────────────────────────────────────


async def poll_job(job_id: str) -> dict[str, Any]:
    """Poll backend until job is done or error."""
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT) as client:
        for _ in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL)
            resp = await client.get(f"{BACKEND_URL}/api/jobs/{job_id}")
            if resp.status_code == 404:
                return {"status": "error", "error": "Job nicht gefunden."}
            data = resp.json()
            if data["status"] in ("done", "error"):
                return data
    return {"status": "error", "error": "Zeitüberschreitung."}


# ── Message Handler ──────────────────────────────────────────────

async def _run_analysis(bot: TelegramBot, chat_id: int, msg_id: int, text: str, tier: str) -> None:
    """Submit text to backend with the given tier and show results."""
    import time as _time

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
            resp = await client.post(f"{BACKEND_URL}/api/analyze", json=body, headers=auth_headers)
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

                resp = await client.get(f"{BACKEND_URL}/api/jobs/{job_id}")
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

                    formatted = format_result(result)

                    if status_msg_id:
                        try:
                            await bot._call("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
                        except Exception:
                            pass

                    if len(formatted) > MSG_CHUNK_SIZE:
                        chunks = _split_message(formatted, MSG_CHUNK_SIZE)
                        for chunk in chunks:
                            await bot.send_message(chat_id, chunk, reply_to_message_id=msg_id)
                            await asyncio.sleep(0.5)
                    else:
                        await bot.send_message(chat_id, formatted, reply_to_message_id=msg_id)
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
                    f"{BACKEND_URL}/api/auth/telegram/verify-link",
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

    # ── /lite, /pro, /max – start analysis with specific tier ──
    for tier_cmd in ("lite", "pro", "max"):
        if text.startswith(f"/{tier_cmd}"):
            # Check tier access: user can only use tiers up to their own level
            tier_levels = {"lite": 0, "pro": 1, "max": 2}
            if tier_levels.get(tier_cmd, 0) > tier_levels.get(user_tier, 0):
                await bot.send_message(
                    chat_id,
                    f"\u274C {bold('Kein Zugriff')}\n\n"
                    f"{escape_md(f'Dein aktueller Plan ({user_tier.upper()}) erlaubt keinen Zugriff auf {tier_cmd.upper()}.')}\n"
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

    # Default: run analysis with user's tier
    await _run_analysis(bot, chat_id, msg_id, text, user_tier)


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
                updates = await bot.get_updates()
                for update in updates:
                    message = update.get("message")
                    if message:
                        # Handle each message in a separate task
                        asyncio.create_task(handle_message(bot, message))
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
