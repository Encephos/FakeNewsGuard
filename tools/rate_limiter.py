"""Token-Bucket Rate-Limiter für die FastAPI-Endpoints.

Begrenzt Analyse-Anfragen pro Client-IP, um API-Overload zu verhindern.
Leichtgewichtig, kein Redis nötig – in-memory mit automatischem Cleanup.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field

from config import RateLimitConfig


@dataclass
class _Bucket:
    """Ein Token-Bucket für eine einzelne Client-IP."""
    tokens: float
    last_refill: float
    max_tokens: float
    refill_rate: float  # Tokens pro Sekunde

    def consume(self) -> bool:
        """Versuche ein Token zu verbrauchen. Gibt True zurück bei Erfolg."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> float:
        """Sekunden bis zum nächsten verfügbaren Token."""
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.refill_rate


class RateLimiter:
    """In-memory Token-Bucket Rate-Limiter, thread-safe.

    Jede Client-IP bekommt einen eigenen Bucket. Alte Buckets werden
    periodisch aufgeräumt.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self.enabled = config.enabled
        self._max_tokens = float(config.burst)
        self._refill_rate = config.requests_per_minute / 60.0  # Tokens/Sekunde
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0  # Alle 5 Minuten alte Buckets entfernen

    def check(self, client_ip: str) -> tuple[bool, float]:
        """Prüfe ob ein Request erlaubt ist.

        Args:
            client_ip: Die IP-Adresse des Clients.

        Returns:
            (allowed, retry_after_seconds)
            - allowed=True: Request darf durch.
            - allowed=False: Rate-Limit erreicht, retry_after gibt an wann.
        """
        if not self.enabled:
            return True, 0.0

        with self._lock:
            self._maybe_cleanup()

            bucket = self._buckets.get(client_ip)
            if bucket is None:
                bucket = _Bucket(
                    tokens=self._max_tokens,
                    last_refill=time.monotonic(),
                    max_tokens=self._max_tokens,
                    refill_rate=self._refill_rate,
                )
                self._buckets[client_ip] = bucket

            if bucket.consume():
                return True, 0.0
            return False, bucket.retry_after

    def _maybe_cleanup(self) -> None:
        """Entferne Buckets die seit >10 Minuten nicht benutzt wurden."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - 600.0  # 10 Minuten Inaktivität
        stale = [ip for ip, b in self._buckets.items() if b.last_refill < cutoff]
        for ip in stale:
            del self._buckets[ip]
