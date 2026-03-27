"""Valkey/Redis-Backend für den ClaimCache.

Drop-in-Ersatz für tools/cache.ClaimCache – gleiche öffentliche Schnittstelle.

Vorteile gegenüber SQLite:
  - TTL nativ (kein TTL-Scan nötig)
  - Thread-/Prozess-sicher ohne Lock
  - Bereits im Docker-Stack (fakenewsguard-valkey)
  - Keine Datei-Locks, kein WAL-Overhead

Aktivierung: CACHE_BACKEND=valkey (Fallback: sqlite)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from config import CacheConfig, ValkeyConfig


def _claim_key(claim_text: str, agent_name: str, context: str = "") -> str:
    """Stabiler Cache-Key – identisch mit tools/cache._claim_key."""
    context_part = context.strip().lower()[:100] if context else ""
    raw = f"{agent_name}::{claim_text.strip().lower()}::{context_part}"
    return "fng:cache:" + hashlib.sha256(raw.encode()).hexdigest()


class ValkeyClaimCache:
    """Valkey-backed ClaimCache mit derselben Schnittstelle wie ClaimCache.

    Benötigt das ``redis``-Paket (kompatibel mit Valkey):
        pip install redis
    """

    def __init__(self, valkey_cfg: ValkeyConfig, cache_cfg: CacheConfig) -> None:
        self._cfg = cache_cfg
        self._ttl = cache_cfg.ttl_hours * 3600
        self._client = self._connect(valkey_cfg)

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

    # ── Öffentliche Schnittstelle (kompatibel mit ClaimCache) ─────────────────

    def get(self, claim_text: str, agent_name: str, context: str = "") -> dict | None:
        """Lies ein gecachtes Ergebnis. None wenn nicht vorhanden oder deaktiviert."""
        if not self._cfg.enabled:
            return None
        key = _claim_key(claim_text, agent_name, context)
        raw = self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, claim_text: str, agent_name: str, result: dict, context: str = "") -> None:
        """Speichere ein Ergebnis. TTL wird automatisch von Valkey verwaltet."""
        if not self._cfg.enabled:
            return
        key = _claim_key(claim_text, agent_name, context)
        self._client.setex(key, self._ttl, json.dumps(result))

    def delete(self, claim_text: str, agent_name: str, context: str = "") -> None:
        """Lösche einen einzelnen Cache-Eintrag."""
        key = _claim_key(claim_text, agent_name, context)
        self._client.delete(key)

    def clear_expired(self) -> int:
        """Valkey verwaltet Expiry selbst – diese Methode ist ein No-op.

        Gibt 0 zurück (keine manuell ablaufenden Einträge zu bereinigen).
        """
        return 0

    def clear_all(self) -> None:
        """Lösche alle FakeNewsGuard-Cache-Einträge (Prefix fng:cache:*)."""
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match="fng:cache:*", count=200)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def stats(self) -> dict[str, Any]:
        """Cache-Statistiken (approximiert via DBSIZE + INFO)."""
        if not self._cfg.enabled:
            return {"enabled": False}
        try:
            info = self._client.info("keyspace")
            # Approximation: zähle alle Keys im konfigurierten DB-Index
            db_key = f"db{self._client.connection_pool.connection_kwargs.get('db', 0)}"
            db_info = info.get(db_key, {})
            total = db_info.get("keys", 0)
        except Exception:
            total = -1
        return {
            "enabled": True,
            "backend": "valkey",
            "total_entries": total,
            "ttl_hours": self._cfg.ttl_hours,
        }
