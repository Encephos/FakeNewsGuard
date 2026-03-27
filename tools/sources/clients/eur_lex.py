"""EUR-Lex – EU Legislation & Court Decisions Adapter.

API-Dokumentation: https://eur-lex.europa.eu/content/help/faq/intro.html

Zugang:
    REST API: https://eur-lex.europa.eu/CELEX_API/search
              ?format=json&qId={celex_number}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (Public Domain).
    - CELEX-Nummern als Primärschlüssel (z.B. "32016R0679" für GDPR).
    - Format: YYssncxxxxx (YY=Jahr, ss=Sektor, n=Typ, c=Übergangsform, xxxxx=lfd. Nr.).

record_id-Format:
    CELEX-Nummer: "32016R0679" (GDPR) oder "62020CJ0799" (Court case)
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

_HALF_LIFE_YEARS = 10.0  # Rechtsdokumente altern sehr langsam


class EURLexClient(BaseSourceAdapter):
    """Adapter für EUR-Lex (EU Legislation und Court Decisions)."""

    config = SourceRegistry.get("eur_lex")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://eur-lex.europa.eu/CELEX_API",
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
        """Suche EUR-Lex-Dokumente.

        Hinweis: EUR-Lex hat keine echte Keyword-Suche über REST API.
        Für MVP: einfach leere Liste zurückgeben.
        """
        logger.debug("EUR-Lex search: no free-text API available")
        return []

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein EUR-Lex-Dokument per CELEX-Nummer ab."""
        celex = record_id.strip().upper()
        if not celex or len(celex) < 8:
            logger.warning("Invalid EUR-Lex CELEX number: %r", record_id)
            return None

        params = {
            "format": "json",
            "qId": celex,
        }
        try:
            raw = self._http.get("/search", params)
        except AdapterHTTPError as exc:
            logger.warning("EUR-Lex fetch_details failed: %s", exc)
            return None

        if not raw or not raw.get("results"):
            return None

        result = raw["results"][0]
        item = self.normalize(result)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere EUR-Lex Result zu OfficialEvidenceItem."""
        # EUR-Lex gibt unterschiedliche Feldnamen je nach Dokumenttyp.
        # Vereinfachte Annahme:
        celex = record.get("celex", "")
        title = record.get("title", "") or record.get("name", "")
        sector = record.get("sector", "")
        type_desc = record.get("type", "")

        # Datum (EUR-Lex format: "YYYY-MM-DD")
        date_doc = record.get("date_document", "") or record.get("pubOJ", "")
        published_at = _parse_eur_lex_date(date_doc)

        url = f"https://eur-lex.europa.eu/eli/{celex}/en" if celex else ""

        abstract = f"EU {type_desc}: {title}"[:1200]

        facts = [
            NormalizedFact(
                fact_type=FactType.LEGAL_PROVISION,
                subject=title,
                predicate="EU Legal Document",
                value=f"CELEX: {celex}",
                reference_period=date_doc[:10] if date_doc else "",
                qualifier=f"Sector: {sector}, Type: {type_desc}",
                source_snippet=f"{celex}: {title}",
                confidence=1.0,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=celex,
            title=title,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="EU",
            entity_mentions=[sector, type_desc],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "celex": celex,
                "sector": sector,
                "type": type_desc,
            },
        )
        return item


def _parse_eur_lex_date(date_str: str) -> date | None:
    """Parse EUR-Lex Datum (ISO 8601 oder freetext)."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
