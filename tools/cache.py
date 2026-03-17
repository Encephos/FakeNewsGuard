"""SQLite-basierter Claim-Cache mit TTL-Unterstützung."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from config import CacheConfig


def _claim_key(claim_text: str, agent_name: str) -> str:
    """Erstelle einen stabilen Cache-Key aus Claim-Text und Agent-Name."""
    raw = f"{agent_name}::{claim_text.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()


class ClaimCache:
    """Thread-safe SQLite-Cache für Fact-Check und Number-Audit Ergebnisse."""

    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self._db_path = config.db_path
        self._ttl_seconds = config.ttl_hours * 3600
        if config.enabled:
            self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS claim_cache (
                    cache_key  TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at  REAL NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")  # Bessere Concurrency
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get(self, claim_text: str, agent_name: str) -> dict | None:
        """Lies ein gecachtes Ergebnis.  Gibt None zurück, wenn kein gültiger Cache."""
        if not self.config.enabled:
            return None

        key = _claim_key(claim_text, agent_name)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json, created_at FROM claim_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return None

        result_json, created_at = row
        age = time.time() - created_at
        if age > self._ttl_seconds:
            self.delete(claim_text, agent_name)
            return None

        return json.loads(result_json)

    def set(self, claim_text: str, agent_name: str, result: dict) -> None:
        """Speichere ein Ergebnis im Cache."""
        if not self.config.enabled:
            return

        key = _claim_key(claim_text, agent_name)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claim_cache (cache_key, agent_name, result_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, agent_name, json.dumps(result), time.time()),
            )

    def delete(self, claim_text: str, agent_name: str) -> None:
        """Lösche einen einzelnen Cache-Eintrag."""
        key = _claim_key(claim_text, agent_name)
        with self._connect() as conn:
            conn.execute("DELETE FROM claim_cache WHERE cache_key = ?", (key,))

    def clear_expired(self) -> int:
        """Lösche alle abgelaufenen Einträge und gib deren Anzahl zurück."""
        if not self.config.enabled:
            return 0

        cutoff = time.time() - self._ttl_seconds
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM claim_cache WHERE created_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def clear_all(self) -> None:
        """Lösche den gesamten Cache."""
        with self._connect() as conn:
            conn.execute("DELETE FROM claim_cache")

    def stats(self) -> dict[str, Any]:
        """Gib Cache-Statistiken zurück."""
        if not self.config.enabled:
            return {"enabled": False}

        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM claim_cache").fetchone()[0]
            cutoff = time.time() - self._ttl_seconds
            valid = conn.execute(
                "SELECT COUNT(*) FROM claim_cache WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]

        return {
            "enabled": True,
            "total_entries": total,
            "valid_entries": valid,
            "expired_entries": total - valid,
            "ttl_hours": self.config.ttl_hours,
        }
