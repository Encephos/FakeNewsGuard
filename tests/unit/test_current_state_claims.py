"""Tests für Current-State-Claim-Handling, Tavily-Default und DB-Konfiguration.

Abgedeckt:
  - Erkennung von Amtsinhaber-Claims (_is_current_state_claim)
  - SearXNG nutzt time_range aus Config (nicht hartcodiert)
  - Veraltete Quellen senken Confidence (Ceiling-Guardrail)
  - Fehlende frische Direktbeweise → UNVERIFIABLE statt FALSE/MISLEADING
  - Tavily standardmäßig deaktiviert
  - Pipeline läuft sauber ohne Tavily
  - CACHE_DB_PATH env var wird geladen
  - Alle persistenten DB-Configs lesen aus env vars
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from config import (
    AppConfig,
    CacheConfig,
    EvidenceRetrievalConfig,
    TavilyConfig,
    UserDBConfig,
    ArchiveConfig,
    GraphConfig,
)
from agents.fact_checker import _is_current_state_claim


# ── 1. Erkennung von Aktuell-Zustand-Claims ───────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    # Typische Amtsinhaber-Claims → True
    ("Olaf Scholz ist Bundeskanzler.", True),
    ("Die Bundeskanzlerin ist Angela Merkel.", True),
    ("Er ist seit 2023 Präsident.", True),
    ("Sie amtiert als Gesundheitsministerin.", True),
    ("Tim Cook ist CEO von Apple.", True),
    ("Der Bürgermeister leitet die Stadt.", True),
    ("Er ist Generalsekretär der NATO.", True),
    ("Sie fungiert als Vorstandsvorsitzende.", True),
    ("Der Premier ist seit 2022 im Amt.", True),
    ("Der König regiert das Land.", True),
    # Keine Amtsinhaber-Claims → False
    ("Die Kriminalität ist gestiegen.", False),
    ("Das BIP wächst um 2%.", False),
    ("Die Inflation ist hoch.", False),
    ("Er war früher Präsident.", True),   # "war" ist Zustandsverb → True
    ("Das Parlament hat abgestimmt.", False),
])
def test_is_current_state_claim_detection(text: str, expected: bool) -> None:
    """_is_current_state_claim() erkennt Amtsinhaber-Claims zuverlässig."""
    assert _is_current_state_claim(text) is expected


def test_is_current_state_claim_no_false_positives() -> None:
    """Generische 'ist'-Sätze ohne Positionsbegriff werden nicht erkannt."""
    generic_claims = [
        "Das Wetter ist schön.",
        "Der Markt ist volatil.",
        "Das Ergebnis ist eindeutig.",
        "Die Aussage ist falsch.",
    ]
    for claim in generic_claims:
        assert _is_current_state_claim(claim) is False, f"False positive: {claim!r}"


# ── 2. SearXNG time_range kommt aus Config, nicht hartcodiert ─────────────────


def test_current_state_time_range_default_is_month() -> None:
    """EvidenceRetrievalConfig.current_state_time_range ist standardmäßig 'month'."""
    cfg = EvidenceRetrievalConfig()
    assert cfg.current_state_time_range == "month"


def test_current_state_time_range_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CURRENT_STATE_TIME_RANGE env var überschreibt den Default."""
    monkeypatch.setenv("CURRENT_STATE_TIME_RANGE", "week")
    cfg = EvidenceRetrievalConfig()
    assert cfg.current_state_time_range == "week"


def test_current_state_time_range_year_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CURRENT_STATE_TIME_RANGE kann auf 'year' gesetzt werden."""
    monkeypatch.setenv("CURRENT_STATE_TIME_RANGE", "year")
    cfg = EvidenceRetrievalConfig()
    assert cfg.current_state_time_range == "year"


# ── 3. Veraltete Quellen senken Confidence ────────────────────────────────────


def _make_quality(
    freshness: float = 0.5,
    overall: float = 0.7,
    consensus: str = "agreeing",
    has_primary: bool = False,
    has_fc: bool = False,
    has_fc_direct: bool = False,
    has_direct_refutation: bool = False,
    off_topic_rate: float = 0.0,
    avg_top5_relevance: float = 0.6,
    low_trust_rate: float = 0.0,
    direct_evidence_count: int = 0,
    contextual_only_rate: float = 0.0,
):
    """Hilfsfunktion: EvidenceQualitySignals-ähnliches Mock mit echten Attributnamen."""
    from models.evidence_models import EvidenceQualitySignals, SourceConsensus
    return EvidenceQualitySignals(
        has_primary_source_any=has_primary,
        has_primary_direct_evidence=has_primary,
        has_primary_sources=has_primary,
        has_fact_check_any=has_fc,
        has_fact_check_direct_match=has_fc_direct,
        has_fact_check_org_result=has_fc,
        has_direct_refutation=has_direct_refutation,
        source_consensus=SourceConsensus(consensus),
        freshness_score=freshness,
        overall_quality=overall,
        off_topic_rate=off_topic_rate,
        avg_top5_relevance=avg_top5_relevance,
        low_trust_rate=low_trust_rate,
        direct_evidence_count=direct_evidence_count,
        contextual_only_rate=contextual_only_rate,
    )


def test_stale_sources_trigger_confidence_ceiling() -> None:
    """Veraltete Quellen (freshness < threshold) senken die Confidence unter Ceiling."""
    from agents.verdict_agent import _calibrate_confidence

    pack = MagicMock()
    pack.evidence_quality = _make_quality(
        freshness=0.20,   # Deutlich unter Stale-Threshold
        has_primary=True,
        avg_top5_relevance=0.7,
        direct_evidence_count=1,
    )
    pack.web_results = []
    pack.google_fact_check_matches = []

    raw_confidence = 0.90
    calibrated, reasons = _calibrate_confidence(
        raw_confidence, pack, cove_trace=None,
        is_current_state_claim=True,
        stale_freshness_threshold=0.60,   # Erhöhter Threshold für current-state
    )

    # Muss unter dem Current-State-Ceiling liegen (0.55)
    assert calibrated <= 0.55, f"Erwartet ≤0.55, got {calibrated}"
    assert any("Aktuell-Zustand" in r or "veraltet" in r.lower() for r in reasons)


def test_no_stale_penalty_for_non_current_state_claim() -> None:
    """Bei nicht-aktuellen Claims greift der Current-State-Ceiling nicht."""
    from agents.verdict_agent import _calibrate_confidence

    pack = MagicMock()
    pack.evidence_quality = _make_quality(
        freshness=0.30,   # Unter Standard-Stale-Threshold (0.35)
        has_primary=True,
        avg_top5_relevance=0.7,
        direct_evidence_count=2,
    )
    pack.web_results = []
    pack.google_fact_check_matches = []

    raw_confidence = 0.85
    calibrated, reasons = _calibrate_confidence(
        raw_confidence, pack, cove_trace=None,
        is_current_state_claim=False,
        stale_freshness_threshold=0.35,
    )

    # Kein Current-State-Ceiling – nur normaler Stale-Ceiling (0.72)
    assert calibrated <= 0.72
    assert not any("Aktuell-Zustand" in r for r in reasons)


# ── 4. Fehlende frische Direktbeweise → UNVERIFIABLE ─────────────────────────


def test_current_state_false_without_direct_evidence_becomes_unverifiable() -> None:
    """Current-State-Claim mit Rating FALSE und 0 direkten Belegen → UNVERIFIABLE."""
    from agents.verdict_agent import _calibrate_rating
    from models.schemas import FactRating

    pack = MagicMock()
    quality = MagicMock()
    quality.source_consensus.value = "insufficient"
    quality.has_direct_refutation = False
    quality.has_fact_check_direct_match = False
    quality.has_fact_check_any = False
    quality.direct_evidence_count = 0
    quality.contextual_evidence_rate = 0.8
    pack.evidence_quality = quality
    pack.web_results = []

    rating, reasons = _calibrate_rating(
        FactRating.FALSE, pack,
        is_current_state_claim=True,
    )

    assert rating == FactRating.UNVERIFIABLE
    assert any("Aktuell-Zustand" in r for r in reasons)


def test_current_state_misleading_without_direct_evidence_becomes_unverifiable() -> None:
    """Current-State-Claim mit MISLEADING und 0 direkten Belegen → UNVERIFIABLE."""
    from agents.verdict_agent import _calibrate_rating
    from models.schemas import FactRating

    pack = MagicMock()
    quality = MagicMock()
    quality.source_consensus.value = "insufficient"
    quality.has_direct_refutation = False
    quality.has_fact_check_direct_match = False
    quality.has_fact_check_any = False
    quality.direct_evidence_count = 0
    quality.contextual_evidence_rate = 0.9
    pack.evidence_quality = quality
    pack.web_results = []

    rating, reasons = _calibrate_rating(
        FactRating.MISLEADING, pack,
        is_current_state_claim=True,
    )

    assert rating == FactRating.UNVERIFIABLE


def test_current_state_false_with_direct_refutation_stays_false() -> None:
    """Current-State mit FALSE + aktiver Widerlegung bleibt FALSE."""
    from agents.verdict_agent import _calibrate_rating
    from models.schemas import FactRating

    pack = MagicMock()
    quality = MagicMock()
    quality.source_consensus.value = "contradictory"
    quality.has_direct_refutation = True
    quality.has_fact_check_direct_match = True
    quality.has_fact_check_any = True
    quality.direct_evidence_count = 2
    quality.contextual_evidence_rate = 0.2
    quality.direct_refutation_freshness = 0.80  # frische Widerlegungsquellen
    pack.evidence_quality = quality
    pack.web_results = []

    rating, reasons = _calibrate_rating(
        FactRating.FALSE, pack,
        is_current_state_claim=True,
    )

    assert rating == FactRating.FALSE
    assert not any("Aktuell-Zustand" in r for r in reasons)


def test_non_current_state_false_not_affected_by_guardrail() -> None:
    """Nicht-Aktuell-Zustand-Claims werden nicht durch den Current-State-Guardrail beeinflusst."""
    from agents.verdict_agent import _calibrate_rating
    from models.schemas import FactRating

    pack = MagicMock()
    quality = MagicMock()
    quality.source_consensus.value = "contradictory"
    quality.has_direct_refutation = True
    quality.has_fact_check_direct_match = False
    quality.has_fact_check_any = True
    quality.direct_evidence_count = 0
    quality.contextual_evidence_rate = 0.5
    pack.evidence_quality = quality
    pack.web_results = []

    rating, reasons = _calibrate_rating(
        FactRating.FALSE, pack,
        is_current_state_claim=False,
    )

    # Ohne Current-State-Flag greift der Guardrail nicht
    assert not any("Aktuell-Zustand" in r for r in reasons)


# ── 5. Tavily standardmäßig deaktiviert ──────────────────────────────────────


def test_tavily_disabled_by_default() -> None:
    """TavilyConfig.enabled ist standardmäßig False."""
    cfg = TavilyConfig()
    assert cfg.enabled is False


def test_tavily_disabled_even_with_api_key() -> None:
    """Tavily bleibt deaktiviert auch wenn ein API-Key vorhanden ist."""
    cfg = TavilyConfig(api_key="tvly-testkey")
    assert cfg.enabled is False


def test_tavily_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """TAVILY_ENABLED=true aktiviert Tavily explizit."""
    monkeypatch.setenv("TAVILY_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-testkey")
    cfg = TavilyConfig()
    assert cfg.enabled is True


def test_tavily_disabled_in_appconfig() -> None:
    """AppConfig enthält TavilyConfig mit enabled=False als Default."""
    cfg = AppConfig()
    assert cfg.tavily.enabled is False


# ── 6. Pipeline läuft sauber ohne Tavily ─────────────────────────────────────


def test_tavily_client_returns_empty_when_disabled() -> None:
    """TavilyClient.search() gibt [] zurück wenn enabled=False."""
    from tools.web_search import TavilyClient

    cfg = TavilyConfig(enabled=False, api_key="")
    client = TavilyClient(cfg)
    results = client.search("Wer ist Bundeskanzler?")
    assert results == []


@pytest.mark.asyncio
async def test_tavily_client_async_returns_empty_when_disabled() -> None:
    """TavilyClient.search_async() gibt [] zurück wenn enabled=False."""
    from tools.web_search import TavilyClient

    cfg = TavilyConfig(enabled=False, api_key="")
    client = TavilyClient(cfg)
    results = await client.search_async("Wer ist Bundeskanzler?")
    assert results == []


def test_searxng_only_config_is_valid() -> None:
    """Eine AppConfig mit nur SearXNG (ohne Tavily/LangSearch) ist valide."""
    from config import SearchConfig, LangSearchConfig

    cfg = AppConfig(
        search=SearchConfig(provider="searxng", base_url="http://localhost:8888"),
        langsearch=LangSearchConfig(api_key="", enabled=False),
        # tavily bleibt Default (enabled=False)
    )
    assert cfg.tavily.enabled is False
    assert cfg.langsearch.enabled is False
    # SearXNG-Config ist gesetzt
    assert cfg.searxng.base_url == "http://localhost:8888"


# ── 7. CACHE_DB_PATH env var wird geladen ─────────────────────────────────────


def test_cache_db_path_default() -> None:
    """CacheConfig nutzt Standard-Pfad wenn kein env var gesetzt."""
    cfg = CacheConfig()
    assert cfg.db_path == ".fakeguard_cache.db"


def test_cache_db_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CACHE_DB_PATH env var überschreibt den Default-Pfad."""
    monkeypatch.setenv("CACHE_DB_PATH", "/app/data/fakeguard_cache.db")
    cfg = CacheConfig()
    assert cfg.db_path == "/app/data/fakeguard_cache.db"


def test_cache_ttl_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CACHE_TTL_HOURS env var überschreibt den Default-TTL."""
    monkeypatch.setenv("CACHE_TTL_HOURS", "48")
    cfg = CacheConfig()
    assert cfg.ttl_hours == 48


# ── 8. Persistente DB-Konfiguration korrekt geladen ──────────────────────────


def test_users_db_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """USERS_DB_PATH env var wird von UserDBConfig geladen."""
    monkeypatch.setenv("USERS_DB_PATH", "/data/users.db")
    cfg = UserDBConfig()
    assert cfg.db_path == "/data/users.db"


def test_archive_db_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCHIVE_DB_PATH env var wird von ArchiveConfig geladen."""
    monkeypatch.setenv("ARCHIVE_DB_PATH", "/data/archive.db")
    cfg = ArchiveConfig()
    assert cfg.db_path == "/data/archive.db"


def test_graph_db_path_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GRAPH_DB_PATH env var wird von GraphConfig geladen."""
    monkeypatch.setenv("GRAPH_DB_PATH", "/data/graph.db")
    cfg = GraphConfig()
    assert cfg.db_path == "/data/graph.db"


def test_all_db_configs_have_env_var_support() -> None:
    """Alle DB-Konfigurationsklassen unterstützen env vars für den Pfad."""
    # Smoke-Test: alle Config-Klassen instanziierbar ohne Fehler
    _ = CacheConfig()
    _ = UserDBConfig()
    _ = ArchiveConfig()
    _ = GraphConfig()


def test_production_db_paths_use_absolute_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absolute Pfade werden unverändert übernommen (Production-Szenario)."""
    monkeypatch.setenv("CACHE_DB_PATH", "/app/data/cache.db")
    monkeypatch.setenv("USERS_DB_PATH", "/app/data/users.db")
    monkeypatch.setenv("ARCHIVE_DB_PATH", "/app/data/archive.db")
    monkeypatch.setenv("GRAPH_DB_PATH", "/app/data/graph.db")

    assert CacheConfig().db_path == "/app/data/cache.db"
    assert UserDBConfig().db_path == "/app/data/users.db"
    assert ArchiveConfig().db_path == "/app/data/archive.db"
    assert GraphConfig().db_path == "/app/data/graph.db"
