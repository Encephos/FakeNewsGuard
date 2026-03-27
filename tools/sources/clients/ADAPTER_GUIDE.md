# Source Adapter Implementation Guide

## Übersicht

14 institutionelle Datenquellen wurden als konsistente Adapter implementiert:

| # | Adapter | Quelle | Domänen | Besonderheiten |
|---|---------|--------|---------|---|
| 1 | `WorldBankClient` | World Bank Open Data | Economic, Statistical, Financial, Trade | Indikator-Suche; 16.000+ Indikatoren |
| 2 | `OpenAlexClient` | OpenAlex | Scientific, Medical | Polite-Pool; Inverted-Index Abstract |
| 3 | `ClinicalTrialsClient` | ClinicalTrials.gov | Clinical, Medical, Pharmaceutical | NCT-IDs; Status + Enrollment + Outcomes |
| 4 | `GLEIFClient` | GLEIF | Corporate, Legal, Financial | LEI-IDs; Fast-changing (alters 1.5 years) |
| 5 | `OpenFDAClient` | openFDA | Pharmaceutical, Regulatory, Medical | Indikationen + Kontraindikationen |
| 6 | `CrossrefClient` | Crossref | Scientific | Polite-Pool; date-parts Parsing |
| 7 | `ArXivClient` | arXiv | Scientific | Metadata-only; Atom XML Format |
| 8 | `EurostatClient` | Eurostat | Economic, Statistical | **MVP-Impl** (echte SDMX-Integration komplex) |
| 9 | `EURLexClient` | EUR-Lex | Legal, Regulatory | **MVP-Impl** (fetch_details nur) |
| 10 | `USPTOClient` | USPTO PatentsView | Patent | Elasticsearch-Query-Syntax |
| 11 | `CompaniesHouseClient` | Companies House | Corporate, Legal | **API-Key erforderlich** |
| 12 | `DailyMedClient` | DailyMed | Pharmaceutical, Medical | SPL-SetID primär |
| 13 | `PubMedClient` | PubMed | Medical, Clinical, Scientific | Metadata-only; eSearch + eFetch |
| 14 | `CERNOpenDataClient` | CERN Open Data | Scientific | Invenio-basiert; physics datasets |

---

## Designprinzipien

### 1. Interface-Konsistenz

Alle Adapter erben von `BaseSourceAdapter` und implementieren:

```python
def search(query: str, *, max_results: int, page: int) → list[OfficialEvidenceItem]
def fetch_details(record_id: str) → OfficialEvidenceItem | None
def normalize(record: dict) → OfficialEvidenceItem
def get_policy(record=None) → SourceConfig  # default impl: return self.config
```

### 2. HTTP-Client

- Alle Adapter nutzen `AdapterHTTPClient` (dünner httpx-Wrapper)
- Wiederverwendet `retry_call()` aus `tools/retry.py`
- Retryable: HTTP 429, 500–504, Netzwerkfehler
- Nicht-retried: 400, 401, 403, 404

```python
self._http = AdapterHTTPClient(
    base_url="https://api.example.org",
    timeout=15.0,
    max_attempts=3,
    headers={"User-Agent": "..."},  # optional
)
```

### 3. normalize() – Zentrale Normalisierung

Jeder Adapter befüllt `OfficialEvidenceItem` **vollständig**:

```python
def normalize(self, record: dict) -> OfficialEvidenceItem:
    item = OfficialEvidenceItem(
        **self._policy_kwargs(),              # source_id, authority_score, etc.
        record_id="...",                      # native PK
        title="...",
        url="...",
        abstract="...",                       # max 1200 Zeichen
        published_at=...,                     # date | None
        jurisdiction="...",                   # ISO 3166-1, 'EU', 'global'
        entity_mentions=[...],                # Entitäten für Scoring
        normalized_facts=[...],               # NormalizedFact-Liste
        recency_score=compute_recency_score(..., half_life_years=...),
        raw_fields={...},                     # Debug-Felder nur
    )
    item.confidence = item.compute_confidence()
    return item
```

**Wichtig**: `_policy_kwargs()` hat alle Lizenz/Storage/Display-Felder:
```python
{
    "source_id": self.config.source_id,
    "source_class": self.config.source_class,
    "authority_score": self.config.authority_weight,
    "license_status": self.config.commercial_reuse_ok,
    "storage_policy": self.config.allowed_storage,
    "display_policy": self.config.allowed_display,
    "domains": list(self.config.claim_domains),
}
```

### 4. Pagination & Error Handling

```python
def search(self, query, *, max_results=10, page=1):
    try:
        raw = self._http.get("/endpoint", {"query": query, "page": page})
    except AdapterHTTPError as exc:
        logger.warning("Source search failed: %s", exc)
        return []  # graceful degradation

    items = []
    for record in raw.get("results", []):
        try:
            item = self.normalize(record)
            item.claim_relevance = 0.65
            item.confidence = item.compute_confidence()
            items.append(item)
        except Exception as exc:
            logger.debug("normalize failed: %s", exc)

    return items[:max_results]
```

---

## Quellspezifische Besonderheiten

### World Bank
- **Struktur**: `[metadata, data_list]` (API gibt Array zurück)
- **Indikator-Suche**: `/indicator?q={query}` → Indikator-Liste
- **Datenabruf**: `/country/{iso}/indicator/{code}?mrv=1` → Zeitreihen
- **record_id**: `<iso3>/<code>/<year>` z.B. `"DEU/NY.GDP.MKTP.CD/2023"`
- **Halbwertszeit**: 2.0 Jahre (Wirtschaft altert schnell)

### OpenAlex
- **Polite Pool**: mailto im User-Agent → `OPENALEX_CONTACT_EMAIL` env var
- **Abstract**: Inverted-Index-Format `{word: [pos1, pos2]}` → muss rekonstruiert werden
- **Zitationen**: `cited_by_count` als zusätzlicher Fakt
- **record_id**: DOI-URL oder OpenAlex-ID

### ClinicalTrials.gov
- **Pagination**: cursor-basiert (pageToken), aber hier offset simuliert
- **Status-Map**: `COMPLETED → "Abgeschlossen"` etc.
- **Primäre Endpunkte**: z.B. "Change in body weight at X weeks"
- **record_id**: NCT-ID `"NCT04788511"`

### GLEIF
- **LEI-ID**: ISO 17442, 20 Zeichen (z.B. `"529900HNOAA1KXQJUQ27"`)
- **Struktur**: geschachtelt in `data[].attributes.lei-record`
- **Halbwertszeit**: 1.5 Jahre (Fusionen, Auflösungen)

### openFDA
- **Application Numbers**: `ANDA075258` (Generika) oder `NDA202008` (Originalwirkstoff)
- **Pagination**: offset-basiert (`limit` + `skip`)
- **Indikationen/Kontraindikationen**: 3 separate Fakten

### Crossref
- **Polite Pool**: `CROSSREF_CONTACT_EMAIL` env var
- **date-parts**: `[year, month, day]` Array-Format
- **Fallback**: ohne Abstract wird Titel als Abstract verwendet

### arXiv
- **Metadata-only**: kein Volltext-Zugriff
- **Format**: Atom XML (nicht JSON!)
- **arXiv-ID**: `YYMM.NNNNN` (z.B. `"2301.12345"`)
- **Kategorien**: Primary Category (z.B. `cs.CL`)
- **Halbwertszeit**: 2.0 Jahre (Preprints)

### Eurostat
- **MVP-Implementation**: echte SDMX-Integration wäre zu komplex
- **Limitation**: keine Keyword-Suche über API
- **fetch_details** nur skizziert

### EUR-Lex
- **MVP-Implementation**: REST API hat keine Free-Text-Suche
- **CELEX-Nummer**: Primärschlüssel z.B. `"32016R0679"` (GDPR)
- **fetch_details**: funktional, search() gibt leere Liste
- **Halbwertszeit**: 10.0 Jahre (Recht ändert sich selten)

### USPTO PatentsView
- **Elasticsearch-Syntax**: `q={"patent_title": query}` als JSON
- **Patent-Nummer**: `"US10234567"`
- **Fallback**: Suchanfragen als JSON serialisieren

### Companies House
- **API-Key**: `COMPANIES_HOUSE_API_KEY` env var (erforderlich!)
- **Basic Auth**: `Authorization: Basic base64(key:)`
- **Company Number**: 8-stellig mit führenden Nullen

### DailyMed
- **SPL-SetID**: Primärschlüssel (UUID Format)
- **API**: JSON `/spls.json` + XML `/spls/{id}.xml`
- **Limitation**: keine Datierungsinfo über JSON API

### PubMed
- **Metadata-only**: kein Volltext-Zugriff
- **PMID**: primäre Identifier (z.B. `"34747358"`)
- **eSearch + eFetch**: Suche gibt nur PMIDs, muss dann Details abrufen
- **Diverse Datumsformate**: YYYY-MM-DD, YYYY-MM, YYYY
- **Polite**: max. 3 req/s ohne Key, 10 req/s mit `NCBI_API_KEY`

### CERN Open Data
- **Invenio-basiert**: Struktur hat `metadata` oder flache Felder
- **Datensatz-Typen**: Tools, Daten, Simulationen
- **Halbwertszeit**: 5.0 Jahre (Forschungsdaten)

---

## record_id-Konventionen

| Quelle | Format | Beispiel |
|--------|--------|----------|
| World Bank | `<iso3>/<code>/<year>` | `DEU/NY.GDP.MKTP.CD/2023` |
| OpenAlex | DOI-URL oder ID | `https://doi.org/10.1038/...` |
| ClinicalTrials | NCT-ID | `NCT04788511` |
| GLEIF | LEI (20 Zeichen) | `529900HNOAA1KXQJUQ27` |
| openFDA | Application Number | `ANDA075258` |
| Crossref | DOI | `10.1038/s41591-021-01583-4` |
| arXiv | arXiv-ID | `2301.12345` |
| Eurostat | `<dataset>/<geo>/<time>` | `tps00001/DE/2023` |
| EUR-Lex | CELEX-Nummer | `32016R0679` |
| USPTO | Patent-Nummer | `US10234567` |
| Companies House | Company Number | `00102498` |
| DailyMed | SPL Set-ID | UUID |
| PubMed | PMID | `34747358` |
| CERN | Invenio Record-ID | `1234567` |

---

## Neuen Adapter hinzufügen

### 1. Datei anlegen
```bash
touch tools/sources/clients/my_source.py
```

### 2. Template
```python
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

_HALF_LIFE_YEARS = 5.0


class MySourceClient(BaseSourceAdapter):
    """Adapter for MySource API."""

    config = SourceRegistry.get("my_source")  # Muss in registry.py exist!

    def __init__(self) -> None:
        self._http = AdapterHTTPClient(
            "https://api.example.org",
            timeout=15.0,
            max_attempts=3,
        )

    def search(self, query, *, max_results=10, page=1) -> list[OfficialEvidenceItem]:
        # ...

    def fetch_details(self, record_id: str) -> OfficialEvidenceItem | None:
        # ...

    def normalize(self, record: dict) -> OfficialEvidenceItem:
        item = OfficialEvidenceItem(
            **self._policy_kwargs(),
            record_id="...",
            title="...",
            url="...",
            abstract="...",
            published_at=...,
            jurisdiction="...",
            entity_mentions=[...],
            normalized_facts=[...],
            recency_score=compute_recency_score(..., half_life_years=_HALF_LIFE_YEARS),
        )
        item.confidence = item.compute_confidence()
        return item
```

### 3. In `__init__.py` exportieren
```python
from tools.sources.clients.my_source import MySourceClient

__all__ = [
    # ...
    "MySourceClient",
]
```

### 4. Tests schreiben
```python
def test_my_source_search():
    client = MySourceClient()
    items = client.search("test query", max_results=5)
    assert len(items) > 0
    assert all(isinstance(i, OfficialEvidenceItem) for i in items)
```

---

## Integration in EvidenceBuilderAgent

Adapter werden vom `EvidenceBuilderAgent` aufgerufen (noch zu implementieren):

```python
from tools.sources import SourceRegistry
from tools.sources.clients import (
    WorldBankClient, OpenAlexClient, ClinicalTrialsClient, ...
)

# Routing: welche Adapter für diesen Claim?
route_result = claim_router.route_and_apply(claim)
adapters_to_use = [
    SourceRegistry.get(src.source_id).source_class  # → string path
    for src in route_result.sources
]

# Dynamisches Laden (optional):
for adapter_class_path in adapters_to_use:
    module_path, class_name = adapter_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    AdapterClass = getattr(module, class_name)
    client = AdapterClass()
    items = client.search(claim.text, max_results=5)
    evidence_pack.official_results.extend(items)
```

---

## Fehlerbehandlung & Logging

- **AdapterHTTPError** wird von `AdapterHTTPClient` nach Retry-Exhaustion geworfen
- Adapter fangen diese und geben leere Listen zurück (graceful degradation)
- Detaillierte Fehler in `logger.warning()`, Debug-Info in `logger.debug()`
- Keine Exception-Eskalation → System läuft weiter auch bei Adapter-Ausfällen

---

## Performance-Hinweise

1. **Pagination**: Offset-basiert wo möglich (einfacher für Tests)
2. **Timeouts**: 15–20 Sekunden pro Request
3. **max_results capping**: `min(max_results, 100)` um Rate-Limits zu schonen
4. **Caching**: Wird vom `ClaimCache` in `tools/cache.py` übernommen (nicht im Adapter)
5. **Polite-Pool**: OpenAlex, Crossref, PubMed → mailto env vars beachten

---

## Testing

Alle Adapter sollten getestet werden mit Mock-Records:

```python
def test_normalize():
    client = MySourceClient()
    mock_record = {...}
    item = client.normalize(mock_record)

    assert item.record_id == "..."
    assert item.confidence > 0
    assert len(item.normalized_facts) > 0
```

Siehe `tests/unit/test_source_adapters.py` (zu implementieren).
