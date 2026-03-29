"""Valkey-Backend für den Search-Result-Cache.

Cached SearXNG/LangSearch-Ergebnisse um wiederholte Anfragen an Suchmaschinen
zu vermeiden. Reduziert Rate-Limiting-Probleme drastisch.

Key-Schema: fng:search:{sha256(query::categories)}
Wert: JSON-serialisierte Liste von SearchResult-Dicts
TTL: Automatisch via Valkey SETEX (Default: 6h)

Aktivierung: Automatisch wenn Valkey verfügbar, sonst In-Memory-Fallback.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from config import SearchCacheConfig, ValkeyConfig


def _search_key(query: str, categories: str = "") -> str:
    """Stabiler Cache-Key aus Query + Kategorien."""
    raw = f"{query.strip().lower()}::{categories.strip().lower()}"
    return "fng:search:" + hashlib.sha256(raw.encode()).hexdigest()


class ValkeySearchCache:
    """Valkey-backed Search-Result-Cache.

    Gleiche Schnittstelle wie InMemorySearchCache für Drop-in-Austausch.
    """

    def __init__(self, valkey_cfg: ValkeyConfig, cache_cfg: SearchCacheConfig) -> None:
        self._cfg = cache_cfg
        self._ttl = cache_cfg.ttl_hours * 3600
        self._client = self._connect(valkey_cfg)
        self.hit_count = 0
        self.miss_count = 0

    @staticmethod
    def _connect(cfg: ValkeyConfig):
        try:
            import redis
        except ImportError as exc:
            raise ImportError(
                "Das 'redis'-Paket ist nicht installiert. "
                "Bitte 'pip install redis' ausführen."
            ) from exc
        return redis.Redis.from_url(cfg.url, db=cfg.db, decode_responses=True)

    def get(self, query: str, categories: str = "") -> list[dict] | None:
        """Lies gecachte Suchergebnisse. None wenn nicht vorhanden."""
        if not self._cfg.enabled:
            return None
        key = _search_key(query, categories)
        raw = self._client.get(key)
        if raw is None:
            self.miss_count += 1
            return None
        self.hit_count += 1
        return json.loads(raw)

    def set(self, query: str, results: list[dict], categories: str = "") -> None:
        """Speichere Suchergebnisse mit TTL."""
        if not self._cfg.enabled:
            return
        key = _search_key(query, categories)
        self._client.setex(key, self._ttl, json.dumps(results))

    def clear_all(self) -> None:
        """Lösche alle Search-Cache-Einträge."""
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match="fng:search:*", count=200)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def stats(self) -> dict[str, Any]:
        """Cache-Statistiken."""
        if not self._cfg.enabled:
            return {"enabled": False}
        cursor, count = 0, 0
        while True:
            cursor, keys = self._client.scan(cursor, match="fng:search:*", count=200)
            count += len(keys)
            if cursor == 0:
                break
        total = self.hit_count + self.miss_count
        return {
            "enabled": True,
            "backend": "valkey",
            "total_entries": count,
            "ttl_hours": self._cfg.ttl_hours,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "hit_rate": self.hit_count / total if total > 0 else 0.0,
        }


class InMemorySearchCache:
    """In-Memory-Fallback wenn Valkey nicht verfügbar.

    Nutzt ein einfaches Dict mit TTL-Prüfung. Nicht persistent,
    aber verhindert wiederholte Anfragen innerhalb einer Session.
    """

    def __init__(self, cache_cfg: SearchCacheConfig) -> None:
        self._cfg = cache_cfg
        self._ttl = cache_cfg.ttl_hours * 3600
        self._store: dict[str, tuple[float, list[dict]]] = {}
        self.hit_count = 0
        self.miss_count = 0

    def get(self, query: str, categories: str = "") -> list[dict] | None:
        if not self._cfg.enabled:
            return None
        import time
        key = _search_key(query, categories)
        entry = self._store.get(key)
        if entry is None:
            self.miss_count += 1
            return None
        created_at, results = entry
        if time.time() - created_at > self._ttl:
            del self._store[key]
            self.miss_count += 1
            return None
        self.hit_count += 1
        return results

    def set(self, query: str, results: list[dict], categories: str = "") -> None:
        if not self._cfg.enabled:
            return
        import time
        key = _search_key(query, categories)
        self._store[key] = (time.time(), results)

    def clear_all(self) -> None:
        self._store.clear()

    def stats(self) -> dict[str, Any]:
        if not self._cfg.enabled:
            return {"enabled": False}
        total = self.hit_count + self.miss_count
        return {
            "enabled": True,
            "backend": "in-memory",
            "total_entries": len(self._store),
            "ttl_hours": self._cfg.ttl_hours,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "hit_rate": self.hit_count / total if total > 0 else 0.0,
        }
