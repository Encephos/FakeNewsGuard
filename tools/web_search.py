"""Web-Search-Abstraktionsschicht – unterstützt SearXNG, Tavily, Serper und Brave."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from config import RetryConfig, SearchConfig
from tools.retry import retry_call, retry_call_async


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""  # Volltext, falls verfügbar (Tavily)


class WebSearchClient:
    """Einheitliches Interface für verschiedene Search-APIs."""

    def __init__(self, config: SearchConfig, retry: RetryConfig | None = None) -> None:
        self.config = config
        self._retry = retry or RetryConfig()

    def search(
        self, query: str, max_results: int | None = None, categories: str = "general",
    ) -> list[SearchResult]:
        """Führe eine Websuche durch.

        Args:
            query: Suchbegriff.
            max_results: Überschreibt den Default aus der Config.
            categories: SearXNG-Kategorien (kommasepariert). Wird nur bei SearXNG genutzt.

        Returns:
            Liste von SearchResult-Objekten.
        """
        n = max_results or self.config.max_results

        if self.config.provider == "searxng":
            return self._search_searxng(query, n, categories=categories)
        elif self.config.provider == "tavily":
            return self._search_tavily(query, n)
        elif self.config.provider == "serper":
            return self._search_serper(query, n)
        elif self.config.provider == "brave":
            return self._search_brave(query, n)
        else:
            raise ValueError(f"Unbekannter Search-Provider: {self.config.provider}")

    # ── SearXNG (self-hosted) ────────────────────────────────────

    def _search_searxng(
        self, query: str, max_results: int, categories: str = "general",
    ) -> list[SearchResult]:
        def _call():
            resp = httpx.get(
                f"{self.config.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "pageno": 1,
                    "language": "de",
                    "categories": categories,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])[:max_results]
        ]

    # ── Tavily ───────────────────────────────────────────────────

    def _search_tavily(self, query: str, max_results: int) -> list[SearchResult]:
        def _call():
            resp = httpx.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "search_depth": "advanced",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:500],
                content=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]

    # ── Serper (Google) ──────────────────────────────────────────

    def _search_serper(self, query: str, max_results: int) -> list[SearchResult]:
        def _call():
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.config.api_key},
                json={"q": query, "num": max_results, "gl": "de", "hl": "de"},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
            )
            for r in data.get("organic", [])
        ]

    # ── Brave Search ─────────────────────────────────────────────

    def _search_brave(self, query: str, max_results: int) -> list[SearchResult]:
        def _call():
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.config.api_key,
                },
                params={"q": query, "count": max_results, "country": "DE"},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in data.get("web", {}).get("results", [])
        ]

    def multi_search(
        self, queries: list[str], max_results: int | None = None
    ) -> dict[str, list[SearchResult]]:
        """Führe mehrere Suchen durch und gib Ergebnisse nach Query gruppiert zurück."""
        results: dict[str, list[SearchResult]] = {}
        for query in queries:
            try:
                results[query] = self.search(query, max_results)
            except Exception as e:
                print(f"  ⚠ Suche fehlgeschlagen für '{query}': {type(e).__name__}", file=sys.stderr)
                results[query] = []
        return results

    @staticmethod
    def format_results_for_llm(results: list[SearchResult]) -> str:
        """Formatiere Suchergebnisse als Text-Kontext für das LLM."""
        if not results:
            return "Keine Suchergebnisse gefunden."

        parts: list[str] = []
        for i, r in enumerate(results, 1):
            text = r.content if r.content else r.snippet
            parts.append(
                f"[Quelle {i}] {r.title}\n"
                f"URL: {r.url}\n"
                f"Inhalt: {text}\n"
            )
        return "\n---\n".join(parts)


class AsyncWebSearchClient:
    """Async-Version des WebSearchClient – parallele Suchen über asyncio."""

    def __init__(self, config: SearchConfig, retry: RetryConfig | None = None) -> None:
        self.config = config
        self._retry = retry or RetryConfig()

    async def search_async(
        self, query: str, max_results: int | None = None, categories: str = "general",
    ) -> list[SearchResult]:
        """Führe eine Websuche asynchron durch."""
        n = max_results or self.config.max_results

        async with httpx.AsyncClient(timeout=30.0) as client:
            if self.config.provider == "searxng":
                return await self._search_searxng_async(client, query, n, categories=categories)
            elif self.config.provider == "tavily":
                return await self._search_tavily_async(client, query, n)
            elif self.config.provider == "serper":
                return await self._search_serper_async(client, query, n)
            elif self.config.provider == "brave":
                return await self._search_brave_async(client, query, n)
            else:
                raise ValueError(f"Unbekannter Search-Provider: {self.config.provider}")

    async def multi_search_async(
        self,
        queries: list[str],
        max_results: int | None = None,
        categories: str = "general",
    ) -> dict[str, list[SearchResult]]:
        """Führe mehrere Suchen parallel durch."""
        semaphore = asyncio.Semaphore(self.config.max_concurrent_searches)

        async def _bounded(query: str) -> tuple[str, list[SearchResult]]:
            async with semaphore:
                try:
                    results = await self.search_async(query, max_results, categories=categories)
                    return query, results
                except Exception as e:
                    print(
                        f"  ⚠ Async-Suche fehlgeschlagen für '{query}': {type(e).__name__}",
                        file=sys.stderr,
                    )
                    return query, []

        pairs = await asyncio.gather(*[_bounded(q) for q in queries])
        return dict(pairs)

    async def _search_searxng_async(
        self, client: httpx.AsyncClient, query: str, max_results: int,
        categories: str = "general",
    ) -> list[SearchResult]:
        async def _call():
            resp = await client.get(
                f"{self.config.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "pageno": 1,
                    "language": "de",
                    "categories": categories,
                },
            )
            resp.raise_for_status()
            return resp.json()

        data = await retry_call_async(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])[:max_results]
        ]

    async def _search_tavily_async(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        async def _call():
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "search_depth": "advanced",
                },
            )
            resp.raise_for_status()
            return resp.json()

        data = await retry_call_async(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "")[:500],
                content=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]

    async def _search_serper_async(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        async def _call():
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.config.api_key},
                json={"q": query, "num": max_results, "gl": "de", "hl": "de"},
            )
            resp.raise_for_status()
            return resp.json()

        data = await retry_call_async(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
            )
            for r in data.get("organic", [])
        ]

    async def _search_brave_async(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        async def _call():
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.config.api_key,
                },
                params={"q": query, "count": max_results, "country": "DE"},
            )
            resp.raise_for_status()
            return resp.json()

        data = await retry_call_async(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in data.get("web", {}).get("results", [])
        ]
