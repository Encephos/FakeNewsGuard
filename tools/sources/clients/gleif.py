"""GLEIF (Global Legal Entity Identifier Foundation) – Unternehmensregister-Adapter.

API-Dokumentation: https://www.gleif.org/en/about-lei/gleif-data-quality-processes

Endpunkte:
    LEI-Suche:   GET https://leidata.gleif.org/api/v1/lei-records
                     ?search={query}&page[size]={n}&page[number]={p}
    LEI-Detail:  GET https://leidata.gleif.org/api/v1/lei-records/{lei_id}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (CC BY 4.0).
    - Pagination: page[number] + page[size].
    - LEI-ID: ISO 17442 Standard, 20 alphanumerische Zeichen.
    - record_id: LEI selbst (z.B. "529900HNOAA1KXQJUQ27").

Datenstruktur:
    LEI-Records sind geschachtelt: data[].attributes.lei-record
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

_HALF_LIFE_YEARS = 1.5  # Unternehmensregister altert schnell (Fusionen, Auflösungen)


class GLEIFClient(BaseSourceAdapter):
    """Adapter für die GLEIF LEI-Datenbank (Unternehmensregistrierung)."""

    config = SourceRegistry.get("gleif")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://leidata.gleif.org/api/v1",
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
        """Suche Unternehmenseinträge per Name/LEI."""
        params = {
            "search": query,
            "page[size]": min(max_results, 100),
            "page[number]": page,
        }
        try:
            raw = self._http.get("/lei-records", params)
        except AdapterHTTPError as exc:
            logger.warning("GLEIF search failed: %s", exc)
            return []

        records = raw.get("data", [])
        items = []
        for record in records[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("GLEIF normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe eine LEI per ID ab."""
        lei = record_id.strip().upper()
        if not (len(lei) == 20 and lei.isalnum()):
            logger.warning("Invalid GLEIF LEI format: %r", record_id)
            return None

        try:
            raw = self._http.get(f"/lei-records/{lei}")
        except AdapterHTTPError as exc:
            logger.warning("GLEIF fetch_details failed for %r: %s", record_id, exc)
            return None

        if not raw or "data" not in raw:
            return None

        item = self.normalize(raw["data"])
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere GLEIF-Record zu OfficialEvidenceItem."""
        attrs = record.get("attributes", {})
        lei_record = attrs.get("lei-record", {})

        # Identifikation
        lei_id = lei_record.get("lei", "")
        name = lei_record.get("legal-name", {})
        legal_name = name.get("value", "") if isinstance(name, dict) else name

        # Gründung / Registrierung
        registration = lei_record.get("registration", {})
        reg_date = registration.get("registration-date", "")
        status = registration.get("status", "ACTIVE")

        # Rechtsperson
        entity = lei_record.get("entity", {})
        jurisdiction = entity.get("jurisdiction", "")
        entity_type = entity.get("entity-category", "")

        # Gültig bis
        validity = lei_record.get("managing-lou", {})
        next_renewal = validity.get("validation", {}).get("next-renewal-date", "")
        published_at = _parse_gleif_date(reg_date) or _parse_gleif_date(next_renewal)

        # URLs / Title
        url = f"https://search.gleif.org/record/{lei_id}" if lei_id else ""
        title = f"{legal_name} ({lei_id})" if legal_name and lei_id else legal_name or lei_id

        abstract_parts = [
            f"Legal Entity: {legal_name}",
            f"LEI: {lei_id}",
            f"Status: {status}",
            f"Jurisdiction: {jurisdiction}",
            f"Type: {entity_type}",
        ]
        abstract = " | ".join(filter(None, abstract_parts))[:1200]

        # Facts
        facts = [
            NormalizedFact(
                fact_type=FactType.ENTITY_REGISTRATION,
                subject=legal_name,
                predicate="LEI (Legal Entity Identifier)",
                value=lei_id,
                reference_entity=jurisdiction,
                qualifier=entity_type,
                source_snippet=f"{legal_name}: LEI={lei_id}, Status={status}",
                confidence=1.0,
            ),
            NormalizedFact(
                fact_type=FactType.ENTITY_STATUS,
                subject=legal_name,
                predicate="Registration Status",
                value=status,
                reference_period=reg_date[:10] if reg_date else "",
                source_snippet=f"Status: {status} (registered {reg_date[:10]})",
                confidence=0.95,
            ),
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=lei_id,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction=jurisdiction,
            entity_mentions=[legal_name],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "lei": lei_id,
                "legal_name": legal_name,
                "status": status,
                "entity_type": entity_type,
                "jurisdiction": jurisdiction,
            },
        )
        return item


def _parse_gleif_date(date_str: str) -> date | None:
    """Parse ISO Datum."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
