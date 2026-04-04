"""URL-Content-Cache – session-scoped In-Memory-Cache für gescrapte URL-Inhalte.

Verhindert redundantes Scraping derselben URL bei verschiedenen Claims
innerhalb einer Analyse-Session.

Architektur:
    L1: In-Memory-Dict (schnell, session-scoped, nicht persistent)
    L2: Valkey (optional, überlebt Worker-Restarts, TTL-nativ)

Thread-Safety via threading.Lock auf dem In-Memory-Dict.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

from models.evidence_models import ScrapedContent


def _normalize_url(url: str) -> str:
    """Normalisiere URL für Cache-Key: lowercase, trailing slash entfernen, Query-Params sortieren."""
    parsed = urlparse(url.lower().rstrip("/"))
    sorted_query = urlencode(sorted(parse_qs(parsed.query, keep_blank_values=True).items()), doseq=True)
    return parsed._replace(query=sorted_query).geturl()


def _url_key(url: str) -> str:
    """SHA256-Hash der normalisierten URL."""
    return hashlib.sha256(_normalize_url(url).encode()).hexdigest()


class UrlContentCache:
    """In-Memory + optionaler Valkey L2 Cache für gescrapte URL-Inhalte.

    Lebt für die Dauer einer Analyse-Session (nicht persistent).
    Thread-safe via threading.Lock.

    Key-Schema: SHA256(_normalize_url(url))
    Valkey-Key: fng:urlcache:{sha256}
    """

    def __init__(self, valkey_config=None, ttl_seconds: int = 3600) -> None:
        self._store: dict[str, tuple[float, ScrapedContent]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0
        self._valkey = None

        if valkey_config is not None:
            try:
                import redis  # type: ignore[import-untyped]

                client = redis.from_url(valkey_config.url, db=valkey_config.db, socket_connect_timeout=2)
                client.ping()
                self._valkey = client
            except Exception:
                self._valkey = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, url: str) -> ScrapedContent | None:
        """Cache-Lookup. Gibt gecachten Content zurück oder None bei Miss."""
        key = _url_key(url)

        # L1: In-Memory
        with self._lock:
            if key in self._store:
                created_at, content = self._store[key]
                if time.time() - created_at <= self._ttl:
                    self._hits += 1
                    return content
                del self._store[key]

        # L2: Valkey
        if self._valkey is not None:
            try:
                raw = self._valkey.get(f"fng:urlcache:{key}")
                if raw:
                    data = json.loads(raw)
                    content = ScrapedContent(**data)
                    with self._lock:
                        self._store[key] = (time.time(), content)
                        self._hits += 1
                    return content
            except Exception:
                pass

        with self._lock:
            self._misses += 1
        return None

    def set(self, url: str, content: ScrapedContent) -> None:
        """Speichere Content in L1 (sync) und optional L2 (async fire-and-forget)."""
        key = _url_key(url)

        with self._lock:
            self._store[key] = (time.time(), content)

        if self._valkey is not None:
            try:
                payload = json.dumps({
                    "url": content.url,
                    "text": content.text,
                    "tier_label": content.tier_label,
                    "publish_date": content.publish_date,
                    "scraped_at": content.scraped_at,
                    "content_hash": content.content_hash,
                })
                threading.Thread(
                    target=self._valkey.setex,
                    args=(f"fng:urlcache:{key}", self._ttl, payload),
                    daemon=True,
                ).start()
            except Exception:
                pass

    def stats(self) -> dict:
        """Gibt Cache-Statistiken zurück: hits, misses, size."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._store),
            }
