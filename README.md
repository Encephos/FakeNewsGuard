# FakeNewsGuard

**Multi-Agent-System zur Erkennung von Fake News, Faktenverzerrung und manipulativer Rhetorik.**

Python-Backend mit FastAPI, asyncio – nutzt LLM-APIs (Anthropic/OpenAI/OpenRouter), mehrere Web-Search-Quellen (SearXNG, LangSearch, Google Fact Check API) und 14 institutionelle Primärquellen. Dev-Default: SQLite; Produktion: PostgreSQL + Valkey.

---

## Architektur (Überblick)

```
User-Input
    │
    ▼
┌─────────────────────┐
│     ORCHESTRATOR     │  Steuert den Gesamtworkflow
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  CLAIM PROCESSOR    │  6-stufige Pipeline:
│  (ClaimExtractor)   │  Splitter → Selector → Disambiguator →
└──────────┬──────────┘  Decomposer → Canonicalizer → Prioritizer
           │
           │  Top-N checkworthy Claims (konfigurierbar)
           ▼
    ┌──────┴──────┐
    │  pro Claim  │
    │             │
    ▼             ▼
┌────────┐  ┌───────────────┐
│NUMBER  │  │EVIDENCE BUILDER│  SearXNG + LangSearch + GFC parallel
│AUDITOR │  └───────┬───────┘  → EvidencePack (Trust Boundary)
└────┬───┘          │
     │              ▼
     │     ┌────────────────┐
     │     │ CoVe PROCESSOR │  Baseline → Verifikationsfragen →
     │     │ (optional)     │  unabh. Antworten → Reconciliation
     │     └───────┬────────┘
     │             │
     │             ▼
     │     ┌────────────────┐
     └────▶│ VERDICT AGENT  │  Arbeitet nur auf EvidencePack (kein Raw-HTML)
           └───────┬────────┘
                   │
                   ▼
         ┌──────────────────┐
         │ RHETORIC ANALYZER│  Analysiert Gesamt-Framing
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │   SYNTHESIZER    │  Aggregiert → SynthesisResult
         └──────────────────┘
```

Detaillierte Architektur-Dokumentation: [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## Setup

### 1. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
```

### 2. Konfiguration

```bash
cp .env.example .env
# .env mit API-Keys befüllen
```

**Minimal erforderlich:**
- Ein LLM: `ANTHROPIC_API_KEY` **oder** `OPENAI_API_KEY` **oder** Ollama (lokal)
- Web-Suche: `SEARXNG_BASE_URL` (selbst gehostet, kostenlos) **oder** ein anderer Provider

**Optional (empfohlen):**
- `LANGSEARCH_API_KEY` – zusätzliche Web-Suche mit strukturierten Ergebnissen
- `GOOGLE_FACT_CHECK_API_KEY` – Google Fact Check API (kostenlos, 1000 Anfragen/Tag)

### 3. Nutzung

**CLI:**
```bash
python main.py "Die Ausländerkriminalität ist unter der Ampel um 40% gestiegen."
python main.py --file rede_auszug.txt
python main.py --interactive
python main.py --json "..."       # JSON-Output für Weiterverarbeitung
```

**API-Server:**
```bash
uvicorn api:app --reload
# Swagger UI: http://localhost:8000/docs
```

**Docker:**
```bash
docker compose up --build
```

---

## Projektstruktur

```
FakeNewsGuard/
├── main.py                      # CLI – Entry Point
├── api.py                       # FastAPI – REST API + SSE-Streaming
├── orchestrator.py              # Zentrale Steuerung, Top-N Claim-Auswahl
│
├── config/                      # Konfigurationspackage (alle Werte aus .env)
│   ├── app.py                   # AppConfig + ScoutTier
│   ├── llm.py                   # LLMConfig, RetryConfig
│   ├── search.py                # SearXNGConfig (primär), SearchConfig (legacy), LangSearch, Tavily, GFC
│   ├── processing.py            # ClaimProcessingConfig, CoVeConfig, EvidenceRetrievalConfig, SynthesizerConfig
│   ├── database.py              # CacheConfig, ValkeyConfig, PostgreSQLConfig
│   └── infrastructure.py       # ArchiveConfig, UserDBConfig, RateLimitConfig, TelegramConfig, GraphConfig
│
├── agents/
│   ├── base.py                  # BaseAgent – LLM + Search + Logging
│   ├── claim_extractor.py       # Facade → ClaimProcessorAgent
│   ├── claim_processor.py       # 6-stufige Claim-Processing-Pipeline + Deduplizierung
│   ├── evidence_builder.py      # Retrieval → strukturiertes EvidencePack + Widerspruch-Erkennung
│   ├── cove_processor.py        # Chain-of-Verification (CoVe)
│   ├── verdict_agent.py         # Verdikt auf Basis von EvidencePack + Consensus-Override
│   ├── fact_checker.py          # Facade: EvidenceBuilder + CoVe + Verdict
│   ├── number_auditor.py        # Zahlen- und Statistikprüfung
│   ├── rhetoric_analyzer.py     # Framing, Dog Whistles, Manipulation
│   ├── image_analyzer.py        # Bildanalyse (multimodal)
│   ├── synthesizer.py           # Aggregation → Gesamtverdikt
│   ├── evidence_scoring.py      # Widerspruchserkennung mit Typisierung und Gewichtung
│   └── verdict_calibration.py   # Verdikt-Nachbearbeitung, Confidence-Ceilings, Overrides
│
├── models/
│   ├── schemas.py               # Kern-Datenmodelle (ProcessedClaim, etc.)
│   ├── evidence_models.py       # EvidencePack + Trust-Boundary-Modelle
│   ├── verdict_models.py        # CoVeTrace, BaselineAssessment, etc.
│   └── source_evidence.py       # OfficialEvidenceItem (institutionelle Quellen)
│
├── tools/
│   ├── llm.py                   # LLM-Abstraktion (Anthropic/OpenAI/Ollama)
│   ├── cache.py                 # SQLite-Claim-Cache mit optionalem Semantic Cache (Embeddings)
│   ├── calibration_tracker.py   # Confidence-Kalibrierung, Brier Scores, Reliability Diagramme
│   ├── web_search.py            # [Backward-Compat Re-Export]
│   ├── claim_router.py          # Heuristische Quellenauswahl (ClaimRouter)
│   ├── data_loader.py           # Hot-reloadbare YAML-Configs (Domain-Tiers, etc.)
│   ├── iterative_search.py      # Iterative Retrieval-Runden
│   ├── search/                  # Suchclients
│   │   ├── __init__.py          # Re-Exports (SearXNGClient, LangSearchClient, …)
│   │   └── models.py            # SearchResult-Dataclass
│   └── sources/                 # Institutionelle Primärquellen (14 Clients)
│       ├── registry.py          # SourceRegistry (by_domain, by_jurisdiction_safe, …)
│       ├── types.py             # SourceConfig, ClaimDomain, CommercialUsePolicy
│       └── adapter_guardian.py  # SourceCache, SourceRateLimiter, CircuitBreaker
│
├── tests/
│   ├── conftest.py              # Shared fixtures (minimal_config, etc.)
│   └── unit/                   # Unit-Tests (25+ Dateien, alle mock-basiert)
│
├── .env.example                 # Alle Konfigurationsvariablen dokumentiert
├── requirements.txt
└── docker-compose.yml
```

---

## Konfiguration

Alle Einstellungen über Umgebungsvariablen (`.env`). Vollständige Referenz in `.env.example`.

| Variable | Default | Beschreibung |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` / `openai` / `openrouter` / `ollama` |
| `ANTHROPIC_API_KEY` | – | API-Key für Claude |
| `SEARXNG_URL` | `http://localhost:8888` | SearXNG-Instanz (primäres Suchbackend) |
| `LANGSEARCH_API_KEY` | – | LangSearch API-Key (optional) |
| `LANGSEARCH_ENABLED` | `auto` | `true` wenn Key gesetzt, sonst `false` |
| `GOOGLE_FACT_CHECK_API_KEY` | – | Google Fact Check API-Key (optional) |
| `GOOGLE_FACT_CHECK_ENABLED` | `auto` | `true` wenn Key gesetzt, sonst `false` |
| `TAVILY_API_KEY` | – | Tavily API-Key (optionales Plugin) |
| `TAVILY_ENABLED` | `false` | Tavily explizit aktivieren |
| `CLAIM_TOP_N` | `0` | Max. Anzahl Claims pro Analyse (0 = unbegrenzt) |
| `COVE_ENABLED` | `true` | Chain-of-Verification (default: aktiv) |
| `MAX_VERIFICATION_QUESTIONS` | `3` | Max. CoVe-Verifikationsfragen pro Claim |
| `USE_CANONICAL_CACHE` | `true` | Canonical Hash für Cache-Lookup nutzen |
| `DB_BACKEND` | `sqlite` | `postgres` für Produktion |
| `CACHE_BACKEND` | `sqlite` | `valkey` für Produktion |

---

## Was erkennt das System?

### Zahlen-Tricks
- Verdrehte Prozentangaben und Rechenfehler
- Cherry-Picked Vergleichszeiträume
- Wechsel zwischen absoluten und relativen Zahlen zur Dramatisierung
- Fehlende Pro-Kopf-Normalisierung bei Ländervergleichen
- Verwechslung von Kategorien (Tatverdächtige ≠ Verurteilte ≠ Anzeigen)
- Statistische Schwankungen als "Trend" verkauft

### Rhetorische Manipulation
- Loaded Language und Dog Whistles
- Strohmann-Argumente
- Appeal to Fear / Angstrhetorik
- Whataboutism
- Implizite Kausalität (Korrelation → Kausalität suggeriert)
- Anekdotische Verallgemeinerung

### Quellen-Probleme
- Veraltete oder nicht-existente Quellen
- Falsch zitierte Statistiken
- Fehlender Kontext bei technisch korrekten Zahlen

---

## Design-Entscheidungen

**Warum kein LangChain/CrewAI/AutoGen?**
Minimale Dependencies, volle Kontrolle über Prompt-Qualität und Routing-Logik.

**Warum Trust Boundary beim EvidencePack?**
Der VerdictAgent sieht nie rohes HTML – nur strukturierte Excerpts (max. 800 Zeichen). Das verhindert Prompt-Injection aus Web-Inhalten und macht Verdikt-Prompts reproduzierbar und testbar.

**Warum Chain-of-Verification?**
Ein einzelnes LLM-Urteil tendiert zur Bestätigung der ersten Einschätzung. CoVe zwingt das Modell, gezielt nach Widersprüchen zu suchen, bevor das finale Verdikt gefällt wird.

**Warum Top-N Claim-Auswahl?**
Lange Texte enthalten viele Behauptungen, aber nur wenige sind wirklich prüfenswert und schädlich. Der Priorisierer berechnet `priority_score`, `harm_score` und `checkworthiness_score` – Top-N sorgt für fokussierte, schnelle Analysen.
