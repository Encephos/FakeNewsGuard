"""Message and callback query routing for the Telegram bot."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from typing import TYPE_CHECKING, Any

import httpx

from bot.cache import PENDING_TEXT_TTL
from bot.client import BACKEND_URL, HTTP_TIMEOUT
from bot.keyboards import TIER_LEVELS, build_result_keyboard, build_tier_keyboard
from telegram_formatting import (
    bold,
    code,
    escape_md,
    format_audience_section,
    format_claim_detail,
    format_corrections_section,
    format_fairness_section,
    format_help_message,
    format_narrative_section,
    format_rhetoric_section,
    format_sources_section,
    format_start_message,
    format_tier_selection,
)

if TYPE_CHECKING:
    from bot.auth import BotAuth
    from bot.cache import BotCache
    from bot.client import TelegramBot

log = logging.getLogger("fng-telegram")


async def handle_message(
    bot: TelegramBot,
    message: dict[str, Any],
    cache: BotCache,
    auth: BotAuth,
) -> None:
    """Process an incoming Telegram message."""
    from bot.analysis import run_analysis

    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)
    msg_id = message.get("message_id")
    text = message.get("text", "").strip()

    if not text:
        return

    # ── /adduser (admin only) ──
    if text.startswith("/adduser"):
        if not auth.is_admin(user_id):
            await bot.send_message(chat_id, f"\u274C {escape_md('Keine Berechtigung.')}")
            return
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(
                chat_id,
                f"\u2139\uFE0F {bold('/adduser')}\n\n"
                f"{escape_md('Verwendung:')} {code('/adduser <Telegram-ID>')}\n"
                f"_{escape_md('Fügt einen neuen Nutzer mit Tier LITE hinzu.')}_",
            )
            return
        new_uid = parts[1].strip()
        if auth.add_user(new_uid):
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
                f"_{escape_md('Den Code findest du in deinem Profil auf der Webseite.')}_",
            )
            return
        link_code = parts[1].strip().upper()
        existing = auth.get_user(user_id)
        if existing is not None:
            await bot.send_message(
                chat_id,
                f"\u2139\uFE0F {escape_md('Dein Telegram-Konto ist bereits mit einem Account verknüpft.')}",
            )
            return
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
                        f"_{escape_md('Du kannst jetzt Analysen direkt hier starten.')}_",
                    )
                else:
                    detail = resp.json().get("detail", "Unbekannter Fehler.")
                    await bot.send_message(chat_id, f"\u274C {escape_md(detail)}")
        except Exception as e:
            log.error("Link verification failed: %s", e)
            await bot.send_message(
                chat_id,
                f"\u274C {escape_md('Verbindung zum Server fehlgeschlagen. Bitte versuche es erneut.')}",
            )
        return

    # ── /zustimmen – consent to logging ──
    if text == "/zustimmen":
        user = auth.get_user(user_id)
        if user is None:
            await bot.send_message(
                chat_id,
                f"\U0001F6AB {escape_md('Bitte registriere dich zuerst. Sende /start für Infos.')}",
            )
            return
        auth.set_consent(user_id, True)
        await bot.send_message(
            chat_id,
            f"\u2705 {bold('Zustimmung erteilt')}\n\n"
            f"{escape_md('Vielen Dank! Du kannst FakeNewsGuard jetzt nutzen.')}\n"
            f"{escape_md('Sende einfach einen Text oder Link zur Analyse.')}",
        )
        return

    # ── Access check: user must be registered ──
    user = auth.get_user(user_id)
    if user is None:
        await bot.send_message(
            chat_id,
            f"\U0001F6AB {bold('Nicht registriert')}\n\n"
            f"{escape_md('Um FakeNewsGuard zu nutzen, verknüpfe dein Telegram-Konto:')}\n\n"
            f"  *1\\.* {escape_md('Erstelle ein Konto auf der Webseite')}\n"
            f"  *2\\.* {escape_md('Kopiere den Verknüpfungscode aus deinem Profil')}\n"
            f"  *3\\.* {escape_md('Sende hier:')} {code('/link <CODE>')}\n",
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
            f"{escape_md('Sende')} {code('/zustimmen')}{escape_md(', um zuzustimmen und loszulegen.')}",
        )
        return

    # ── /lite, /pro, /max, /commander-pro, /commander-max ──
    for tier_cmd in ("commander-pro", "commander-max", "lite", "pro", "max"):
        if text.startswith(f"/{tier_cmd}"):
            tier_levels = {"lite": 0, "pro": 1, "max": 2}
            base_tier = tier_cmd.replace("commander-", "") if tier_cmd.startswith("commander-") else tier_cmd
            if tier_levels.get(base_tier, 0) > tier_levels.get(user_tier, 0):
                label = tier_cmd.upper().replace("-", " ")
                await bot.send_message(
                    chat_id,
                    f"\u274C {bold('Kein Zugriff')}\n\n"
                    f"{escape_md(f'Dein aktueller Plan ({user_tier.upper()}) erlaubt keinen Zugriff auf {label}.')}\n"
                    f"_{escape_md('Upgrade deinen Plan auf der Webseite.')}_",
                )
                return
            analysis_text = text[len(f"/{tier_cmd}"):].strip()
            if not analysis_text:
                await bot.send_message(
                    chat_id,
                    f"\u2139\uFE0F {bold(f'/{tier_cmd}')}\n\n"
                    f"{escape_md(f'Sende einen Text oder Link nach dem Befehl:')}\n"
                    f"  {code(f'/{tier_cmd}')} {escape_md('Dein Text oder Link hier')}",
                )
                return
            await run_analysis(bot, chat_id, msg_id, analysis_text, tier_cmd, cache, auth)
            return

    # ── /start ──
    if text == "/start":
        await bot.send_message(chat_id, format_start_message())
        return

    # ── /help ──
    if text == "/help":
        await bot.send_message(chat_id, format_help_message())
        return

    # Default: show tier selection keyboard
    msg_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    cache.pending_texts[msg_hash] = {
        "chat_id": chat_id,
        "msg_id": msg_id,
        "text": text,
        "user_id": user_id,
        "expires": _time.time() + PENDING_TEXT_TTL,
    }
    keyboard = build_tier_keyboard(user_tier, msg_hash)
    await bot.send_message(
        chat_id,
        format_tier_selection(),
        reply_to_message_id=msg_id,
        reply_markup=keyboard,
    )


async def handle_callback(
    bot: TelegramBot,
    cq: dict[str, Any],
    cache: BotCache,
    auth: BotAuth,
) -> None:
    """Route an incoming callback query by data prefix."""
    cq_id = cq.get("id", "")
    data = cq.get("data", "")
    user_id = cq.get("from", {}).get("id", 0)
    message = cq.get("message", {})
    chat_id = message.get("chat", {}).get("id", 0)
    message_id = message.get("message_id", 0)

    try:
        if data.startswith("t:"):
            await _handle_tier_callback(bot, cq_id, data, user_id, chat_id, message_id, cache, auth)
        elif data.startswith("c:"):
            await _handle_claim_callback(bot, cq_id, data, user_id, chat_id, message_id, cache)
        elif data.startswith("s:"):
            await _handle_section_callback(bot, cq_id, data, user_id, chat_id, message_id, cache)
        elif data.startswith("b:"):
            await _handle_back_callback(bot, cq_id, data, user_id, chat_id, message_id, cache)
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
    bot: TelegramBot,
    cq_id: str,
    data: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    cache: BotCache,
    auth: BotAuth,
) -> None:
    from bot.analysis import run_analysis

    parts = data.split(":", 2)
    if len(parts) != 3:
        await bot.answer_callback_query(cq_id)
        return

    tier = parts[1]
    msg_hash = parts[2]

    pending = cache.pending_texts.get(msg_hash)
    if pending is None or _time.time() > pending.get("expires", 0):
        cache.pending_texts.pop(msg_hash, None)
        await bot.answer_callback_query(cq_id, text="Auswahl abgelaufen. Sende den Text erneut.", show_alert=True)
        await bot.edit_message(
            chat_id, message_id,
            f"\u23F3 {escape_md('Auswahl abgelaufen. Sende den Text erneut.')}",
            reply_markup={"inline_keyboard": []},
        )
        return

    if pending["user_id"] != user_id:
        await bot.answer_callback_query(cq_id, text="Du kannst nur deine eigene Analyse steuern.", show_alert=True)
        return

    base_tier = tier.replace("commander-", "") if tier.startswith("commander-") else tier
    user = auth.get_user(user_id)
    user_tier = user.get("tier", "lite") if user else "lite"
    if TIER_LEVELS.get(base_tier, 0) > TIER_LEVELS.get(user_tier, 0):
        await bot.answer_callback_query(cq_id, text="Kein Zugriff auf diesen Tier.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    tier_label = tier.upper().replace("-", " ")
    await bot.edit_message(
        chat_id, message_id,
        f"\U0001F9E0 {escape_md(f'Analyse mit {tier_label} gestartet...')}",
        reply_markup={"inline_keyboard": []},
    )

    original_text = pending["text"]
    original_msg_id = pending["msg_id"]
    cache.pending_texts.pop(msg_hash, None)
    await run_analysis(bot, chat_id, original_msg_id, original_text, tier, cache, auth)


async def _handle_claim_callback(
    bot: TelegramBot,
    cq_id: str,
    data: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    cache: BotCache,
) -> None:
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

    cached = cache.result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        cache.result_cache.pop(job_short, None)
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
        await bot.send_message(chat_id, detail, reply_markup=back_keyboard)


async def _handle_section_callback(
    bot: TelegramBot,
    cq_id: str,
    data: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    cache: BotCache,
) -> None:
    parts = data.split(":", 2)
    if len(parts) != 3:
        await bot.answer_callback_query(cq_id)
        return

    job_short = parts[1]
    section = parts[2]

    cached = cache.result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        cache.result_cache.pop(job_short, None)
        await bot.answer_callback_query(cq_id, text="Ergebnis nicht mehr verfügbar. Starte eine neue Analyse.", show_alert=True)
        return

    await bot.answer_callback_query(cq_id)

    result = cached["result"]
    section_formatters = {
        "rhet": lambda: format_rhetoric_section(result.get("rhetoric", [])),
        "src": lambda: format_sources_section(result.get("sources", [])),
        "corr": lambda: format_corrections_section(result.get("corrections", [])),
        "fair": lambda: format_fairness_section(result.get("fairness", [])),
        "narr": lambda: format_narrative_section(result.get("narrative_patterns", [])),
        "aud": lambda: format_audience_section(result.get("audience_manipulation")),
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
    bot: TelegramBot,
    cq_id: str,
    data: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    cache: BotCache,
) -> None:
    parts = data.split(":", 1)
    if len(parts) != 2:
        await bot.answer_callback_query(cq_id)
        return

    job_short = parts[1]

    cached = cache.result_cache.get(job_short)
    if cached is None or _time.time() > cached.get("expires", 0):
        cache.result_cache.pop(job_short, None)
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
