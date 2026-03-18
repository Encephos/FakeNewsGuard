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
import logging
import os
import re
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL = 2.0  # seconds between polling backend
MAX_POLL_ATTEMPTS = 360  # 12 min timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fng-telegram")


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


# ── Format Analysis Result ───────────────────────────────────────

def format_result(result: dict[str, Any]) -> str:
    """Format the analysis result as Telegram MarkdownV2 message."""
    parts: list[str] = []

    # Header with verdict
    rating = result.get("overall_rating", "?")
    confidence = result.get("confidence", 0)
    emoji = rating_emoji(rating)

    parts.append(f"{emoji} {bold('FAKTENCHECK-ERGEBNIS')}")
    parts.append("")
    parts.append(f"{bold('Bewertung:')} {escape_md(rating)}")
    parts.append(f"{bold('Konfidenz:')} {escape_md(str(confidence))}%")
    parts.append("")

    # Summary
    summary = result.get("summary", "")
    if summary:
        parts.append(f"{bold('Zusammenfassung:')}")
        parts.append(escape_md(summary))
        parts.append("")

    # Claims
    claims = result.get("claims", [])
    if claims:
        parts.append(f"\U0001F50D {bold('CLAIMS')}")
        parts.append("")
        for i, claim in enumerate(claims, 1):
            r = claim.get("rating", "")
            re_ = fact_rating_emoji(r)
            label = fact_rating_label(r)
            parts.append(f"{re_} {bold(f'Claim {i}:')} {escape_md(claim.get('text', '')[:200])}")
            parts.append(f"   {italic('Bewertung:')} {escape_md(label)}")

            evidence = claim.get("evidence", "")
            if evidence:
                parts.append(f"   {italic('Evidenz:')} {escape_md(evidence[:300])}")

            correction = claim.get("correction", "")
            if correction:
                parts.append(f"   {italic('Korrektur:')} {escape_md(correction[:200])}")

            missing = claim.get("missing_context", "")
            if missing:
                parts.append(f"   {italic('Fehlender Kontext:')} {escape_md(missing[:200])}")

            # Number audit
            na = claim.get("number_audit")
            if na and na.get("manipulation", "NONE") != "NONE":
                parts.append(f"   \U0001F4CA {italic('Zahlenmanipulation:')} {escape_md(na.get('manipulation', ''))}")
                if na.get("correct_value"):
                    parts.append(f"   {italic('Korrekte Einordnung:')} {escape_md(na['correct_value'][:200])}")

            parts.append("")

    # Rhetoric / Manipulation Techniques
    rhetoric = result.get("rhetoric", [])
    if rhetoric:
        parts.append(f"\U0001F3AD {bold('MANIPULATIONSTECHNIKEN')}")
        parts.append("")
        for tech in rhetoric:
            sev = severity_emoji(tech.get("severity", ""))
            parts.append(f"{sev} {bold(tech.get('name', ''))}")
            if tech.get("description"):
                parts.append(f"   {escape_md(tech['description'][:200])}")
            if tech.get("example"):
                parts.append(f"   {italic('Beispiel:')} {escape_md(tech['example'][:150])}")
            parts.append("")

    # Corrections
    corrections = result.get("corrections", [])
    if corrections:
        parts.append(f"\u270F\uFE0F {bold('KORREKTUREN')}")
        for i, corr in enumerate(corrections, 1):
            parts.append(f"  {escape_md(str(i))}\\. {escape_md(corr[:200])}")
        parts.append("")

    # Fairness
    fairness = result.get("fairness", [])
    if fairness:
        parts.append(f"\u2696\uFE0F {bold('WAS KORREKT WAR')}")
        for note in fairness:
            parts.append(f"  \u2022 {escape_md(note[:200])}")
        parts.append("")

    # Sources
    sources = result.get("sources", [])
    if sources:
        parts.append(f"\U0001F4CE {bold('QUELLEN')}")
        for src in sources[:8]:
            if src.startswith("http"):
                # Show domain as link
                domain = re.sub(r"^https?://(?:www\.)?", "", src).split("/")[0]
                parts.append(f"  \u2022 {link(domain, src)}")
            else:
                parts.append(f"  \u2022 {escape_md(src[:100])}")

    return "\n".join(parts)


def format_steps_progress(steps: list[dict[str, Any]]) -> str:
    """Format current progress steps as a short status message."""
    if not steps:
        return escape_md("Analyse gestartet...")

    last = steps[-1]
    phase = last.get("phase", "")
    agent = last.get("agent", "")
    msg = last.get("message", "")
    status = last.get("status", "")

    icon = "\U0001F504" if status == "running" else "\u2705"
    return f"{icon} {escape_md(f'{phase} | {agent}')}\n{escape_md(msg[:100])}"


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

async def submit_to_backend(text: str, url: str = "") -> str:
    """Submit analysis job to backend, return job_id."""
    body: dict[str, str] = {"text": text}
    if url:
        body["url"] = url

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{BACKEND_URL}/api/analyze", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["job_id"]


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

async def handle_message(bot: TelegramBot, message: dict[str, Any]) -> None:
    """Process an incoming Telegram message."""
    chat_id = message["chat"]["id"]
    msg_id = message.get("message_id")
    text = message.get("text", "").strip()

    if not text:
        return

    # Handle /start command
    if text == "/start":
        welcome = (
            f"\U0001F50D {bold('FakeNewsGuard Bot')}\n\n"
            f"{escape_md('Sende mir einen Text, Artikel oder Link und ich prüfe ihn auf:')}\n\n"
            f"  \u2022 {escape_md('Faktentreue')}\n"
            f"  \u2022 {escape_md('Zahlenmanipulation')}\n"
            f"  \u2022 {escape_md('Rhetorische Manipulationstechniken')}\n\n"
            f"{bold('Unterstützte Plattformen:')}\n"
            f"  \U0001D54F Twitter/X\n"
            f"  \U0001F9F5 Threads\n"
            f"  \U0001F4F7 Instagram\n"
            f"  \U0001F4D8 Facebook\n"
            f"  \u25B6\uFE0F YouTube\n"
            f"  \U0001F4F0 News\\-Artikel\n\n"
            f"{escape_md('Einfach Text oder Link senden!')}"
        )
        await bot.send_message(chat_id, welcome)
        return

    # Handle /help command
    if text == "/help":
        help_text = (
            f"{bold('Nutzung:')}\n\n"
            f"  \u2022 {escape_md('Sende einen Text oder eine Behauptung')}\n"
            f"  \u2022 {escape_md('Sende einen Link zu einem Social-Media-Post oder Artikel')}\n"
            f"  \u2022 {escape_md('Der Bot extrahiert den Inhalt und analysiert ihn')}\n\n"
            f"{bold('Beispiele:')}\n"
            f"  {escape_md('• \"Deutschland hat die höchste Inflationsrate in Europa\"')}\n"
            f"  {escape_md('• https://x.com/user/status/123...')}\n"
            f"  {escape_md('• https://www.spiegel.de/...')}\n\n"
            f"{escape_md('Die Analyse dauert ca. 1-3 Minuten.')}"
        )
        await bot.send_message(chat_id, help_text)
        return

    # Send typing indicator
    await bot.send_chat_action(chat_id)

    # Send initial "processing" message
    status_resp = await bot.send_message(
        chat_id,
        f"\u23F3 {escape_md('Analyse wird gestartet…')}",
        reply_to_message_id=msg_id,
    )
    status_msg_id = status_resp.get("result", {}).get("message_id")

    try:
        # Detect if text is a URL
        url = ""
        url_match = re.search(r"https?://[^\s]+", text)
        if url_match:
            url = url_match.group(0)
            # If text is essentially just the URL
            remaining = text.replace(url, "").strip()
            if len(remaining) < 20:
                text = ""

        # Submit to backend
        job_id = await submit_to_backend(text, url)

        # Update status
        if status_msg_id:
            try:
                await bot.edit_message(
                    chat_id, status_msg_id,
                    f"\U0001F504 {escape_md('Analyse läuft… Dies kann 1-3 Minuten dauern.')}"
                )
            except Exception:
                pass

        # Poll for result
        last_step_count = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)

                # Keep typing indicator alive
                if attempt % 5 == 0:
                    await bot.send_chat_action(chat_id)

                resp = await client.get(f"{BACKEND_URL}/api/jobs/{job_id}")
                if resp.status_code == 404:
                    raise ValueError("Job nicht gefunden.")

                data = resp.json()

                # Update progress message periodically
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

                    # Format and send result
                    formatted = format_result(result)

                    # Delete progress message
                    if status_msg_id:
                        try:
                            await bot._call("deleteMessage", chat_id=chat_id, message_id=status_msg_id)
                        except Exception:
                            pass

                    # Send result (may need to split if too long)
                    if len(formatted) > 4000:
                        # Split into multiple messages
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
        error_text = f"\u274C {bold('Fehler')}\n\n{escape_md(str(e))}"
        if status_msg_id:
            try:
                await bot.edit_message(chat_id, status_msg_id, error_text)
            except Exception:
                await bot.send_message(chat_id, error_text)
        else:
            await bot.send_message(chat_id, error_text)


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
