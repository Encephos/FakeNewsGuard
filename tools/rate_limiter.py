"""Rate-Limiter für die FastAPI-Endpoints.

Zwei Implementierungen:
  - RateLimiter:        In-memory Token-Bucket pro Prozess (kein Redis nötig).
  - ValkeyRateLimiter:  Verteiltes Sliding-Window via Valkey/Redis (prozessübergreifend).

Über create_rate_limiter() wird automatisch die passende Implementierung gewählt.
"""

from __future__ import annotations

import logging
import time
import threading
import uuid
from dataclasses import dataclass, field

from config import RateLimitConfig

logger = logging.getLogger("fng.rate_limiter")


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


# ── Lua-Skript für atomares Sliding-Window ────────────────────────────────────
_SLIDING_WINDOW_SCRIPT = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]
local ttl    = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, ttl)
    return {1, 0}
else
    redis.call('EXPIRE', key, ttl)
    local oldest = redis.call('ZRANGE', key, '0', '0', 'WITHSCORES')
    local oldest_score = (oldest[2] and tonumber(oldest[2])) or now
    return {0, oldest_score}
end
"""


class ValkeyRateLimiter:
    """Verteilter Sliding-Window Rate-Limiter via Valkey/Redis.

    Nutzt ein Sorted-Set pro Client-IP. Jeder Request wird als Element mit
    dem aktuellen Timestamp als Score gespeichert. Einträge älter als das
    Fenster (60 s) werden vor jedem Check entfernt. Das gesamte Prüf- und
    Schreib-Protokoll läuft atomar in einem Lua-Skript.

    Das check()-Interface ist identisch mit RateLimiter.
    """

    _WINDOW: float = 60.0   # Sliding-Window in Sekunden
    _TTL: int = 120          # Redis-Key TTL = Window + Buffer

    def __init__(self, config: RateLimitConfig, valkey_cfg) -> None:
        self.enabled = config.enabled
        self._limit = config.requests_per_minute
        self._client = self._connect(valkey_cfg)
        self._sha: str | None = None  # gecachte SCRIPT LOAD SHA

    @staticmethod
    def _connect(cfg):
        import redis
        return redis.Redis.from_url(cfg.url, db=cfg.db, decode_responses=False)

    def check(self, client_ip: str) -> tuple[bool, float]:
        """Prüfe ob ein Request erlaubt ist (Sliding-Window, prozessübergreifend).

        Returns:
            (allowed, retry_after_seconds)
        """
        if not self.enabled:
            return True, 0.0

        key = f"fng:rl:{client_ip}"
        now = time.time()
        member = f"{now:.6f}:{uuid.uuid4().hex}"

        try:
            result = self._eval(key, now, member)
            if result[0]:
                return True, 0.0
            oldest_score = float(result[1])
            retry_after = max(0.0, oldest_score + self._WINDOW - now)
            return False, retry_after
        except Exception:
            logger.warning("ValkeyRateLimiter: Redis-Fehler, Request wird durchgelassen",
                           exc_info=True)
            return True, 0.0  # fail-open

    def _eval(self, key: str, now: float, member: str) -> list:
        """Führt das Lua-Skript aus. Nutzt EVALSHA nach dem ersten EVAL."""
        args = [str(now), str(self._WINDOW), str(self._limit), member, str(self._TTL)]
        if self._sha is not None:
            try:
                return self._client.evalsha(self._sha, 1, key, *args)
            except Exception as exc:
                # NOSCRIPT: Skript nicht mehr im Cache → neu laden
                if "NOSCRIPT" not in str(exc):
                    raise
                self._sha = None
        result = self._client.eval(_SLIDING_WINDOW_SCRIPT, 1, key, *args)
        self._sha = self._client.script_load(_SLIDING_WINDOW_SCRIPT)
        return result


def create_rate_limiter(
    config: RateLimitConfig,
    valkey_cfg=None,
) -> RateLimiter | ValkeyRateLimiter:
    """Factory: gibt ValkeyRateLimiter zurück wenn Valkey erreichbar, sonst RateLimiter.

    Args:
        config:     RateLimitConfig (RPM, Burst, enabled).
        valkey_cfg: ValkeyConfig oder None. Bei None wird immer In-Memory verwendet.

    Returns:
        ValkeyRateLimiter wenn Valkey konfiguriert und erreichbar, sonst RateLimiter.
    """
    if valkey_cfg is not None and getattr(valkey_cfg, "enabled", False):
        try:
            limiter = ValkeyRateLimiter(config, valkey_cfg)
            limiter._client.ping()
            logger.info("Rate-Limiter: ValkeyRateLimiter aktiv (%s)", valkey_cfg.url)
            return limiter
        except Exception:
            logger.warning(
                "Rate-Limiter: Valkey nicht erreichbar – Fallback auf In-Memory RateLimiter"
            )
    return RateLimiter(config)
