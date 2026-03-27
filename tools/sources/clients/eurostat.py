"""Eurostat – EU Statistics API Adapter.

API-Dokumentation: https://ec.europa.eu/eurostat/web/main/data/database

Endpunkte (SDMX API):
    Dataset list: GET https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data
                      ?detail={detail}&dimensionAtObservation=TIME_PERIOD
    Daten:        GET https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{dataset}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (CC BY 4.0).
    - SDMX 2.1 XML-Format (auch JSON-Fallback).
    - Dataset-Codes wie "tps00001", "nama_10_gdp".
    - Dimensionen: Geographie (GEO), Zeit (TIME_PERIOD), etc.

record_id-Format:
    "<dataset_code>/<geo>/<time>"  z.B. "tps00001/DE/2023"
"""

from __future__ import annotations

import logging
from datetime import date

from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)
from tools.sources.clients.base import AdapterHTTPClient, AdapterHTTPError, BaseSourceAdapter
from tools.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

_HALF_LIFE_YEARS = 2.0  # Statistiken


class EurostatClient(BaseSourceAdapter):
    """Adapter für Eurostat (EU-Statistiken)."""

    config = SourceRegistry.get("eurostat")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1",
            timeout=20.0,
            max_attempts=3,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche Eurostat-Datensätze per Keyword."""
        # Hinweis: Eurostat hat keine echte Keyword-Suche über API.
        # Wir simulieren durch bekannte Datensatzcodes + einfache Filterung.
        try:
            # Abruf der gesamten Datensatzliste
            params = {
                "detail": "full",
            }
            raw = self._http.get("/data", params)
        except AdapterHTTPError as exc:
            logger.warning("Eurostat search failed: %s", exc)
            return []

        # Fallback: Kein echter Suchalgorithmus – geben wir leere Liste zurück.
        logger.debug("Eurostat search: no implementation (API returns datasets, not searchable)")
        return []

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe einen Eurostat-Datenpunkt ab.

        Args:
            record_id: Format "<dataset_code>/<geo>/<time>"
        """
        parts = record_id.split("/")
        if len(parts) < 2:
            logger.warning("Invalid Eurostat record_id: %r", record_id)
            return None

        dataset_code = parts[0]
        geo = parts[1]
        time_period = parts[2] if len(parts) > 2 else None

        # Hinweis: Dieser Adapter ist vereinfacht – echte Eurostat-Integration
        # wäre komplexer (SDMX XML-Parsing).
        logger.debug("Eurostat fetch_details: simplified implementation")
        return None

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Placeholder für Eurostat-Normalisierung."""
        # Eurostat-Normalisierung ist komplex (SDMX-Format).
        # Für diese MVP-Implementierung: einfach ein leeres Item zurückgeben.
        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id="",
            title="Eurostat (placeholder)",
            url="https://ec.europa.eu/eurostat",
            abstract="Eurostat data point (simplified implementation)",
            jurisdiction="EU",
            recency_score=0.0,
        )
        return item
