# Systemarchitektur

> Zurück: [[README]]

FakeNewsGuard ist in klare, voneinander entkoppelte Schichten aufgeteilt. Jede Schicht kommuniziert nur mit der direkt darunterliegenden.

---

## Schichtenmodell

```
┌─────────────────────────────────────────────────────┐
│  Eingabe-Kanäle                                     │
│  CLI (main.py) │ FastAPI (api.py) │ Telegram Bot    │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│  Orchestrator (orchestrator.py)                     │
│  4-Phasen-Workflow, asyncio.gather, Job-Steuerung   │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│  Agenten-Schicht (agents/)                          │
│  BaseAgent → 6 spezialisierte Agenten               │
└──────┬─────────────────┬───────────────────┬────────┘
       │                 │                   │
┌──────▼──────┐  ┌───────▼──────┐  ┌────────▼───────┐
│ LLM-Tool    │  │ Search-Tool  │  │  Cache / DB     │
│ (tools/llm) │  │ (tools/web_  │  │  (tools/cache,  │
│             │  │  search)     │  │   archive,      │
└──────┬──────┘  └───────┬──────┘  │   user_db)      │
       │                 │         └─────────────────┘
┌──────▼─────────────────▼────────────────────────────┐
│  Externe Dienste                                    │
│  OpenRouter / Anthropic / OpenAI │ SearXNG / Tavily │
└─────────────────────────────────────────────────────┘
```

---

## Modulübersicht

### `agents/` – Agenten-Schicht

| Agent | Input | Output | Fassade | Key Method |
|-------|-------|--------|---------|-----------|
| **BaseAgent** | – | – | – | `run()`, `run_async()`, `run_safe_async()` |
| **ClaimExtractorAgent** | `str` (Rohtext) | `list[ProcessedClaim]` | Wraps ClaimProcessorAgent | `execute(text)` |
| **ClaimProcessorAgent** | `str` | `list[ProcessedClaim]` | – | `_split()→_select()→_disambiguate()→_decompose()→_canonicalize()→_prioritize()` |
| **FactCheckerAgent** | `ProcessedClaim` + Text | `FactCheckResult` | Wraps EvidenceBuilder+CoVe+Verdict | `execute(claim)` |
| **EvidenceBuilderAgent** | `ProcessedClaim` | `EvidencePack` | – | `run_async(claim)` |
| **CoVeProcessor** | `ProcessedClaim`, `EvidencePack` | `CoVeTrace` | – | `process(claim, evidence)` |
| **VerdictAgent** | `ProcessedClaim`, `EvidencePack` | `FactCheckResult` | – | `execute(claim, evidence)` |
| **NumberAuditorAgent** | `ProcessedClaim` | `NumberAuditResult` | – | `execute(claim)` |
| **RhetoricAnalyzerAgent** | `str` (Originaltext) | `RhetoricAnalysisResult` | – | `execute(text)` |
| **ImageAnalyzerAgent** | `list[bytes]` | `ImageAnalysisResult` | – | `execute(images)` |
| **SynthesizerAgent** | Alle Teilergebnisse | `SynthesisResult` | – | `execute(synthesis_input)` |

**BaseAgent Interface** (`agents/base.py`):

```python
class BaseAgent(ABC):
    name: str = "BaseAgent"
    emoji: str = "🤖"
    AGENT_TIMEOUT: float = 180.0  # Max. Sekunden pro Agent-Durchlauf

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        search_client: WebSearchClient | None = None,
        cache: ClaimCache | None = None,
    ) -> None:
        self.config = config
        self.llm = llm_client or LLMClient(config.llm, config.retry)
        self.search = search_client or WebSearchClient(config.search, config.retry)
        self.cache = cache

    # ── Public Interface ─────────────────────────────────────────
    def run(self, input_data, context="") -> Any:           # Sync: Logging + execute()
    async def execute_async(self, input_data, context=""):  # Async: execute() im Thread-Pool
    async def run_async(self, input_data, context=""):      # Async: Logging + Timeout + execute_async()
    async def run_safe_async(self, input_data, context=""):  # Graceful Degradation → (result, None) | (None, error)
    def run_safe(self, input_data, context=""):              # Sync Graceful Degradation

    @abstractmethod
    def execute(self, input_data, context="") -> Any:       # Muss von Subklassen überschrieben werden

    # ── Hilfsmethoden ────────────────────────────────────────────
    def _llm_json(self, system_prompt, user_message) -> dict
    def _llm_structured(self, system_prompt, user_message, schema, model_class, fallback_fn=None)
    def _llm_text(self, system_prompt, user_message) -> str
    async def _llm_vision(self, system_prompt, image_urls, user_message="") -> dict
    def _web_search(self, query, max_results=5) -> list[SearchResult]
    async def _web_multi_search(self, queries, n_per_query=5) -> dict[str, list[SearchResult]]
    def _cache_get(self, claim_text, context="") -> dict | None
    def _cache_set(self, claim_text, result, context="")
    def _log(self, message) -> None
```

→ Details: [[Agenten]]

### `tools/` – Infrastruktur
| Datei | Zweck |
|---|---|
| `llm.py` | Provider-agnostische LLM-Abstraktion |
| `web_search.py` | Suchmaschinen-Abstraktion (4 Provider) |
| `cache.py` | SQLite-Cache für Agenten-Ergebnisse |
| `retry.py` | Exponential-Backoff-Retry |
| `archive.py` | Persistentes Analyse-Archiv |
| `user_db.py` | Nutzer-Datenbank, JWT-Auth |
| `logger.py` | Strukturiertes Logging + Metriken |
| `source_classifier.py` | Quelltier-Klassifikation |
| `scrape_ranker.py` | Relevanz-Scoring für Scraping |
| `source_scraper.py` | Async-HTTP-Scraper |
| `cross_reference.py` | Claim-Wissens-Graph |
| `rate_limiter.py` | Token-Bucket per IP |
| `factcheck_databases.py` | Google Fact Check + lokaler Fallback |
| `factcheck_local.py` | Offline-Faktencheck (DataCommons, SQLite+FTS5) |
| `domain_trust.py` | OpenPageRank Domain-Trust-Signal |
| `data_loader.py` | Hot-reloadbare YAML-Konfigurationsdaten |
| `calibration_tracker.py` | Brier-Score-Tracking + Reliability-Diagramme |
| `ner_extractor.py` | Entity-Overlap-Score für Evidence-Relevanz |
| `extractors/` | Platform-Extraktion: YouTube, Instagram, HTML |

→ Details: [[Tools]]

### `models/schemas.py` – Datenmodelle
Alle Pydantic-v2-Modelle, Enums und JSON-Schemas für strukturierten Output.
→ Details: [[Datenmodelle]]

### `orchestrator.py` – Workflow-Motor
Verbindet alle Agenten, verwaltet den 4-Phasen-Ablauf, führt asyncio.gather aus.
→ Details: [[Orchestrator]]

### `api/` – HTTP-Schicht
FastAPI-App-Package mit Job-Queue, Auth-Middleware, Rate-Limiting, CORS. Einstiegspunkt: `api/__init__.py`, Module: `analysis.py`, `auth.py`, `admin.py`, `archive.py`, `export.py`, `graph.py`.
→ Details: [[API]]

### `config/` – Konfiguration
Konfigurations-Package mit Dataclasses und Env-Var-Overrides. Einstiegspunkt: `config/app.py` (AppConfig). Module: `llm.py`, `search.py`, `processing.py`, `infrastructure.py`, `database.py`.
→ Details: [[Konfiguration]]

---

## Datenbank-Topologie

Das System verwendet **sechs getrennte SQLite-Datenbanken** (in Produktion: PostgreSQL + Valkey als Backends):

| Datenbank | Pfad (Standard) | Zweck |
|---|---|---|
| Claim-Cache | `.fakeguard_cache.db` | Zwischenspeicher für Agenten |
| Kalibrierung | `data/calibration.db` | Brier-Scores + Confidence-Kalibrierung |
| Analyse-Archiv | `.fakeguard_archive.db` | Alle abgeschlossenen Analysen |
| Nutzer-DB | `.fakeguard_users.db` | Accounts, JWT, Usage-Log |
| Cross-Reference | `.fakeguard_graph.db` | Claim-Wissens-Graph |
| Faktencheck-Lokal | `data/factcheck_local.db` | DataCommons Offline-Fallback |

Alle im WAL-Modus für Thread-Sicherheit bei gleichzeitigen API-Anfragen.

---

## Shared Clients

LLM- und Such-Client werden **einmal im Orchestrator erstellt** und an alle Agenten weitergereicht. Kein Agent instanziiert eigene HTTP-Clients. Das spart Ressourcen und macht die Retry-Konfiguration zentral.

```python
# orchestrator.py
self.llm_client = LLMClient(config.llm)
self.search_client = WebSearchClient(config.search, config.retry)
# → wird jedem Agenten im Konstruktor übergeben
```

---

## Verwandte Dokumente

- [[Datenfluss]] – Wie eine Anfrage konkret durch alle Schichten fließt
- [[Orchestrator]] – Phasensteuerung im Detail
- [[Scout-Tiers]] – Wie Modellauswahl per Tier funktioniert
- [[Konfiguration]] – Alle Konfigurationsparameter
