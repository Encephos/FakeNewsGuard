"""USPTO PatentsView – US Patent Data Adapter.

API-Dokumentation: https://patentsview.org/api/

Endpunkte:
    Patents Search:  GET https://api.patentsview.org/patents/query
                         ?q={query}&f=patent_number,patent_title,...
    Patent Detail:   GET https://api.patentsview.org/patents/query
                         ?q={"patent_number":"US12345678"}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (US Public Domain).
    - Elasticsearch-Abfrage-Syntax für q-Parameter.
    - Patent-Nummern im Format "US" + Nummern (z.B. "US10234567").

record_id-Format:
    Patent-Nummer: "US10234567"
"""

from __future__ import annotations

import json
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

_HALF_LIFE_YEARS = 10.0  # Patente ändern sich selten


class USPTOClient(BaseSourceAdapter):
    """Adapter für USPTO PatentsView API."""

    config = SourceRegistry.get("uspto")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://api.patentsview.org",
            timeout=15.0,
            max_attempts=3,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche Patente per Keyword."""
        # PatentsView nutzt Elasticsearch-Syntax
        es_query = json.dumps({"patent_title": query})
        params = {
            "q": es_query,
            "f": ["patent_number", "patent_title", "patent_date", "inventors"],
            "per_page": min(max_results, 100),
            "page": page,
        }
        try:
            raw = self._http.get("/patents/query", params)
        except AdapterHTTPError as exc:
            logger.warning("USPTO search failed: %s", exc)
            return []

        patents = raw.get("patents", [])
        items = []
        for record in patents[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("USPTO normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein Patent per Nummer ab."""
        patent_num = record_id.strip()
        if not patent_num.startswith("US"):
            patent_num = f"US{patent_num}"

        es_query = json.dumps({"patent_number": patent_num})
        params = {
            "q": es_query,
            "f": ["patent_number", "patent_title", "patent_date", "inventors"],
        }
        try:
            raw = self._http.get("/patents/query", params)
        except AdapterHTTPError as exc:
            logger.warning("USPTO fetch_details failed: %s", exc)
            return None

        patents = raw.get("patents", [])
        if not patents:
            return None

        item = self.normalize(patents[0])
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere USPTO Patent zu OfficialEvidenceItem."""
        patent_num = record.get("patent_number", "")
        title = record.get("patent_title", "")
        date_str = record.get("patent_date", "")
        inventors = record.get("inventors", [])

        published_at = _parse_uspto_date(date_str)

        inventor_names = []
        if isinstance(inventors, list):
            inventor_names = [
                inv.get("inventor_name", "") for inv in inventors[:3] if inv.get("inventor_name")
            ]
        elif isinstance(inventors, str):
            inventor_names = [inventors]

        url = f"https://www.uspto.gov/cgi-bin/wipo?DocID={patent_num}" if patent_num else ""

        abstract_parts = [
            f"Patent: {title}",
            f"Number: {patent_num}",
            f"Date: {date_str}",
        ]
        if inventor_names:
            abstract_parts.append(f"Inventors: {', '.join(inventor_names)}")

        abstract = " | ".join(filter(None, abstract_parts))[:1200]

        facts = [
            NormalizedFact(
                fact_type=FactType.PATENT_STATUS,
                subject=title,
                predicate="US Patent Grant",
                value=patent_num,
                reference_period=date_str[:10] if date_str else "",
                qualifier=f"Inventors: {', '.join(inventor_names)}" if inventor_names else "",
                source_snippet=f"{patent_num}: {title}",
                confidence=1.0,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=patent_num,
            title=f"{title} ({patent_num})",
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="US",
            entity_mentions=inventor_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "patent_number": patent_num,
                "inventors": inventor_names,
            },
        )
        return item


def _parse_uspto_date(date_str: str) -> date | None:
    """Parse USPTO Datum (YYYY-MM-DD Format)."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
