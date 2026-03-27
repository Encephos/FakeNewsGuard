"""Crossref – DOI Metadata Registry Adapter.

API-Dokumentation: https://github.com/CrossRef/rest-api-doc

Endpunkte:
    Works Search:  GET https://api.crossref.org/works
                       ?query={query}&rows={n}&offset={offset}
    Work Detail:   GET https://api.crossref.org/works/{doi}

API-Eigenschaften:
    - Keine Authentifizierung, aber Polite-Pool per mailto im User-Agent.
    - Pagination: rows + offset.
    - DOI als Primärschlüssel (z.B. "10.1038/s41591-021-01583-4").
    - JSON-Standard-Format für DOI-Metadaten (ISO 8601 Daten).

record_id-Format:
    DOI: "10.1038/s41591-021-01583-4"
"""

from __future__ import annotations

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

_HALF_LIFE_YEARS = 7.0  # Wissenschaftliche Literatur
_CONTACT_EMAIL = os.getenv("CROSSREF_CONTACT_EMAIL", "research@fakeguard.example.com")


class CrossrefClient(BaseSourceAdapter):
    """Adapter für Crossref DOI Metadata API."""

    config = SourceRegistry.get("crossref")

    def __init__(self) -> None:
        polite_agent = (
            f"FakeNewsGuard/1.0 (mailto:{_CONTACT_EMAIL})"
        )
        self._http = AdapterHTTPClient(
            "https://api.crossref.org",
            timeout=15.0,
            max_attempts=3,
            headers={"User-Agent": polite_agent},
        )

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche Werke per Keyword."""
        offset = (page - 1) * max_results
        params = {
            "query": query,
            "rows": min(max_results, 100),
            "offset": offset,
        }
        try:
            raw = self._http.get("/works", params)
        except AdapterHTTPError as exc:
            logger.warning("Crossref search failed: %s", exc)
            return []

        items_raw = raw.get("message", {}).get("items", [])
        items = []
        for record in items_raw[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("Crossref normalize failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein Werk per DOI ab."""
        doi = record_id.strip()
        if not doi.startswith("10."):
            logger.warning("Invalid Crossref DOI: %r", record_id)
            return None

        try:
            raw = self._http.get(f"/works/{doi}")
        except AdapterHTTPError as exc:
            logger.warning("Crossref fetch_details failed: %s", exc)
            return None

        if not raw or "message" not in raw:
            return None

        item = self.normalize(raw["message"])
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere Crossref Work zu OfficialEvidenceItem."""
        # Identifikation
        doi = record.get("DOI", "")
        title_parts = record.get("title", [])
        title = title_parts[0] if title_parts else ""
        container_title = record.get("container-title", [""])[0] or ""

        # Autoren
        authors = record.get("author", [])
        author_names = [
            f"{a.get('family', '')} {a.get('given', '')}".strip()
            for a in authors[:3]
        ]

        # Datierung
        issued = record.get("issued", {})
        date_parts = issued.get("date-parts", [[]])[0]
        published_at = _parse_crossref_date(date_parts)

        # Abstract / Summary
        abstract = record.get("abstract", "")
        if not abstract:
            # Fallback auf Titel
            abstract = title

        # URLs
        url = f"https://doi.org/{doi}" if doi else ""

        title_full = f"{title} ({container_title})" if container_title else title
        if authors:
            title_full = f"{author_names[0]} et al. — {title_full}"

        # Facts
        facts = []
        if abstract:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.RESEARCH_FINDING,
                    subject=title[:120],
                    predicate="Publication (Crossref)",
                    value=abstract[:200],
                    source_snippet=abstract[:400],
                    reference_period=str(date_parts[0]) if date_parts else "",
                    confidence=0.9,
                )
            )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=doi,
            title=title_full,
            url=url,
            abstract=abstract[:1200],
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=author_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "doi": doi,
                "container_title": container_title,
                "authors": author_names,
            },
        )
        return item


def _parse_crossref_date(date_parts: list) -> date | None:
    """Parse Crossref date-parts [year, month, day]."""
    if not date_parts:
        return None
    try:
        year = date_parts[0]
        month = date_parts[1] if len(date_parts) > 1 else 1
        day = date_parts[2] if len(date_parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None
