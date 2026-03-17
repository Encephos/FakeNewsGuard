"""Pytest-Fixtures für FakeNewsGuard Tests."""

from __future__ import annotations

import pytest

from config import AppConfig, CacheConfig, LLMConfig, RetryConfig, SearchConfig


# ── Config Fixtures ───────────────────────────────────────────────


@pytest.fixture
def minimal_config() -> AppConfig:
    """AppConfig mit deaktiviertem Cache und einem einzigen Retry."""
    return AppConfig(
        llm=LLMConfig(provider="anthropic", api_key="test-key"),
        search=SearchConfig(provider="tavily", api_key="test-search-key"),
        retry=RetryConfig(max_attempts=1, base_delay_s=0.0),
        cache=CacheConfig(enabled=False),
        verbose=False,
    )


@pytest.fixture
def cache_config(tmp_path) -> CacheConfig:
    """CacheConfig mit temporärer DB-Datei."""
    return CacheConfig(
        enabled=True,
        db_path=str(tmp_path / "test_cache.db"),
        ttl_hours=1,
    )


# ── LLM Mock Fixtures ─────────────────────────────────────────────


@pytest.fixture
def mock_llm_client(mocker):
    """Mock für LLMClient.complete_json – gibt sofort ein Dict zurück."""
    mock = mocker.MagicMock()
    mock.complete_json.return_value = {
        "claim_id": "C1",
        "rating": "MISLEADING",
        "evidence": "Test-Evidenz",
        "correction": "Test-Korrektur",
        "missing_context": "",
        "sources": ["https://example.com"],
    }
    mock.complete_structured.return_value = mock.complete_json.return_value
    return mock


@pytest.fixture
def mock_search_client(mocker):
    """Mock für WebSearchClient.search – gibt leere Ergebnisliste zurück."""
    from tools.web_search import SearchResult

    mock = mocker.MagicMock()
    mock.search.return_value = [
        SearchResult(
            title="Test-Artikel",
            url="https://example.com",
            snippet="Test-Snippet",
            content="Test-Content",
        )
    ]
    mock.multi_search.return_value = {"query": mock.search.return_value}
    mock.format_results_for_llm.return_value = "[Quelle 1] Test-Artikel\nURL: https://example.com\nInhalt: Test-Content"
    return mock


# ── Claim Fixtures ────────────────────────────────────────────────


@pytest.fixture
def sample_factual_claim():
    """Ein einfacher faktischer Claim zum Testen."""
    from models.schemas import Claim, ClaimType

    return Claim(
        id="C1",
        text="Die Kriminalität ist seit 2015 um 50% gestiegen.",
        type=ClaimType.FACTUAL,
        context="Politische Rede",
    )


@pytest.fixture
def sample_statistical_claim():
    """Ein statistischer Claim zum Testen."""
    from models.schemas import Claim, ClaimType

    return Claim(
        id="C2",
        text="40% der Einbrüche werden von Ausländern begangen.",
        type=ClaimType.STATISTICAL,
        context="",
        requires_agents=["number_auditor"],
    )


@pytest.fixture
def sample_opinion_claim():
    """Ein Meinungs-Claim – sollte übersprungen werden."""
    from models.schemas import Claim, ClaimType

    return Claim(
        id="C3",
        text="Die Regierung versagt beim Thema Sicherheit.",
        type=ClaimType.OPINION,
    )
