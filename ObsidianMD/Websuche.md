# Websuche

> Zurück: [[Tools]] | Siehe auch: [[Agent-FactChecker]], [[Konfiguration]]

`tools/web_search.py` abstrahiert verschiedene Suchmaschinen-Backends hinter einer einheitlichen Schnittstelle.

---

## Unterstützte Provider

| Provider | Typ | Env-Var | Besonderheit |
|---|---|---|---|
| **SearXNG** | Self-hosted | `SEARXNG_URL` | Kostenlos, volle Kontrolle |
| **Tavily** | Cloud API | `TAVILY_API_KEY` | KI-optimiert für Research |
| **Serper** | Cloud API | `SERPER_API_KEY` | Google-Ergebnisse |
| **Brave** | Cloud API | `BRAVE_API_KEY` | Datenschutzfreundlich |

---

## SearchResult-Modell

```python
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str | None = None   # Falls Volltext verfügbar
```

---

## WebSearchClient (sync)

```python
client = WebSearchClient(config.search)
results = client.search("Rentenerhöhung 2023 Deutschland", n=10)
# → list[SearchResult]
```

---

## AsyncWebSearchClient (async)

```python
client = AsyncWebSearchClient(config.search)

# Einzelsuche:
results = await client.search_async("query", n=10)

# Multi-Search (mehrere Queries parallel):
results = await client.multi_search_async(
    queries=["query 1", "query 2", "query 3"],
    n_per_query=5
)
# → list[SearchResult], dedupliziert nach URL
```

---

## Multi-Search und Deduplication

Multi-Search ist die Kernfunktion für den [[Agent-FactChecker]]:

1. Alle Queries werden **gleichzeitig** als asyncio-Tasks gestartet
2. Ergebnisse werden gesammelt
3. **Deduplizierung nach URL**: jede URL erscheint nur einmal
4. Sortierung nach Relevanz-Score (Snippet-Overlap)

```python
# Intern vereinfacht:
tasks = [search_async(q) for q in queries]
all_results = await asyncio.gather(*tasks)
seen_urls = set()
deduplicated = []
for r in flatten(all_results):
    if r.url not in seen_urls:
        seen_urls.add(r.url)
        deduplicated.append(r)
```

---

## SearchConfig

```python
@dataclass
class SearchConfig:
    provider: str = "searxng"
    base_url: str = "http://localhost:8888"
    api_key: str = ""
    max_results: int = 10
    scrape_top_n: int = 8       # Max. Quellen zum Scrapen
    scrape_timeout: int = 30    # Sekunden
```

→ [[Konfiguration]]

---

## SearXNG (Empfohlen)

SearXNG ist die **empfohlene Konfiguration** für Selbst-Hosting:

```yaml
# docker-compose.yml
searxng:
  image: searxng/searxng
  ports:
    - "8080:8888"
```

Vorteile:
- Kein API-Key nötig
- Aggregiert Google, Bing, DuckDuckGo gleichzeitig
- Keine Nutzungslimits
- Vollständige Datenkontrolle

Für die Cloud-Version sind **Tavily** oder **Serper** empfohlen.

---

## Verwandte Dokumente

- [[Agent-FactChecker]] – hauptsächlicher Nutzer
- [[Tools]] – Übersicht aller Werkzeuge
- [[Docker]] – SearXNG-Container-Setup
- [[Konfiguration]] – SearchConfig
