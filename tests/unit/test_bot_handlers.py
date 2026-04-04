"""Tests for bot/handlers.py – command routing logic."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cache import BotCache, _pending_texts, _result_cache
from bot.handlers import handle_message


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value={"result": {"message_id": 42}})
    bot.edit_message = AsyncMock()
    bot.answer_callback_query = AsyncMock()
    bot.send_chat_action = AsyncMock()
    bot._call = AsyncMock()
    return bot


def _make_auth(*, registered: bool = True, admin: bool = False, consent: bool = True, tier: str = "lite") -> MagicMock:
    auth = MagicMock()
    if registered:
        user = {"id": "u1", "tier": tier, "admin": 1 if admin else 0, "consent": 1 if consent else 0}
        auth.get_user = MagicMock(return_value=user)
    else:
        auth.get_user = MagicMock(return_value=None)
    auth.is_admin = MagicMock(return_value=admin)
    auth.add_user = MagicMock(return_value=True)
    auth.set_consent = MagicMock()
    return auth


def _make_message(text: str, user_id: int = 123, chat_id: int = 456, msg_id: int = 1) -> dict:
    return {
        "message_id": msg_id,
        "chat": {"id": chat_id},
        "from": {"id": user_id},
        "text": text,
    }


@pytest.fixture(autouse=True)
def clear_caches():
    _pending_texts.clear()
    _result_cache.clear()
    yield
    _pending_texts.clear()
    _result_cache.clear()


class TestHandleMessageCommands:
    @pytest.mark.asyncio
    async def test_start_sends_start_message(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth()
        await handle_message(bot, _make_message("/start"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "FakeNewsGuard" in text or "start" in text.lower()

    @pytest.mark.asyncio
    async def test_help_sends_help_message(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth()
        await handle_message(bot, _make_message("/help"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "help" in text.lower() or "Hilfe" in text or "/lite" in text

    @pytest.mark.asyncio
    async def test_unregistered_user_gets_registration_prompt(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(registered=False)
        await handle_message(bot, _make_message("Bitte analysiere das"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "registriert" in text.lower() or "link" in text.lower()

    @pytest.mark.asyncio
    async def test_plain_text_shows_tier_keyboard(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth()
        await handle_message(bot, _make_message("Analysiere diesen Text bitte"), cache, auth)
        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args[1]
        assert "reply_markup" in call_kwargs
        assert "inline_keyboard" in call_kwargs["reply_markup"]

    @pytest.mark.asyncio
    async def test_plain_text_stores_pending_entry(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth()
        await handle_message(bot, _make_message("Analysiere diesen Text bitte"), cache, auth)
        assert len(_pending_texts) == 1

    @pytest.mark.asyncio
    async def test_adduser_non_admin_denied(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(admin=False)
        await handle_message(bot, _make_message("/adduser 99999"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "Berechtigung" in text or "berechtigung" in text.lower()

    @pytest.mark.asyncio
    async def test_adduser_admin_adds_user(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(admin=True)
        await handle_message(bot, _make_message("/adduser 99999"), cache, auth)
        auth.add_user.assert_called_once_with("99999")
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_consent_required_for_regular_text(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(consent=False)
        await handle_message(bot, _make_message("Analyse dies"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "Zustimmung" in text or "zustimmen" in text.lower()

    @pytest.mark.asyncio
    async def test_zustimmen_sets_consent(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(consent=False)
        await handle_message(bot, _make_message("/zustimmen"), cache, auth)
        auth.set_consent.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth()
        await handle_message(bot, _make_message(""), cache, auth)
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_lite_command_without_text_shows_usage(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(tier="lite")
        await handle_message(bot, _make_message("/lite"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "/lite" in text

    @pytest.mark.asyncio
    async def test_pro_command_denied_for_lite_user(self):
        bot = _make_bot()
        cache = BotCache()
        auth = _make_auth(tier="lite")
        await handle_message(bot, _make_message("/pro Analysiere das"), cache, auth)
        bot.send_message.assert_called_once()
        text = bot.send_message.call_args[0][1]
        assert "Zugriff" in text or "zugriff" in text.lower()
