"""Tests für tools/cache.py."""

from __future__ import annotations

import time

import pytest

from config import CacheConfig
from tools.cache import ClaimCache, _claim_key


# ── _claim_key ────────────────────────────────────────────────────


def test_claim_key_is_deterministic():
    k1 = _claim_key("Die Erde ist rund.", "FactChecker")
    k2 = _claim_key("Die Erde ist rund.", "FactChecker")
    assert k1 == k2


def test_claim_key_differs_by_agent():
    k1 = _claim_key("Claim text", "FactChecker")
    k2 = _claim_key("Claim text", "NumberAuditor")
    assert k1 != k2


def test_claim_key_normalizes_whitespace():
    k1 = _claim_key("  Die Erde ist rund.  ", "FactChecker")
    k2 = _claim_key("die erde ist rund.", "FactChecker")
    assert k1 == k2


# ── ClaimCache ────────────────────────────────────────────────────


def test_cache_set_and_get(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("test claim", "FactChecker", {"rating": "TRUE"})
    result = cache.get("test claim", "FactChecker")
    assert result == {"rating": "TRUE"}


def test_cache_miss_returns_none(cache_config):
    cache = ClaimCache(cache_config)
    result = cache.get("nonexistent claim", "FactChecker")
    assert result is None


def test_cache_disabled_always_returns_none(tmp_path):
    config = CacheConfig(enabled=False, db_path=str(tmp_path / "disabled.db"))
    cache = ClaimCache(config)
    cache.set("test", "Agent", {"x": 1})
    assert cache.get("test", "Agent") is None


def test_cache_ttl_expiry(tmp_path):
    """Cache-Einträge mit abgelaufener TTL sollen None zurückgeben."""
    config = CacheConfig(enabled=True, db_path=str(tmp_path / "ttl.db"), ttl_hours=0)
    cache = ClaimCache(config)
    cache.set("claim", "Agent", {"data": "value"})
    # TTL = 0 hours = sofort abgelaufen
    result = cache.get("claim", "Agent")
    assert result is None


def test_cache_overwrite(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("claim", "FactChecker", {"rating": "TRUE"})
    cache.set("claim", "FactChecker", {"rating": "FALSE"})
    assert cache.get("claim", "FactChecker") == {"rating": "FALSE"}


def test_cache_clear_expired(tmp_path):
    config = CacheConfig(enabled=True, db_path=str(tmp_path / "expired.db"), ttl_hours=0)
    cache = ClaimCache(config)
    cache.set("a", "Agent", {})
    cache.set("b", "Agent", {})
    removed = cache.clear_expired()
    assert removed == 2


def test_cache_stats(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("c1", "Agent", {})
    cache.set("c2", "Agent", {})
    stats = cache.stats()
    assert stats["enabled"] is True
    assert stats["valid_entries"] == 2
    assert stats["expired_entries"] == 0


def test_cache_delete(cache_config):
    cache = ClaimCache(cache_config)
    cache.set("claim", "Agent", {"x": 1})
    cache.delete("claim", "Agent")
    assert cache.get("claim", "Agent") is None
