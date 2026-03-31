"""Tests für den GDELT DOC API Adapter."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tools.sources.clients.gdelt import GDELTClient, _parse_gdelt_date


# ── Fixtures ─────────────────────────────────────────────────────────────────


SAMPLE_ARTICLE = {
    "url": "https://example.com/article-1",
    "title": "Impfpflicht in Deutschland beschlossen",
    "seendate": "20260315T120000Z",
    "domain": "example.com",
    "language": "German",
    "sourcecountry": "GM",
    "tone": -2.5,
}

SAMPLE_RESPONSE = {"articles": [SAMPLE_ARTICLE]}


@pytest.fixture
def client():
    """GDELTClient mit gemocktem HTTP-Client."""
    c = GDELTClient()
    c._http = MagicMock()
    return c


# ── Datumsparser ─────────────────────────────────────────────────────────────


class TestParseGdeltDate:
    def test_valid_date(self):
        assert _parse_gdelt_date("20260315T120000Z") == date(2026, 3, 15)

    def test_different_date(self):
        assert _parse_gdelt_date("20250101T000000Z") == date(2025, 1, 1)

    def test_empty_string(self):
        assert _parse_gdelt_date("") is None

    def test_invalid_format(self):
        assert _parse_gdelt_date("2026-03-15") is None

    def test_invalid_month(self):
        assert _parse_gdelt_date("20261315T120000Z") is None


# ── normalize() ──────────────────────────────────────────────────────────────


class TestNormalize:
    def test_basic_normalize(self, client):
        item = client.normalize(SAMPLE_ARTICLE)

        assert item.record_id == "https://example.com/article-1"
        assert item.title == "Impfpflicht in Deutschland beschlossen"
        assert item.url == "https://example.com/article-1"
        assert item.published_at == date(2026, 3, 15)
        assert item.jurisdiction == "GM"
        assert "example.com" in item.entity_mentions
        assert item.source_id == "gdelt"

    def test_normalized_facts(self, client):
        item = client.normalize(SAMPLE_ARTICLE)

        assert len(item.normalized_facts) == 2
        corr_fact = item.normalized_facts[0]
        assert corr_fact.fact_type.value == "media_corroboration"
        assert "example.com" in corr_fact.value

        tone_fact = item.normalized_facts[1]
        assert tone_fact.fact_type.value == "tone_analysis"
        assert tone_fact.numeric_value == -2.5

    def test_missing_tone(self, client):
        article = {**SAMPLE_ARTICLE, "tone": None}
        item = client.normalize(article)
        assert len(item.normalized_facts) == 1

    def test_missing_fields(self, client):
        item = client.normalize({"url": "https://x.com/a", "title": "Test"})
        assert item.record_id == "https://x.com/a"
        assert item.jurisdiction == "global"

    def test_abstract_truncation(self, client):
        item = client.normalize(SAMPLE_ARTICLE)
        assert len(item.abstract) <= 1200


# ── search() ─────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_returns_items(self, client):
        client._http.get.return_value = SAMPLE_RESPONSE

        items = client.search("Impfpflicht", max_results=5)

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

    def test_search_max_records(self, client):
        client._http.get.return_value = SAMPLE_RESPONSE
        client.search("test", max_results=100)

        call_args = client._http.get.call_args
        assert call_args[1]["params"]["maxrecords"] <= 250 if "params" in call_args[1] else call_args[0][1]["maxrecords"] <= 250


# ── fetch_details() ──────────────────────────────────────────────────────────


class TestFetchDetails:
    def test_always_returns_none(self, client):
        assert client.fetch_details("anything") is None


# ── corroboration_count() ────────────────────────────────────────────────────


class TestCorroborationCount:
    def test_counts_unique_domains(self, client):
        client._http.get.return_value = {
            "articles": [
                {"domain": "bbc.com"},
                {"domain": "reuters.com"},
                {"domain": "bbc.com"},  # Duplikat
                {"domain": "spiegel.de"},
            ]
        }
        assert client.corroboration_count("test") == 3

    def test_error_returns_zero(self, client):
        from tools.sources.clients.base import AdapterHTTPError

        client._http.get.side_effect = AdapterHTTPError("fail")
        assert client.corroboration_count("fail") == 0

    def test_empty_response(self, client):
        client._http.get.return_value = {}
        assert client.corroboration_count("empty") == 0
