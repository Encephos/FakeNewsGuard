"""Domain-Trust-Signal via OpenPageRank API.

Liefert ein PageRank-basiertes Reputationssignal für Domains.
Wird optional in der Evidence-Scoring-Pipeline genutzt, um das
domain_tier-Signal mit einer unabhängigen Metrik zu ergänzen.

API-Dokumentation: https://www.domcop.com/openpagerank/documentation

Endpunkt:
    GET https://openpagerank.com/api/v1.0/getPageRank?domains[]={domain}

API-Eigenschaften:
    - API-Key erforderlich (Header ``API-OPR``).
    - Rate-Limit: 10.000 Calls/Stunde.
    - Batch-Support: Bis zu 100 Domains pro Request.
    - Kostenlos für kommerzielle Nutzung.

Wichtig:
    - PageRank ist KEIN Wahrheitsindikator – nur ein Domain-Autoritätssignal.
    - Wird als schwaches, ergänzendes Signal behandelt.
    - Graceful degradation: Ohne API-Key wird das Signal übersprungen.

Umgebungsvariable:
    OPENPAGERANK_API_KEY – API-Key für OpenPageRank (optional).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

from config.infrastructure import HTTPTimeoutsConfig
from tools.data_loader import pagerank_adjustments
from tools.retry import retry_call

# PageRank Tier-Adjustments (aus data/domain_tiers.yaml)
_PR_ADJUSTMENTS = pagerank_adjustments()

logger = logging.getLogger(__name__)

_API_BASE = "https://openpagerank.com/api/v1.0/getPageRank"
_TIMEOUT = HTTPTimeoutsConfig().domain_trust


@dataclass(frozen=True)
class DomainRankResult:
    """Ergebnis einer OpenPageRank-Abfrage für eine Domain.

    Attributes:
        domain:             Abgefragte Domain.
        page_rank_integer:  PageRank auf ganzzahliger Skala (0–10).
        page_rank_decimal:  PageRank als Dezimalwert.
        rank:               Globaler Rangplatz (niedriger = besser).
    """

    domain: str
    page_rank_integer: int
    page_rank_decimal: float
    rank: int


class DomainTrustClient:
    """Client für das OpenPageRank Domain-Trust-Signal.

    Verwendung::

        client = DomainTrustClient()
        if client.is_available:
            result = client.get_rank("reuters.com")
            adjustment = client.tier_adjustment("reuters.com")

    Tier-Adjustment-Logik:
        PR >= 7  → -1.0 (Tier verbessern, z.B. 3 → 2)
        PR >= 5  → -0.5
        PR >= 3  →  0.0 (neutral)
        PR <  3  → +0.5 (Tier verschlechtern)
        Nicht verfügbar → 0.0 (kein Einfluss)
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("OPENPAGERANK_API_KEY", "")
        self._cache: dict[str, DomainRankResult | None] = {}

    @property
    def is_available(self) -> bool:
        """True wenn ein API-Key konfiguriert ist."""
        return bool(self._api_key)

    def get_rank(self, domain: str) -> DomainRankResult | None:
        """Hole den PageRank für eine einzelne Domain.

        Args:
            domain: Domain ohne Protokoll, z.B. "reuters.com".

        Returns:
            DomainRankResult oder None wenn nicht gefunden/Fehler.
        """
        domain = domain.lower().strip()
        if domain in self._cache:
            return self._cache[domain]

        if not self._api_key:
            return None

        result = self._fetch([domain]).get(domain)
        self._cache[domain] = result
        return result

    def get_ranks_batch(self, domains: list[str]) -> dict[str, DomainRankResult]:
        """Hole PageRanks für mehrere Domains (max. 100 pro Request).

        Args:
            domains: Liste von Domains.

        Returns:
            Dict von Domain → DomainRankResult (nur erfolgreiche Abfragen).
        """
        if not self._api_key or not domains:
            return {}

        # Cache-Hits filtern
        uncached = [d.lower().strip() for d in domains if d.lower().strip() not in self._cache]
        results: dict[str, DomainRankResult] = {}

        # Cached results sammeln
        for d in domains:
            d_lower = d.lower().strip()
            if d_lower in self._cache and self._cache[d_lower] is not None:
                results[d_lower] = self._cache[d_lower]

        # Uncached in Batches à 100 abfragen
        for i in range(0, len(uncached), 100):
            batch = uncached[i : i + 100]
            fetched = self._fetch(batch)
            for d, r in fetched.items():
                self._cache[d] = r
                results[d] = r

        return results

    def tier_adjustment(self, domain: str) -> float:
        """Berechne die Tier-Anpassung basierend auf dem PageRank.

        Returns:
            Float-Wert der zum domain_tier addiert werden kann:
            -1.0 (verbessern), -0.5, 0.0 (neutral), +0.5 (verschlechtern).
        """
        result = self.get_rank(domain)
        if result is None:
            return 0.0

        pr = result.page_rank_integer
        # _PR_ADJUSTMENTS ist absteigend nach min_rank sortiert (7, 5, 3, 0)
        for min_rank, adjustment in _PR_ADJUSTMENTS:
            if pr >= min_rank:
                return adjustment
        # Fallback falls Liste leer
        return 0.0

    def _fetch(self, domains: list[str]) -> dict[str, DomainRankResult]:
        """HTTP-Abfrage an die OpenPageRank API.

        Args:
            domains: Liste von Domains (max. 100).

        Returns:
            Dict von Domain → DomainRankResult.
        """
        params = [("domains[]", d) for d in domains]

        def _do_request() -> dict:
            response = httpx.get(
                _API_BASE,
                params=params,
                headers={"API-OPR": self._api_key},
                timeout=_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.json()

        try:
            data = retry_call(_do_request, max_attempts=2, base_delay=1.0)
        except Exception as exc:
            logger.warning("OpenPageRank Abfrage fehlgeschlagen: %s", exc)
            return {}

        results: dict[str, DomainRankResult] = {}
        for item in data.get("response", []):
            domain = (item.get("domain") or "").lower()
            if not domain:
                continue

            try:
                results[domain] = DomainRankResult(
                    domain=domain,
                    page_rank_integer=int(item.get("page_rank_integer", 0)),
                    page_rank_decimal=float(item.get("page_rank_decimal", 0.0)),
                    rank=int(item.get("rank", 0)),
                )
            except (ValueError, TypeError):
                logger.debug("OpenPageRank: Ungültiger Datensatz für %s", domain)

        return results
