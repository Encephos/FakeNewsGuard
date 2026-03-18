"""Tests für tools/rate_limiter.py."""

from __future__ import annotations

import time

import pytest

from config import RateLimitConfig
from tools.rate_limiter import RateLimiter


# ── Grundfunktionalität ─────────────────────────────────────────


def test_allows_requests_within_limit():
    config = RateLimitConfig(enabled=True, requests_per_minute=60, burst=3)
    limiter = RateLimiter(config)

    allowed, retry_after = limiter.check("127.0.0.1")
    assert allowed is True
    assert retry_after == 0.0


def test_blocks_after_burst_exceeded():
    config = RateLimitConfig(enabled=True, requests_per_minute=60, burst=2)
    limiter = RateLimiter(config)

    # Burst aufbrauchen
    limiter.check("127.0.0.1")
    limiter.check("127.0.0.1")

    # Dritter Request sollte blockiert werden
    allowed, retry_after = limiter.check("127.0.0.1")
    assert allowed is False
    assert retry_after > 0


def test_different_ips_are_independent():
    config = RateLimitConfig(enabled=True, requests_per_minute=60, burst=1)
    limiter = RateLimiter(config)

    limiter.check("10.0.0.1")
    allowed, _ = limiter.check("10.0.0.2")
    assert allowed is True


def test_disabled_always_allows():
    config = RateLimitConfig(enabled=False)
    limiter = RateLimiter(config)

    for _ in range(100):
        allowed, _ = limiter.check("127.0.0.1")
        assert allowed is True


def test_tokens_refill_over_time():
    config = RateLimitConfig(enabled=True, requests_per_minute=6000, burst=1)
    limiter = RateLimiter(config)

    # Burst aufbrauchen
    limiter.check("127.0.0.1")

    # Kurz warten – bei 6000 RPM = 100/s sollte nach 0.02s ein Token da sein
    time.sleep(0.02)
    allowed, _ = limiter.check("127.0.0.1")
    assert allowed is True


def test_retry_after_is_reasonable():
    config = RateLimitConfig(enabled=True, requests_per_minute=60, burst=1)
    limiter = RateLimiter(config)

    limiter.check("127.0.0.1")
    _, retry_after = limiter.check("127.0.0.1")

    # Bei 60 RPM = 1/s sollte retry_after ~1s sein
    assert 0 < retry_after <= 2.0
