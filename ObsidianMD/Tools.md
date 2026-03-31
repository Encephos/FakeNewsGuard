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
| `factcheck_databases.py` | `FactCheckDatabaseClient` | Google Fact Check + ClaimBuster |
| `content_extractor.py` | `ContentExtractor` | URL → Text-Extraktion |
| `pdf_export.py` | `PDFExporter` | Analyse → PDF |

---

## Source Scraper (`tools/source_scraper.py`)

Async-HTTP-Scraper mit mehrstufiger Extraktion:

```
1. HTTP-GET mit aiohttp (Timeout: konfigurierbar)
2. trafilatura.extract() → Haupttext
3. Fallback: BeautifulSoup (p-Tags)
4. Relevante Passagen filtern (Keyword-Overlap mit Claim)
5. ScrapedSource { url, content, low_relevance, is_paywall }
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

**Regeln:**
- FACT_CHECKER → immer scrapen (unabhängig vom Score)
- Andere → nur wenn Score > Threshold
- Pro Domain: nur die URL mit dem höchsten Score
- Max. 5 Quellen pro Claim (konfigurierbar via `SearchConfig.scrape_top_n`)

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

Externe Faktenchecking-APIs:

**Google Fact Check Tools:**
- Durchsucht verifizierte Faktenchecks von Organisationen wie AFP, dpa-Faktenfinder, Correctiv
- API-Key erforderlich

**ClaimBuster:**
- KI-basierte Claim-Datenbank
- Bewertet, ob ein Claim überprüfenswert ist
- API-Key erforderlich

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
