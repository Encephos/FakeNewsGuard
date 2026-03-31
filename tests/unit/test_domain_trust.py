"""Tests für den OpenPageRank DomainTrustClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.domain_trust import DomainRankResult, DomainTrustClient


# ── Fixtures ─────────────────────────────────────────────────────────────────


SAMPLE_API_RESPONSE = {
    "status_code": 200,
    "response": [
        {
            "status_code": 200,
            "domain": "reuters.com",
            "page_rank_integer": 8,
            "page_rank_decimal": 8.12,
            "rank": 450,
        },
        {
            "status_code": 200,
            "domain": "example-blog.xyz",
            "page_rank_integer": 2,
            "page_rank_decimal": 2.34,
            "rank": 9500000,
        },
    ],
}


@pytest.fixture
def client():
    """DomainTrustClient mit gemocktem API-Key."""
    with patch.dict("os.environ", {"OPENPAGERANK_API_KEY": "test-key-123"}):
        return DomainTrustClient()


@pytest.fixture
def client_no_key():
    """DomainTrustClient ohne API-Key."""
    with patch.dict("os.environ", {"OPENPAGERANK_API_KEY": ""}):
        return DomainTrustClient()


# ── Verfügbarkeit ────────────────────────────────────────────────────────────


class TestAvailability:
    def test_available_with_key(self, client):
        assert client.is_available is True

    def test_not_available_without_key(self, client_no_key):
        assert client_no_key.is_available is False


# ── get_rank() ───────────────────────────────────────────────────────────────


class TestGetRank:
    @patch("tools.domain_trust.retry_call")
    def test_returns_rank(self, mock_retry, client):
        mock_retry.return_value = SAMPLE_API_RESPONSE

        result = client.get_rank("reuters.com")

        assert result is not None
        assert result.domain == "reuters.com"
        assert result.page_rank_integer == 8
        assert result.page_rank_decimal == 8.12
        assert result.rank == 450

    @patch("tools.domain_trust.retry_call")
    def test_caches_result(self, mock_retry, client):
        mock_retry.return_value = SAMPLE_API_RESPONSE

        result1 = client.get_rank("reuters.com")
        result2 = client.get_rank("reuters.com")

        assert result1 == result2
        assert mock_retry.call_count == 1  # Nur ein HTTP-Call

    def test_returns_none_without_key(self, client_no_key):
        result = client_no_key.get_rank("reuters.com")
        assert result is None

    @patch("tools.domain_trust.retry_call", side_effect=Exception("network error"))
    def test_error_returns_none(self, mock_retry, client):
        result = client.get_rank("fail.com")
        assert result is None


# ── get_ranks_batch() ────────────────────────────────────────────────────────


class TestGetRanksBatch:
    @patch("tools.domain_trust.retry_call")
    def test_batch_returns_multiple(self, mock_retry, client):
        mock_retry.return_value = SAMPLE_API_RESPONSE

        results = client.get_ranks_batch(["reuters.com", "example-blog.xyz"])

        assert len(results) == 2
        assert results["reuters.com"].page_rank_integer == 8
        assert results["example-blog.xyz"].page_rank_integer == 2

    def test_batch_empty_without_key(self, client_no_key):
        results = client_no_key.get_ranks_batch(["reuters.com"])
        assert results == {}

    def test_batch_empty_list(self, client):
        results = client.get_ranks_batch([])
        assert results == {}


# ── tier_adjustment() ────────────────────────────────────────────────────────


class TestTierAdjustment:
    @patch("tools.domain_trust.retry_call")
    def test_high_pr_improves_tier(self, mock_retry, client):
        """PR >= 7 → Tier verbessert sich um 1."""
        mock_retry.return_value = {
            "response": [{"domain": "reuters.com", "page_rank_integer": 8, "page_rank_decimal": 8.0, "rank": 100}]
        }
        assert client.tier_adjustment("reuters.com") == -1.0

    @patch("tools.domain_trust.retry_call")
    def test_medium_pr_slight_improvement(self, mock_retry, client):
        """PR 5-6 → Tier verbessert sich um 0.5."""
        mock_retry.return_value = {
            "response": [{"domain": "medium.com", "page_rank_integer": 6, "page_rank_decimal": 6.0, "rank": 5000}]
        }
        assert client.tier_adjustment("medium.com") == -0.5

    @patch("tools.domain_trust.retry_call")
    def test_average_pr_neutral(self, mock_retry, client):
        """PR 3-4 → Kein Einfluss."""
        mock_retry.return_value = {
            "response": [{"domain": "average.com", "page_rank_integer": 4, "page_rank_decimal": 4.0, "rank": 50000}]
        }
        assert client.tier_adjustment("average.com") == 0.0

    @patch("tools.domain_trust.retry_call")
    def test_low_pr_worsens_tier(self, mock_retry, client):
        """PR < 3 → Tier verschlechtert sich um 0.5."""
        mock_retry.return_value = {
            "response": [{"domain": "sketchy.xyz", "page_rank_integer": 1, "page_rank_decimal": 1.0, "rank": 9000000}]
        }
        assert client.tier_adjustment("sketchy.xyz") == 0.5

    def test_unavailable_returns_zero(self, client_no_key):
        """Ohne API-Key → kein Einfluss."""
        assert client_no_key.tier_adjustment("reuters.com") == 0.0

    @patch("tools.domain_trust.retry_call", side_effect=Exception("error"))
    def test_error_returns_zero(self, mock_retry, client):
        """Bei Fehler → kein Einfluss."""
        assert client.tier_adjustment("fail.com") == 0.0
