"""Tests für den Wikipedia REST API Adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tools.sources.clients.wikipedia import WikipediaClient, _strip_html


# ── Fixtures ─────────────────────────────────────────────────────────────────


SAMPLE_SEARCH_PAGE = {
    "key": "Cholera",
    "title": "Cholera",
    "description": "Infektionskrankheit",
    "excerpt": 'Die <span class="searchmatch">Cholera</span> ist eine schwere Durchfallerkrankung.',
}

SAMPLE_SEARCH_RESPONSE = {"pages": [SAMPLE_SEARCH_PAGE]}

SAMPLE_SUMMARY = {
    "title": "Cholera",
    "description": "Infektionskrankheit",
    "extract": "Die Cholera ist eine schwere bakterielle Infektionskrankheit vorwiegend des Dünndarms.",
    "type": "standard",
    "content_urls": {
        "desktop": {"page": "https://de.wikipedia.org/wiki/Cholera"},
    },
}


@pytest.fixture
def client():
    """WikipediaClient mit gemockten HTTP-Clients."""
    c = WikipediaClient()
    c._http = MagicMock()
    c._summary_http = MagicMock()
    return c


# ── HTML Stripping ───────────────────────────────────────────────────────────


class TestStripHtml:
    def test_removes_span_tags(self):
        text = '<span class="searchmatch">Cholera</span> ist eine Krankheit.'
        assert _strip_html(text) == "Cholera ist eine Krankheit."

    def test_removes_nested_tags(self):
        text = "<b><i>Test</i></b> text"
        assert _strip_html(text) == "Test text"

    def test_normalizes_whitespace(self):
        text = "  Too   many   spaces  "
        assert _strip_html(text) == "Too many spaces"

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self):
        assert _strip_html("No tags here") == "No tags here"


# ── normalize() ──────────────────────────────────────────────────────────────


class TestNormalize:
    def test_basic_normalize(self, client):
        item = client.normalize(SAMPLE_SEARCH_PAGE)

        assert item.record_id == "Cholera"
        assert item.title == "Cholera"
        assert item.url == "https://de.wikipedia.org/wiki/Cholera"
        assert "Infektionskrankheit" in item.abstract
        assert item.source_id == "wikipedia"

    def test_html_stripped_from_excerpt(self, client):
        item = client.normalize(SAMPLE_SEARCH_PAGE)
        assert "<span" not in item.abstract
        assert "Cholera" in item.abstract

    def test_normalized_facts(self, client):
        item = client.normalize(SAMPLE_SEARCH_PAGE)
        assert len(item.normalized_facts) == 1
        fact = item.normalized_facts[0]
        assert fact.fact_type.value == "context_summary"
        assert "Cholera" in fact.subject

    def test_missing_description(self, client):
        record = {"key": "Test", "title": "Test", "excerpt": "Some text"}
        item = client.normalize(record)
        assert item.abstract == "Some text"

    def test_abstract_truncation(self, client):
        item = client.normalize(SAMPLE_SEARCH_PAGE)
        assert len(item.abstract) <= 1200


# ── search() ─────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_returns_items(self, client):
        client._http.get.return_value = SAMPLE_SEARCH_RESPONSE

        items = client.search("Cholera", max_results=5)

        assert len(items) == 1
        assert items[0].claim_relevance == 0.65
        assert items[0].confidence > 0
        client._http.get.assert_called_once()

    def test_search_empty_response(self, client):
        client._http.get.return_value = {}
        items = client.search("nothing")
        assert items == []

    def test_search_error_returns_empty(self, client):
        from tools.sources.clients.base import AdapterHTTPError

        client._http.get.side_effect = AdapterHTTPError("timeout")
        items = client.search("fail")
        assert items == []


# ── fetch_details() ──────────────────────────────────────────────────────────


class TestFetchDetails:
    def test_fetch_summary(self, client):
        client._summary_http.get.return_value = SAMPLE_SUMMARY

        item = client.fetch_details("Cholera")

        assert item is not None
        assert item.title == "Cholera"
        assert item.claim_relevance == 0.85
        assert "bakterielle Infektionskrankheit" in item.abstract
        assert item.url == "https://de.wikipedia.org/wiki/Cholera"

    def test_fetch_not_found(self, client):
        client._summary_http.get.return_value = {}
        assert client.fetch_details("Nonexistent") is None

    def test_fetch_error(self, client):
        from tools.sources.clients.base import AdapterHTTPError

        client._summary_http.get.side_effect = AdapterHTTPError("404")
        assert client.fetch_details("Fail") is None

    def test_spaces_in_title(self, client):
        client._summary_http.get.return_value = SAMPLE_SUMMARY
        client.fetch_details("Deutsche Bahn")
        client._summary_http.get.assert_called_once_with("/page/summary/Deutsche_Bahn")
