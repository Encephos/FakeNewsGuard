"""LangSearch-Client – semantische Websuche."""

from __future__ import annotations

import asyncio
import sys

import httpx

from config import LangSearchConfig, RetryConfig
from tools.retry import retry_call, retry_call_async
from tools.search.models import SearchResult


class LangSearchClient:
    """Semantische Websuche via LangSearch API.

    Wird im EvidenceBuilderAgent parallel zu SearXNG genutzt.
    Liefert semantisch gerankete Ergebnisse mit optionalem Summary-Snippet.

    API-Dokumentation: https://api.langsearch.com
    """

    def __init__(
        self,
        config: LangSearchConfig,
        retry: RetryConfig | None = None,
        search_cache=None,
    ) -> None:
        self.config = config
        self._retry = retry or RetryConfig()
        self._cache = search_cache

    def search(
        self, query: str, max_results: int | None = None
    ) -> list[SearchResult]:
        """Synchrone Suche."""
        if not self.config.enabled or not self.config.api_key:
            return []

        n = max_results or self.config.max_results

        # Cache-Check
        if self._cache:
            cached = self._cache.get(query, "langsearch")
            if cached is not None:
                return [SearchResult(**r) for r in cached]

        def _call():
            resp = httpx.post(
                f"{self.config.base_url}/web-search",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "freshness": "noLimit",
                    "summary": True,
                    "count": n,
                },
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
            print(f"  ⚠ LangSearch fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        results = self._parse_response(data, n)

        # Cache-Store
        if self._cache and results:
            self._cache.set(
                query,
                [{"title": r.title, "url": r.url, "snippet": r.snippet, "content": r.content} for r in results],
                "langsearch",
            )

        return results

    async def search_async(
        self, query: str, max_results: int | None = None
    ) -> list[SearchResult]:
        """Asynchrone Suche."""
        if not self.config.enabled or not self.config.api_key:
            return []

        n = max_results or self.config.max_results

        # Cache-Check
        if self._cache:
            cached = self._cache.get(query, "langsearch")
            if cached is not None:
                return [SearchResult(**r) for r in cached]

        async def _call():
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.config.base_url}/web-search",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": query,
                        "freshness": "noLimit",
                        "summary": True,
                        "count": n,
                    },
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
            print(f"  ⚠ LangSearch async fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        results = self._parse_response(data, n)

        # Cache-Store
        if self._cache and results:
            self._cache.set(
                query,
                [{"title": r.title, "url": r.url, "snippet": r.snippet, "content": r.content} for r in results],
                "langsearch",
            )

        return results

    async def multi_search_async(
        self,
        queries: list[str],
        max_results: int | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Mehrere Suchen parallel."""
        semaphore = asyncio.Semaphore(5)  # LangSearch hat großzügige API-Limits

        async def _bounded(query: str) -> tuple[str, list[SearchResult]]:
            async with semaphore:
                results = await self.search_async(query, max_results)
                return query, results

        pairs = await asyncio.gather(*[_bounded(q) for q in queries])
        return dict(pairs)

    @staticmethod
    def _parse_response(data: dict, max_results: int) -> list[SearchResult]:
        # LangSearch API wraps response in {"code":200,"data":{"webPages":...}}
        inner = data.get("data", data)
        results = []
        for r in inner.get("webPages", {}).get("value", [])[:max_results]:
            results.append(SearchResult(
                title=r.get("name", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                content=r.get("summary", ""),
            ))
        return results
