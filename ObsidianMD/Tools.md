# Tools – Infrastruktur-Werkzeuge

> Zurück: [[README]] | Detailseiten: [[LLM-Abstraktion]], [[Websuche]], [[Cache]], [[Retry]], [[Datenbank]]

Der `tools/`-Ordner enthält alle Infrastruktur-Werkzeuge. Agenten nutzen diese Werkzeuge über die `BaseAgent`-Hilfsmethoden – nie direkt.

---

## Übersicht

| Datei | Klasse(n) | Zweck |
|---|---|---|
| `llm.py` | `LLMClient` | Provider-agnostische LLM-Abstraktion |
| `web_search.py` | `WebSearchClient`, `AsyncWebSearchClient` | Suchmaschinen-Abstraktion |
| `cache.py` | `ClaimCache` | SQLite-Cache mit optionalem Semantic Cache |
| `calibration_tracker.py` | `CalibrationTracker` | Brier Score, Reliability Diagrams |
| `data_loader.py` | Funktionen + `reload_all()` | Hot-reloadbare YAML-Konfigurationen |
| `retry.py` | `retry_call`, `retry_call_async` | Exponential-Backoff-Retry |
| `archive.py` | `AnalysisArchive` | Persistentes Analyse-Archiv |
| `user_db.py` | `UserDB` | SQLite-Nutzerdatenbank + JWT |
| `logger.py` | `StructuredLogger` | JSON-Logging + Metriken |
| `source_classifier.py` | `SourceClassifier` | Domain → Tier-Klassifikation |
| `scrape_ranker.py` | `ScrapeRanker` | Relevanz-Scoring für Scraping |
| `source_scraper.py` | `AsyncSourceScraper` | HTTP-Scraper mit Fallback |
| `cross_reference.py` | `CrossReferenceGraph` | Claim-Wissens-Graph (SQLite) |
| `rate_limiter.py` | `RateLimiter` | Token-Bucket per IP |
| `factcheck_databases.py` | `FactCheckDatabaseClient` | Google Fact Check + lokaler Fallback |
| `factcheck_local.py` | `LocalFactCheckDatabase` | Offline-Faktencheck (DataCommons, FTS5) |
| `domain_trust.py` | `DomainTrustClient` | OpenPageRank Domain-Trust-Signal |
| `content_extractor.py` | `ContentExtractor` | URL → Text-Extraktion |
| `pdf_export.py` | `PDFExporter` | Analyse → PDF |
| `ner_extractor.py` | `entity_overlap_score()` | Entity-Overlap-Score für Evidence-Relevanz |

---

## Source Scraper (`tools/source_scraper.py`)

Async-HTTP-Scraper mit mehrstufiger Extraktion:

```
1. HTTP-GET mit aiohttp (Timeout: konfigurierbar)
2. trafilatura.extract() → Haupttext
3. Fallback: Crawl4AI (_crawl4ai_extract) – Headless-Browser für JS-Seiten
4. Fallback: BeautifulSoup (p-Tags)
5. Relevante Passagen filtern (Keyword-Overlap mit Claim)
6. ScrapedSource { url, passage, low_relevance, fetch_success, error, publication_date }
```

**Paywall-Erkennung:**
- Liste bekannter Paywall-Domains (spiegel.de+, sz.de+ …)
- Soft-Paywall-Erkennung: kurzer Content trotz langer Seite

**Parallelität:** Max. 8 gleichzeitige Scrape-Requests (Semaphore).

---

## Source Classifier (`tools/source_classifier.py`)

Klassifiziert Domains in Vertrauens-Tiers:

```
OFFICIAL         → destatis.de, eurostat.eu, bundesregierung.de
FACT_CHECKER     → correctiv.org, faktenfinder.tagesschau.de, snopes.com
QUALITY_JOURNALISM → spiegel.de, zeit.de, nytimes.com, bbc.com
MEDIA            → Allgemeine Nachrichtenmedien
USER_GENERATED   → Wikipedia, Reddit, Blogs
```

Unbekannte Domains → `MEDIA` (Fallback).

---

## Scrape Ranker (`tools/scrape_ranker.py`)

Priorisiert, welche Quellen gescrapt werden:

```python
score = keyword_overlap(claim_text, snippet + title)
# 0.0 → 1.0
```

**Scoring:** Hybrides BM25 + Semantik + Profil-Anchor-Scoring + Source-Tier-Bonus

**Regeln:**
- FACT_CHECKER → immer scrapen (unabhängig vom Score)
- Andere → nur wenn Score > Threshold
- Pro Domain: nur die URL mit dem höchsten Score
- Max. `scrape_top_n` Quellen pro Claim (Standard: 10, via `SearXNGConfig.scrape_top_n`)

---

## Cross-Reference Graph (`tools/cross_reference.py`)

Persistenter Wissens-Graph in SQLite:

```
Nodes:
  CLAIM   → Behauptungstext
  SOURCE  → URL / Domain
  ACTOR   → Person, Organisation

Edges:
  supported_by     → Claim ← Source
  contradicted_by  → Claim ← Source
  mentions         → Claim → Actor
  related_to       → Claim ↔ Claim
  cites            → Source → Source
```

Wird nach jeder Analyse aktualisiert. Ermöglicht zukünftig: „Wurde diese Behauptung schon geprüft?"

---

## Factcheck Databases (`tools/factcheck_databases.py`)

Externe Faktenchecking-APIs mit lokalem Offline-Fallback:

**Google Fact Check Tools:**
- Durchsucht verifizierte Faktenchecks von Organisationen wie AFP, dpa-Faktenfinder, Correctiv
- API-Key erforderlich

**Lokaler Fallback (DataCommons):**
- Wenn Google FCT keine Treffer liefert → automatischer Fallback auf `LocalFactCheckDatabase`
- Integration über optionalen `local_db`-Parameter im Konstruktor

---

## Lokale Faktencheck-Datenbank (`tools/factcheck_local.py`)

Offline-Fallback auf Basis von DataCommons ClaimReview-Daten (CC-BY 4.0):

```python
class LocalFactCheckDatabase:
    def __init__(self, db_path="data/factcheck_local.db")
    def import_datacommons(self, json_path: str) -> int   # Bulk-Import
    def search(self, query: str, max_results=5) -> list[ExternalFactCheck]
    def count(self) -> int
    def close(self) -> None
```

**Architektur:**
- SQLite + FTS5-Volltextsuche auf Claim-Texten
- Schema: `claim_reviews(claim_text, rating, publisher, url, review_date, language)`
- Trigger-basierte FTS-Synchronisation
- Wörter ≤2 Zeichen werden aus Suchanfragen gefiltert

**Import:**
```bash
python scripts/import_datacommons.py data/factchecks.json
python scripts/import_datacommons.py data/factchecks.json --db data/factcheck_local.db
```

Unterstützt zwei JSON-Formate:
- Flat: JSON-Array mit ClaimReview-Objekten
- DataCommons: `{"dataFeedElement": [{"item": [{"@type": "ClaimReview", ...}]}]}`

---

## Domain Trust (`tools/domain_trust.py`)

OpenPageRank-basiertes Domain-Trust-Signal für Evidence-Scoring:

```python
class DomainTrustClient:
    def get_rank(self, domain: str) -> DomainRankResult | None
    def get_ranks_batch(self, domains: list[str]) -> dict[str, DomainRankResult]
    def tier_adjustment(self, domain: str) -> float
```

**Tier-Adjustment:**
| PageRank | Adjustment | Effekt |
|---|---|---|
| ≥ 7 | −1.0 | Tier deutlich verbessern |
| ≥ 5 | −0.5 | Tier leicht verbessern |
| ≥ 3 | 0.0 | Neutral |
| < 3 | +0.5 | Tier verschlechtern |

**Integration:** Wird in `agents/evidence_scoring.py` → `_domain_tier()` als optionales Adjustment addiert. Lazy-Init, graceful Degradation wenn kein API-Key (`OPENPAGERANK_API_KEY`).

**Features:**
- In-Memory-Cache (Dict) für wiederholte Lookups
- Batch-Support (bis 100 Domains pro API-Request)
- Auth via `API-OPR`-Header

---

## Content Extractor (`tools/content_extractor.py`)

Extrahiert Text aus einer URL:

```python
result = await extractor.extract(url)
# → { platform, title, author, text, images[], published_date }
```

Erkennt Plattformen: YouTube, Twitter/X, Instagram, Standard-Webseiten.
Nutzt trafilatura + BeautifulSoup.

---

## Rate Limiter (`tools/rate_limiter.py`)

Token-Bucket-Algorithmus per IP-Adresse:

```
Standard: 10 Requests/Minute, Burst: 3
Konfigurierbar via RateLimitConfig
```

Wird als FastAPI-Middleware eingebunden.

---

## Calibration Tracker (`tools/calibration_tracker.py`)

SQLite-basierter Tracker für Confidence-Kalibrierung:

```python
class CalibrationTracker:
    def record_prediction(claim_id, confidence, rating, analysis_id) → int
    def record_ground_truth(claim_id, is_correct) → int
    def compute_report(n_buckets=10) → CalibrationReport
    def stats() → dict
```

**Metriken:**
- **Brier Score:** `mean((confidence - is_correct)^2)` – misst Calibration-Fehler
- **Reliability Diagram:** Buckets zeigen Über-/Unterkonfidenz nach Confidence-Intervall
- **Accuracy:** Anteil korrekter Vorhersagen

Genutzt von [[API|POST /api/admin/calibration]] zur Überwachung der Modell-Zuverlässigkeit.

---

## Data Loader (`tools/data_loader.py`)

Hot-reloadbare Konfigurationen für Domain-Tiers, Scoring-Weights und andere Runtime-Daten:

```python
def reload_all() → int  # Clearing count
```

19 LRU-cached Funktionen laden YAML-Dateien – `reload_all()` setzt alle Caches zurück ohne Server-Neustart. Genutzt von [[API|POST /api/admin/reload-data]].

---

## PDF Export (`tools/pdf_export.py`)

Exportiert ein `SynthesisResult` als formatiertes PDF.
Genutzt über [[API|GET /api/export/{job_id}/pdf]].

---

## Logger (`tools/logger.py`)

→ [[Datenbank#Logging und Metriken]]

---

## Detailseiten

- [[LLM-Abstraktion]] – Provider, Structured Output, Vision
- [[Websuche]] – Multi-Search, Async, Deduplication
- [[Cache]] – SQLite, TTL, Keys
- [[Retry]] – Exponential Backoff, Jitter
- [[Datenbank]] – Archive, User-DB, Graph, Logger
