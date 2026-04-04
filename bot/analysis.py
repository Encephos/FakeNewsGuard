"""Analysis submission, polling, and result delivery."""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from typing import TYPE_CHECKING, Any

import httpx

from bot.client import BACKEND_URL, HTTP_TIMEOUT, MAX_POLL_ATTEMPTS, MSG_CHUNK_SIZE, POLL_INTERVAL, POLL_TIMEOUT
from telegram_formatting import bold, escape_md, format_result_overview, format_steps_progress

if TYPE_CHECKING:
    from bot.auth import BotAuth
    from bot.cache import BotCache
    from bot.client import TelegramBot

log = logging.getLogger("fng-telegram")


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


async def run_analysis(
    bot: TelegramBot,
    chat_id: int,
    msg_id: int,
    text: str,
    tier: str,
    cache: BotCache,
    auth: BotAuth,
) -> None:
    """Submit text to backend with the given tier and show results."""
    from bot.keyboards import build_result_keyboard

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
        tg_user = auth.get_user(chat_id)
        if tg_user is None:
            raise ValueError("Nutzer nicht registriert.")
        auth_token = auth.mint_jwt(tg_user)
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
                    keyboard = build_result_keyboard(result, job_short)

                    # Cache result for callback navigation
                    from bot.cache import RESULT_CACHE_TTL
                    cache.result_cache[job_short] = {
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
