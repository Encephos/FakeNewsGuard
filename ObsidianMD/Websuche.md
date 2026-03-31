# Websuche

> Zurück: [[Tools]] | Siehe auch: [[Agent-FactChecker]], [[Konfiguration]]

`tools/search/` (Package) stellt die Suchclients bereit. `tools/web_search.py` existiert
noch als Backward-Compat Re-Export – neuer Code importiert direkt aus `tools/search/`.

---

## Unterstützte Provider

| Provider | Typ | Env-Var | Besonderheit |
|---|---|---|---|
| **SearXNG** | Self-hosted (primär) | `SEARXNG_URL` | Kostenlos, volle Kontrolle, Standard |
| **LangSearch** | Cloud API | `LANGSEARCH_API_KEY` | Semantische Suche, ergänzend |
| **Tavily** | Cloud API (optional) | `TAVILY_API_KEY` | KI-optimiert, kostenpflichtig, deaktiviert per Default |
| **Serper** | Cloud API (optional) | `SERPER_API_KEY` | Google-Ergebnisse, kostenpflichtig |
| **Brave** | Cloud API (optional) | `BRAVE_API_KEY` | Datenschutzfreundlich, kostenpflichtig |

---

## SearchResult-Modell

`tools/search/models.py`

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str | None = None   # Falls Volltext verfügbar
```

---

## SearXNGClient (primär)

`tools/search/` – wird als `config.searxng` (SearXNGConfig) konfiguriert:

```python
from tools.search import SearXNGClient

client = SearXNGClient(config.searxng)

# Multi-Search (mehrere Queries parallel):
results_by_query = await client.multi_search_async(
    queries=["query 1", "query 2", "query 3"],
    n_per_query=5,
    categories=["general", "news"],
)
# → dict[str, list[SearchResult]]
```

---

## WebSearchClient / AsyncWebSearchClient (Legacy)

`tools/web_search.py` – Backward-Compat-Layer über `config.search` (SearchConfig).
Für neuen Code `SearXNGClient` direkt verwenden.

```python
# [Legacy]
client = AsyncWebSearchClient(config.search)
results = await client.multi_search_async(queries=["query"], n_per_query=5)
```

---

## Multi-Search und Deduplication

Multi-Search ist die Kernfunktion im [[Agent-FactChecker]] / EvidenceBuilderAgent:

1. Alle Queries werden **gleichzeitig** als asyncio-Tasks gestartet
2. Ergebnisse werden pro Query in `dict[str, list[SearchResult]]` gesammelt
3. EvidenceBuilderAgent dedupliziert nach URL und rankt nach Multi-Faktor-Score

---

## SearXNGConfig (primär)

```python
@dataclass
class SearXNGConfig:
    base_url: str = ""                    # Env: SEARXNG_URL
    engines: list[str] = []               # Env: SEARXNG_ENGINES
    categories: list[str] = ["general", "news"]
    language: str = "de"                  # Env: SEARXNG_LANGUAGE
    max_results: int = 15
    max_concurrent_searches: int = 3      # Parallele Queries
    scrape_top_n: int = 10               # Max. Quellen die gescrapt werden
    scrape_timeout: float = 10.0         # Sekunden pro Scrape-Request
    inter_query_delay: float = 1.5       # Pause zwischen Queries (Anti-Rate-Limit)
    engine_rotation_enabled: bool = True  # Rotiert Engines pro Query
    engines_per_query: int = 3           # Anzahl Engines pro Einzelquery
```

→ [[Konfiguration#SearXNGConfig (primär)]]

---

## SearchConfig (Legacy)

```python
# [Legacy – Backward-Compat-Routing-Layer]
# Primäres Backend: SearXNGConfig (config.searxng)
@dataclass
class SearchConfig:
    provider: str = "searxng"    # searxng | tavily | serper | brave
    api_key: str = ""
    base_url: str = ""
    max_results: int = 15
```

→ [[Konfiguration#SearchConfig (Legacy)]]

---

## SearXNG (Empfohlen)

SearXNG ist die **empfohlene Konfiguration** für Selbst-Hosting:

```yaml
# docker-compose.yml
searxng:
  image: searxng/searxng
  ports:
    - "8888:8080"   # extern 8888, intern 8080
```

Vorteile:
- Kein API-Key nötig
- Aggregiert Google, Bing, DuckDuckGo gleichzeitig
- Keine Nutzungslimits
- Vollständige Datenkontrolle

Für Cloud-Deployments ohne SearXNG stehen **Tavily**, **Serper** oder **Brave** als kostenpflichtige Alternativen zur Verfügung (standardmäßig deaktiviert).

---

## Institutionelle Datenquellen (ergänzend)

Neben der Web-Suche nutzt der EvidenceBuilder 17 institutionelle API-Adapter (`tools/sources/clients/`), geroutet über den ClaimRouter. Darunter drei neue Quellen für Entity-Verifizierung und Cross-Source-Corroboration:

| Client | API | Nutzen |
|---|---|---|
| GDELTClient | GDELT DOC API | Zählt unabhängige Domains, die über ein Thema berichten |
| WikidataClient | Wikidata SPARQL | Verifiziert Personen-/Orts-/Organisations-Fakten strukturiert |
| WikipediaClient | Wikipedia DE REST | Liefert enzyklopädischen Kontext zu Entitäten |

→ Details: [[Agent-FactChecker#Institutionelle Datenquellen (17 Adapter)]]

---

## Verwandte Dokumente

- [[Agent-FactChecker]] – hauptsächlicher Nutzer
- [[Tools]] – Übersicht aller Werkzeuge
- [[Docker]] – SearXNG-Container-Setup
- [[Konfiguration]] – SearchConfig
