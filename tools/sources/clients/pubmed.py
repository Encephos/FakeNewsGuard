"""PubMed – Biomedical Literature Metadata Adapter (metadata-only).

API-Dokumentation: https://www.ncbi.nlm.nih.gov/books/NBK25499/

Endpunkte (NCBI eUtilities):
    Suche:   GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
                 ?db=pubmed&term={query}&retmax={n}
    Detail:  GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
                 ?db=pubmed&id={pmid}&rettype=json&retmode=json

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (NCBI Public Domain).
    - Polite: max. 3 requests/s ohne API-Key, 10/s mit Key.
    - PMID (PubMed ID) als Primärschlüssel.
    - XML und JSON-Format verfügbar.
    - METADATA-ONLY: kein Volltext-Zugriff ohne PubMedCentral PMC-ID.

record_id-Format:
    PMID: "34747358" (10-stellige Zahl)
"""

from __future__ import annotations

import logging
import re
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

_HALF_LIFE_YEARS = 7.0  # Biomedizinische Publikationen


class PubMedClient(BaseSourceAdapter):
    """Adapter für PubMed API (biomedical literature, metadata-only)."""

    config = SourceRegistry.get("pubmed")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
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
        """Suche PubMed nach Keyword (metadata-only)."""
        offset = (page - 1) * max_results
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": min(max_results, 100),
            "retstart": offset,
            "rettype": "json",
        }
        try:
            raw = self._http.get("/esearch.fcgi", params)
        except AdapterHTTPError as exc:
            logger.warning("PubMed search failed: %s", exc)
            return []

        # Fallback: esearch.fcgi gibt JSON zurück, aber nur PMID-Liste.
        # Wir müssen zusätzliche efetch-Aufrufe machen für Metadaten.
        result = raw.get("esearchresult", {})
        pmids = result.get("idlist", [])

        items = []
        for pmid in pmids[:max_results]:
            try:
                item = self.fetch_details(pmid)
                if item:
                    item.claim_relevance = 0.65
                    item.confidence = item.compute_confidence()
                    items.append(item)
            except Exception as exc:
                logger.debug("PubMed fetch for pmid %s failed: %s", pmid, exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe eine PubMed-Publikation per PMID ab (metadata-only)."""
        pmid = record_id.strip()
        if not pmid or not pmid.isdigit():
            logger.warning("Invalid PubMed PMID: %r", record_id)
            return None

        params = {
            "db": "pubmed",
            "id": pmid,
            "rettype": "json",
            "retmode": "json",
        }
        try:
            raw = self._http.get("/efetch.fcgi", params)
        except AdapterHTTPError as exc:
            logger.warning("PubMed fetch_details failed: %s", exc)
            return None

        if not raw:
            return None

        # JSON-Antwort ist verschachtelt: result -> uids -> [0] -> PMID
        result = raw.get("result", {})
        if pmid not in result:
            return None

        item = self.normalize(result[pmid])
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere PubMed Article zu OfficialEvidenceItem (metadata-only)."""
        pmid = record.get("uid", record.get("PMID", ""))
        title = record.get("title", "")

        # Autoren
        authors = record.get("authors", [])
        author_names = [
            a.get("name", "") for a in authors[:3] if a.get("name")
        ]

        # Abstract
        abstract_text = record.get("abstract", "")
        if not abstract_text:
            # Fallback
            abstract_text = title

        # Datum
        pubdate = record.get("pubdate", "")
        year_str = pubdate.split()[0] if pubdate else ""
        published_at = _parse_pubmed_date(pubdate or year_str)

        # Journal
        journal = record.get("source", "") or record.get("fulljournalname", "")

        # URL
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        title_full = f"{title} ({journal})" if journal else title
        if author_names:
            title_full = f"{author_names[0]} et al. — {title_full}"

        abstract_parts = [abstract_text[:200]]
        if journal:
            abstract_parts.append(f"Published in: {journal}")
        abstract = " | ".join(abstract_parts)[:1200]

        # Facts
        facts = [
            NormalizedFact(
                fact_type=FactType.RESEARCH_FINDING,
                subject=title[:120],
                predicate="PubMed Article (metadata)",
                value=abstract_text[:200],
                source_snippet=abstract_text[:400],
                reference_period=year_str,
                qualifier=f"Authors: {', '.join(author_names)}" if author_names else "",
                confidence=0.85,
            )
        ]

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=pmid,
            title=title_full,
            url=url,
            abstract=abstract,
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=author_names,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "pmid": pmid,
                "journal": journal,
                "authors": author_names,
            },
        )
        return item


def _parse_pubmed_date(date_str: str) -> date | None:
    """Parse PubMed Datum (diverse Formate: YYYY, YYYY-MM, YYYY-MM-DD)."""
    if not date_str:
        return None
    # Extrahiere Zahlen
    parts = re.findall(r"\d+", date_str)
    if not parts:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None
