"""arXiv – Preprint Metadata Adapter (metadata-only, no fulltext).

API-Dokumentation: https://arxiv.org/help/api/user-manual

Endpunkte:
    Search:  GET http://export.arxiv.org/api/query
                 ?search_query={query}&start={offset}&max_results={n}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich.
    - Pagination: start + max_results (offset-basiert).
    - arXiv-ID Format: "YYMM.NNNNN" (z.B. "2301.12345") oder "arch-ive/0712345".
    - Atom XML-Format (wird als JSON geparst nach Konvertierung).
    - Metadata-only: kein Volltext-Abruf.

record_id-Format:
    arXiv-ID: "2301.12345"
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
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

_HALF_LIFE_YEARS = 2.0  # Preprints altern schnell


class ArXivClient(BaseSourceAdapter):
    """Adapter für arXiv Preprint API (metadata-only)."""

    config = SourceRegistry.get("arxiv")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "http://export.arxiv.org/api",
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
        """Suche arXiv-Preprints per Keyword."""
        offset = (page - 1) * max_results
        params = {
            "search_query": query,
            "start": offset,
            "max_results": min(max_results, 100),
        }
        try:
            # arXiv gibt Atom XML zurück
            raw_xml = self._http.get("/query", params)
        except AdapterHTTPError as exc:
            logger.warning("arXiv search failed: %s", exc)
            return []

        # Hack: Wenn raw_xml ein dict ist (JSON parse fehlgeschlagen),
        # geben wir leere Liste zurück. Ideal wäre XML-Parse, aber
        # AdapterHTTPClient erwartet JSON.
        if isinstance(raw_xml, dict):
            return []

        items = []
        try:
            root = ET.fromstring(str(raw_xml))
            entries = root.findall(".//entry", {"": "http://www.w3.org/2005/Atom"})
            for entry in entries[:max_results]:
                try:
                    item = self._normalize_entry(entry)
                    item.claim_relevance = 0.65
                    item.confidence = item.compute_confidence()
                    items.append(item)
                except Exception as exc:
                    logger.debug("arXiv normalize failed: %s", exc)
        except Exception as exc:
            logger.warning("arXiv XML parsing failed: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe einen Preprint per arXiv-ID ab."""
        arxiv_id = record_id.strip()
        if not arxiv_id:
            return None

        params = {
            "search_query": f"arxiv:{arxiv_id}",
            "max_results": 1,
        }
        try:
            raw_xml = self._http.get("/query", params)
        except AdapterHTTPError as exc:
            logger.warning("arXiv fetch_details failed: %s", exc)
            return None

        if isinstance(raw_xml, dict):
            return None

        try:
            root = ET.fromstring(str(raw_xml))
            entry = root.find(".//entry", {"": "http://www.w3.org/2005/Atom"})
            if entry is None:
                return None

            item = self._normalize_entry(entry)
            item.claim_relevance = 0.85
            item.confidence = item.compute_confidence()
            return item
        except Exception as exc:
            logger.warning("arXiv fetch_details parsing failed: %s", exc)
            return None

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere einen arXiv-Dict-Datensatz zu OfficialEvidenceItem.

        Spiegelt _normalize_entry() 1:1, liest aber aus Dict-Keys statt XML-Elementen.
        Setzt item.confidence = item.compute_confidence() selbst (im Gegensatz zu
        _normalize_entry(), dessen Aufrufer search()/fetch_details() dies übernehmen).

        Args:
            record: Dict mit folgenden Keys (alle optional):
                "id"         – volle arXiv-URL (z.B. "http://arxiv.org/abs/2301.12345v2")
                "title"      – Titel
                "summary"    – Abstract
                "published"  – ISO-8601 (z.B. "2023-01-15T12:34:56Z")
                "authors"    – Liste von Autorennamen (Strings)
                "categories" – Liste von Kategorien (Strings)

        Returns:
            Vollständig befülltes OfficialEvidenceItem mit gesetztem confidence.
        """
        arxiv_url = record.get("id", "")
        arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""

        title = record.get("title", "")
        summary = record.get("summary", "")

        published_str = record.get("published", "")
        published_at = _parse_arxiv_date(published_str)

        author_names = record.get("authors", [])[:3]

        categories_list = record.get("categories", [])
        category = categories_list[0] if categories_list else ""

        url = arxiv_url

        facts = [
            NormalizedFact(
                fact_type=FactType.RESEARCH_FINDING,
                subject=title[:120],
                predicate="arXiv Preprint",
                value=summary[:200] if summary else "Preprint submitted to arXiv",
                source_snippet=summary[:400] if summary else title,
                reference_period=published_str[:10] if published_str else "",
                qualifier=f"Category: {category}" if category else "",
                confidence=0.85,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=arxiv_id,
            title=title,
            url=url,
            abstract=summary[:1200] if summary else "",
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=author_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "arxiv_id": arxiv_id,
                "category": category,
                "authors": author_names,
            },
        )
        item.confidence = item.compute_confidence()
        return item

    def _normalize_entry(self, entry: ET.Element) -> OfficialEvidenceItem:
        """Konvertiere arXiv Atom-Entry zu OfficialEvidenceItem."""
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        # IDs
        id_elem = entry.find("atom:id", ns)
        arxiv_url = id_elem.text if id_elem is not None else ""
        arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""

        # Titel
        title_elem = entry.find("atom:title", ns)
        title = title_elem.text if title_elem is not None else ""

        # Summary
        summary_elem = entry.find("atom:summary", ns)
        summary = summary_elem.text if summary_elem is not None else ""

        # Datum
        published_elem = entry.find("atom:published", ns)
        published_str = published_elem.text if published_elem is not None else ""
        published_at = _parse_arxiv_date(published_str)

        # Autoren
        authors = entry.findall("atom:author", ns)
        author_names = [
            a.find("atom:name", ns).text
            for a in authors[:3]
            if a.find("atom:name", ns) is not None
        ]

        # Kategorien (Fachgebiet)
        categories = entry.findall("arxiv:primary-category", ns)
        category = categories[0].get("term", "") if categories else ""

        url = arxiv_url

        # Facts
        facts = [
            NormalizedFact(
                fact_type=FactType.RESEARCH_FINDING,
                subject=title[:120],
                predicate="arXiv Preprint",
                value=summary[:200] if summary else "Preprint submitted to arXiv",
                source_snippet=summary[:400] if summary else title,
                reference_period=published_str[:10] if published_str else "",
                qualifier=f"Category: {category}" if category else "",
                confidence=0.85,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=arxiv_id,
            title=title,
            url=url,
            abstract=summary[:1200] if summary else "",
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=author_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "arxiv_id": arxiv_id,
                "category": category,
                "authors": author_names,
            },
        )
        return item


def _parse_arxiv_date(date_str: str) -> date | None:
    """Parse arXiv ISO 8601 Datum (z.B. '2023-01-15T12:34:56Z')."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, IndexError):
        return None
