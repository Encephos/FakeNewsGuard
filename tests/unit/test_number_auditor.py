"""Tests für agents/number_auditor.py – dynamische Suchtiefe und institutionelle Quellen."""

from __future__ import annotations

import pytest

from agents.number_auditor import NumberAuditorAgent
from models.schemas import Claim, ClaimType, ManipulationType


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm_for_audit(mocker):
    """LLMClient-Mock mit gültigem NumberAuditResult-JSON."""
    mock = mocker.MagicMock()
    mock.complete_structured.return_value = {
        "claim_id": "C1",
        "calculation_check": "Rechnerisch korrekt.",
        "methodology_issues": [],
        "correct_interpretation": "Keine Auffälligkeiten.",
        "manipulation_type": "NONE",
    }
    mock.complete_json.return_value = mock.complete_structured.return_value
    return mock


@pytest.fixture
def statistical_claim_with_percent():
    """STATISTICAL-Claim mit Prozentangabe – maximale Tiefe."""
    return Claim(
        id="C1",
        text="Die Arbeitslosenquote in der EU ist 2023 um 2,5% gestiegen.",
        type=ClaimType.STATISTICAL,
        context="Wirtschaftsbericht",
    )


@pytest.fixture
def simple_factual_claim():
    """FACTUAL-Claim ohne Sondermerkmale – Basis-Tiefe."""
    return Claim(
        id="C2",
        text="Der Bundestag hat das Gesetz verabschiedet.",
        type=ClaimType.FACTUAL,
        context="",
    )


def _make_route_result(source_ids: list[str], confidence: float = 0.85):
    """Erstellt ein minimales RouteResult mit echten SourceConfigs aus der Registry."""
    from tools.claim_router import RouteResult
    from tools.sources.registry import SourceRegistry
    from tools.sources.types import ClaimDomain

    sources = [s for sid in source_ids if (s := SourceRegistry.get(sid)) is not None]
    return RouteResult(
        sources=sources,
        domains=[ClaimDomain.ECONOMIC],
        jurisdiction="eu",
        site_hints=[],
        rationale="Test-Route",
        confidence=confidence,
    )


# ── _compute_search_depth ─────────────────────────────────────────────────────


def test_depth_base_for_factual_claim(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Faktenbehauptung ohne Zahlen.", type=ClaimType.FACTUAL, context="")
    depth = agent._compute_search_depth(claim, None)
    assert depth == 5


def test_depth_statistical_adds_three(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Keine Prozente.", type=ClaimType.STATISTICAL, context="")
    depth = agent._compute_search_depth(claim, None)
    assert depth == 8


def test_depth_percent_adds_two(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Die Rate beträgt 15%.", type=ClaimType.FACTUAL, context="")
    depth = agent._compute_search_depth(claim, None)
    assert depth == 7


def test_depth_currency_adds_two(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Das Paket kostet 500 EUR.", type=ClaimType.FACTUAL, context="")
    depth = agent._compute_search_depth(claim, None)
    assert depth == 7


def test_depth_high_confidence_route_adds_two(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Keine Zahlen.", type=ClaimType.FACTUAL, context="")
    route = _make_route_result(["eurostat"], confidence=0.85)
    depth = agent._compute_search_depth(claim, route)
    assert depth == 7


def test_depth_low_confidence_route_no_bonus(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    claim = Claim(id="C", text="Keine Zahlen.", type=ClaimType.FACTUAL, context="")
    route = _make_route_result(["eurostat"], confidence=0.5)
    depth = agent._compute_search_depth(claim, route)
    assert depth == 5


def test_depth_capped_at_twelve(minimal_config, mock_llm_for_audit, mock_search_client):
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)
    # STATISTICAL (+3) + % (+2) + high-confidence route (+2) = 5+3+2+2 = 12
    claim = Claim(id="C", text="Anstieg um 15%.", type=ClaimType.STATISTICAL, context="")
    route = _make_route_result(["eurostat"], confidence=0.9)
    depth = agent._compute_search_depth(claim, route)
    assert depth == 12


# ── Test 1: STATISTICAL + Eurostat → depth=10, Client wird aufgerufen ─────────


def test_statistical_with_eurostat_route_uses_deep_search_and_client(
    minimal_config, mock_llm_for_audit, mock_search_client, mocker, statistical_claim_with_percent
):
    """STATISTICAL-Claim + Eurostat-Route → search_depth=12, Eurostat.search() aufgerufen."""
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)

    route_result = _make_route_result(["eurostat"], confidence=0.85)

    # Mock für Eurostat-Client
    mock_item = mocker.MagicMock()
    mock_item.title = "Eurostat Arbeitslosenquote 2023"
    mock_item.url = "https://ec.europa.eu/eurostat/test"
    mock_item.abstract = "EU-Arbeitslosigkeit 2023: 6,0%"
    mock_eurostat_instance = mocker.MagicMock()
    mock_eurostat_instance.search.return_value = [mock_item]
    mock_eurostat_cls = mocker.MagicMock(return_value=mock_eurostat_instance)

    mocker.patch("agents.number_auditor.importlib.import_module", return_value=mocker.MagicMock(
        **{"EurostatClient": mock_eurostat_cls}
    ))
    mocker.patch("agents.number_auditor.SourceRegistry.get", return_value=mocker.MagicMock(
        source_class="tools.sources.clients.eurostat.EurostatClient",
        source_id="eurostat",
    ))

    result = agent.execute(
        {"claim": statistical_claim_with_percent, "route_result": route_result}
    )

    # Tiefe: STATISTICAL(+3) + %(+2) + confidence>0.7(+2) = 12
    mock_search_client.search.assert_called_once()
    _, kwargs = mock_search_client.search.call_args
    assert kwargs.get("max_results", mock_search_client.search.call_args[0][1] if len(mock_search_client.search.call_args[0]) > 1 else None) == 12 or \
        mock_search_client.search.call_args[0][1] == 12

    # Eurostat-Client wurde abgefragt
    mock_eurostat_instance.search.assert_called_once_with(
        statistical_claim_with_percent.text, max_results=3
    )

    assert result.claim_id == "C1"
    assert isinstance(result.manipulation_type, ManipulationType)


# ── Test 2: FACTUAL Claim ohne route_result → depth=5, keine institutionellen Clients ──


def test_factual_claim_uses_base_depth_no_institutional(
    minimal_config, mock_llm_for_audit, mock_search_client, mocker, simple_factual_claim
):
    """FACTUAL Claim + kein route_result → max_results=5, SourceRegistry nie aufgerufen."""
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)

    registry_spy = mocker.patch("agents.number_auditor.SourceRegistry.get")

    result = agent.execute(simple_factual_claim)

    mock_search_client.search.assert_called_once()
    _, kwargs = mock_search_client.search.call_args
    search_max = kwargs.get("max_results") or (
        mock_search_client.search.call_args[0][1]
        if len(mock_search_client.search.call_args[0]) > 1
        else None
    )
    assert search_max == 5

    registry_spy.assert_not_called()
    assert result.claim_id == "C2"


# ── Test 3: Client-Fehler → graceful degradation ──────────────────────────────


def test_institutional_client_error_is_caught(
    minimal_config, mock_llm_for_audit, mock_search_client, mocker, statistical_claim_with_percent
):
    """Exception im Source-Client → Audit läuft trotzdem, kein Raise."""
    agent = NumberAuditorAgent(minimal_config, mock_llm_for_audit, mock_search_client)

    route_result = _make_route_result(["eurostat"], confidence=0.85)

    mock_eurostat_instance = mocker.MagicMock()
    mock_eurostat_instance.search.side_effect = RuntimeError("Netzwerkfehler")
    mock_eurostat_cls = mocker.MagicMock(return_value=mock_eurostat_instance)

    mocker.patch("agents.number_auditor.importlib.import_module", return_value=mocker.MagicMock(
        **{"EurostatClient": mock_eurostat_cls}
    ))
    mocker.patch("agents.number_auditor.SourceRegistry.get", return_value=mocker.MagicMock(
        source_class="tools.sources.clients.eurostat.EurostatClient",
        source_id="eurostat",
    ))

    # Kein Exception-Raise erwartet
    result = agent.execute(
        {"claim": statistical_claim_with_percent, "route_result": route_result}
    )

    assert result is not None
    assert result.claim_id == "C1"
