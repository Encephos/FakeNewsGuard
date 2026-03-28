"""SearXNG-Client – dedizierter Client für self-hosted SearXNG-Instanzen."""

from __future__ import annotations

import asyncio
import itertools
import random
import sys
import threading
from dataclasses import dataclass

import httpx

from config import RetryConfig, SearXNGConfig
from tools.retry import retry_call, retry_call_async
from tools.search.models import SearchResult


@dataclass
class SearXNGQuery:
    """SearXNG-Query mit optionalen Per-Query-Overrides für Engine- und Zeitraum-Routing.

    Ermöglicht gezieltes Routing:
      - Faktencheck-Queries → Nachrichten-Engines
      - Aktualitäts-sensitive Queries → time_range="year"
      - Referenz-Queries → wikipedia, wikidata
      - pageno=2 für Multi-Page-Suche (mehr Tiefe bei starken Queries)
    """

    query: str
    categories: list[str] | str | None = None
    engines: list[str] | None = None
    time_range: str | None = None
    pageno: int = 1


class SearXNGClient:
    """Dedizierter SearXNG-Client – explizit SearXNG-only, kein Provider-Routing.

    Features:
      - format=json zwingend (JSON-Ausgabe immer sichergestellt)
      - Kategorien konfigurierbar (general, news, etc.)
      - Engines konfigurierbar
      - categories-Parameter: str ("news,general") oder list[str] werden normalisiert
      - Retry-Robustheit via retry_call / retry_call_async (wie TavilyClient)
    """

    # Standard-Engine-Pool für Rotation (Web-Engines, keine Reference-Engines)
    _DEFAULT_ENGINE_POOL: list[str] = [
        "duckduckgo", "brave", "qwant", "startpage", "google",
        "yahoo", "bing", "mojeek", "yep", "presearch",
    ]

    def __init__(self, config: SearXNGConfig, retry: RetryConfig | None = None, search_cache=None) -> None:
        self.config = config
        self._retry = retry or RetryConfig()
        self._cache = search_cache

        # Engine-Rotation: Round-Robin über shuffled Pool
        if config.engine_rotation_enabled:
            pool = list(self._DEFAULT_ENGINE_POOL)
            random.shuffle(pool)
            self._engine_cycle = itertools.cycle(pool)
        else:
            self._engine_cycle = None
        self._rotation_lock = threading.Lock()

    def _select_engines(self) -> list[str] | None:
        """Wähle nächste N Engines aus dem Round-Robin-Pool."""
        if not self._engine_cycle:
            return None
        with self._rotation_lock:
            return [next(self._engine_cycle) for _ in range(self.config.engines_per_query)]

    def _build_params(
        self,
        query: str,
        max_results: int,
        categories: list[str] | str | None = None,
        engines: list[str] | None = None,
        time_range: str | None = None,
        pageno: int = 1,
    ) -> dict:
        """Baue SearXNG-Request-Parameter. Normalisiert categories: str → list.

        Priorität für Engines:
          1. Per-Query-Override (engines-Parameter) – höchste Priorität
          2. Engine-Rotation (wenn aktiviert) – wählt N Engines round-robin
          3. Config-Default (self.config.engines) – Fallback
        """
        if isinstance(categories, str):
            cats = [c.strip() for c in categories.split(",") if c.strip()] or self.config.categories
        else:
            cats = categories or self.config.categories
        params: dict = {
            "q": query,
            "format": "json",
            "pageno": pageno,
            "language": self.config.language,
            "categories": ",".join(cats),
        }
        # Engine-Auswahl: Per-Query > Rotation > Config-Default
        if engines:
            effective_engines = engines
        elif self.config.engine_rotation_enabled:
            effective_engines = self._select_engines()
        else:
            effective_engines = self.config.engines if self.config.engines else None
        if effective_engines:
            params["engines"] = ",".join(effective_engines)
        # Per-Query-Zeitraum überschreibt Config-Zeitraum
        effective_time_range = time_range or self.config.time_range
        if effective_time_range:
            params["time_range"] = effective_time_range
        return params

    @staticmethod
    def _parse_response(data: dict, max_results: int) -> list[SearchResult]:
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])[:max_results]
        ]

    def search(
        self,
        query: str,
        max_results: int | None = None,
        categories: list[str] | str | None = None,
    ) -> list[SearchResult]:
        """Synchrone SearXNG-Suche mit Cache + Retry."""
        n = max_results or self.config.max_results

        # Cache-Check
        cat_str = categories if isinstance(categories, str) else ",".join(categories) if categories else ""
        if self._cache:
            cached = self._cache.get(query, cat_str)
            if cached is not None:
                return [SearchResult(**r) for r in cached]

        params = self._build_params(query, n, categories)

        def _call():
            resp = httpx.get(
                f"{self.config.base_url}/search",
                params=params,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            data = retry_call(
                _call,
                max_attempts=self._retry.max_attempts,
                base_delay=self._retry.base_delay_s,
                max_delay=self._retry.max_delay_s,
                backoff_factor=self._retry.backoff_factor,
            )
        except Exception as e:
            print(f"  ⚠ SearXNG fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        results = self._parse_response(data, n)

        if self._cache and results:
            self._cache.set(
                query,
                [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
                cat_str,
            )

        return results

    async def search_async(
        self,
        query: str,
        max_results: int | None = None,
        categories: list[str] | str | None = None,
        engines: list[str] | None = None,
        time_range: str | None = None,
        pageno: int = 1,
    ) -> list[SearchResult]:
        """Asynchrone SearXNG-Suche mit Cache + Retry."""
        n = max_results or self.config.max_results

        # Cache-Check: Query + Kategorien als Key
        cat_str = categories if isinstance(categories, str) else ",".join(categories) if categories else ""
        if self._cache:
            cached = self._cache.get(query, cat_str)
            if cached is not None:
                return [SearchResult(**r) for r in cached]

        params = self._build_params(query, n, categories, engines=engines, time_range=time_range, pageno=pageno)

        async def _call():
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{self.config.base_url}/search",
                    params=params,
                )
                resp.raise_for_status()
                return resp.json()

        try:
            data = await retry_call_async(
                _call,
                max_attempts=self._retry.max_attempts,
                base_delay=self._retry.base_delay_s,
                max_delay=self._retry.max_delay_s,
                backoff_factor=self._retry.backoff_factor,
            )
        except Exception as e:
            print(f"  ⚠ SearXNG async fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        results = self._parse_response(data, n)

        # Cache-Set: Ergebnisse für zukünftige Anfragen speichern
        if self._cache and results:
            self._cache.set(
                query,
                [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
                cat_str,
            )

        return results

    async def multi_search_async(
        self,
        queries: list[str] | list[SearXNGQuery],
        max_results: int | None = None,
        categories: list[str] | str | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Mehrere SearXNG-Suchen parallel – Semaphore-kontrolliert.

        Akzeptiert sowohl list[str] (rückwärtskompatibel) als auch list[SearXNGQuery]
        für per-Query-Engine- und Zeitraum-Routing.
        """
        semaphore = asyncio.Semaphore(self.config.max_concurrent_searches)

        # Normalisiere str → SearXNGQuery für einheitliche Verarbeitung
        normalized: list[SearXNGQuery] = [
            q if isinstance(q, SearXNGQuery) else SearXNGQuery(query=q, categories=categories)
            for q in queries
        ]

        async def _bounded(sq: SearXNGQuery) -> tuple[str, list[SearchResult]]:
            async with semaphore:
                # Delay nach Semaphore-Acquire verhindert Engine-Suspendierung
                # durch zu viele gleichzeitige Anfragen (DuckDuckGo, Brave, Qwant)
                await asyncio.sleep(self.config.inter_query_delay)
                # Eindeutiger Key: query + pageno (damit pageno=1 und pageno=2 nicht kollidieren)
                key = sq.query if sq.pageno == 1 else f"{sq.query}__p{sq.pageno}"
                results = await self.search_async(
                    sq.query,
                    max_results,
                    sq.categories or categories,
                    engines=sq.engines,
                    time_range=sq.time_range,
                    pageno=sq.pageno,
                )
                return key, results

        pairs = await asyncio.gather(*[_bounded(sq) for sq in normalized])
        return dict(pairs)
