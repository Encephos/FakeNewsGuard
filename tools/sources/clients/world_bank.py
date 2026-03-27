"""World Bank Open Data – Source-Adapter.

API-Dokumentation: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392

Endpunkte:
    Indikator-Suche:    GET /v2/indicator?q={query}&format=json&per_page={n}&page={p}
    Datenpunkte:        GET /v2/country/{iso}/indicator/{code}?format=json&mrv={n}
    Einzelpunkt:        GET /v2/country/{iso}/indicator/{code}?format=json&date={year}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich.
    - Rate-Limit: ~10 RPS (laut Registry).
    - Antwortformat: JSON-Array [metadata_obj, data_list].
    - Über 16.000 Indikatoren aus 50+ Quellen.
    - Ländercodes: ISO 3166-1 Alpha-2 und Alpha-3 werden akzeptiert.
    - Aggregat-Codes: WLD (Welt), EUU (EU), LCN (Latein Amerika), ...

Adapter-Strategie in search():
    1. Indikator-Suche per Keyword → top 3 Indikatoren.
    2. Für jeden Indikator: abruf des Weltaggregats (WLD) plus (wenn vorhanden)
       des letzten Wertes → je ein OfficialEvidenceItem.
    3. Maximal 4 API-Calls pro search()-Aufruf.

record_id-Format (gemäß models/source_evidence.py):
    "<iso3>/<indicator_code>/<year>"  z.B. "DEU/NY.GDP.MKTP.CD/2023"
    "<iso3>/<indicator_code>"         → letzter verfügbarer Wert (mrv=1)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)
from tools.sources.clients.base import AdapterHTTPClient, AdapterHTTPError, BaseSourceAdapter
from tools.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

# Halbwertszeit für Wirtschaftsstatistiken: Indikatoren altern schnell.
_HALF_LIFE_YEARS = 2.0

# Weltbank-Aggregat-Codes für globale Referenzwerte.
_GLOBAL_AGGREGATES = ("WLD", "EUU", "HIC", "MIC", "LMC")

# Maximale Anzahl Indikatoren pro search()-Aufruf (limitiert API-Calls).
_MAX_INDICATORS_PER_SEARCH = 3


class WorldBankClient(BaseSourceAdapter):
    """Adapter für die World Bank Open Data API (v2).

    Liefert normalisierte Wirtschafts-, Entwicklungs- und Handelsdaten
    als OfficialEvidenceItem-Objekte für den EvidenceBuilderAgent.

    Verwendung::

        client = WorldBankClient()
        items = client.search("Germany GDP 2023", max_results=5)
        detail = client.fetch_details("DEU/NY.GDP.MKTP.CD/2023")
    """

    config = SourceRegistry.get("world_bank")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            self.config.base_url,
            timeout=15.0,
            max_attempts=3,
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche Indikatoren per Keyword und rufe aktuelle Weltbank-Daten ab.

        Strategie:
            1. Indikator-Suche: /indicator?q={query}  → bis zu 3 Indikatoren
            2. Für jeden Indikator: /country/WLD/indicator/{code}?mrv=1
            3. Je ein OfficialEvidenceItem pro Indikator-Datenpunkt

        Bei API-Fehlern wird eine leere Liste zurückgegeben (graceful degradation).
        """
        try:
            indicators = self._search_indicators(query, per_page=min(max_results, 10), page=page)
        except AdapterHTTPError as exc:
            logger.warning("WorldBank Indikator-Suche fehlgeschlagen: %s", exc)
            return []

        if not indicators:
            return []

        items: list[OfficialEvidenceItem] = []
        for indicator in indicators[:_MAX_INDICATORS_PER_SEARCH]:
            try:
                data_points = self._fetch_indicator_data(
                    iso="WLD",
                    code=indicator["id"],
                    mrv=1,
                )
                for dp in data_points:
                    item = self.normalize(dp)
                    # claim_relevance: Query hat Indikator gefunden → mittlere Relevanz.
                    item.claim_relevance = 0.65
                    item.confidence = item.compute_confidence()
                    items.append(item)
            except AdapterHTTPError as exc:
                logger.warning(
                    "WorldBank Datenabruf für Indikator '%s' fehlgeschlagen: %s",
                    indicator["id"], exc,
                )

        return items[:max_results]

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe einen spezifischen World-Bank-Datenpunkt per record_id ab.

        Args:
            record_id: Format "<iso3>/<indicator_code>/<year>" oder
                       "<iso3>/<indicator_code>" (letzter verfügbarer Wert).

        Returns:
            OfficialEvidenceItem oder None wenn Datenpunkt nicht gefunden.
        """
        parts = record_id.split("/")
        if len(parts) < 2:
            logger.warning("Ungültige WorldBank record_id: %r", record_id)
            return None

        iso = parts[0]
        code = parts[1]
        year = parts[2] if len(parts) >= 3 else None

        try:
            data_points = self._fetch_indicator_data(iso=iso, code=code, year=year, mrv=1)
        except AdapterHTTPError as exc:
            logger.warning("WorldBank fetch_details fehlgeschlagen für %r: %s", record_id, exc)
            return None

        if not data_points:
            return None

        item = self.normalize(data_points[0])
        item.claim_relevance = 0.85  # Direktabruf → höhere Relevanz
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere einen World-Bank-Datenpunkt in ein OfficialEvidenceItem.

        Erwartet einen Datenpunkt aus der /country/{iso}/indicator/{code} API:
            {
                "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                "country":   {"id": "DE", "value": "Germany"},
                "countryiso3code": "DEU",
                "date": "2023",
                "value": 4082669200000.0,
                "unit": "",
                "decimal": 0
            }
        """
        indicator = record.get("indicator", {})
        country = record.get("country", {})
        raw_value = record.get("value")
        year_str = record.get("date", "")
        iso3 = record.get("countryiso3code", country.get("id", ""))
        unit = record.get("unit", "") or ""

        indicator_code = indicator.get("id", "")
        indicator_name = indicator.get("value", "")
        country_name = country.get("value", iso3)

        # Datumsobjekt aus Jahresangabe rekonstruieren (WB liefert "2023").
        published_at: date | None = None
        if year_str and year_str.isdigit():
            try:
                published_at = date(int(year_str), 12, 31)  # Jahresende als Proxy
            except ValueError:
                pass

        # Formatierter Wert für Anzeige.
        value_str = ""
        numeric_value: float | None = None
        if raw_value is not None:
            numeric_value = float(raw_value)
            decimals = record.get("decimal", 2) or 2
            value_str = f"{numeric_value:,.{decimals}f}"

        record_id = f"{iso3}/{indicator_code}/{year_str}" if year_str else f"{iso3}/{indicator_code}"
        url = (
            f"https://data.worldbank.org/indicator/{indicator_code}"
            f"?locations={iso3}"
        )
        title = f"{indicator_name} – {country_name} ({year_str})"
        abstract = (
            f"{country_name}: {indicator_name} = {value_str} {unit}".strip()
            + f" (Weltbank-Datenpunkt {year_str}, Indikator: {indicator_code})"
        )[:1200]

        fact = NormalizedFact(
            fact_type=FactType.INDICATOR_VALUE,
            subject=country_name,
            predicate=indicator_name,
            value=value_str,
            numeric_value=numeric_value,
            unit=unit,
            reference_period=year_str,
            reference_entity=iso3,
            source_snippet=f"{country_name}: {indicator_name} = {value_str} {unit} ({year_str})".strip()[:400],
            confidence=1.0,
        )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=record_id,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction=iso3 if iso3 not in _GLOBAL_AGGREGATES else "global",
            entity_mentions=[country_name, indicator_code],
            recency_score=recency,
            normalized_facts=[fact],
            raw_fields={
                "indicator_id": indicator_code,
                "country_iso3": iso3,
                "date": year_str,
                "raw_value": raw_value,
                "unit": unit,
            },
        )
        return item

    # ── Interne Hilfsmethoden ─────────────────────────────────────────────────

    def _search_indicators(
        self, query: str, per_page: int = 10, page: int = 1
    ) -> list[dict]:
        """Indikator-Suche über /v2/indicator?q={query}.

        Returns:
            Liste von Indikator-Dicts mit 'id' und 'name'. Leer wenn keine Treffer.

        Raises:
            AdapterHTTPError: Bei HTTP-Fehler nach allen Retries.
        """
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "per_page": per_page,
            "page": page,
        }
        raw = self._http.get("/indicator", params)

        if not raw or not isinstance(raw, list) or len(raw) < 2:
            return []

        data = raw[1]
        if not data or not isinstance(data, list):
            return []

        return [ind for ind in data if ind.get("id")]

    def _fetch_indicator_data(
        self,
        iso: str,
        code: str,
        *,
        year: str | None = None,
        mrv: int = 1,
    ) -> list[dict]:
        """Abruf von Datenpunkten für ein Land/Aggregat und einen Indikator.

        Args:
            iso:  ISO 3166-1 Alpha-2/3 oder Aggregat-Code (WLD, EUU, ...).
            code: Weltbank-Indikatorcode (z.B. 'NY.GDP.MKTP.CD').
            year: Jahresfilter (z.B. '2023'). None → mrv-letzter Wert.
            mrv:  Most-Recent-Values – Anzahl letzter Datenpunkte.

        Returns:
            Liste von Datenpunkt-Dicts. Leer wenn keine Daten verfügbar.

        Raises:
            AdapterHTTPError: Bei HTTP-Fehler nach allen Retries.
        """
        params: dict[str, Any] = {
            "format": "json",
            "per_page": 10,
        }
        if year:
            params["date"] = year
        else:
            params["mrv"] = mrv

        path = f"/country/{iso}/indicator/{code}"
        raw = self._http.get(path, params)

        if not raw or not isinstance(raw, list) or len(raw) < 2:
            return []

        data = raw[1]
        if not data or not isinstance(data, list):
            return []

        # Nur Datenpunkte mit einem tatsächlichen Wert (kein None) zurückgeben.
        return [dp for dp in data if dp.get("value") is not None]
