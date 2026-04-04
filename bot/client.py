"""Telegram Bot API HTTP client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config.infrastructure import TelegramConfig

_tg_cfg = TelegramConfig()

BACKEND_URL = _tg_cfg.backend_url
POLL_INTERVAL = _tg_cfg.poll_interval
MAX_POLL_ATTEMPTS = _tg_cfg.max_poll_attempts
MSG_CHUNK_SIZE = _tg_cfg.message_chunk_size
HTTP_TIMEOUT = _tg_cfg.http_timeout
POLL_TIMEOUT = _tg_cfg.poll_timeout

log = logging.getLogger("fng-telegram")


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
