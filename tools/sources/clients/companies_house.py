"""UK Companies House – Company Register Adapter.

API-Dokumentation: https://developer.company-information.service.gov.uk/

Endpunkte (REST API):
    Companies Search:  GET https://api.companieshouse.gov.uk/search/companies
                           ?q={query}
    Company Detail:    GET https://api.companieshouse.gov.uk/company/{company_number}

API-Eigenschaften:
    - Authentifizierung erforderlich: API-Key im Authorization Header (Basic Auth).
    - Company Number: 8-stellige Nummern (z.B. "00102498").
    - JSON-Format mit schachtelter Struktur.

record_id-Format:
    Company Number: "00102498" (8 Ziffern, führende Nullen)
"""

from __future__ import annotations

import base64
import logging
import os
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

_HALF_LIFE_YEARS = 1.5

_CH_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")


class CompaniesHouseClient(BaseSourceAdapter):
    """Adapter für UK Companies House API."""

    config = SourceRegistry.get("companies_house")

    def __init__(self) -> None:
        headers = {}
        if _CH_API_KEY:
            # Basic Auth: "key:"
            auth_str = base64.b64encode(f"{_CH_API_KEY}:".encode()).decode()
            headers["Authorization"] = f"Basic {auth_str}"

        self._http = AdapterHTTPClient(
            "https://api.companieshouse.gov.uk",
            timeout=15.0,
            max_attempts=3,
            headers=headers,
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche Unternehmen per Name."""
        if not _CH_API_KEY:
            logger.warning("Companies House API key not set (COMPANIES_HOUSE_API_KEY)")
            return []

        params = {
            "q": query,
            "items_per_page": min(max_results, 100),
            "start_index": (page - 1) * max_results,
        }
        try:
            raw = self._http.get("/search/companies", params)
        except AdapterHTTPError as exc:
            logger.warning("Companies House search failed: %s", exc)
            return []

        items_raw = raw.get("items", [])
        items = []
        for record in items_raw[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("Companies House normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein Unternehmen per Company Number ab."""
        if not _CH_API_KEY:
            logger.warning("Companies House API key not set")
            return None

        company_num = record_id.strip()
        try:
            raw = self._http.get(f"/company/{company_num}")
        except AdapterHTTPError as exc:
            logger.warning("Companies House fetch_details failed: %s", exc)
            return None

        if not raw:
            return None

        item = self.normalize(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere Companies House Record zu OfficialEvidenceItem."""
        company_num = record.get("company_number", "")
        company_name = record.get("company_name", "")
        status = record.get("company_status", "")
        company_type = record.get("type", "")

        # Gründung
        date_of_creation = record.get("date_of_creation", "")
        published_at = _parse_ch_date(date_of_creation)

        # Adresse
        address = record.get("registered_office_address", {})
        locality = address.get("locality", "") if isinstance(address, dict) else ""

        url = f"https://beta.companieshouse.gov.uk/company/{company_num}"

        abstract_parts = [
            f"Company: {company_name}",
            f"Number: {company_num}",
            f"Status: {status}",
            f"Type: {company_type}",
            f"Location: {locality}" if locality else None,
        ]
        abstract = " | ".join(filter(None, abstract_parts))[:1200]

        facts = [
            NormalizedFact(
                fact_type=FactType.ENTITY_REGISTRATION,
                subject=company_name,
                predicate="UK Company Number",
                value=company_num,
                reference_period=date_of_creation[:10] if date_of_creation else "",
                qualifier=f"Type: {company_type}",
                source_snippet=f"{company_name}: {company_num}, Status: {status}",
                confidence=1.0,
            ),
            NormalizedFact(
                fact_type=FactType.ENTITY_STATUS,
                subject=company_name,
                predicate="Company Status",
                value=status,
                source_snippet=f"Status: {status}",
                confidence=0.95,
            ),
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=company_num,
            title=f"{company_name} ({company_num})",
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="GB",
            entity_mentions=[company_name],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "company_number": company_num,
                "company_name": company_name,
                "status": status,
                "company_type": company_type,
            },
        )
        return item


def _parse_ch_date(date_str: str) -> date | None:
    """Parse Companies House Datum (YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
