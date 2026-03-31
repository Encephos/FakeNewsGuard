"""Tavily-Client – KI-optimierte Websuche."""

from __future__ import annotations

import asyncio
import sys

import httpx

from config import RetryConfig, TavilyConfig
from config.infrastructure import HTTPTimeoutsConfig
from tools.retry import retry_call, retry_call_async
from tools.search.models import SearchResult

_SEARCH_TIMEOUT = HTTPTimeoutsConfig().search


class TavilyClient:
    """KI-optimierte Websuche via Tavily API.

    Wird im EvidenceBuilderAgent parallel zu LangSearch als primäre Quelle
    genutzt. Liefert hochrelevante Ergebnisse mit optionalem Volltext-Content.

    API-Dokumentation: https://docs.tavily.com
    """

    def __init__(
        self,
        config: TavilyConfig,
        retry: RetryConfig | None = None,
    ) -> None:
        self.config = config
        self._retry = retry or RetryConfig()

    def search(
        self, query: str, max_results: int | None = None
    ) -> list[SearchResult]:
        """Synchrone Suche."""
        if not self.config.enabled or not self.config.api_key:
            return []

        n = max_results or self.config.max_results

        def _call():
            resp = httpx.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "query": query,
                    "max_results": n,
                    "include_answer": False,
                    "include_raw_content": False,
                    "search_depth": self.config.search_depth,
                },
                timeout=_SEARCH_TIMEOUT,
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
            print(f"  ⚠ Tavily fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        return self._parse_response(data, n)

    async def search_async(
        self, query: str, max_results: int | None = None
    ) -> list[SearchResult]:
        """Asynchrone Suche."""
        if not self.config.enabled or not self.config.api_key:
            return []

        n = max_results or self.config.max_results

        async def _call():
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json={
                        "query": query,
                        "max_results": n,
                        "include_answer": False,
                        "include_raw_content": False,
                        "search_depth": self.config.search_depth,
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
            print(f"  ⚠ Tavily async fehlgeschlagen: {type(e).__name__}: {e}", file=sys.stderr)
            return []

        return self._parse_response(data, n)

    async def multi_search_async(
        self,
        queries: list[str],
        max_results: int | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Mehrere Suchen parallel."""
        semaphore = asyncio.Semaphore(3)

        async def _bounded(query: str) -> tuple[str, list[SearchResult]]:
            async with semaphore:
                results = await self.search_async(query, max_results)
                return query, results

        pairs = await asyncio.gather(*[_bounded(q) for q in queries])
        return dict(pairs)

    @staticmethod
    def _parse_response(data: dict, max_results: int) -> list[SearchResult]:
        results = []
        for r in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:500],
                content=r.get("content", ""),
            ))
        return results
