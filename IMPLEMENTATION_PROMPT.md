# Implementierungs-Prompt: Source Scraping Pipeline für den Fact Checker

## Kontext & Ausgangslage

Du arbeitest an **FakeNewsGuard**, einem Multi-Agent Fake-News-Detection-System in Python.
Der Fact Checker (`agents/fact_checker.py`) sucht aktuell per SearXNG nach Quellen und
übergibt die Ergebnisse als Text an ein LLM, das dann das Urteil fällt. Das Problem:
**SearXNG liefert nur kurze Snippets (~150 Zeichen)** — kein Volltext. Der Fact Checker
urteilt also auf Basis von Überschriften und Kurzausschnitten statt echter Artikelinhalte.

Die Aufgabe ist es, eine **Source Scraping Pipeline** zu implementieren, die zwischen
Suche und LLM-Call die relevantesten Quellen vollständig liest und den Inhalt
aufbereitet. Der Fact Checker selbst bleibt unverändert der alleinige Entscheider;
er bekommt nur besseren Input.

---

## Relevante Dateien (vor der Implementierung lesen!)

- `agents/fact_checker.py` — Eingriffspunkt: `execute_async()` (Zeile 211) und `_fact_check_with_context()` (Zeile 270)
- `agents/base.py` — Basisklasse aller Agenten, Infrastruktur für LLM, Suche, Cache
- `tools/web_search.py` — `SearchResult` Dataclass (Felder: title, url, snippet, content), `WebSearchClient`, `AsyncWebSearchClient`
- `tools/source_classifier.py` — `SourceTier` Enum (0-5), `classify_source()`, `ClassifiedSource` Dataclass
- `tools/content_extractor.py` — `_extract_article_text(html)` und `_extract_article()` bereits implementiert, `_HEADERS` für HTTP-Requests
- `config.py` — `SearchConfig` Dataclass (provider, api_key, base_url, max_results, max_concurrent_searches)
- `searxng/settings.yml` — SearXNG Engine-Konfiguration
- `models/schemas.py` — Pydantic-Modelle, insbesondere `Claim`, `ClaimType`

---

## Schritt 1: Neue Datei `tools/scrape_ranker.py`

Erstelle eine neue Datei `tools/scrape_ranker.py`. Sie enthält die gesamte Ranking-
und Passage-Extraktions-Logik und hat **keine Abhängigkeiten zu Agents oder dem LLM**.

### 1a — Stoppwörter und Paywall-Domains

```python
# Deutsche Stoppwörter (konsistent mit fact_checker.py Zeile 129-138)
STOPWORDS: set[str] = {
    "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
    "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
    "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
    "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
    "gegen", "damit", "dabei", "dafür", "darin", "darauf", "davon",
    "denen", "deren", "zeigen", "zeigt", "laut", "mehr", "sehr",
    "andere", "anderen", "anderer", "wieder", "bereits", "dabei",
}

# Domains mit bekannten harten Paywalls
KNOWN_PAYWALLS: set[str] = {
    "bild.de", "welt.de", "faz.net", "handelsblatt.com",
    "nytimes.com", "washingtonpost.com", "economist.com",
    "ft.com", "wsj.com",
}

# Domains mit teilweiser Paywall — werden nur als Fallback gescraped
SOFT_PAYWALLS: set[str] = {
    "spiegel.de", "zeit.de", "sz.de", "sueddeutsche.de",
}
```

### 1b — Dataclass `RankedSource`

```python
@dataclass
class RankedSource:
    result: SearchResult
    tier: SourceTier
    relevance_score: float      # 0.0–1.0, Keyword-Overlap mit Claim
    should_scrape: bool         # Finale Entscheidung
    skip_reason: str | None     # "irrelevant" | "duplicate" | "paywall" | "low_tier" | None
```

### 1c — Hilfsfunktion: `_extract_claim_keywords(claim_text: str) -> set[str]`

Zerlegt den Claim-Text in relevante Schlüsselwörter:
- Tokenisierung per `re.findall(r"[A-ZÄÖÜa-zäöüß]{4,}", text.lower())`
- Entferne alle Wörter aus `STOPWORDS`
- Gib das resultierende Set zurück

### 1d — Hilfsfunktion: `_keyword_overlap(keywords: set[str], text: str) -> float`

```
Berechne: Anzahl der keywords, die in text.lower() vorkommen / len(keywords)
Gibt 0.0 zurück wenn keywords leer ist
```

### 1e — Hilfsfunktion: `_extract_domain(url: str) -> str`

Nutzt `urllib.parse.urlparse`. Entfernt führendes `www.`. Gibt den Hostnamen zurück.

### 1f — Hauptfunktion: `rank_sources(results_by_query, claim_text, max_scrape=5) -> list[RankedSource]`

**Signatur:**
```python
def rank_sources(
    results_by_query: dict[str, list[SearchResult]],
    claim_text: str,
    max_scrape: int = 5,
) -> list[RankedSource]:
```

**Algorithmus:**

1. Alle `SearchResult` aus allen Queries in eine flache Liste zusammenführen.

2. **Domain-Deduplizierung:** Iteriere über die Liste. Führe ein `seen_domains: dict[str, SearchResult]` Dict. Wenn eine Domain bereits gesehen wurde, behalte nur das Ergebnis mit dem höheren Keyword-Overlap-Score. Das direkt per `_keyword_overlap()` und `_extract_domain()` berechnen.

3. **Klassifizierung:** Wende `classify_source()` aus `tools/source_classifier.py` auf jedes verbleibende Ergebnis an, um `tier` und `domain` zu ermitteln.

4. **Relevanz-Score berechnen** per `_keyword_overlap(keywords, result.snippet)`.

5. **Scrape-Entscheidung** für jedes Ergebnis (in dieser Priorität, erste zutreffende Regel gewinnt):
   - Domain in `KNOWN_PAYWALLS` → `should_scrape=False`, `skip_reason="paywall"`
   - `tier <= SourceTier.USER_GENERATED` (also 0 oder 1) → `should_scrape=False`, `skip_reason="low_tier"`
   - `relevance_score < 0.15` UND `tier < SourceTier.FACT_CHECKER` → `should_scrape=False`, `skip_reason="irrelevant"`
   - `tier >= SourceTier.FACT_CHECKER` (4 oder 5) → `should_scrape=True`, `skip_reason=None` (immer)
   - `tier == SourceTier.QUALITY_JOURNALISM` UND `relevance_score >= 0.20` → `should_scrape=True`
   - `tier == SourceTier.QUALITY_JOURNALISM` UND `relevance_score < 0.20` → `should_scrape=False`, `skip_reason="irrelevant"`
   - `tier == SourceTier.MEDIA` → vorläufig `should_scrape=False`, `skip_reason="low_tier"` (wird in Schritt 6 ggf. überschrieben)

6. **Soft-Paywall- und Media-Fallback:** Zähle die Ergebnisse mit `should_scrape=True`. Wenn weniger als 2 übrig sind:
   - Erlaube zusätzlich Ergebnisse aus `SOFT_PAYWALLS` (`should_scrape=True`, `skip_reason=None`)
   - Erlaube zusätzlich `tier == SourceTier.MEDIA` mit `relevance_score >= 0.15`

7. **Limit auf `max_scrape`:** Sortiere alle `should_scrape=True`-Ergebnisse nach `tier` (absteigend), dann `relevance_score` (absteigend). Setze bei den Ergebnissen ab Position `max_scrape` `should_scrape=False` und `skip_reason="limit_reached"`.

8. Gib alle `RankedSource`-Objekte zurück, sortiert nach `tier` absteigend, dann `relevance_score` absteigend.

---

## Schritt 2: Neue Funktion `extract_relevant_passages()` in `tools/scrape_ranker.py`

### Signatur:
```python
def extract_relevant_passages(
    article_text: str,
    claim_text: str,
    max_chars: int = 1500,
) -> tuple[str, bool]:
    """
    Returns: (passage_text, low_relevance_flag)
    low_relevance_flag=True wenn kein Absatz einen Score > 0.1 hat
    """
```

### Algorithmus:

1. **Absätze extrahieren:** `paragraphs = [p.strip() for p in article_text.split('\n\n') if len(p.strip()) >= 40]`. Falls weniger als 2 Absätze: Gib `article_text[:max_chars], False` zurück.

2. **Claim-Keywords und Claim-Zahlen extrahieren:**
   - Keywords: `_extract_claim_keywords(claim_text)` (aus Schritt 1c)
   - Zahlen: `re.findall(r'\d+[\.,]?\d*', claim_text)` → als Set speichern

3. **Jeden Absatz scoren** — drei Signale:

   **Signal A — Keyword-Overlap** (Gewicht 0.5):
   `_keyword_overlap(keywords, paragraph)`

   **Signal B — Zahlen-Match** (Gewicht 0.3):
   Wenn `claim_numbers` leer ist → Score 0.0.
   Sonst: `paragraph_numbers = set(re.findall(r'\d+[\.,]?\d*', paragraph))`.
   Score = `len(claim_numbers & paragraph_numbers) / len(claim_numbers)`

   **Signal C — Positions-Bonus** (Gewicht 0.2):
   `1.0 - (index / len(paragraphs)) * 0.5`
   Ergibt 1.0 für den ersten Absatz, 0.5 für den letzten.

   **Gesamtscore:** `0.5 * A + 0.3 * B + 0.2 * C`

4. **Top-Absätze auswählen:**
   - Prüfe ob der höchste Score < 0.1 ist → falls ja, `low_relevance=True` setzen und `article_text[:500], True` zurückgeben
   - Sortiere Absätze nach Score absteigend
   - Wähle Absätze von oben, bis `max_chars` erreicht (mit `sum(len(p) for p in selected) <= max_chars` prüfen)
   - **Wichtig:** Sortiere die ausgewählten Absätze zurück in ihre **Originalreihenfolge** (nach ursprünglichem Index im `paragraphs`-Array)

5. Gib `'\n\n'.join(selected_paragraphs), False` zurück.

---

## Schritt 3: Neues async Tool `tools/source_scraper.py`

Erstelle `tools/source_scraper.py`. Diese Datei kapselt den eigentlichen HTTP-Fetch
und nutzt die bereits vorhandene Extraktions-Logik aus `content_extractor.py`.

### Imports die benötigt werden:
- `httpx` (bereits in requirements.txt)
- `asyncio`
- `tools.content_extractor._HEADERS`, `_extract_article_text`
- `tools.web_search.SearchResult`
- `tools.scrape_ranker.RankedSource`, `extract_relevant_passages`

### Dataclass `ScrapedSource`:
```python
@dataclass
class ScrapedSource:
    url: str
    tier_label: str
    passage: str            # Relevanter Ausschnitt (nach extract_relevant_passages)
    low_relevance: bool     # Flag aus extract_relevant_passages
    fetch_success: bool
    error: str | None       # Falls fetch_success=False
```

### Async-Funktion `scrape_source(ranked: RankedSource, claim_text: str, timeout: float = 10.0) -> ScrapedSource`

```
1. Versuche httpx.AsyncClient mit _HEADERS und timeout die URL zu fetchen
2. Bei HTTP-Fehler oder Exception: Gib ScrapedSource(fetch_success=False, error=...) zurück
3. Extrahiere Artikeltext per _extract_article_text(response.text)
4. Falls text leer oder kürzer als 100 Zeichen: fetch_success=False, error="Kein Inhalt extrahierbar"
5. Wende extract_relevant_passages(text, claim_text) an
6. Gib ScrapedSource(fetch_success=True, passage=passage, low_relevance=low_relevance, ...) zurück
```

### Async-Funktion `scrape_sources(ranked_sources: list[RankedSource], claim_text: str, max_concurrent: int = 3) -> list[ScrapedSource]`

```
1. Filtere: only where ranked.should_scrape == True
2. Nutze asyncio.Semaphore(max_concurrent) für parallele Requests
3. Rufe scrape_source() für alle gefilterten Quellen parallel via asyncio.gather auf
4. Gib Liste der ScrapedSource-Objekte zurück (in der Reihenfolge der Input-Liste)
```

---

## Schritt 4: `agents/fact_checker.py` — `execute_async()` erweitern

Der Eingriffspunkt ist nach dem `asyncio.gather` in `execute_async()` (Zeile 232).
Aktuell werden `results_by_query` direkt zu einem String formatiert.

**Neuer Ablauf nach dem gather:**

```python
# 1. Alle SearchResults flach sammeln (bereits aus results_by_query verfügbar)
# 2. rank_sources() aufrufen (aus tools.scrape_ranker)
ranked = rank_sources(results_by_query, claim.text)

# 3. Quellen mit should_scrape=True parallel scrapen
from tools.source_scraper import scrape_sources
scraped = await scrape_sources(ranked, claim.text)

# 4. search_context neu aufbauen via _build_enriched_context()
search_context = _build_enriched_context(ranked, scraped)
```

### Neue Hilfsfunktion `_build_enriched_context(ranked, scraped) -> str` in `fact_checker.py`

Diese Funktion ersetzt die bisherige einfache Formatierung. Sie baut den LLM-Kontext
so auf, dass:

1. Ein Dict `scraped_by_url: dict[str, ScrapedSource]` aus der `scraped`-Liste aufgebaut wird
2. Über alle `ranked`-Ergebnisse iteriert wird (sortiert: `should_scrape=True` zuerst)
3. Für jede Quelle wird ein Block formatiert:

**Wenn Volltext vorhanden** (`url in scraped_by_url` und `fetch_success=True`):
```
[Quelle N] [<tier_label>] <title>
URL: <url>
Volltext-Auszug:
  <passage>
```

**Wenn Scrape fehlgeschlagen** (`fetch_success=False`):
```
[Quelle N] [<tier_label>] <title>
URL: <url>
Snippet: <original snippet>
[Kein Volltext: <error>]
```

**Wenn nicht gescraped** (`should_scrape=False`):
```
[Quelle N] [<tier_label>] <title>
URL: <url>
Snippet: <original snippet>
[Kein Volltext: <skip_reason>]
```

4. Blöcke mit `\n---\n` trennen und als einen String zurückgeben.

**Wichtig:** `skip_reason` soll für den Fact Checker lesbar übersetzt werden:
- `"paywall"` → `"Paywall"`
- `"low_tier"` → `"Niedriger Quellen-Tier"`
- `"irrelevant"` → `"Kein thematischer Bezug (Snippet-Analyse)"`
- `"limit_reached"` → `"Scrape-Limit erreicht"`

---

## Schritt 5: `config.py` — `SearchConfig` erweitern

Füge zwei neue Felder zu `SearchConfig` hinzu:

```python
scrape_top_n: int = 5           # Maximale Anzahl zu scrapender Quellen pro Claim
scrape_timeout: float = 10.0    # HTTP-Timeout pro Scrape-Request in Sekunden
```

Diese Werte sollen über Umgebungsvariablen überschreibbar sein:
- `SCRAPE_TOP_N` → `scrape_top_n`
- `SCRAPE_TIMEOUT` → `scrape_timeout`

Im `__post_init__` der `SearchConfig` entsprechend auslesen (analog zu `RATE_LIMIT_RPM` in `RateLimitConfig`).

Passe `scrape_sources()` in `tools/source_scraper.py` an, sodass `max_concurrent` aus
`config.search.max_concurrent_searches` und der `timeout` aus `config.search.scrape_timeout`
kommt. Dafür muss `scrape_sources()` entweder die Config direkt übergeben bekommen oder
die Parameter explizit gesetzt werden beim Aufruf aus `execute_async()`.

---

## Schritt 6: `searxng/settings.yml` — Engines erweitern

Ergänze folgende Engines in der `engines:`-Liste:

```yaml
  - name: google news
    engine: google_news
    shortcut: gn
    disabled: false

  - name: wikidata
    engine: wikidata
    shortcut: wd
    disabled: false

  - name: google scholar
    engine: google_scholar
    shortcut: gsc
    disabled: false
```

Füge außerdem unter `search:` die zusätzlichen Formate hinzu, falls noch nicht vorhanden:
```yaml
search:
  formats:
    - html
    - json
```

---

## Schritt 7: `tools/web_search.py` — Dynamische Kategorien für SearXNG

Die `_search_searxng()`-Methode (Zeile 56) sendet aktuell immer `"categories": "general"`.
Erweitere die Methode um einen optionalen Parameter `categories: str = "general"`.

Füge außerdem eine neue Methode `search_with_category()` oder erweitere `search()` um
einen `categories`-Parameter, der durchgereicht wird.

Im `FactCheckerAgent._build_search_queries()` bzw. beim Aufruf von `multi_search_async()`
soll die Kategorie nach `ClaimType` gesetzt werden:

| ClaimType | SearXNG-Kategorien |
|---|---|
| `STATISTICAL` | `"general,science,news"` |
| `CAUSAL` | `"general,science"` |
| `FACTUAL` | `"general,news"` |
| `CONTEXTUAL` | `"general,news"` |
| Fallback | `"general"` |

**Achtung:** SearXNG akzeptiert mehrere Kategorien als kommaseparierten String im
`categories`-Parameter. Prüfe anhand der SearXNG-Dokumentation oder der bestehenden
Instanz, ob dies korrekt so unterstützt wird — falls nicht, reicht auch nur `"news"`
statt `"general,news"` für News-orientierte Claims.

---

## Zusammenfassung: Welche Dateien werden angelegt/geändert

| Datei | Aktion |
|---|---|
| `tools/scrape_ranker.py` | **Neu anlegen** |
| `tools/source_scraper.py` | **Neu anlegen** |
| `agents/fact_checker.py` | **Erweitern:** `execute_async()` + neue Hilfsfunktion `_build_enriched_context()` |
| `config.py` | **Erweitern:** `SearchConfig` um `scrape_top_n` und `scrape_timeout` |
| `tools/web_search.py` | **Erweitern:** `_search_searxng()` und `_search_searxng_async()` um `categories`-Parameter |
| `searxng/settings.yml` | **Erweitern:** Neue Engines hinzufügen |

**Nicht ändern:** `agents/base.py`, `models/schemas.py`, `tools/source_classifier.py`,
`tools/content_extractor.py`, `orchestrator.py` — diese bleiben unberührt.

---

## Wichtige Constraints

1. **Der Fact Checker fällt weiterhin allein das Urteil.** Die Pipeline verbessert nur
   den Input — kein neuer Agent, keine neue Bewertungslogik.

2. **Graceful Degradation:** Wenn der Scrape einer Quelle fehlschlägt (Timeout, 403,
   JavaScript-only-Seite), wird das still ignoriert und der Snippet verwendet.
   Kein `raise`, kein Abbruch der gesamten Analyse.

3. **Kein Einfluss auf den sync-Pfad:** `execute()` (nicht async) wird nicht verändert.
   Der Scraping-Layer läuft nur in `execute_async()`. Der sync-Pfad bleibt als
   Fallback unberührt.

4. **Bestehende Tests dürfen nicht brechen.** Da `execute()` unverändert bleibt und
   `execute_async()` per Default den Thread-Pool-Fallback in `base.py` nutzt, sind
   bestehende Unit-Tests nicht betroffen. Neue Tests für `scrape_ranker.py` und
   `source_scraper.py` sind wünschenswert aber nicht zwingend Teil dieser Aufgabe.
