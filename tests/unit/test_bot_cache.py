"""Tests for bot/cache.py – TTL logic and BotCache class."""

from __future__ import annotations

import time

import pytest

from bot.cache import BotCache, _cleanup_caches, _pending_texts, _result_cache


@pytest.fixture(autouse=True)
def clear_caches():
    _pending_texts.clear()
    _result_cache.clear()
    yield
    _pending_texts.clear()
    _result_cache.clear()


class TestCleanupCaches:
    def test_expired_pending_removed(self):
        _pending_texts["old"] = {"expires": time.time() - 10}
        _pending_texts["new"] = {"expires": time.time() + 300}
        _cleanup_caches()
        assert "old" not in _pending_texts
        assert "new" in _pending_texts

    def test_expired_result_removed(self):
        _result_cache["expired"] = {"expires": time.time() - 10}
        _result_cache["valid"] = {"expires": time.time() + 1800}
        _cleanup_caches()
        assert "expired" not in _result_cache
        assert "valid" in _result_cache

    def test_hard_cap_enforced(self):
        for i in range(250):
            _pending_texts[f"k{i}"] = {"expires": time.time() + 300 + i}
        _cleanup_caches()
        assert len(_pending_texts) <= 200

    def test_hard_cap_keeps_newest(self):
        for i in range(250):
            _pending_texts[f"k{i}"] = {"expires": time.time() + i}
        _cleanup_caches()
        # The 50 oldest entries (k0..k49) should be evicted
        assert "k0" not in _pending_texts
        assert "k249" in _pending_texts


class TestBotCache:
    def test_pending_texts_property_is_module_dict(self):
        cache = BotCache()
        _pending_texts["sentinel"] = {"expires": time.time() + 100}
        assert cache.pending_texts is _pending_texts
        assert "sentinel" in cache.pending_texts

    def test_result_cache_property_is_module_dict(self):
        cache = BotCache()
        _result_cache["sentinel"] = {"expires": time.time() + 100}
        assert cache.result_cache is _result_cache
        assert "sentinel" in cache.result_cache

    def test_cleanup_delegates(self):
        cache = BotCache()
        _pending_texts["stale"] = {"expires": time.time() - 1}
        cache.cleanup()
        assert "stale" not in _pending_texts

    def test_writes_via_property_visible_in_module_dict(self):
        cache = BotCache()
        cache.pending_texts["injected"] = {"expires": time.time() + 100}
        assert "injected" in _pending_texts
