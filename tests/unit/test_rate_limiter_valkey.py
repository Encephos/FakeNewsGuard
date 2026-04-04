"""Tests für ValkeyRateLimiter und create_rate_limiter Factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import time

import pytest

from config import RateLimitConfig
from tools.rate_limiter import RateLimiter, ValkeyRateLimiter, create_rate_limiter


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_valkey_cfg(enabled: bool = True, url: str = "redis://localhost:6379/0", db: int = 0):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.url = url
    cfg.db = db
    return cfg


def _make_limiter(rpm: int = 10, enabled: bool = True) -> tuple[ValkeyRateLimiter, MagicMock]:
    """Erstellt einen ValkeyRateLimiter mit gemocktem Redis-Client."""
    config = RateLimitConfig(enabled=enabled, requests_per_minute=rpm, burst=3)
    valkey_cfg = _make_valkey_cfg()
    mock_client = MagicMock()
    with patch("redis.Redis.from_url", return_value=mock_client):
        limiter = ValkeyRateLimiter(config, valkey_cfg)
    return limiter, mock_client


# ── 1. Erlaubter Request (ZCARD unter Limit) ──────────────────────────────────

def test_allows_within_limit():
    limiter, mock_client = _make_limiter(rpm=10)
    # Lua-Skript gibt {1, 0} zurück → allowed
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "abc123"

    allowed, retry_after = limiter.check("192.168.1.1")

    assert allowed is True
    assert retry_after == 0.0


# ── 2. Blockierter Request (ZCARD >= Limit) ───────────────────────────────────

def test_blocks_at_limit():
    limiter, mock_client = _make_limiter(rpm=10)
    now = time.time()
    oldest_score = now - 30.0  # 30 Sekunden alt → retry_after ≈ 30s
    mock_client.eval.return_value = [0, oldest_score]
    mock_client.script_load.return_value = "abc123"

    allowed, retry_after = limiter.check("192.168.1.1")

    assert allowed is False
    assert retry_after > 0.0


# ── 3. retry_after-Berechnung ─────────────────────────────────────────────────

def test_retry_after_calculation():
    limiter, mock_client = _make_limiter(rpm=10)
    now = time.time()
    oldest_score = now - 40.0  # 40s ago → 60s window → retry in ~20s
    mock_client.eval.return_value = [0, oldest_score]
    mock_client.script_load.return_value = "abc123"

    _, retry_after = limiter.check("10.0.0.1")

    # retry_after = oldest_score + 60 - now ≈ 20s
    assert 15.0 < retry_after < 25.0


# ── 4. retry_after wird auf 0.0 geclampt wenn Window schon abgelaufen ─────────

def test_retry_after_clamped_to_zero():
    limiter, mock_client = _make_limiter(rpm=10)
    now = time.time()
    oldest_score = now - 70.0  # 70s ago → window (60s) already past
    mock_client.eval.return_value = [0, oldest_score]
    mock_client.script_load.return_value = "abc123"

    _, retry_after = limiter.check("10.0.0.1")

    assert retry_after == 0.0


# ── 5. IP-Isolation: unterschiedliche Keys pro IP ─────────────────────────────

def test_ip_isolation():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "sha"
    mock_client.evalsha.return_value = [1, 0]

    limiter.check("10.0.0.1")   # → eval
    limiter.check("10.0.0.2")   # → evalsha (sha cached after first call)

    key_first = mock_client.eval.call_args.args[2]
    key_second = mock_client.evalsha.call_args.args[2]
    assert key_first != key_second
    assert key_first == "fng:rl:10.0.0.1"
    assert key_second == "fng:rl:10.0.0.2"


# ── 6. Key-Prefix ist fng:rl:{ip} ────────────────────────────────────────────

def test_key_prefix():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "sha"

    limiter.check("1.2.3.4")

    key_arg = mock_client.eval.call_args.args[2]
    assert key_arg == "fng:rl:1.2.3.4"


# ── 7. enabled=False → kein Redis-Aufruf, immer erlaubt ──────────────────────

def test_disabled_always_allows():
    limiter, mock_client = _make_limiter(rpm=10, enabled=False)

    for _ in range(5):
        allowed, retry_after = limiter.check("127.0.0.1")
        assert allowed is True
        assert retry_after == 0.0

    mock_client.eval.assert_not_called()
    mock_client.evalsha.assert_not_called()


# ── 8. Redis-Fehler → fail-open (True, 0.0) ───────────────────────────────────

def test_redis_error_fail_open():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.side_effect = ConnectionError("Redis nicht erreichbar")

    allowed, retry_after = limiter.check("127.0.0.1")

    assert allowed is True
    assert retry_after == 0.0


# ── 9. Member-Wert ist bei jedem Aufruf eindeutig ────────────────────────────

def test_member_is_unique():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "sha"
    mock_client.evalsha.return_value = [1, 0]

    limiter.check("10.0.0.1")   # → eval, ARGV[4] = member at index args[5]
    limiter.check("10.0.0.1")   # → evalsha, ARGV[4] = member at index args[5]

    # Both eval and evalsha: (script/sha, numkeys, key, now, window, limit, member, ttl)
    member_first = mock_client.eval.call_args.args[6]
    member_second = mock_client.evalsha.call_args.args[6]
    assert member_first != member_second


# ── 10. EXPIRE wird auch bei Deny gesetzt ────────────────────────────────────

def test_expire_called_on_deny():
    # EXPIRE wird im Lua-Skript intern gesetzt – wir prüfen, dass eval aufgerufen wird
    # (das Skript ruft EXPIRE immer auf, im deny-Pfad auch)
    limiter, mock_client = _make_limiter(rpm=10)
    now = time.time()
    mock_client.eval.return_value = [0, now - 5.0]
    mock_client.script_load.return_value = "sha"

    allowed, _ = limiter.check("10.0.0.1")

    assert allowed is False
    mock_client.eval.assert_called_once()


# ── 11. TTL-Wert = 120 Sekunden ──────────────────────────────────────────────

def test_ttl_value():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "sha"

    limiter.check("10.0.0.1")

    # ARGV[5] (Index 6 in args: script, numkeys, key, now, window, limit, member, ttl)
    ttl_arg = mock_client.eval.call_args.args[7]
    assert ttl_arg == "120"


# ── 12. EVALSHA wird nach dem ersten EVAL gecacht ────────────────────────────

def test_evalsha_used_after_first_eval():
    limiter, mock_client = _make_limiter(rpm=10)
    mock_client.eval.return_value = [1, 0]
    mock_client.script_load.return_value = "cached_sha"
    mock_client.evalsha.return_value = [1, 0]

    limiter.check("10.0.0.1")  # erstes Mal → eval + script_load
    limiter.check("10.0.0.1")  # zweites Mal → evalsha

    mock_client.eval.assert_called_once()
    mock_client.evalsha.assert_called_once()
    assert limiter._sha == "cached_sha"


# ── 13. Factory → ValkeyRateLimiter wenn Valkey erreichbar ───────────────────

def test_create_rate_limiter_returns_valkey():
    config = RateLimitConfig(enabled=True, requests_per_minute=10, burst=3)
    valkey_cfg = _make_valkey_cfg(enabled=True)
    mock_client = MagicMock()

    with patch("redis.Redis.from_url", return_value=mock_client):
        limiter = create_rate_limiter(config, valkey_cfg)

    assert isinstance(limiter, ValkeyRateLimiter)
    mock_client.ping.assert_called_once()


# ── 14. Factory → RateLimiter wenn Valkey nicht erreichbar ───────────────────

def test_create_rate_limiter_fallback_on_error():
    config = RateLimitConfig(enabled=True, requests_per_minute=10, burst=3)
    valkey_cfg = _make_valkey_cfg(enabled=True)
    mock_client = MagicMock()
    mock_client.ping.side_effect = ConnectionError("offline")

    with patch("redis.Redis.from_url", return_value=mock_client):
        limiter = create_rate_limiter(config, valkey_cfg)

    assert isinstance(limiter, RateLimiter)


# ── 15. Factory → RateLimiter wenn valkey_cfg=None ───────────────────────────

def test_create_rate_limiter_no_valkey_cfg():
    config = RateLimitConfig(enabled=True, requests_per_minute=10, burst=3)

    limiter = create_rate_limiter(config, valkey_cfg=None)

    assert isinstance(limiter, RateLimiter)


# ── 16. Factory → RateLimiter wenn Valkey disabled ───────────────────────────

def test_create_rate_limiter_disabled_valkey():
    config = RateLimitConfig(enabled=True, requests_per_minute=10, burst=3)
    valkey_cfg = _make_valkey_cfg(enabled=False)

    with patch("redis.Redis.from_url") as mock_from_url:
        limiter = create_rate_limiter(config, valkey_cfg)

    assert isinstance(limiter, RateLimiter)
    mock_from_url.assert_not_called()
