"""Unit-Tests für tools/search/client.py – WebSearchClient und AsyncWebSearchClient.

Strategie: respx.mock patcht httpx auf Transport-Ebene, keine echten HTTP-Calls.
asyncio_mode=auto → async def test_... ohne Dekorator.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from config import RetryConfig, SearchConfig


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _searxng_config() -> SearchConfig:
    return SearchConfig(
        provider="searxng",
        base_url="http://searxng.test",
        max_results=10,
        max_concurrent_searches=3,
    )


def _provider_config(provider: str, api_key: str = "test-key") -> SearchConfig:
    return SearchConfig(
        provider=provider,
        api_key=api_key,
        base_url="",
        max_results=5,
        max_concurrent_searches=3,
    )


def _no_retry() -> RetryConfig:
    return RetryConfig(max_attempts=1, base_delay_s=0.0)


# ── SearXNG ───────────────────────────────────────────────────────────────────


class TestWebSearchClientSearXNG:
    def test_searxng_returns_search_results(self):
        from tools.search.client import WebSearchClient

        with respx.mock:
            respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(
                    200,
                    json={"results": [{"title": "T1", "url": "http://a.de", "content": "S1"}]},
                )
            )
            client = WebSearchClient(_searxng_config(), _no_retry())
            results = client.search("test query", max_results=5)
        assert len(results) == 1
        assert results[0].title == "T1"

    def test_searxng_maps_result_fields_correctly(self):
        from tools.search.client import WebSearchClient

        with respx.mock:
            respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "results": [
                            {"title": "Titel", "url": "https://example.com", "content": "Inhalt"}
                        ]
                    },
                )
            )
            client = WebSearchClient(_searxng_config(), _no_retry())
            r = client.search("q")[0]
        assert r.title == "Titel"
        assert r.url == "https://example.com"
        assert r.snippet == "Inhalt"

    def test_searxng_respects_max_results_limit(self):
        from tools.search.client import WebSearchClient

        items = [{"title": f"T{i}", "url": f"http://x.de/{i}", "content": ""} for i in range(10)]
        with respx.mock:
            respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(200, json={"results": items})
            )
            client = WebSearchClient(_searxng_config(), _no_retry())
            results = client.search("q", max_results=3)
        assert len(results) == 3

    def test_searxng_passes_categories_param(self):
        from tools.search.client import WebSearchClient

        with respx.mock:
            route = respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            client = WebSearchClient(_searxng_config(), _no_retry())
            client.search("q", categories="news")
        # categories=news soll als Query-Parameter übergeben werden
        request = route.calls[0].request
        assert "news" in request.url.params.get("categories", "")


# ── Tavily ────────────────────────────────────────────────────────────────────


class TestWebSearchClientTavily:
    def test_tavily_posts_to_correct_endpoint(self):
        from tools.search.client import WebSearchClient

        with respx.mock:
            route = respx.post("https://api.tavily.com/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            client = WebSearchClient(_provider_config("tavily"), _no_retry())
            client.search("q")
        assert route.called

    def test_tavily_maps_results_correctly(self):
        from tools.search.client import WebSearchClient

        payload = {
            "results": [
                {"title": "Tavily Title", "url": "http://t.com", "content": "Long content here"}
            ]
        }
        with respx.mock:
            respx.post("https://api.tavily.com/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("tavily"), _no_retry())
            r = client.search("q")[0]
        assert r.title == "Tavily Title"
        assert r.url == "http://t.com"
        assert r.content == "Long content here"

    def test_tavily_truncates_snippet_to_500_chars(self):
        from tools.search.client import WebSearchClient

        long_content = "x" * 600
        payload = {"results": [{"title": "T", "url": "http://t.com", "content": long_content}]}
        with respx.mock:
            respx.post("https://api.tavily.com/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("tavily"), _no_retry())
            r = client.search("q")[0]
        assert len(r.snippet) == 500


# ── Serper ────────────────────────────────────────────────────────────────────


class TestWebSearchClientSerper:
    def test_serper_uses_link_field_for_url(self):
        from tools.search.client import WebSearchClient

        payload = {
            "organic": [
                {"title": "Serper", "link": "https://g.com/result", "snippet": "snip"}
            ]
        }
        with respx.mock:
            respx.post("https://google.serper.dev/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("serper"), _no_retry())
            r = client.search("q")[0]
        assert r.url == "https://g.com/result"

    def test_serper_maps_organic_results(self):
        from tools.search.client import WebSearchClient

        payload = {
            "organic": [
                {"title": "A", "link": "http://a.com", "snippet": "Snippet A"},
                {"title": "B", "link": "http://b.com", "snippet": "Snippet B"},
            ]
        }
        with respx.mock:
            respx.post("https://google.serper.dev/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("serper"), _no_retry())
            results = client.search("q")
        assert len(results) == 2
        assert results[1].snippet == "Snippet B"


# ── Brave ─────────────────────────────────────────────────────────────────────


class TestWebSearchClientBrave:
    def test_brave_uses_description_for_snippet(self):
        from tools.search.client import WebSearchClient

        payload = {
            "web": {
                "results": [
                    {"title": "Brave", "url": "https://b.com", "description": "desc text"}
                ]
            }
        }
        with respx.mock:
            respx.get("https://api.search.brave.com/res/v1/web/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("brave"), _no_retry())
            r = client.search("q")[0]
        assert r.snippet == "desc text"

    def test_brave_navigates_nested_web_results(self):
        from tools.search.client import WebSearchClient

        payload = {"web": {"results": [{"title": "X", "url": "http://x.de", "description": "d"}]}}
        with respx.mock:
            respx.get("https://api.search.brave.com/res/v1/web/search").mock(
                return_value=httpx.Response(200, json=payload)
            )
            client = WebSearchClient(_provider_config("brave"), _no_retry())
            results = client.search("q")
        assert len(results) == 1


# ── Unbekannter Provider ──────────────────────────────────────────────────────


class TestUnknownProvider:
    def test_unknown_provider_raises_value_error(self):
        from tools.search.client import WebSearchClient

        client = WebSearchClient(_provider_config("unknown_provider"), _no_retry())
        with pytest.raises(ValueError, match="Unbekannter Search-Provider"):
            client.search("q")


# ── multi_search ──────────────────────────────────────────────────────────────


class TestMultiSearch:
    def test_multi_search_returns_dict_keyed_by_query(self):
        from tools.search.client import WebSearchClient

        with respx.mock:
            respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(200, json={"results": []})
            )
            client = WebSearchClient(_searxng_config(), _no_retry())
            results = client.multi_search(["q1", "q2"])
        assert "q1" in results
        assert "q2" in results

    def test_multi_search_handles_single_failure_gracefully(self):
        from tools.search.client import WebSearchClient

        # Provider wirft ValueError → leere Liste für diese Query, andere unberührt
        client = WebSearchClient(_provider_config("unknown_provider"), _no_retry())
        results = client.multi_search(["q1", "q2"])
        # Alle Queries müssen im Ergebnis sein, auch wenn sie fehlschlagen
        assert "q1" in results
        assert results["q1"] == []


# ── format_results_for_llm ────────────────────────────────────────────────────


class TestFormatResultsForLLM:
    def test_empty_results_returns_fallback_message(self):
        from tools.search.client import WebSearchClient

        result = WebSearchClient.format_results_for_llm([])
        assert "Keine" in result

    def test_uses_content_when_available(self):
        from tools.search.models import SearchResult
        from tools.search.client import WebSearchClient

        r = SearchResult(title="T", url="http://x.de", snippet="short", content="full content")
        formatted = WebSearchClient.format_results_for_llm([r])
        assert "full content" in formatted

    def test_falls_back_to_snippet_when_no_content(self):
        from tools.search.models import SearchResult
        from tools.search.client import WebSearchClient

        r = SearchResult(title="T", url="http://x.de", snippet="only snippet", content="")
        formatted = WebSearchClient.format_results_for_llm([r])
        assert "only snippet" in formatted

    def test_formats_multiple_results_with_separator(self):
        from tools.search.models import SearchResult
        from tools.search.client import WebSearchClient

        results = [
            SearchResult(title=f"T{i}", url=f"http://x.de/{i}", snippet=f"s{i}")
            for i in range(3)
        ]
        formatted = WebSearchClient.format_results_for_llm(results)
        assert "---" in formatted
        assert "[Quelle 1]" in formatted
        assert "[Quelle 3]" in formatted


# ── AsyncWebSearchClient ──────────────────────────────────────────────────────


class TestAsyncWebSearchClient:
    async def test_searxng_async_returns_results(self):
        from tools.search.client import AsyncWebSearchClient

        with respx.mock:
            respx.get("http://searxng.test/search").mock(
                return_value=httpx.Response(
                    200,
                    json={"results": [{"title": "Async T", "url": "http://a.de", "content": ""}]},
                )
            )
            client = AsyncWebSearchClient(_searxng_config(), _no_retry())
            results = await client.search_async("q")
        assert len(results) == 1
        assert results[0].title == "Async T"

    async def test_unknown_provider_async_raises(self):
        from tools.search.client import AsyncWebSearchClient

        client = AsyncWebSearchClient(_provider_config("unknown"), _no_retry())
        with pytest.raises(ValueError, match="Unbekannter Search-Provider"):
            await client.search_async("q")
