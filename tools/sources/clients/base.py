"""Adapter-Basisklasse und HTTP-Hilfsclient für institutionelle Datenquellen.

Alle konkreten Source-Adapter erben von ``BaseSourceAdapter`` und implementieren
das einheitliche Vier-Methoden-Interface:

    search(query, *, max_results, page)  → list[OfficialEvidenceItem]
    fetch_details(record_id)             → OfficialEvidenceItem | None
    normalize(record)                    → OfficialEvidenceItem
    get_policy(record)                   → SourceConfig

Der ``AdapterHTTPClient`` ist ein dünner httpx-Wrapper, der die bestehende
Retry-Logik aus ``tools/retry.py`` wiederverwendet:
    - Retryable:     HTTP 429, 500–504, Netzwerkfehler
    - Nicht retried: HTTP 400, 401, 403, 404
    - Backoff:       Exponentiell mit ±50 % Jitter (identisch zu tools/retry.py)

Designprinzipien:
    - Adapter sind zustandslos (kein Claim-spezifischer State).
    - ``AdapterHTTPClient`` ist synchron; async wird über den Thread-Pool in
      ``agents/base.py`` abgebildet (identisch zu bestehenden Agents).
    - Adapter werfen ``AdapterHTTPError`` nach allen Retry-Versuchen;
      Aufrufer (z.B. EvidenceBuilderAgent) behandeln dies als graceful degradation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from models.source_evidence import OfficialEvidenceItem
from tools.retry import retry_call
from tools.sources.types import SourceConfig

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0   # Sekunden pro Request
_DEFAULT_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_MAX_DELAY = 30.0

# User-Agent für "Polite Pool" – von OpenAlex und Crossref bevorzugt.
# Operator kann per Umgebungsvariable ADAPTER_CONTACT_EMAIL überschreiben.
_USER_AGENT = "FakeNewsGuard/1.0 (academic research; https://github.com/fakeguard)"


# ── HTTP-Client ───────────────────────────────────────────────────────────────


class AdapterHTTPError(Exception):
    """Permanenter HTTP-Fehler nach allen Retry-Versuchen.

    Attribute:
        status_code: HTTP-Statuscode (int) oder None bei Netzwerkfehlern.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdapterHTTPClient:
    """Synchroner HTTP-Client mit Retry, Timeout und gemeinsamem User-Agent.

    Wiederverwendet ``retry_call`` aus ``tools/retry.py`` sodass alle Adapter
    dieselbe Backoff-Strategie nutzen wie der Rest des Systems.

    Retryable (identisch zu RETRYABLE_HTTP_CODES in retry.py):
        - HTTP 429 (Rate Limit)
        - HTTP 500, 502, 503, 504 (Server-Fehler)
        - Netzwerkfehler ohne Response (Connection refused, Timeout, ...)

    Nicht retried:
        - HTTP 400 (Bad Request) – Adapter-Bug, kein Retry sinnvoll
        - HTTP 401 / 403 (Auth) – Konfigurationsfehler
        - HTTP 404 (Not Found) – Valide Antwort, gibt {} zurück

    Args:
        base_url:     Basis-URL ohne abschließenden Slash.
        timeout:      Request-Timeout in Sekunden (Standard: 15 s).
        max_attempts: Gesamtanzahl Versuche inkl. Erstversuch (Standard: 3).
        base_delay:   Startwartezeit für exponentielles Backoff.
        max_delay:    Maximale Wartezeit zwischen Versuchen.
        headers:      Zusätzliche HTTP-Header (ergänzen User-Agent).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_attempts: int = _DEFAULT_RETRIES,
        base_delay: float = _DEFAULT_BASE_DELAY,
        max_delay: float = _DEFAULT_MAX_DELAY,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._headers: dict[str, str] = {
            "User-Agent": _USER_AGENT,
            **(headers or {}),
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """GET-Request mit Retry. Gibt das geparste JSON-Dict zurück.

        Bei HTTP 404 wird ein leeres Dict zurückgegeben (kein Exception).
        Bei allen anderen Fehlern wird nach Retry-Erschöpfung eine
        ``AdapterHTTPError`` ausgelöst.

        Args:
            path:   URL-Pfad, wird an base_url angehängt (muss mit / beginnen).
            params: Query-Parameter als Dict (werden URL-enkodiert).

        Returns:
            Geparste JSON-Antwort als dict oder {} bei 404.

        Raises:
            AdapterHTTPError: Nach allen Retry-Versuchen oder bei nicht-retrieybaren Fehlern.
        """
        url = f"{self._base_url}{path}"

        def _do_request() -> dict:
            # Rohe httpx-Exception propagiert zu retry_call → _is_retryable prüft Status.
            response = httpx.get(
                url,
                params=params,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

        try:
            return retry_call(
                _do_request,
                max_attempts=self._max_attempts,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise AdapterHTTPError(
                f"HTTP {status} von {url}",
                status_code=status,
            ) from exc
        except Exception as exc:
            raise AdapterHTTPError(
                f"Netzwerkfehler beim Abruf von {url}: {exc}"
            ) from exc


# ── Abstrakte Basisklasse ─────────────────────────────────────────────────────


class BaseSourceAdapter(ABC):
    """Gemeinsames Interface für alle institutionellen Source-Adapter.

    Unterklassen:
        - Setzen ``config`` als Klassenattribut (aus SourceRegistry.get(...)).
        - Implementieren search, fetch_details, normalize.
        - Geben ausschließlich ``OfficialEvidenceItem``-Objekte zurück.
        - Rufen ``item.compute_confidence()`` auf und weisen das Ergebnis zu.

    Typischer Adapter-Aufbau::

        class MySourceClient(BaseSourceAdapter):
            config = SourceRegistry.get("my_source")

            def __init__(self) -> None:
                self._http = AdapterHTTPClient(self.config.base_url)

            def search(self, query, *, max_results=10, page=1):
                raw = self._http.get("/search", {"q": query, "limit": max_results})
                return [self.normalize(r) for r in raw.get("results", [])]

            def fetch_details(self, record_id):
                raw = self._http.get(f"/records/{record_id}")
                return self.normalize(raw) if raw else None

            def normalize(self, record):
                item = OfficialEvidenceItem(
                    **self._policy_kwargs(),
                    record_id=record["id"],
                    title=record["title"],
                    ...
                )
                item.recency_score = compute_recency_score(item.published_at, ...)
                item.confidence = item.compute_confidence()
                return item

    claim_relevance-Richtwerte (Adapter-Default, wird von EvidenceBuilderAgent
    ggf. durch semantisches Re-Ranking überschrieben):
        - search()-Ergebnisse:   0.65  (Query-Match, aber nicht claim-spezifisch)
        - fetch_details()-Items: 0.85  (Direktabruf per ID)

    Integrationshinweis:
        Adapter sind bewusst von der Routing-Logik (tools/claim_router.py) getrennt.
        Der ClaimRouter wählt aus, welche Adapter für einen Claim relevant sind.
        Der EvidenceBuilderAgent ruft die Adapter auf und aggregiert die Ergebnisse
        in einem EvidencePack.
    """

    config: SourceConfig  # Von Unterklassen als Klassenattribut gesetzt.

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        page: int = 1,
    ) -> list[OfficialEvidenceItem]:
        """Suche nach Einträgen für den gegebenen Query-String.

        Args:
            query:       Freitext-Query (z.B. Claim-Text, Schlüsselterme).
            max_results: Maximale Anzahl zurückgegebener Items pro Seite.
            page:        Seitennummer für Pagination (1-basiert).

        Returns:
            Liste normalisierter Evidence-Items. Leer wenn keine Treffer.
            Fehler werden geloggt und als leere Liste zurückgegeben.
        """

    @abstractmethod
    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        """Rufe ein einzelnes Dokument per nativem Primärschlüssel ab.

        Args:
            record_id: Quelleigener Identifier (DOI, NCT-ID, Indikatorcode, ...).
                       Format je Quelle – siehe OfficialEvidenceItem.record_id.

        Returns:
            Normalisiertes Evidence-Item oder None wenn nicht gefunden / Fehler.
        """

    @abstractmethod
    def normalize(self, record: dict) -> OfficialEvidenceItem:
        """Konvertiere einen rohen API-Datensatz in ein OfficialEvidenceItem.

        Adapter befüllen alle relevanten Felder und schließen mit::

            item.recency_score = compute_recency_score(item.published_at, half_life_years=...)
            item.confidence = item.compute_confidence()
            return item

        Args:
            record: Originaler API-Datensatz als Dict.

        Returns:
            Vollständig befülltes OfficialEvidenceItem mit gesetztem confidence.
        """

    def get_policy(self, record: dict | None = None) -> SourceConfig:
        """Gibt die SourceConfig dieser Quelle zurück.

        Standardimplementierung gibt immer ``self.config`` zurück.
        Adapter mit record-abhängigen Policies (z.B. Fulltext vs. Metadata)
        können diese Methode überschreiben.

        Args:
            record: Optionaler roher Datensatz (wird in der Standardimpl. ignoriert).

        Returns:
            SourceConfig mit allen Lizenz-, Speicher- und Anzeige-Policies.
        """
        return self.config

    def _policy_kwargs(self) -> dict[str, Any]:
        """Extrahiert Policy-Felder aus SourceConfig als kwargs für OfficialEvidenceItem.

        Interne Hilfsmethode – vermeidet Wiederholung in allen normalize()-Methoden.
        Gibt die Felder zurück, die direkt aus SourceConfig übernommen werden:
        source_id, source_class, authority_score, license_status,
        storage_policy, display_policy, domains.
        """
        return {
            "source_id": self.config.source_id,
            "source_class": self.config.source_class,
            "authority_score": self.config.authority_weight,
            "license_status": self.config.commercial_reuse_ok,
            "storage_policy": self.config.allowed_storage,
            "display_policy": self.config.allowed_display,
            "domains": list(self.config.claim_domains),
        }
