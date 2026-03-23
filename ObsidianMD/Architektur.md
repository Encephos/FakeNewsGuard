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
| Datei | Klasse | Zweck |
|---|---|---|
| `base.py` | `BaseAgent` | Abstrakte Basisklasse, Retry, Cache, graceful degradation |
| `claim_extractor.py` | `ClaimExtractorAgent` | Zerlegung in atomare Behauptungen |
| `fact_checker.py` | `FactCheckerAgent` | Faktenprüfung mit Websuche |
| `number_auditor.py` | `NumberAuditorAgent` | Statistische Validierung |
| `rhetoric_analyzer.py` | `RhetoricAnalyzerAgent` | Manipulationstechniken |
| `synthesizer.py` | `SynthesizerAgent` | Gesamturteil |
| `image_analyzer.py` | `ImageAnalyzerAgent` | Vision / OCR / Bildmanipulation |

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
| `factcheck_databases.py` | Google Fact Check + ClaimBuster |

→ Details: [[Tools]]

### `models/schemas.py` – Datenmodelle
Alle Pydantic-v2-Modelle, Enums und JSON-Schemas für strukturierten Output.
→ Details: [[Datenmodelle]]

### `orchestrator.py` – Workflow-Motor
Verbindet alle Agenten, verwaltet den 4-Phasen-Ablauf, führt asyncio.gather aus.
→ Details: [[Orchestrator]]

### `api.py` – HTTP-Schicht
FastAPI-App mit Job-Queue, Auth-Middleware, Rate-Limiting, CORS.
→ Details: [[API]]

### `config.py` – Konfiguration
Dataclasses mit Env-Var-Overrides für alle Systemparameter.
→ Details: [[Konfiguration]]

---

## Datenbank-Topologie

Das System verwendet **vier getrennte SQLite-Datenbanken**:

| Datenbank | Pfad (Standard) | Zweck |
|---|---|---|
| Claim-Cache | `.fakeguard_cache.db` | Zwischenspeicher für Agenten |
| Analyse-Archiv | `.fakeguard_archive.db` | Alle abgeschlossenen Analysen |
| Nutzer-DB | `.fakeguard_users.db` | Accounts, JWT, Usage-Log |
| Cross-Reference | `.fakeguard_graph.db` | Claim-Wissens-Graph |

Alle im WAL-Modus für Thread-Sicherheit bei gleichzeitigen API-Anfragen.

---

## Shared Clients

LLM- und Such-Client werden **einmal im Orchestrator erstellt** und an alle Agenten weitergereicht. Kein Agent instanziiert eigene HTTP-Clients. Das spart Ressourcen und macht die Retry-Konfiguration zentral.

```python
# orchestrator.py
self.llm_client = LLMClient(config.llm)
self.search_client = AsyncWebSearchClient(config.search)
# → wird jedem Agenten im Konstruktor übergeben
```

---

## Verwandte Dokumente

- [[Datenfluss]] – Wie eine Anfrage konkret durch alle Schichten fließt
- [[Orchestrator]] – Phasensteuerung im Detail
- [[Scout-Tiers]] – Wie Modellauswahl per Tier funktioniert
- [[Konfiguration]] – Alle Konfigurationsparameter
