"""Schutzschicht für Source-Adapter: Caching, Rate-Limits, Timeouts, Circuit-Breaker.

Dieses Modul bildet die "Sicherheits-Umhüllung" um die Adapter-Layer.
Nutzt bestehende Infrastruktur (tools/cache.py, tools/rate_limiter.py)
und erweitert sie um Source-spezifische Rate-Limits und Circuit-Breaker.

Architektur:
    AdapterGuardian (diese Datei)
    ├─ SourceCache (nutzt SQLite wie ClaimCache)
    ├─ SourceRateLimiter (per source_id + global)
    └─ CircuitBreaker (pro Source-Adapter)

Verwendung::

    guardian = AdapterGuardian(config)

    # Suche mit Schutz
    items = guardian.search(
        adapter=client,
        query="COVID-19 vaccine",
        max_results=10,
    )

    # Detaillierter Abruf
    detail = guardian.fetch_details(
        adapter=client,
        record_id="NCT04280705",
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Generic, Optional, TypeVar

from models.source_evidence import OfficialEvidenceItem

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Cache für Source-Ergebnisse ────────────────────────────────────────────────

def _source_cache_key(
    source_id: str,
    query: str,
    record_id: str = "",
) -> str:
    """Generiere stabilen Cache-Key für Source-Ergebnisse.

    Args:
        source_id:  Registry-ID der Quelle
        query:      Suchtext (oder leer für detail fetch)
        record_id:  Für fetch_details()

    Returns:
        SHA256 Hash als String
    """
    key_parts = [source_id]
    if record_id:
        key_parts.append(f"id:{record_id}")
    elif query:
        key_parts.append(f"q:{query.strip().lower()[:100]}")

    raw = "|".join(key_parts)
    return hashlib.sha256(raw.encode()).hexdigest()


class SourceCache:
    """SQLite-basierter Cache für Source-API-Antworten.

    Trennung von ClaimCache: Quellen-Ergebnisse sind länger haltbar (policy-abhängig)
    und haben andere TTL-Semantik (z.B. 7 Tage für Eurostat, 1 Tag für ClinicalTrials).
    """

    def __init__(self, db_path: str, default_ttl_hours: int = 24) -> None:
        self.db_path = db_path
        self.default_ttl_seconds = default_ttl_hours * 3600
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy-Verbindung mit WAL."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self) -> None:
        """Erstelle Cache-Tabelle falls nicht vorhanden."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_cache (
                    cache_key      TEXT PRIMARY KEY,
                    source_id      TEXT NOT NULL,
                    query          TEXT,
                    record_id      TEXT,
                    result_json    TEXT NOT NULL,
                    ttl_hours      INTEGER NOT NULL,
                    created_at     REAL NOT NULL
                )
                """
            )
            # Indizes separat erstellen
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source ON source_cache(source_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires ON source_cache(created_at)"
            )
            conn.commit()

    def get(self, source_id: str, query: str = "", record_id: str = "") -> list[OfficialEvidenceItem] | None:
        """Abruf gecachter Ergebnisse.

        Returns:
            Liste von OfficialEvidenceItem oder None wenn kein/abgelaufener Cache
        """
        key = _source_cache_key(source_id, query, record_id)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT result_json, ttl_hours, created_at FROM source_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()

        if not row:
            return None

        result_json, ttl_hours, created_at = row
        age_seconds = time.time() - created_at
        if age_seconds > ttl_hours * 3600:
            # Abgelaufen → löschen
            self.delete(source_id, query, record_id)
            return None

        try:
            data = json.loads(result_json)
            return [OfficialEvidenceItem(**item) for item in data]
        except (json.JSONDecodeError, ValueError):
            logger.warning("Corrupted source cache entry: %s", key)
            return None

    def set(
        self,
        source_id: str,
        results: list[OfficialEvidenceItem],
        query: str = "",
        record_id: str = "",
        ttl_hours: int | None = None,
    ) -> None:
        """Speichere Ergebnisse im Cache.

        Args:
            source_id:  Registry-ID
            results:    Liste normalisierter Evidence-Items
            query:      Suchtext
            record_id:  Für detail lookups
            ttl_hours:  Überschreibe Default-TTL (z.B. 24 für dynamic data, 336 für static)
        """
        if not results:
            return

        key = _source_cache_key(source_id, query, record_id)
        ttl = ttl_hours if ttl_hours is not None else 24  # Default: 1 Tag

        result_json = json.dumps(
            [item.model_dump(mode="json") for item in results],
            default=str,  # Fallback für non-JSON-serializable
        )

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO source_cache
                (cache_key, source_id, query, record_id, result_json, ttl_hours, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (key, source_id, query, record_id, result_json, ttl, time.time()),
            )
            conn.commit()
        logger.debug("Cached source results: %s (ttl=%dh)", source_id, ttl)

    def delete(self, source_id: str, query: str = "", record_id: str = "") -> None:
        """Lösche Cache-Eintrag."""
        key = _source_cache_key(source_id, query, record_id)
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM source_cache WHERE cache_key = ?", (key,))
            conn.commit()

    def clear_expired(self) -> int:
        """Lösche abgelaufene Einträge."""
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """
                DELETE FROM source_cache
                WHERE created_at + (ttl_hours * 3600) < ?
                """,
                (now,),
            )
            conn.commit()
            return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        """Cache-Statistiken."""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0]
            per_source = conn.execute(
                "SELECT source_id, COUNT(*) FROM source_cache GROUP BY source_id"
            ).fetchall()

        return {
            "total_entries": total,
            "entries_per_source": {sid: count for sid, count in per_source},
        }


# ── Source-spezifische Rate-Limits ──────────────────────────────────────────────

class SourceRateLimiter:
    """Token-Bucket Rate-Limiter pro Source-Adapter.

    Nutzt Konfiguration aus SourceConfig.rate_limit_rps.
    """

    def __init__(self) -> None:
        self._limiters: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def get_limiter(self, source_id: str, rate_rps: Optional[float]) -> TokenBucket:
        """Abruf/Erzeuge Limiter für eine Quelle."""
        if rate_rps is None or rate_rps <= 0:
            # Unbegrenzt
            return TokenBucket(capacity=float("inf"), refill_rate=float("inf"))

        with self._lock:
            if source_id not in self._limiters:
                self._limiters[source_id] = TokenBucket(
                    capacity=max(1.0, rate_rps),  # Burst: 1 Sekunde Daten
                    refill_rate=rate_rps,
                )
            return self._limiters[source_id]

    def acquire(self, source_id: str, rate_rps: Optional[float]) -> tuple[bool, float]:
        """Versuche ein Token zu konsumieren.

        Returns:
            (allowed, retry_after_seconds)
        """
        limiter = self.get_limiter(source_id, rate_rps)
        return limiter.try_consume()


@dataclass
class TokenBucket:
    """Einfacher Token-Bucket für Rate-Limiting."""
    capacity: float
    refill_rate: float  # Tokens pro Sekunde
    tokens: float = field(default_factory=lambda: 1.0)
    last_refill: float = field(default_factory=time.monotonic)

    def try_consume(self) -> tuple[bool, float]:
        """Versuche 1 Token zu konsumieren.

        Returns:
            (success, retry_after_seconds)
        """
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0

        # Berechne Wartezeit bis nächstes Token
        retry_after = (1.0 - self.tokens) / self.refill_rate
        return False, retry_after


# ── Circuit Breaker für Quellen ────────────────────────────────────────────────

class CircuitBreakerState(Enum):
    """Circuit-Breaker-Zustände."""
    CLOSED = "closed"        # Normal, Anfragen durchlassen
    OPEN = "open"            # Zu viele Fehler, Anfragen blockiert
    HALF_OPEN = "half_open"  # Testet ob Source wieder ok


class CircuitBreaker:
    """Circuit-Breaker pro Source-Adapter.

    Verhindert Cascade-Fehler: wenn eine API down ist,
    stoppen wir schnell statt endlos zu versuchen.

    Zustände:
    - CLOSED: Normal, 1 Token pro Request
    - OPEN: Fehlerrate > threshold, blockiert alle Anfragen
    - HALF_OPEN: Nach timeout, erlaubt 1 Test-Anfrage
    """

    def __init__(
        self,
        source_id: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.source_id = source_id
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds

        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float | None = None
        self._lock = threading.Lock()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Rufe Funktion mit Circuit-Breaker-Schutz auf."""
        # Prüfe State ohne Lock zu halten (für lange Operationen)
        if self.state == CircuitBreakerState.OPEN:
            if time.monotonic() - (self.last_failure_time or 0) > self.timeout_seconds:
                with self._lock:
                    if self.state == CircuitBreakerState.OPEN:  # Double-check
                        logger.info("CircuitBreaker %s: HALF_OPEN (testing)", self.source_id)
                        self.state = CircuitBreakerState.HALF_OPEN
                        self.success_count = 0
            else:
                raise CircuitBreakerError(
                    f"Circuit breaker OPEN for {self.source_id}; retry after {self.timeout_seconds}s"
                )

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            raise

    def _record_success(self) -> None:
        with self._lock:
            self.failure_count = 0
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    logger.info("CircuitBreaker %s: CLOSED (recovered)", self.source_id)
                    self.state = CircuitBreakerState.CLOSED
                    self.success_count = 0

    def _record_failure(self) -> None:
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                logger.warning(
                    "CircuitBreaker %s: OPEN (failures=%d)",
                    self.source_id,
                    self.failure_count,
                )
                self.state = CircuitBreakerState.OPEN
                self.success_count = 0

    def reset(self) -> None:
        """Manuelles Reset (für Tests)."""
        with self._lock:
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None


class CircuitBreakerError(Exception):
    """Circuit Breaker ist offen."""
    pass


# ── AdapterGuardian: Zentrale Schutzschicht ────────────────────────────────────

class AdapterGuardian:
    """Umhüllung aller Source-Adapter mit Caching, Rate-Limits und Circuit-Breaker.

    Nutzt:
    - SourceCache (SQLite mit TTL)
    - SourceRateLimiter (Token-Bucket per Source)
    - CircuitBreaker (pro Adapter)

    Logging:
    - DEBUG: Cache hits/misses, rate-limit details
    - INFO: Circuit breaker state changes
    - WARNING: Rate-limit exceeded, circuit breaker opens
    - ERROR: Unerwartete Fehler
    """

    def __init__(
        self,
        cache_db: str = "/tmp/source_cache.db",
        default_ttl_hours: int = 24,
    ) -> None:
        self.cache = SourceCache(cache_db, default_ttl_hours)
        self.rate_limiter = SourceRateLimiter()
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_circuit_breaker(self, source_id: str) -> CircuitBreaker:
        """Abruf/Erzeuge Circuit-Breaker für Quelle."""
        with self._lock:
            if source_id not in self.circuit_breakers:
                self.circuit_breakers[source_id] = CircuitBreaker(source_id)
            return self.circuit_breakers[source_id]

    def search(
        self,
        adapter: Any,  # BaseSourceAdapter
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
        use_cache: bool = True,
    ) -> list[OfficialEvidenceItem]:
        """Suche mit allen Schutzmaßnahmen.

        Args:
            adapter:      Source-Adapter-Instanz
            query:        Suchtext
            max_results:  Maximale Ergebnisse
            page:         Seite für Pagination
            use_cache:    Cache-Nutzung aktivieren

        Returns:
            Liste normalisierter Evidence-Items
        """
        source_id = adapter.config.source_id

        # 1. Cache-Check
        if use_cache:
            cached = self.cache.get(source_id, query)
            if cached:
                logger.debug("Source cache HIT: %s (query=%s)", source_id, query[:50])
                return cached[:max_results]
            logger.debug("Source cache MISS: %s", source_id)

        # 2. Rate-Limit-Check
        allowed, retry_after = self.rate_limiter.acquire(
            source_id,
            adapter.config.rate_limit_rps,
        )
        if not allowed:
            logger.warning(
                "Rate limit exceeded for %s; retry after %.2fs",
                source_id,
                retry_after,
            )
            return []

        # 3. Circuit-Breaker-Schutz
        cb = self.get_circuit_breaker(source_id)
        try:
            results = cb.call(
                adapter.search,
                query,
                max_results=max_results,
                page=page,
            )
        except CircuitBreakerError as exc:
            logger.error("CircuitBreaker error for %s: %s", source_id, exc)
            return []
        except Exception as exc:
            logger.error("Search failed for %s: %s", source_id, exc)
            return []

        # 4. Cache-Speicherung
        if use_cache and results:
            ttl = self._get_ttl_for_source(source_id)
            self.cache.set(source_id, results, query=query, ttl_hours=ttl)

        return results

    def fetch_details(
        self,
        adapter: Any,  # BaseSourceAdapter
        record_id: str,
        use_cache: bool = True,
    ) -> OfficialEvidenceItem | None:
        """Detail-Abruf mit allen Schutzmaßnahmen.

        Args:
            adapter:      Source-Adapter-Instanz
            record_id:    Nativer Primärschlüssel
            use_cache:    Cache-Nutzung aktivieren

        Returns:
            Normalisiertes Evidence-Item oder None
        """
        source_id = adapter.config.source_id

        # 1. Cache-Check
        if use_cache:
            cached = self.cache.get(source_id, record_id=record_id)
            if cached:
                logger.debug("Source cache HIT: %s (id=%s)", source_id, record_id)
                return cached[0] if cached else None
            logger.debug("Source cache MISS: %s (id=%s)", source_id, record_id)

        # 2. Rate-Limit-Check
        allowed, retry_after = self.rate_limiter.acquire(
            source_id,
            adapter.config.rate_limit_rps,
        )
        if not allowed:
            logger.warning(
                "Rate limit exceeded for %s; retry after %.2fs",
                source_id,
                retry_after,
            )
            return None

        # 3. Circuit-Breaker-Schutz
        cb = self.get_circuit_breaker(source_id)
        try:
            result = cb.call(adapter.fetch_details, record_id)
        except CircuitBreakerError as exc:
            logger.error("CircuitBreaker error for %s: %s", source_id, exc)
            return None
        except Exception as exc:
            logger.error("Fetch details failed for %s: %s", source_id, exc)
            return None

        # 4. Cache-Speicherung
        if use_cache and result:
            ttl = self._get_ttl_for_source(source_id)
            self.cache.set(source_id, [result], record_id=record_id, ttl_hours=ttl)

        return result

    @staticmethod
    def _get_ttl_for_source(source_id: str) -> int:
        """TTL-Heuristik basierend auf Source-Typ.

        Statische Quellen (Gesetze, Patente) → lange TTL
        Dynamische Quellen (ClinicalTrials, FDA) → kurze TTL
        """
        long_ttl_sources = {
            "eur_lex", "uspto", "gleif", "companies_house",
            "cern_open_data", "arxiv", "pubmed", "crossref",
        }
        if source_id in long_ttl_sources:
            return 168  # 1 Woche
        return 24  # 1 Tag (Standard für dinamische Daten)

    def stats(self) -> dict[str, Any]:
        """Gesamtstatistiken für alle Schutzmechanismen."""
        return {
            "cache": self.cache.stats(),
            "circuit_breakers": {
                sid: {
                    "state": cb.state.value,
                    "failures": cb.failure_count,
                    "successes": cb.success_count,
                }
                for sid, cb in self.circuit_breakers.items()
            },
        }
