"""Long-polling entry point for the FakeNewsGuard Telegram bot."""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from bot.auth import BotAuth
from bot.cache import BotCache
from bot.client import BACKEND_URL, TelegramBot
from bot.handlers import handle_callback, handle_message
from config.infrastructure import TelegramConfig

log = logging.getLogger("fng-telegram")

_tg_cfg = TelegramConfig()
BOT_TOKEN = _tg_cfg.bot_token


async def main() -> None:
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN nicht gesetzt. Bitte in .env konfigurieren.", file=sys.stderr)
        sys.exit(1)

    bot = TelegramBot(BOT_TOKEN)
    cache = BotCache()
    auth = BotAuth()

    try:
        me = await bot.get_me()
        bot_name = me.get("result", {}).get("username", "?")
        log.info("Bot gestartet: @%s", bot_name)
        log.info("Backend: %s", BACKEND_URL)

        while True:
            try:
                cache.cleanup()
                updates = await bot.get_updates()
                for update in updates:
                    message = update.get("message")
                    if message:
                        asyncio.create_task(handle_message(bot, message, cache, auth))
                    callback_query = update.get("callback_query")
                    if callback_query:
                        asyncio.create_task(handle_callback(bot, callback_query, cache, auth))
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
