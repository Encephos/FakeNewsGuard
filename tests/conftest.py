"""Pytest-Fixtures für FakeNewsGuard Tests."""

from __future__ import annotations

import pytest

from config import (
    AppConfig,
    CacheConfig,
    ClaimProcessingConfig,
    CoVeConfig,
    GoogleFactCheckConfig,
    LangSearchConfig,
    LLMConfig,
    RetryConfig,
    SearchConfig,
)


# ── Config Fixtures ───────────────────────────────────────────────


@pytest.fixture
def minimal_config() -> AppConfig:
    """AppConfig mit deaktiviertem Cache und einem einzigen Retry."""
    return AppConfig(
        llm=LLMConfig(provider="anthropic", api_key="test-key"),
        search=SearchConfig(provider="searxng", base_url="http://localhost:8888"),
        langsearch=LangSearchConfig(api_key="", enabled=False),
        google_fact_check=GoogleFactCheckConfig(api_key="", enabled=False),
        claim_processing=ClaimProcessingConfig(top_n=0),
        cove=CoVeConfig(enabled=False),  # CoVe in Tests standardmäßig aus
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


@pytest.fixture
def sample_processed_claim():
    """Ein ProcessedClaim mit allen neuen Feldern."""
    from models.schemas import AmbiguityLevel, ClaimType, ProcessedClaim

    return ProcessedClaim(
        id="C1",
        text="Die Kriminalität in Deutschland ist 2023 um 50 Prozent gestiegen.",
        type=ClaimType.STATISTICAL,
        context="Politische Rede",
        requires_agents=["fact_checker", "number_auditor"],
        canonical_text="Die Kriminalität in Deutschland ist 2023 um 50% gestiegen.",
        canonical_hash="abc12345",
        normalized_entities=["Deutschland"],
        normalized_dates=["2023"],
        normalized_numbers=["50"],
        ambiguity_level=AmbiguityLevel.NONE,
        priority_score=0.85,
        harm_score=0.7,
        checkworthiness_score=0.9,
        priority_reason="Statistischer Claim mit hohem Schadenspotenzial",
        recommended_processing_order=1,
        is_checkworthy=True,
    )


@pytest.fixture
def sample_evidence_pack():
    """Ein minimales EvidencePack für Tests."""
    from models.evidence_models import (
        EvidenceItem,
        EvidencePack,
        EvidenceQualitySignals,
        EvidenceSource,
        GoogleFactCheckMatch,
        SourceConsensus,
    )

    return EvidencePack(
        claim_id="C1",
        claim_text="Die Kriminalität in Deutschland ist 2023 um 50 Prozent gestiegen.",
        canonical_text="Die Kriminalität in Deutschland ist 2023 um 50% gestiegen.",
        queries_used=["Kriminalität Deutschland 2023 Statistik"],
        google_fact_check_matches=[
            GoogleFactCheckMatch(
                claim_reviewed="Kriminalität gestiegen",
                rating="Falsch",
                publisher="Correctiv",
                url="https://correctiv.org/faktencheck/test",
                language="de",
            )
        ],
        web_results=[
            EvidenceItem(
                source=EvidenceSource(
                    url="https://destatis.de/test",
                    title="PKS 2023",
                    domain="destatis.de",
                    domain_tier=1,
                    is_primary_source=True,
                ),
                excerpt="Die Polizeiliche Kriminalstatistik 2023 zeigt einen Anstieg von 5,5%.",
                relevance_score=0.9,
                extraction_confidence=0.8,
                supports_claim=False,
            )
        ],
        evidence_quality=EvidenceQualitySignals(
            has_primary_sources=True,
            has_fact_check_org_result=True,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=0.9,
            overall_quality=0.85,
            top_tier_count=1,
        ),
        source_count=3,
        retrieval_notes=["Test Pack"],
    )
