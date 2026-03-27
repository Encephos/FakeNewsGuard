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

load_dotenv()

# ── Configuration ────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL = 2.0  # seconds between polling backend
MAX_POLL_ATTEMPTS = 960  # 32 min timeout (backend hard cap at 30 min)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fng-telegram")

# ── User Database (SQLite) ───────────────────────────────────────

from config import AppConfig as _AppConfig
from tools.db.factory import create_user_db as _create_user_db

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


# ── Telegram MarkdownV2 Helpers ──────────────────────────────────

def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!\\"
    result = []
    for ch in text:
        if ch in special:
            result.append("\\")
        result.append(ch)
    return "".join(result)


def bold(text: str) -> str:
    return f"*{escape_md(text)}*"


def italic(text: str) -> str:
    return f"_{escape_md(text)}_"


def code(text: str) -> str:
    return f"`{text}`"


def link(text: str, url: str) -> str:
    return f"[{escape_md(text)}]({url})"


def rating_emoji(rating: str) -> str:
    """Map overall rating to emoji."""
    emojis = {
        "Wahr": "\u2705",                    # ✅
        "Größtenteils wahr": "\U0001F7E2",   # 🟢
        "Irreführend": "\U0001F7E1",         # 🟡
        "Größtenteils falsch": "\U0001F7E0", # 🟠
        "Falsch": "\U0001F534",              # 🔴
    }
    return emojis.get(rating, "\u2753")       # ❓


def fact_rating_emoji(rating: str) -> str:
    """Map fact rating to emoji."""
    emojis = {
        "TRUE": "\u2705",
        "MOSTLY_TRUE": "\U0001F7E2",
        "MISLEADING": "\U0001F7E1",
        "MOSTLY_FALSE": "\U0001F7E0",
        "FALSE": "\U0001F534",
        "UNVERIFIABLE": "\u2753",
    }
    return emojis.get(rating, "\u2022")


def fact_rating_label(rating: str) -> str:
    labels = {
        "TRUE": "Wahr",
        "MOSTLY_TRUE": "Größtenteils wahr",
        "MISLEADING": "Irreführend",
        "MOSTLY_FALSE": "Größtenteils falsch",
        "FALSE": "Falsch",
        "UNVERIFIABLE": "Nicht verifizierbar",
    }
    return labels.get(rating, rating)


def severity_emoji(severity: str) -> str:
    return {"LOW": "\U0001F7E2", "MEDIUM": "\U0001F7E1", "HIGH": "\U0001F534"}.get(severity, "\u2022")


def severity_label(severity: str) -> str:
    return {"LOW": "Niedrig", "MEDIUM": "Mittel", "HIGH": "Hoch"}.get(severity, severity)


def confidence_bar(confidence: int) -> str:
    """Create a visual confidence meter: ████████░░ 80%"""
    filled = round(confidence / 10)
    empty = 10 - filled
    bar = "\u2588" * filled + "\u2591" * empty
    return f"`{bar}` {escape_md(str(confidence))}%"


def divider() -> str:
    return escape_md("─" * 28)


# ── Format Analysis Result ───────────────────────────────────────

def format_result(result: dict[str, Any]) -> str:
    """Format the analysis result as Telegram MarkdownV2 message."""
    parts: list[str] = []

    rating = result.get("overall_rating", "?")
    confidence = result.get("confidence", 0)
    emoji = rating_emoji(rating)

    # ── Hero Header ──
    parts.append(f"{emoji}  {bold(rating.upper())}")
    parts.append(confidence_bar(confidence))
    parts.append("")

    # ── Summary ──
    summary = result.get("summary", "")
    if summary:
        parts.append(f"\U0001F4DD {bold('Zusammenfassung')}")
        parts.append(escape_md(summary))
        parts.append("")

    # ── Claims ──
    claims = result.get("claims", [])
    if claims:
        parts.append(divider())
        parts.append(f"\U0001F50D {bold('Behauptungen')}  _{escape_md(f'({len(claims)})')}_")
        parts.append("")

        for i, claim in enumerate(claims, 1):
            r = claim.get("rating", "")
            re_ = fact_rating_emoji(r)
            label = fact_rating_label(r)

            # Claim header line
            parts.append(f"{re_} *{escape_md(f'#{i}')}*  {escape_md(claim.get('text', ''))}")
            parts.append(f"    \u2192 {italic(label)}")

            evidence = claim.get("evidence", "")
            if evidence:
                parts.append(f"    {escape_md(evidence)}")

            correction = claim.get("correction", "")
            if correction:
                parts.append(f"    \u26A0\uFE0F {italic('Korrektur:')} {escape_md(correction)}")

            missing = claim.get("missing_context", "")
            if missing:
                parts.append(f"    \U0001F4AC {italic('Kontext:')} {escape_md(missing)}")

            # Number audit
            na = claim.get("number_audit")
            if na and na.get("manipulation", "NONE") != "NONE":
                parts.append(f"    \U0001F4CA {italic('Zahlenmanipulation:')} {escape_md(na.get('manipulation', ''))}")
                if na.get("correct_value"):
                    parts.append(f"    \u2192 {escape_md(na['correct_value'])}")

            # Add spacing between claims
            if i < len(claims):
                parts.append("")

    # ── Rhetoric / Manipulation Techniques ──
    rhetoric = result.get("rhetoric", [])
    if rhetoric:
        parts.append("")
        parts.append(divider())
        parts.append(f"\U0001F3AD {bold('Manipulationstechniken')}  _{escape_md(f'({len(rhetoric)})')}_")
        parts.append("")
        for tech in rhetoric:
            sev = tech.get("severity", "")
            sev_e = severity_emoji(sev)
            sev_l = severity_label(sev)
            parts.append(f"{sev_e} {bold(tech.get('name', ''))}  \u00B7  {italic(sev_l)}")
            if tech.get("description"):
                parts.append(f"    {escape_md(tech['description'])}")
            if tech.get("example"):
                parts.append(f"    \u00AB{escape_md(tech['example'])}\u00BB")
            parts.append("")

    # ── Corrections ──
    corrections = result.get("corrections", [])
    if corrections:
        parts.append(divider())
        parts.append(f"\u270F\uFE0F {bold('Korrekturen')}")
        parts.append("")
        for i, corr in enumerate(corrections, 1):
            parts.append(f"  {escape_md(str(i))}\\. {escape_md(corr)}")
        parts.append("")

    # ── Fairness ──
    fairness = result.get("fairness", [])
    if fairness:
        parts.append(divider())
        parts.append(f"\u2705 {bold('Was korrekt war')}")
        parts.append("")
        for note in fairness:
            parts.append(f"    \u2022 {escape_md(note)}")
        parts.append("")

    # ── Sources ──
    sources = result.get("sources", [])
    if sources:
        parts.append(divider())
        parts.append(f"\U0001F517 {bold('Quellen')}")
        parts.append("")
        for src in sources[:8]:
            if src.startswith("http"):
                domain = re.sub(r"^https?://(?:www\.)?", "", src).split("/")[0]
                parts.append(f"    \u2023 {link(domain, src)}")
            else:
                parts.append(f"    \u2023 {escape_md(src)}")

    # Footer
    parts.append("")
    parts.append(escape_md("_____"))
    parts.append(f"_{escape_md('FakeNewsGuard \u00B7 KI-Faktencheck')}_")

    return "\n".join(parts)


_MAIN_PHASES = ["Phase 1", "Phase 2", "Phase 3", "Phase 4"]


def format_steps_progress(steps: list[dict[str, Any]]) -> str:
    """Format current progress steps as a compact status message."""
    if not steps:
        return f"\U0001F9E0 {escape_md('Analyse wird vorbereitet...')}"

    # Phase-based counting: count how many main phases are fully done
    phases_done = sum(
        1 for phase_id in _MAIN_PHASES
        if any(s.get("phase") == phase_id for s in steps)
        and all(
            s.get("status") != "running"
            for s in steps
            if s.get("phase") == phase_id
        )
    )
    total_phases = len(_MAIN_PHASES)

    last = steps[-1]
    agent = last.get("agent", "")
    msg = last.get("message", "")
    status = last.get("status", "")

    # Progress dots (one dot per main phase)
    dots = "\u25C9" * phases_done + "\u25CB" * (total_phases - phases_done)

    icon = "\U0001F9E0" if status == "running" else "\u2705"
    header = f"{icon} {bold('Analyse')}  {escape_md(dots)}  {escape_md(f'{phases_done}/{total_phases}')}"
    detail = escape_md(msg[:120]) if msg else ""

    lines = [header]
    if agent:
        lines.append(f"    \u2192 {italic(agent)}")
    if detail:
        lines.append(f"    {detail}")
    return "\n".join(lines)


# ── Telegram Bot API Client ─────────────────────────────────────

class TelegramBot:
    """Minimal Telegram Bot using the Bot API via httpx."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=30.0)
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
    async with httpx.AsyncClient(timeout=15.0) as client:
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
    await bot.send_chat_action(chat_id)

    status_resp = await bot.send_message(
        chat_id,
        f"\U0001F9E0 {escape_md('Analyse wird gestartet...')}",
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

        # Submit to backend with tier
        body: dict[str, str] = {"text": text, "tier": tier}
        if url:
            body["url"] = url

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{BACKEND_URL}/api/analyze", json=body)
            resp.raise_for_status()
            job_id = resp.json()["job_id"]

        # Update status
        if status_msg_id:
            try:
                await bot.edit_message(
                    chat_id, status_msg_id,
                    f"\U0001F9E0 {bold('Analyse')} \\({escape_md(tier.upper())}\\)  {escape_md('\u25CB\u25CB\u25CB\u25CB')}\n    {escape_md('Dies kann 1\u20133 Minuten dauern...')}"
                )
            except Exception:
                pass

        # Poll for result
        last_step_count = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)

                if attempt % 5 == 0:
                    await bot.send_chat_action(chat_id)

                resp = await client.get(f"{BACKEND_URL}/api/jobs/{job_id}")
                if resp.status_code == 404:
                    raise ValueError("Job nicht gefunden.")

                data = resp.json()

                steps = data.get("steps", [])
                if status_msg_id and len(steps) > last_step_count and attempt % 3 == 0:
                    last_step_count = len(steps)
                    progress = format_steps_progress(steps)
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

                    if len(formatted) > 4000:
                        chunks = _split_message(formatted, 4000)
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
        welcome = (
            f"\U0001F9E0 {bold('FakeNewsGuard')}\n"
            f"_{escape_md('KI-gestützter Faktencheck')}_\n"
            f"\n"
            f"{escape_md('Sende mir einen Text, eine Behauptung oder einen Link')} \u2013 "
            f"{escape_md('ich prüfe den Inhalt automatisch auf:')}\n"
            f"\n"
            f"  \U0001F50D {escape_md('Faktentreue der Behauptungen')}\n"
            f"  \U0001F4CA {escape_md('Zahlen- & Statistikmanipulation')}\n"
            f"  \U0001F3AD {escape_md('Rhetorische Manipulationstechniken')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Unterstützte Plattformen')}\n"
            f"  \U0001D54F {escape_md('Twitter/X')}  \u2022  "
            f"\U0001F9F5 {escape_md('Threads')}  \u2022  "
            f"\U0001F4F7 {escape_md('Instagram')}\n"
            f"  \U0001F4D8 {escape_md('Facebook')}  \u2022  "
            f"\u25B6\uFE0F {escape_md('YouTube')}  \u2022  "
            f"\U0001F4F0 {escape_md('Nachrichtenartikel')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Analyse-Stufen')}\n"
            f"  {code('/lite')}  \u2013  {escape_md('Schnellcheck (kostenlose Modelle)')}\n"
            f"  {code('/pro')}   \u2013  {escape_md('Standardanalyse')}\n"
            f"  {code('/max')}   \u2013  {escape_md('Tiefenanalyse (beste Qualität)')}\n"
            f"  {escape_md('Ohne Angabe wird dein Standard-Tier verwendet.')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Befehle')}\n"
            f"  {code('/help')}   \u2013  {escape_md('Hilfe & Beispiele')}\n"
            f"  {code('/link')}   \u2013  {escape_md('Telegram mit Webkonto verknüpfen')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"\u2139\uFE0F {bold('Hinweis zur Datenverarbeitung')}\n"
            f"{escape_md('Alle Anfragen werden protokolliert, um das Modell und die Architektur zu verbessern.')}\n"
            f"{escape_md('Mit der Nutzung stimmst du dem zu. Sende')} {code('/zustimmen')}{escape_md(', um zu starten.')}\n"
            f"\n"
            f"_{escape_md('Einfach Text oder Link senden')} \u2192 {escape_md('los gehts!')}_"
        )
        await bot.send_message(chat_id, welcome)
        return

    # Handle /help command
    if text == "/help":
        help_text = (
            f"\U0001F4D6 {bold('Hilfe')}\n"
            f"\n"
            f"{bold('So funktioniert es')}\n"
            f"  *1\\.* {escape_md('Sende einen Text, eine Behauptung oder einen Link')}\n"
            f"  *2\\.* {escape_md('Die KI extrahiert alle Behauptungen automatisch')}\n"
            f"  *3\\.* {escape_md('Jede Behauptung wird mit aktuellen Quellen geprüft')}\n"
            f"  *4\\.* {escape_md('Du erhältst eine Bewertung mit Belegen & Quellen')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Beispiele')}\n"
            f"\n"
            f"\U0001F4AC {italic('Text senden:')}\n"
            f"  {escape_md('\"Deutschland hat die höchste Inflationsrate in Europa\"')}\n"
            f"\n"
            f"\U0001F517 {italic('Link senden:')}\n"
            f"  `https://x\\.com/user/status/123`\n"
            f"  `https://www\\.spiegel\\.de/artikel/\\.\\.\\.\\.`\n"
            f"\n"
            f"\U0001F3AF {italic('Tier wählen:')}\n"
            f"  {code('/max')} {escape_md('https://x.com/user/status/123')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Befehle')}\n"
            f"  {code('/start')}      \u2013  {escape_md('Willkommensnachricht')}\n"
            f"  {code('/help')}       \u2013  {escape_md('Diese Hilfe anzeigen')}\n"
            f"  {code('/link <CODE>')} \u2013  {escape_md('Telegram mit Webkonto verknüpfen')}\n"
            f"  {code('/lite <Text>')} \u2013  {escape_md('Schnellcheck (kostenlose Modelle)')}\n"
            f"  {code('/pro <Text>')}  \u2013  {escape_md('Standardanalyse')}\n"
            f"  {code('/max <Text>')}  \u2013  {escape_md('Tiefenanalyse (beste Qualität)')}\n"
            f"\n"
            f"{divider()}\n"
            f"\n"
            f"{bold('Analyse-Stufen')}\n"
            f"  {bold('LITE')} \u2013 {escape_md('Schneller Basischeck mit kostenlosen Modellen. Gut für einfache Behauptungen.')}\n"
            f"  {bold('PRO')}  \u2013 {escape_md('Ausgewogene Analyse mit stärkeren Modellen. Empfohlen für die meisten Fälle.')}\n"
            f"  {bold('MAX')}  \u2013 {escape_md('Umfassende Tiefenanalyse mit den besten verfügbaren Modellen.')}\n"
            f"  {escape_md('Ohne Tier-Angabe wird automatisch dein Standard-Tier verwendet.')}\n"
            f"\n"
            f"{divider()}\n"
            f"_{escape_md('Die Analyse dauert je nach Stufe ca. 1\u20133 Minuten.')}_"
        )
        await bot.send_message(chat_id, help_text)
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
