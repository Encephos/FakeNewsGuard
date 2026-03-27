"""CERN Open Data – Physics Research Data Adapter.

API-Dokumentation: https://opendata.cern.ch/docs/

Zugang:
    REST API: https://opendata.cern.ch/api/records
              ?q=search_term&size={n}&page={p}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (CC0/CC BY).
    - Invenio-basierte API.
    - Verschiedene Datensatztypen: Analysetools, Daten, Simulationen, etc.

record_id-Format:
    Invenio Record-ID: z.B. "1234567" (nummerisch)
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

_HALF_LIFE_YEARS = 5.0


class CERNOpenDataClient(BaseSourceAdapter):
    """Adapter für CERN Open Data API (physics research datasets)."""

    config = SourceRegistry.get("cern_open_data")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://opendata.cern.ch/api",
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
        """Suche CERN Open Data Datensätze."""
        params = {
            "q": query,
            "size": min(max_results, 100),
            "page": page,
        }
        try:
            raw = self._http.get("/records", params)
        except AdapterHTTPError as exc:
            logger.warning("CERN OpenData search failed: %s", exc)
            return []

        records = raw.get("hits", {}).get("hits", [])
        items = []
        for record in records[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("CERN OpenData normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe einen CERN Open Data Datensatz ab."""
        rec_id = record_id.strip()
        if not rec_id:
            return None

        try:
            raw = self._http.get(f"/records/{rec_id}")
        except AdapterHTTPError as exc:
            logger.warning("CERN OpenData fetch_details failed: %s", exc)
            return None

        if not raw:
            return None

        item = self.normalize(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere CERN Open Data Record zu OfficialEvidenceItem."""
        # CERN nutzt Invenio – Struktur ist verschachtelt
        metadata = record.get("metadata", record)

        record_id = str(metadata.get("recid", metadata.get("id", "")))
        title = metadata.get("title", "")
        description = metadata.get("description", "")

        # Datum
        created = metadata.get("created", "")
        published_at = _parse_cern_date(created)

        # Autoren
        creators = metadata.get("creators", [])
        author_names = []
        for creator in creators[:3]:
            if isinstance(creator, dict):
                author_names.append(creator.get("name", ""))
            else:
                author_names.append(str(creator))

        url = f"https://opendata.cern.ch/records/{record_id}"

        abstract = description[:1200] if description else title

        facts = [
            NormalizedFact(
                fact_type=FactType.RESEARCH_FINDING,
                subject=title[:120],
                predicate="CERN Open Data Record",
                value=description[:200] if description else "Research dataset",
                source_snippet=description[:400] if description else title,
                reference_period=created[:10] if created else "",
                confidence=0.85,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=record_id,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=author_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "record_id": record_id,
                "creators": author_names,
            },
        )
        return item


def _parse_cern_date(date_str: str) -> date | None:
    """Parse CERN ISO 8601 Datum."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
