"""Tests für agents/fact_checker.py."""

from __future__ import annotations

import pytest

from agents.fact_checker import FactCheckerAgent, _build_search_queries
from models.schemas import Claim, ClaimType, FactRating


# ── _build_search_queries ─────────────────────────────────────────


def test_build_queries_includes_direct_search(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    assert sample_factual_claim.text in queries


def test_build_queries_adds_factcheck_terms(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    assert len(queries) > 1
    combined = " ".join(queries)
    assert "faktencheck" in combined.lower() or "destatis" in combined.lower()


def test_build_queries_max_length(sample_statistical_claim):
    queries = _build_search_queries(sample_statistical_claim)
    assert len(queries) <= 3  # Direktsuche + max 2 Ergänzungen


# ── FactCheckerAgent ──────────────────────────────────────────────


def test_fact_checker_returns_result(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim):
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)
    result = agent.execute(sample_factual_claim)

    assert result.claim_id == sample_factual_claim.id
    assert result.rating == FactRating.MISLEADING
    assert result.evidence == "Test-Evidenz"


def test_fact_checker_fallback_on_invalid_rating(minimal_config, mocker, mock_search_client, sample_factual_claim):
    mock_llm = mocker.MagicMock()
    mock_llm.complete_json.return_value = {
        "claim_id": "C1",
        "rating": "INVALID_RATING",
        "evidence": "some",
        "sources": [],
    }
    mock_llm.complete_structured.return_value = mock_llm.complete_json.return_value

    agent = FactCheckerAgent(minimal_config, mock_llm, mock_search_client)
    result = agent.execute(sample_factual_claim)
    assert result.rating == FactRating.UNVERIFIABLE


def test_fact_checker_uses_cache(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim, cache_config):
    from tools.cache import ClaimCache

    cache = ClaimCache(cache_config)
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client, cache)

    # Erster Aufruf – schreibt in Cache
    result1 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1

    # Zweiter Aufruf – sollte Cache-Treffer sein, kein LLM-Call
    result2 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1  # Kein neuer Call
    assert result1.claim_id == result2.claim_id
    assert result1.rating == result2.rating
