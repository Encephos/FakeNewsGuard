"""In-memory caches for pending texts and analysis results."""

from __future__ import annotations

import time as _time
from typing import Any

PENDING_TEXT_TTL = 300       # 5 minutes
RESULT_CACHE_TTL = 1800      # 30 minutes
MAX_CACHE_ENTRIES = 200

_pending_texts: dict[str, dict[str, Any]] = {}
_result_cache: dict[str, dict[str, Any]] = {}


def _cleanup_caches() -> None:
    """Remove expired entries from both caches."""
    now = _time.time()
    for cache in (_pending_texts, _result_cache):
        expired = [k for k, v in cache.items() if now > v.get("expires", 0)]
        for k in expired:
            del cache[k]
        # Hard cap
        if len(cache) > MAX_CACHE_ENTRIES:
            by_age = sorted(cache.items(), key=lambda kv: kv[1].get("expires", 0))
            for k, _ in by_age[: len(cache) - MAX_CACHE_ENTRIES]:
                del cache[k]


class BotCache:
    """Thin wrapper around module-level cache dicts for dependency injection."""

    @property
    def pending_texts(self) -> dict[str, dict[str, Any]]:
        return _pending_texts

    @property
    def result_cache(self) -> dict[str, dict[str, Any]]:
        return _result_cache

    def cleanup(self) -> None:
        _cleanup_caches()
