"""Unit-Test-Fixtures: Alle externen Netzwerkaufrufe werden gemockt.

Verhindert echte HTTP-Anfragen gegen Suchmaschinen, LLM-APIs und
Fact-Check-Datenbanken in Unit-Tests – Tests laufen dadurch sofort.

Gemockte Schichten:
  - LangSearchClient.multi_search_async  → leere Ergebnisse
  - AsyncWebSearchClient.multi_search_async → leere Ergebnisse
  - FactCheckDatabaseClient.search_async  → leere Ergebnisse
  - scrape_sources                         → leere Ergebnisse
  - LLMClient.complete / complete_json / complete_structured → Dummy-JSON
"""

from __future__ import annotations

import pytest


# ── Netzwerk-Mocks (autouse: greifen in allen Unit-Tests) ─────────────────────


@pytest.fixture(autouse=True)
def mock_network_calls(mocker):
    """Mockt alle externen Netzwerkaufrufe in Unit-Tests.

    Autouse=True → gilt automatisch für jeden Test in tests/unit/.
    Kein Test muss das explizit anfordern.
    """
    from tools.web_search import SearchResult

    # ── LangSearchClient: async multi-search → leere dict ────────────────────
    mocker.patch(
        "agents.evidence_builder.LangSearchClient.multi_search_async",
        return_value={},
    )

    # ── AsyncWebSearchClient: async multi-search → leere dict ────────────────
    mocker.patch(
        "agents.evidence_builder.AsyncWebSearchClient.multi_search_async",
        return_value={},
    )

    # ── FactCheckDatabaseClient: async search → leere Liste ──────────────────
    mocker.patch(
        "agents.evidence_builder.FactCheckDatabaseClient.search_async",
        return_value=[],
    )

    # ── scrape_sources: async scraping → leere Liste ─────────────────────────
    import asyncio

    async def _empty_scrape(*args, **kwargs):
        return []

    mocker.patch(
        "agents.evidence_builder.scrape_sources",
        side_effect=_empty_scrape,
    )

    # ── rank_sources: gibt leere Liste zurück ─────────────────────────────────
    mocker.patch(
        "agents.evidence_builder.rank_sources",
        return_value=[],
    )
