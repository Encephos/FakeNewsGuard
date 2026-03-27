"""OpenAlex – Source-Adapter für wissenschaftliche Literatur.

API-Dokumentation: https://docs.openalex.org/

Endpunkte:
    Werke-Suche:    GET https://api.openalex.org/works?search={query}&per-page={n}&page={p}
    Werk-Detail:    GET https://api.openalex.org/works/{id}
                    GET https://api.openalex.org/works/doi:{doi}

API-Eigenschaften:
    - Keine Authentifizierung erforderlich (CC0-Lizenz).
    - AuthMode.EMAIL_POLITE: mailto-Parameter im User-Agent für den "Polite Pool"
      (höhere Rate-Limits, bevorzugter Server-Zugang).
    - Abstracts als Inverted Index → werden intern rekonstruiert.
    - Pagination: page + per-page Parameter.
    - OpenAlex-ID-Format: "W" + Zahl, z.B. "W2741809807".

Polite-Pool-Konvention (OpenAlex & Crossref):
    User-Agent: "FakeNewsGuard/1.0 (mailto:research@example.com)"
    Betreiber sollten OPENALEX_CONTACT_EMAIL in der Umgebung setzen.

record_id-Format (gemäß models/source_evidence.py):
    DOI-URL:      "https://doi.org/10.1038/s41591-021-01583-4"
    OpenAlex-ID:  "W2741809807"
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

# Halbwertszeit wissenschaftlicher Publikationen: Literatur altert langsam.
_HALF_LIFE_YEARS = 7.0

# E-Mail-Adresse für OpenAlex Polite Pool (überschreibbar per Env-Variable).
_CONTACT_EMAIL = os.getenv("OPENALEX_CONTACT_EMAIL", "research@fakeguard.example.com")

_OPENALEX_BASE = "https://api.openalex.org"


class OpenAlexClient(BaseSourceAdapter):
    """Adapter für die OpenAlex API (wissenschaftliche Literatur, CC0).

    Liefert normalisierte Publikations-Items für den EvidenceBuilderAgent.
    Besonders geeignet für Fakten-Checks mit wissenschaftlichem Bezug
    (Medizin, Klinik, Naturwissenschaften).

    Polite-Pool-Hinweis:
        Umgebungsvariable OPENALEX_CONTACT_EMAIL setzen für höhere Rate-Limits.

    Verwendung::

        client = OpenAlexClient()
        items = client.search("covid vaccine efficacy RCT", max_results=5)
        detail = client.fetch_details("W2741809807")
        detail = client.fetch_details("https://doi.org/10.1038/s41591-021-01583-4")
    """

    config = SourceRegistry.get("openalex")

    def __init__(self) -> None:
        # Polite-Pool: E-Mail im User-Agent verringert Rate-Limit-Risiko.
        polite_agent = (
            f"FakeNewsGuard/1.0 (academic research; mailto:{_CONTACT_EMAIL})"
        )
        self._http = AdapterHTTPClient(
            _OPENALEX_BASE,
            timeout=15.0,
            max_attempts=3,
            headers={"User-Agent": polite_agent},
        )

    # ── Pflichtmethoden ───────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Volltext-Suche in OpenAlex Works.

        Nutzt die OpenAlex-Suchrelevanz-Ranking (BM25-basiert).
        Gibt max. max_results normalisierte Items zurück.

        Bei API-Fehlern wird eine leere Liste zurückgegeben (graceful degradation).
        """
        params = {
            "search": query,
            "per-page": min(max_results, 25),  # OpenAlex: max 200 per_page
            "page": page,
            "select": (
                "id,doi,title,publication_date,cited_by_count,"
                "open_access,host_venue,authorships,concepts,"
                "abstract_inverted_index,primary_location"
            ),
        }

        try:
            raw = self._http.get("/works", params)
        except AdapterHTTPError as exc:
            logger.warning("OpenAlex Suche fehlgeschlagen: %s", exc)
            return []

        results = raw.get("results", [])
        items = []
        for record in results[:max_results]:
            try:
                item = self.normalize(record)
                item.claim_relevance = 0.65
                item.confidence = item.compute_confidence()
                items.append(item)
            except Exception as exc:
                logger.debug("OpenAlex normalize() fehlgeschlagen: %s", exc)

        return items

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein einzelnes Werk per OpenAlex-ID oder DOI ab.

        Args:
            record_id: OpenAlex-ID (z.B. "W2741809807") oder
                       DOI-URL (z.B. "https://doi.org/10.1038/...") oder
                       Kurzform ("10.1038/...").

        Returns:
            OfficialEvidenceItem oder None wenn nicht gefunden.
        """
        # Pfad je nach ID-Format:
        if record_id.startswith("W") and record_id[1:].isdigit():
            path = f"/works/{record_id}"
        elif record_id.startswith("https://doi.org/"):
            doi_part = record_id.removeprefix("https://doi.org/")
            path = f"/works/doi:{doi_part}"
        elif "/" in record_id and not record_id.startswith("W"):
            # Kurz-DOI wie "10.1234/xyz"
            path = f"/works/doi:{record_id}"
        else:
            path = f"/works/{record_id}"

        try:
            raw = self._http.get(path)
        except AdapterHTTPError as exc:
            logger.warning("OpenAlex fetch_details fehlgeschlagen für %r: %s", record_id, exc)
            return None

        if not raw or not raw.get("id"):
            return None

        item = self.normalize(raw)
        item.claim_relevance = 0.85
        item.confidence = item.compute_confidence()
        return item

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere ein OpenAlex-Work-Objekt in ein OfficialEvidenceItem.

        Verarbeitet das OpenAlex-JSON-Format inklusive Inverted-Index-Abstract,
        Venue-Metadaten und Open-Access-Status.
        """
        # ── IDs ──────────────────────────────────────────────────────────────
        openalex_id: str = record.get("id", "")
        short_id = openalex_id.rsplit("/", 1)[-1] if openalex_id else ""
        doi_url: str = record.get("doi") or ""
        record_id = doi_url or short_id

        # ── Inhalt ───────────────────────────────────────────────────────────
        title: str = record.get("title") or ""

        # Abstract aus Inverted Index rekonstruieren (OpenAlex-Eigenformat).
        abstract = _reconstruct_abstract(record.get("abstract_inverted_index"))
        if not abstract:
            # Fallback: primary_location snippet falls vorhanden
            pl = record.get("primary_location") or {}
            abstract = (pl.get("landing_page_url") or "")[:1200]

        # ── Venue / Journal ──────────────────────────────────────────────────
        venue_name = ""
        host_venue = record.get("host_venue") or record.get("primary_location") or {}
        if isinstance(host_venue, dict):
            venue_name = host_venue.get("display_name") or host_venue.get("source", {}).get("display_name", "")

        # ── Datum ────────────────────────────────────────────────────────────
        pub_date_str: str = record.get("publication_date") or ""
        published_at: date | None = _parse_date(pub_date_str)

        # ── URL ──────────────────────────────────────────────────────────────
        url = doi_url or openalex_id or ""
        oa = record.get("open_access") or {}
        if oa.get("oa_url"):
            url = oa["oa_url"]

        # ── Konzepte → entity_mentions ───────────────────────────────────────
        concepts = record.get("concepts") or []
        entity_mentions = [
            c["display_name"] for c in concepts[:8] if c.get("display_name")
        ]
        if venue_name:
            entity_mentions.append(venue_name)

        # ── Zitationsanzahl → NormalizedFact ─────────────────────────────────
        cited_by = record.get("cited_by_count")
        facts: list[NormalizedFact] = []

        if abstract:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.RESEARCH_FINDING,
                    subject=title[:120],
                    predicate="Hauptbefund (Abstract)",
                    value=abstract[:200],
                    source_snippet=abstract[:400],
                    reference_period=pub_date_str[:10],
                    qualifier=f"Venue: {venue_name}" if venue_name else "",
                    confidence=0.9,
                )
            )

        if cited_by is not None:
            facts.append(
                NormalizedFact(
                    fact_type=FactType.CITATION_COUNT,
                    subject=title[:120],
                    predicate="Zitationsanzahl (OpenAlex)",
                    value=str(cited_by),
                    numeric_value=float(cited_by),
                    unit="Zitierungen",
                    source_snippet=f"Zitiert von {cited_by} Werken laut OpenAlex.",
                    confidence=0.95,
                )
            )

        recency = compute_recency_score(published_at, half_life_years=_HALF_LIFE_YEARS)

        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id=record_id,
            title=title,
            url=url,
            abstract=abstract[:1200],
            published_at=published_at,
            jurisdiction="global",
            entity_mentions=entity_mentions,
            recency_score=recency,
            normalized_facts=facts,
            raw_fields={
                "openalex_id": short_id,
                "doi": doi_url,
                "cited_by_count": cited_by,
                "venue": venue_name,
                "is_oa": oa.get("is_oa"),
            },
        )
        return item


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Rekonstruiere den Abstract aus dem OpenAlex Inverted-Index-Format.

    OpenAlex speichert Abstracts als {Wort: [Position1, Position2, ...]} Dict,
    um Deduplizierung zu ermöglichen. Diese Funktion stellt den Originaltext
    wieder her.

    Args:
        inverted_index: Dict mit Wort → Positions-Liste oder None.

    Returns:
        Rekonstruierter Plaintext-Abstract. Leerstring wenn nicht vorhanden.
    """
    if not inverted_index:
        return ""
    position_to_word: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            position_to_word[pos] = word
    if not position_to_word:
        return ""
    return " ".join(position_to_word[i] for i in sorted(position_to_word))


def _parse_date(date_str: str) -> date | None:
    """Parse ISO-Datum-String (YYYY-MM-DD oder YYYY) zu date-Objekt.

    OpenAlex liefert Daten als "2023-11-15" oder nur "2023".
    Gibt None zurück wenn das Format nicht erkannt wird.
    """
    if not date_str:
        return None
    parts = date_str.split("-")
    try:
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == 1 and parts[0].isdigit():
            return date(int(parts[0]), 12, 31)
    except (ValueError, IndexError):
        pass
    return None
