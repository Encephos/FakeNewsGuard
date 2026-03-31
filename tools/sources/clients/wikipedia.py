"""Wikipedia – Source-Adapter für enzyklopädische Kontext-Snippets.

API-Dokumentation: https://www.mediawiki.org/wiki/API:REST_API

Endpunkte (deutschsprachige Wikipedia):
    Suche:       GET https://de.wikipedia.org/w/rest.php/v1/search/page?q={query}&limit={n}
    Seiten-Info: GET https://de.wikipedia.org/w/rest.php/v1/page/{title}/bare
    Summary:     GET https://de.wikipedia.org/api/rest_v1/page/summary/{title}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich.
    - User-Agent Header empfohlen (höhere Rate-Limits).
    - Rate-Limits: ~200 req/sec für identifizierte Clients.
    - Suchresultate enthalten HTML-Excerpts → müssen gestrippt werden.

Lizenz:
    CC-BY-SA 3.0 – Attribution und ShareAlike Pflicht.
    fulltext_allowed=False: Nur Excerpts und Metadaten nutzen.
    Supplementäre Kontextquelle, nicht primäre Evidenz.

record_id-Format:
    Wikipedia-Seitenschlüssel (title slug), z.B. "Cholera".
"""

from __future__ import annotations

import logging
import re

from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)
from tools.sources.clients.base import AdapterHTTPClient, AdapterHTTPError, BaseSourceAdapter
from tools.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)

# Enzyklopädisches Wissen altert moderat.
_HALF_LIFE_YEARS = 3.0

# Regex zum Entfernen von HTML-Tags aus Wikipedia-Excerpts.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class WikipediaClient(BaseSourceAdapter):
    """Adapter für die Wikipedia REST API (deutschsprachig).

    Liefert enzyklopädische Kontext-Snippets für den EvidenceBuilderAgent.
    Dient als supplementäre Kontextquelle – nicht als primäre Evidenz.

    Hauptnutzen:
        - Definitionen und Hintergrundinformationen
        - Kontext für Entitäten (Personen, Orte, Organisationen)
        - Quellenlisten als Startpunkt für weitere Recherche

    Verwendung::

        client = WikipediaClient()
        items = client.search("Cholera", max_results=3)
        detail = client.fetch_details("Cholera")
    """

    config = SourceRegistry.get("wikipedia")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://de.wikipedia.org/w/rest.php/v1",
            timeout=15.0,
            max_attempts=3,
            headers={"User-Agent": "FakeNewsGuard/1.0 (academic research)"},
        )
        # Separater Client für die Summary-API (anderer Basispfad).
        self._summary_http = AdapterHTTPClient(
            "https://de.wikipedia.org/api/rest_v1",
            timeout=15.0,
            max_attempts=2,
            headers={"User-Agent": "FakeNewsGuard/1.0 (academic research)"},
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche in der deutschsprachigen Wikipedia.

        Nutzt den REST-API-Search-Endpoint. HTML in Excerpts wird gestrippt.
        Bei API-Fehlern wird eine leere Liste zurückgegeben.
        """
        params = {
            "q": query,
            "limit": min(max_results, 50),
        }

        try:
            raw = self._http.get("/search/page", params)
        except AdapterHTTPError as exc:
            logger.warning("Wikipedia Suche fehlgeschlagen: %s", exc)
            return []

        pages = raw.get("pages") or []
        items: list[OfficialEvidenceItem] = []
        for record in pages[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("Wikipedia normalize() fehlgeschlagen: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe Wikipedia-Seitensummary per Titel ab.

        Nutzt die Summary-API für reichhaltigere Daten (Beschreibung, Thumbnail, etc.).

        Args:
            record_id: Wikipedia-Seitentitel oder -schlüssel, z.B. "Cholera".

        Returns:
            OfficialEvidenceItem oder None wenn nicht gefunden.
        """
        title = record_id.replace(" ", "_")
        try:
            raw = self._summary_http.get(f"/page/summary/{title}")
        except AdapterHTTPError as exc:
            logger.warning("Wikipedia fetch_details fehlgeschlagen für %r: %s", record_id, exc)
            return None

        if not raw or not raw.get("title"):
            return None

        item = self._normalize_summary(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere ein Wikipedia-Search-Result in ein OfficialEvidenceItem."""
        key: str = record.get("key") or record.get("title", "")
        title: str = record.get("title") or key
        description: str = record.get("description") or ""
        excerpt_html: str = record.get("excerpt") or ""

        # HTML-Tags entfernen
        excerpt = _strip_html(excerpt_html)

        # Abstract zusammenbauen
        abstract_parts = []
        if description:
            abstract_parts.append(description)
        if excerpt:
            abstract_parts.append(excerpt)
        abstract = " – ".join(abstract_parts) if abstract_parts else title

        url = f"https://de.wikipedia.org/wiki/{key}" if key else ""

        facts: list[NormalizedFact] = []
        if abstract:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.CONTEXT_SUMMARY,
                    subject=title[:120],
                    predicate="Enzyklopädie-Eintrag (Wikipedia DE)",
                    value=abstract[:200],
                    source_snippet=abstract[:400],
                    confidence=0.7,
                )
            )

        recency = compute_recency_score(None, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=key,
            title=title,
            url=url,
            abstract=abstract[:1200],
            published_at=None,
            jurisdiction="global",
            entity_mentions=[],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "key": key,
                "description": description,
            },
        )
        return item

    # ── Interne Methoden ─────────────────────────────────────────────────────

    def _normalize_summary(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere eine Wikipedia-Summary-Response in ein OfficialEvidenceItem."""
        title: str = record.get("title") or ""
        description: str = record.get("description") or ""
        extract: str = record.get("extract") or ""

        # Summary-API liefert Plaintext in "extract"
        abstract = f"{description} – {extract}" if description else extract

        url = record.get("content_urls", {}).get("desktop", {}).get("page", "")
        if not url:
            key = title.replace(" ", "_")
            url = f"https://de.wikipedia.org/wiki/{key}"

        facts: list[NormalizedFact] = []
        if abstract:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.CONTEXT_SUMMARY,
                    subject=title[:120],
                    predicate="Enzyklopädie-Zusammenfassung (Wikipedia DE)",
                    value=abstract[:200],
                    source_snippet=abstract[:400],
                    confidence=0.75,
                )
            )

        recency = compute_recency_score(None, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=title.replace(" ", "_"),
            title=title,
            url=url,
            abstract=abstract[:1200],
            published_at=None,
            jurisdiction="global",
            entity_mentions=[],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "description": description,
                "type": record.get("type", ""),
            },
        )
        return item


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


def _strip_html(text: str) -> str:
    """Entferne HTML-Tags und normalisiere Whitespace.

    Wikipedia-Search-Excerpts enthalten <span class="searchmatch">-Tags
    und andere HTML-Fragmente, die für die Weiterverarbeitung gestrippt werden.
    """
    clean = _HTML_TAG_RE.sub("", text)
    return " ".join(clean.split())
