"""DailyMed – FDA Drug Labeling Adapter.

API-Dokumentation: https://dailymed.nlm.nih.gov/

Zugang über Nationale Library of Medicine NLM API:
    Search:  GET https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json
                 ?drug_name={drug}&page={n}
    Detail:  GET https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{spl_id}.xml

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (US Public Domain).
    - SPL-SetID als Primärschlüssel (FDA Standard).
    - JSON/XML-Format.
    - Arzneimittel-Etikettierungen (Packungsbeilagen).

record_id-Format:
    SPL Set-ID: z.B. "38b6ddea-39b7-3b7e-9ab2-3a0a34a71652"
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


class DailyMedClient(BaseSourceAdapter):
    """Adapter für DailyMed API (FDA Drug Labeling)."""

    config = SourceRegistry.get("dailymed")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://dailymed.nlm.nih.gov/dailymed/services/v2",
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
        """Suche Arzneimittel-Labels per Wirkstoff/Brand-Name."""
        params = {
            "drug_name": query,
            "page": page,
        }
        try:
            raw = self._http.get("/spls.json", params)
        except AdapterHTTPError as exc:
            logger.warning("DailyMed search failed: %s", exc)
            return []

        spls = raw.get("data", [])
        items = []
        for record in spls[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("DailyMed normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein Label per SPL-ID ab."""
        spl_id = record_id.strip()
        if not spl_id:
            return None

        try:
            # DailyMed XML API
            raw = self._http.get(f"/spls/{spl_id}.xml")
        except AdapterHTTPError as exc:
            logger.warning("DailyMed fetch_details failed: %s", exc)
            return None

        if not raw:
            return None

        item = self.normalize(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere DailyMed SPL-Record zu OfficialEvidenceItem."""
        # Vereinfachte DailyMed-Integration (echte XML-Verarbeitung wäre komplexer)
        spl_id = record.get("spl_id", record.get("id", ""))
        drug_name = record.get("drugName", record.get("drug_name", ""))
        title = record.get("title", drug_name)
        url = f"https://dailymed.nlm.nih.gov/dailymed/archives/fdaDrugInfo.cfm?archiveid={spl_id}"

        # Keine exakte Datierung in DailyMed API
        published_at = None

        abstract = f"DailyMed: {drug_name} medication guide and prescribing information."[:1200]

        facts = [
            NormalizedFact(
                fact_type=FactType.DRUG_INDICATION,
                subject=drug_name,
                predicate="DailyMed Record",
                value=title,
                source_snippet=f"{drug_name}: FDA-approved prescribing information in DailyMed.",
                confidence=0.90,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=spl_id,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="US",
            entity_mentions=[drug_name],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "spl_id": spl_id,
                "drug_name": drug_name,
            },
        )
        return item
