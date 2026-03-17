"""Tests für tools/retry.py."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, call

import pytest

from tools.retry import _calc_delay, retry_call, retry_call_async, with_retry, with_retry_async


# ── _calc_delay ───────────────────────────────────────────────────


def test_calc_delay_stays_below_max():
    delay = _calc_delay(attempt=10, base_delay=1.0, max_delay=10.0, backoff_factor=2.0)
    assert delay <= 10.0 * 1.5  # max_delay * max_jitter_factor


def test_calc_delay_includes_jitter():
    """Zwei aufeinanderfolgende Calls sollten unterschiedliche Werte liefern."""
    delays = {_calc_delay(0, 1.0, 60.0, 2.0) for _ in range(20)}
    assert len(delays) > 1  # Mit Jitter sollte Variation da sein


# ── retry_call ────────────────────────────────────────────────────


def test_retry_call_succeeds_first_try():
    mock = MagicMock(return_value="ok")
    result = retry_call(mock, max_attempts=3, base_delay=0.0)
    assert result == "ok"
    mock.assert_called_once()


def test_retry_call_retries_on_transient_error():
    """Funktion schlägt 2x fehl, 3. Versuch erfolgreich."""
    mock = MagicMock(side_effect=[RuntimeError("temp"), RuntimeError("temp"), "ok"])
    result = retry_call(mock, max_attempts=3, base_delay=0.0)
    assert result == "ok"
    assert mock.call_count == 3


def test_retry_call_raises_after_max_attempts():
    mock = MagicMock(side_effect=RuntimeError("permanent"))
    with pytest.raises(RuntimeError, match="permanent"):
        retry_call(mock, max_attempts=3, base_delay=0.0)
    assert mock.call_count == 3


def test_retry_call_no_retry_on_4xx():
    """4xx HTTP-Fehler (außer 429) sollten nicht wiederholt werden."""
    import httpx

    response = MagicMock()
    response.status_code = 400
    exc = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=response)

    mock = MagicMock(side_effect=exc)
    with pytest.raises(httpx.HTTPStatusError):
        retry_call(mock, max_attempts=3, base_delay=0.0)
    mock.assert_called_once()  # Kein Retry bei 400


def test_retry_call_retries_on_429():
    """429 Rate Limit sollte Retry auslösen."""
    import httpx

    response = MagicMock()
    response.status_code = 429
    exc = httpx.HTTPStatusError("Too Many Requests", request=MagicMock(), response=response)

    mock = MagicMock(side_effect=[exc, exc, "ok"])
    result = retry_call(mock, max_attempts=3, base_delay=0.0)
    assert result == "ok"
    assert mock.call_count == 3


# ── with_retry decorator ──────────────────────────────────────────


def test_with_retry_decorator():
    calls = []

    @with_retry(max_attempts=3, base_delay=0.0)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("not yet")
        return "done"

    assert flaky() == "done"
    assert len(calls) == 3


# ── retry_call_async ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_call_async_succeeds():
    mock = MagicMock(return_value="ok")

    async def afunc():
        return mock()

    result = await retry_call_async(afunc, max_attempts=3, base_delay=0.0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_retry_call_async_retries():
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("temp")
        return "done"

    result = await retry_call_async(flaky, max_attempts=3, base_delay=0.0)
    assert result == "done"
    assert call_count == 3
