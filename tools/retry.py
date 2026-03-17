"""Retry-Logik mit exponentiellem Backoff und Jitter."""

from __future__ import annotations

import asyncio
import functools
import random
import time
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)

# HTTP-Statuscodes, bei denen ein Retry sinnvoll ist
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def _calc_delay(attempt: int, base_delay: float, max_delay: float, backoff_factor: float) -> float:
    """Berechne Wartezeit mit exponentiellem Backoff und ±50 % Jitter."""
    delay = min(base_delay * (backoff_factor**attempt), max_delay)
    # Jitter: zufälliger Faktor zwischen 0.5 und 1.5
    return delay * (0.5 + random.random())


def _is_retryable(exc: Exception) -> bool:
    """Prüfe, ob eine Exception wiederholbar ist."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status in RETRYABLE_HTTP_CODES
    # Netzwerkfehler (keine Response) sind immer retryable
    return True


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
) -> Callable[[F], F]:
    """Dekorator: Wiederholt eine Funktion mit exponentiellem Backoff.

    Beispiel::

        @with_retry(max_attempts=3, base_delay=1.0)
        def call_api():
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts - 1 or not _is_retryable(exc):
                        raise
                    delay = _calc_delay(attempt, base_delay, max_delay, backoff_factor)
                    time.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator


def with_retry_async(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
) -> Callable[[F], F]:
    """Async-Dekorator: Wie with_retry, aber für async-Funktionen."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts - 1 or not _is_retryable(exc):
                        raise
                    delay = _calc_delay(attempt, base_delay, max_delay, backoff_factor)
                    await asyncio.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator


def retry_call(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    **kwargs,
):
    """Führe eine callable mit Retry aus (ohne Dekorator-Syntax).

    Nützlich, wenn man die Parameter zur Laufzeit aus einer Config lesen will.
    """
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_attempts - 1 or not _is_retryable(exc):
                raise
            delay = _calc_delay(attempt, base_delay, max_delay, backoff_factor)
            time.sleep(delay)


async def retry_call_async(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    **kwargs,
):
    """Async-Version von retry_call."""
    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_attempts - 1 or not _is_retryable(exc):
                raise
            delay = _calc_delay(attempt, base_delay, max_delay, backoff_factor)
            await asyncio.sleep(delay)
