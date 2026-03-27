"""openFDA – Source-Adapter für FDA-Regulierungsdaten.

API-Dokumentation: https://open.fda.gov/apis/

Endpunkte:
    Drug Search:    GET https://api.fda.gov/drug/label.json
                        ?search={query}&limit={n}&skip={offset}
    Drug Detail:    GET https://api.fda.gov/drug/label.json?search=openfda.application_number:{appnum}
    Device Search:  GET https://api.fda.gov/device/classification.json
    Enforcement:    GET https://api.fda.gov/drug/enforcement.json

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (US Public Domain).
    - Pagination: limit + skip (offset-basiert).
    - Application Number als Primärschlüssel (z.B. "ANDA075258").
    - Full-Text-Index mit Lucene-Syntax.

record_id-Format:
    Application Number: "ANDA075258" oder "NDA202008"
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

_HALF_LIFE_YEARS = 3.0


class OpenFDAClient(BaseSourceAdapter):
    """Adapter für openFDA API (Arzneimittel- und Gerätzulassungen)."""

    config = SourceRegistry.get("openfda")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://api.fda.gov",
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
        """Suche FDA-Arzneimitteleinträge."""
        offset = (page - 1) * max_results
        params = {
            "search": query,
            "limit": min(max_results, 100),
            "skip": offset,
        }
        try:
            raw = self._http.get("/drug/label.json", params)
        except AdapterHTTPError as exc:
            logger.warning("openFDA search failed: %s", exc)
            return []

        results = raw.get("results", [])
        items = []
        for record in results[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.70
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("openFDA normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe eine Arzneimittelzulassung per Application-Number ab."""
        app_num = record_id.strip().upper()
        if not app_num:
            return None

        params = {
            "search": f"openfda.application_number:{app_num}",
            "limit": 1,
        }
        try:
            raw = self._http.get("/drug/label.json", params)
        except AdapterHTTPError as exc:
            logger.warning("openFDA fetch_details failed: %s", exc)
            return None

        results = raw.get("results", [])
        if not results:
            return None

        item = self.normalize(results[0])
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere openFDA Drug Label zu OfficialEvidenceItem."""
        # Identifikation
        openfda = record.get("openfda", {})
        app_number = openfda.get("application_number", [""])[0]
        brand_name = record.get("brand_name", [""])[0] or ""
        generic_name = record.get("generic_name", [""])[0] or ""
        drug_name = brand_name or generic_name or "Unknown Drug"

        # Indikationen
        indications = record.get("indications_and_usage", [""])[0] or ""
        contraindications = record.get("contraindications", [""])[0] or ""
        adverse_reactions = record.get("adverse_reactions", [""])[0] or ""

        # Datierung
        effective_time = record.get("set_id", "")
        published_at: date | None = None

        # URL
        url = f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={generic_name}" if generic_name else ""
        title = f"{drug_name} ({app_number})" if app_number else drug_name

        # Abstract
        abstract_parts = [
            f"Drug: {drug_name}",
            f"Generic: {generic_name}" if generic_name else None,
            f"Application: {app_number}" if app_number else None,
        ]
        if indications:
            abstract_parts.append(f"Indications: {indications[:150]}")
        abstract = " | ".join(filter(None, abstract_parts))[:1200]

        # Facts
        facts = []
        if indications:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.DRUG_INDICATION,
                    subject=drug_name,
                    predicate="FDA-approved indication",
                    value=indications[:200],
                    source_snippet=indications[:400],
                    confidence=0.95,
                )
            )
        if contraindications:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.DRUG_CONTRAINDICATION,
                    subject=drug_name,
                    predicate="Contraindication",
                    value=contraindications[:200],
                    source_snippet=contraindications[:400],
                    confidence=0.95,
                )
            )
        if adverse_reactions:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.ADVERSE_EVENT,
                    subject=drug_name,
                    predicate="Reported adverse reactions",
                    value=adverse_reactions[:200],
                    source_snippet=adverse_reactions[:400],
                    confidence=0.90,
                )
            )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=app_number,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="US",
            entity_mentions=[generic_name, brand_name],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "application_number": app_number,
                "brand_name": brand_name,
                "generic_name": generic_name,
            },
        )
        return item
