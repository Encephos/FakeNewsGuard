"""GDELT DOC API – Source-Adapter für globale Medienbeobachtung.

API-Dokumentation: https://blog.gdeltproject.org/gdelt-doc-2-0-api-unveiled/

Endpunkte:
    Artikel-Suche:  GET https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json

API-Eigenschaften:
    - Keine Authentifizierung, keine API-Keys.
    - Rate-Limits nicht formal dokumentiert – Backoff bei 429 via AdapterHTTPClient.
    - ``maxrecords`` begrenzt auf 250 pro Request.
    - ``seendate``-Format: ``YYYYMMDDTHHmmSSZ``.
    - Kein Volltext – nur Metadaten, Titel, URL und ggf. Kurzsnippet.
    - Monitoring in 100+ Sprachen; 65 live-übersetzte Sprachen.
    - Updates alle 15 Minuten.

Lizenz:
    "Available for unlimited and unrestricted use for any academic, commercial,
    or governmental use … without fee" – Zitierung + Link auf GDELT-Website Pflicht.

record_id-Format:
    Artikel-URL (GDELT hat keine nativen IDs).
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

# Nachrichten altern extrem schnell – 6-Monats-Halbwertszeit.
_HALF_LIFE_YEARS = 0.5

# GDELT seendate: "20260315T120000Z" → YYYY-MM-DD
_SEENDATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T")


class GDELTClient(BaseSourceAdapter):
    """Adapter für die GDELT DOC API (globale Medienbeobachtung).

    Hauptnutzen für Fake-News-Checks:
        - Cross-Source-Corroboration: Wie viele unabhängige Quellen berichten?
        - Geographische Streuung der Berichterstattung
        - Zeitliche Erstnennung ("earliest sighting")
        - Tone-/Sentiment-Signale pro Quelle

    Verwendung::

        client = GDELTClient()
        items = client.search("Impfpflicht Deutschland", max_results=10)
        count = client.corroboration_count("Impfpflicht Deutschland")
    """

    config = SourceRegistry.get("gdelt")

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://api.gdeltproject.org/api/v2/doc",
            timeout=20.0,
            max_attempts=3,
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Artikelsuche über die GDELT DOC API (mode=artlist).

        Bei API-Fehlern wird eine leere Liste zurückgegeben (graceful degradation).
        """
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": "3m",
            "sort": "DateDesc",
            "maxrecords": min(max_results, 250),
        }

        try:
            raw = self._http.get("/doc", params)
        except AdapterHTTPError as exc:
            logger.warning("GDELT Suche fehlgeschlagen: %s", exc)
            return []

        articles = raw.get("articles") or []
        items: list[OfficialEvidenceItem] = []
        for record in articles[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("GDELT normalize() fehlgeschlagen: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """GDELT bietet keinen ID-basierten Lookup – gibt immer None zurück."""
        return None

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere einen GDELT-Artikel-Datensatz in ein OfficialEvidenceItem."""
        url: str = record.get("url") or ""
        title: str = record.get("title") or ""
        domain: str = record.get("domain") or ""
        language: str = record.get("language") or ""
        source_country: str = record.get("sourcecountry") or ""

        # Datum parsen
        published_at = _parse_gdelt_date(record.get("seendate") or "")

        # Tone-Score (GDELT liefert manchmal ein Tone-Feld)
        tone = record.get("tone")
        tone_value = None
        if isinstance(tone, (int, float)):
            tone_value = float(tone)

        # Snippet/Abstract zusammenbauen
        snippet_parts = [title]
        if domain:
            snippet_parts.append(f"Quelle: {domain}")
        if source_country:
            snippet_parts.append(f"Land: {source_country}")
        abstract = " | ".join(snippet_parts)

        # NormalizedFacts
        facts: list[NormalizedFact] = []

        facts.append(
            NormalizedFact(
                fact_type=FactType.MEDIA_CORROBORATION,
                subject=title[:120],
                predicate="Medienberichterstattung (GDELT)",
                value=f"Quelle: {domain}",
                source_snippet=abstract[:400],
                reference_period=published_at.isoformat() if published_at else "",
                reference_entity=source_country or "global",
                confidence=0.7,
            )
        )

        if tone_value is not None:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.TONE_ANALYSIS,
                    subject=title[:120],
                    predicate="Tone-Score (GDELT)",
                    value=f"{tone_value:+.1f}",
                    numeric_value=tone_value,
                    source_snippet=f"GDELT Tone-Score: {tone_value:+.1f} für {domain}",
                    confidence=0.6,
                )
            )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        # Jurisdiction aus Ländercode ableiten
        jurisdiction = source_country.upper()[:2] if source_country else "global"

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=url,
            title=title,
            url=url,
            abstract=abstract[:1200],
            published_at=published_at,
            jurisdiction=jurisdiction,
            entity_mentions=[domain] if domain else [],
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "domain": domain,
                "language": language,
                "source_country": source_country,
                "tone": tone_value,
            },
        )
        return item

    # ── Zusatz-Methoden ──────────────────────────────────────────────────────

    def corroboration_count(self, query: str, timespan: str = "3m") -> int:
        """Zähle die Anzahl unabhängiger Domains, die zum Query berichten.

        Nützlich als Corroboration-Signal: Wird ein Thema nur von einer
        Quelle berichtet, oder von Dutzenden unabhängigen Medien?

        Returns:
            Anzahl eindeutiger Domains oder 0 bei Fehler.
        """
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "timespan": timespan,
            "sort": "DateDesc",
            "maxrecords": 250,
        }
        try:
            raw = self._http.get("/doc", params)
        except AdapterHTTPError:
            return 0

        articles = raw.get("articles") or []
        domains = {a.get("domain") for a in articles if a.get("domain")}
        return len(domains)


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


def _parse_gdelt_date(seendate: str) -> date | None:
    """Parse GDELT seendate-Format ``YYYYMMDDTHHmmSSZ`` zu date.

    Args:
        seendate: GDELT-Zeitstempel, z.B. "20260315T120000Z".

    Returns:
        date-Objekt oder None wenn Format nicht erkannt.
    """
    match = _SEENDATE_RE.match(seendate)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except (ValueError, IndexError):
        return None
