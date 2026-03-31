"""SQLite-basierter Claim-Cache mit TTL-Unterstützung und optionalem semantischem Matching.

Semantic Cache:
    Wenn sentence-transformers installiert ist und semantic_cache=True in der Config:
    - Beim set() wird zusätzlich ein Embedding des Claim-Texts gespeichert
    - Beim get() wird bei Cache-Miss eine Cosine-Similarity-Suche als Fallback gemacht
    - Threshold: 0.92 Cosine-Similarity für einen Match

    Graceful Degradation: Ohne sentence-transformers funktioniert der Cache
    wie bisher (nur exakter SHA256-Key-Match).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import struct
import threading
import time
from typing import Any

from config import CacheConfig

logger = logging.getLogger("fng.cache")

# ── Optionale Embedding-Unterstützung ────────────────────────────────────────

_embedding_model = None
_embedding_lock = threading.Lock()


def _get_embedding_model():
    """Lazy-Load des Embedding-Modells (nur beim ersten Aufruf)."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    with _embedding_lock:
        if _embedding_model is not None:
            return _embedding_model
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Semantic cache: all-MiniLM-L6-v2 geladen")
        except ImportError:
            _embedding_model = False  # Sentinel: nicht verfügbar
            logger.debug("Semantic cache nicht verfügbar (sentence-transformers fehlt)")
    return _embedding_model


def _encode_text(text: str) -> bytes | None:
    """Erzeuge ein Embedding als gepackte floats. None wenn nicht verfügbar."""
    model = _get_embedding_model()
    if model is False:
        return None
    vec = model.encode(text.strip().lower(), normalize_embeddings=True)
    return struct.pack(f"{len(vec)}f", *vec)


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine-Similarity zwischen zwei gepackten float-Vektoren."""
    n = len(a) // 4
    va = struct.unpack(f"{n}f", a)
    vb = struct.unpack(f"{n}f", b)
    dot = sum(x * y for x, y in zip(va, vb))
    return dot  # Normalisierte Vektoren: dot == cosine similarity


def _claim_key(
    claim_text: str,
    agent_name: str,
    context: str = "",
    canonical_text: str | None = None,
    use_canonical: bool = False,
) -> str:
    """Erstelle einen stabilen Cache-Key aus Claim-Text, Agent-Name und Kontext.

    Der Kontext (erste 100 Zeichen) wird mit einbezogen, damit gleicher
    Claim-Text in unterschiedlichem Kontext separate Einträge erhält.

    Wenn use_canonical=True und canonical_text vorhanden, wird der kanonische
    Text statt des Roh-Texts verwendet. Damit erzeugen unterschiedliche
    Formulierungen desselben Claims denselben Cache-Key.
    """
    text = canonical_text if (use_canonical and canonical_text) else claim_text
    context_part = context.strip().lower()[:100] if context else ""
    raw = f"{agent_name}::{text.strip().lower()}::{context_part}"
    return hashlib.sha256(raw.encode()).hexdigest()


class ClaimCache:
    """Thread-safe SQLite-Cache für Fact-Check und Number-Audit Ergebnisse.

    Hält eine persistente Verbindung (WAL-Modus) statt pro Operation
    eine neue zu erstellen.  Thread-Safety via threading.Lock.
    """

    # Cosine-Similarity-Schwelle für semantische Cache-Treffer
    SEMANTIC_THRESHOLD = 0.92

    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self._db_path = config.db_path
        self._ttl_seconds = config.ttl_hours * 3600
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._semantic_enabled = getattr(config, "semantic_cache", False)
        if config.enabled:
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy-Singleton-Verbindung mit WAL-Modus."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS claim_embeddings (
                    cache_key  TEXT PRIMARY KEY,
                    embedding  BLOB NOT NULL,
                    FOREIGN KEY (cache_key) REFERENCES claim_cache(cache_key) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    def get(
        self,
        claim_text: str,
        agent_name: str,
        context: str = "",
        canonical_text: str | None = None,
        use_canonical: bool = False,
    ) -> dict | None:
        """Lies ein gecachtes Ergebnis.  Gibt None zurück, wenn kein gültiger Cache."""
        if not self.config.enabled:
            return None

        key = _claim_key(claim_text, agent_name, context, canonical_text, use_canonical)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT result_json, created_at FROM claim_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()

        if row is None:
            # Fallback: semantische Suche (wenn aktiviert)
            if self._semantic_enabled:
                return self._semantic_lookup(claim_text, agent_name)
            return None

        result_json, created_at = row
        age = time.time() - created_at
        if age > self._ttl_seconds:
            self.delete(claim_text, agent_name, context, canonical_text, use_canonical)
            return None

        return json.loads(result_json)

    def set(
        self,
        claim_text: str,
        agent_name: str,
        result: dict,
        context: str = "",
        canonical_text: str | None = None,
        use_canonical: bool = False,
    ) -> None:
        """Speichere ein Ergebnis im Cache."""
        if not self.config.enabled:
            return

        key = _claim_key(claim_text, agent_name, context, canonical_text, use_canonical)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO claim_cache (cache_key, agent_name, result_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, agent_name, json.dumps(result), time.time()),
            )
            # Embedding speichern für semantische Suche
            if self._semantic_enabled:
                emb = _encode_text(claim_text)
                if emb is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO claim_embeddings (cache_key, embedding) "
                        "VALUES (?, ?)",
                        (key, emb),
                    )
            conn.commit()

    def _semantic_lookup(self, claim_text: str, agent_name: str) -> dict | None:
        """Finde einen semantisch ähnlichen Cache-Eintrag via Embedding-Similarity."""
        query_emb = _encode_text(claim_text)
        if query_emb is None:
            return None

        cutoff = time.time() - self._ttl_seconds
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT e.cache_key, e.embedding, c.result_json "
                "FROM claim_embeddings e "
                "JOIN claim_cache c ON e.cache_key = c.cache_key "
                "WHERE c.agent_name = ? AND c.created_at >= ?",
                (agent_name, cutoff),
            ).fetchall()

        best_score = 0.0
        best_result = None
        for _, emb_bytes, result_json in rows:
            try:
                score = _cosine_similarity(query_emb, emb_bytes)
            except (struct.error, ZeroDivisionError):
                continue
            if score > best_score:
                best_score = score
                best_result = result_json

        if best_score >= self.SEMANTIC_THRESHOLD and best_result is not None:
            logger.debug(
                "Semantic cache hit: score=%.3f für '%s'",
                best_score, claim_text[:50],
            )
            return json.loads(best_result)

        return None

    def delete(
        self,
        claim_text: str,
        agent_name: str,
        context: str = "",
        canonical_text: str | None = None,
        use_canonical: bool = False,
    ) -> None:
        """Lösche einen einzelnen Cache-Eintrag."""
        key = _claim_key(claim_text, agent_name, context, canonical_text, use_canonical)
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM claim_cache WHERE cache_key = ?", (key,))
            conn.commit()

    def clear_expired(self) -> int:
        """Lösche alle abgelaufenen Einträge und gib deren Anzahl zurück."""
        if not self.config.enabled:
            return 0

        cutoff = time.time() - self._ttl_seconds
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "DELETE FROM claim_cache WHERE created_at < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount

    def clear_all(self) -> None:
        """Lösche den gesamten Cache."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM claim_cache")
            conn.commit()

    def stats(self) -> dict[str, Any]:
        """Gib Cache-Statistiken zurück."""
        if not self.config.enabled:
            return {"enabled": False}

        with self._lock:
            conn = self._get_conn()
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
