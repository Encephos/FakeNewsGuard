# FakeNewsGuard

**Multi-Agent-System zur Erkennung von Fake News, Faktenverzerrung und manipulativer Rhetorik.**

Python-Backend mit FastAPI, asyncio – nutzt LLM-APIs (Anthropic/OpenAI/OpenRouter), mehrere Web-Search-Quellen (SearXNG, LangSearch, Google Fact Check API) und 14 institutionelle Primärquellen. Dev-Default: SQLite; Produktion: PostgreSQL + Valkey.

---

## Architektur (Überblick)

```
PHASE 1: Extraktion (sequenziell)
─────────────────────────────────
    Text → ClaimExtractor
         (6-stufige Pipeline: Split → Select → Disambiguate →
          Decompose → Canonicalize → Prioritize)
         │
         └──→ Top-N checkworthy Claims (dedupliziert nach canonical_hash)

PHASE 2 + 3: Analyse (parallel)
────────────────────────────────
    ┌─────────────────────────────────────────────────────┐
    │                                                       │
    ├──→ pro Claim (parallel):                             │
    │    ├─ FactChecker:                                   │
    │    │  ├─ EvidenceBuilder (Search + Scraping)        │
    │    │  ├─ CoVeProcessor (optional)                   │
    │    │  └─ VerdictAgent → FactCheckResult             │
    │    │                                                 │
    │    └─ NumberAuditor (nur für STATISTICAL claims)    │
    │                                                      │
    └──→ RhetoricAnalyzer (Originaltext, parallel)        │

PHASE 4: Synthese (sequenziell)
────────────────────────────────
    Alle Ergebnisse → SynthesizerAgent
                   → OverallRating, Confidence, Corrections
                   → SynthesisResult
```

**Legende:**
- Phase 1: Sequenziell (alle Claims müssen zuerst extrahiert sein)
- Phase 2+3: Parallel (alle Claims werden gleichzeitig faktengeprüft, Rhetorik läuft parallel dazu)
- Phase 4: Sequenziell (braucht alle Ergebnisse aus Phase 2+3)

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

**API-Server (Job-Queue Pattern):**
```bash
uvicorn api:app --reload
# Swagger UI: http://localhost:8000/docs
```

Beispiel-Workflow:
```bash
# 1. Analyse starten (returns job_id)
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "Die Ausländerkriminalität ist 2023 um 40% gestiegen.", "tier": "pro"}' \
# Response: {"job_id": "550e8400e29b41d4a716446655440000"}

# 2. Status abfragen (polling)
curl http://localhost:8000/api/jobs/550e8400e29b41d4a716446655440000
# Response: {"status": "processing"} oder {"status": "completed", "result": {...}}
```

**Nützliche Hinweise:**
- Asynchron: Analyseergebnis wird im Hintergrund berechnet
- Polling: Client fragt Status via `GET /api/jobs/{job_id}` ab
- TTL: Ergebnisse bleiben 1h verfügbar, dann werden sie aus RAM gelöscht
- Swagger UI: Alle Endpoints dokumentiert unter `/docs`

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
│   ├── base.py                  # BaseAgent – Abstraktion für run/run_async/run_safe_async, Retry, Logging
│   ├── claim_extractor.py       # Facade → ClaimProcessorAgent (6-Stufen-Pipeline)
│   ├── claim_processor.py       # ClaimProcessingPipeline: Splitter → Selector → Disambiguator → Decomposer → Canonicalizer → Prioritizer
│   ├── evidence_builder.py      # Parallel-Suche (SearXNG, LangSearch, GFC) + Scraping + Widerspruch-Erkennung
│   ├── cove_processor.py        # Chain-of-Verification: Baseline → Verifikationsfragen → Reconciliation
│   ├── verdict_agent.py         # Verdict basierend auf strukturiertem EvidencePack (Trust Boundary)
│   ├── fact_checker.py          # Facade: EvidenceBuilder + CoVe + VerdictAgent (mit optionalen Phasen)
│   ├── number_auditor.py        # Validierung statistischer Claims (Basiseffekt, Cherry-Picking, Rechenfehler)
│   ├── rhetoric_analyzer.py     # Manipulations-Erkennung (Loaded Language, Dog Whistles, Fear Appeals, etc.)
│   ├── image_analyzer.py        # Multimodale Bildanalyse (Vision, OCR, Manipulations-Erkennung)
│   ├── synthesizer.py           # Aggregiert alle Teilergebnisse → SynthesisResult (OverallRating, Confidence, Corrections)
│   ├── evidence_scoring.py      # WeightedContradiction-Erkennung mit Typisierung und Gewichtung
│   ├── verdict_calibration.py   # Confidence-Kalibrierung, Brier Scores, Consensus-Contradiction-Override
│   ├── query_builder.py         # Adaptive Query-Generierung (FACTUAL vs STATISTICAL Routing)
│   └── prompts/
│       └── claim_prompts.py     # LLM-Prompts für alle Phasen (System + User Messages)
│
├── models/
│   ├── schemas.py               # Kern-Datenmodelle (ProcessedClaim, etc.)
│   ├── evidence_models.py       # EvidencePack + Trust-Boundary-Modelle
│   ├── verdict_models.py        # CoVeTrace, BaselineAssessment, etc.
│   └── source_evidence.py       # OfficialEvidenceItem (institutionelle Quellen)
│
├── tools/
│   ├── llm.py                   # LLMClient (Anthropic/OpenAI/OpenRouter/Ollama) mit Retry-Logik
│   ├── cache.py                 # ClaimCache (SQLite/Valkey) mit Semantic Cache (Embeddings, Threshold 0.92)
│   ├── calibration_tracker.py   # Confidence-Kalibrierung, Brier Scores, Reliability Diagrams
│   ├── claim_router.py          # ClaimRouter: Heuristische Quellenauswahl nach ClaimType + Jurisdiktion
│   ├── data_loader.py           # Hot-reloadbare YAML-Configs (Domain-Tiers, Weights, etc.) ohne Neustart
│   ├── iterative_search.py      # Iterative Retrieval-Runden mit verbessertem Ranking
│   ├── logger.py                # Strukturiertes Logging mit Korrelations-IDs (analysis_id)
│   ├── rate_limiter.py          # Token-Bucket Rate-Limiting pro IP
│   ├── search/                  # Suchclients (SearXNG, LangSearch, Tavily, GFC)
│   │   ├── __init__.py          # Re-Exports aller Suchclients
│   │   ├── client.py            # Generischer SearchClient
│   │   ├── searxng.py           # SearXNG-Client (Primärbackend, kostenlos)
│   │   ├── tavily.py            # Tavily-Client (optionales Plugin)
│   │   └── models.py            # SearchResult Dataclass
│   └── sources/                 # Institutionelle Primärquellen (14 Adapter)
│       ├── registry.py          # SourceRegistry (by_domain, by_jurisdiction_safe, tier_hierarchy)
│       ├── types.py             # SourceConfig, ClaimDomain, CommercialUsePolicy
│       ├── adapter_*.py         # 14 spezifische Adapter (Bloomberg, Guardian, Reuters, etc.)
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

**LLM & Anbieter:**

| Variable | Default | Beschreibung | Beispiel |
|---|---|---|---|
| `LLM_PROVIDER` | `anthropic` | LLM-Provider (`anthropic` / `openai` / `openrouter` / `ollama`) | `openai` |
| `ANTHROPIC_API_KEY` | – | API-Key für Anthropic Claude | (42 Zeichen) |
| `OPENAI_API_KEY` | – | API-Key für OpenAI GPT | (sk-...) |
| `OPENROUTER_API_KEY` | – | API-Key für OpenRouter (Multi-Modell) | – |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Lokal laufender Ollama-Server | – |
| `SCOUT_TIER` | `pro` | Modellwahl-Tier (`lite` / `pro` / `max`) | `max` |

**Web-Suche:**

| Variable | Default | Beschreibung | Beispiel |
|---|---|---|---|
| `SEARXNG_URL` | `http://localhost:8888` | SearXNG-Instanz (primäres Backend) | `http://search.example.com` |
| `LANGSEARCH_API_KEY` | – | LangSearch API-Key (strukturierte Suche) | – |
| `LANGSEARCH_ENABLED` | `auto` | LangSearch aktivieren (`true`/`false`/`auto`) | `true` |
| `GOOGLE_FACT_CHECK_API_KEY` | – | Google Fact Check API (kostenlos, 1000/Tag) | – |
| `GOOGLE_FACT_CHECK_ENABLED` | `auto` | Google Fact Check aktivieren | `true` |
| `TAVILY_API_KEY` | – | Tavily Suche (optionales Plugin) | – |
| `TAVILY_ENABLED` | `false` | Tavily explizit aktivieren | `true` |

**Claim-Processing:**

| Variable | Default | Beschreibung | Beispiel |
|---|---|---|---|
| `CLAIM_TOP_N` | `0` | Max. Claims pro Analyse (0 = unbegrenzt) | `5` |
| `COVE_ENABLED` | `true` | Chain-of-Verification aktivieren | `false` |
| `MAX_VERIFICATION_QUESTIONS` | `3` | Max. CoVe-Fragen pro Claim | `5` |
| `AGENT_TIMEOUT` | `180` | Timeout pro Agent (Sekunden) | `300` |
| `USE_CANONICAL_CACHE` | `true` | Canonical Hash für Dedupling nutzen | `true` |
| `SEMANTIC_CACHE_ENABLED` | `false` | Embedding-ähnliche Claims cachen | `true` |
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | Ähnlichkeits-Schwelle für Semantic Cache | `0.90` |

**Datenspeicher:**

| Variable | Default | Beschreibung | Beispiel |
|---|---|---|---|
| `DB_BACKEND` | `sqlite` | Persistenter Store (`sqlite` / `postgres`) | `postgres` |
| `CACHE_BACKEND` | `sqlite` | Claim-Cache (`sqlite` / `valkey` / `redis`) | `valkey` |
| `POSTGRES_URL` | – | PostgreSQL Connection String | `postgresql://user:pass@localhost/db` |
| `VALKEY_URL` | – | Valkey Connection String | `redis://localhost:6379` |
| `DATABASE_DIR` | `.fakeguard_data` | Verzeichnis für SQLite-Dateien | `/var/lib/fakeguard` |

**Sonstiges:**

| Variable | Default | Beschreibung | Beispiel |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | Log-Verbosität (`DEBUG` / `INFO` / `WARNING`) | `DEBUG` |
| `CORS_ORIGINS` | `["*"]` | Erlaubte CORS-Domains | `["https://example.com"]` |
| `MAX_TEXT_LENGTH` | `10000` | Max. Eingabe-Zeichenlänge | `20000` |
| `HOT_RELOAD_DOMAIN_TIERS` | `true` | Domain-Tiers ohne Neustart neu laden | `true` |

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

## Letzte Verbesserungen (v2 Hardening & Extensions)

### Legacy-Cleanup
- **Fallback-Entfernung:** Alter Monolith-Fallback (`_legacy_fallback.py`) wurde entfernt
- **Saubere Architektur:** Alle Agenten nutzen jetzt moderne v2-Pipeline
- **Tests aktiviert:** `test_orchestrator_v2.py` und `test_cove_processor.py` validieren gesamte Pipeline

### Claim-Verarbeitung
- **Deduplication:** Semantisch identische Claims (unterschiedliche Formulierungen) werden nach Kanonisierung dedupliziert
- **Canonical Hash:** `SHA256(canonical_text.strip().lower())` verhindert doppelte Analysen
- **Prioritisierung:** `priority_score`, `harm_score`, `checkworthiness_score` für Top-N Filterung

### Evidence-Verarbeitung
- **Adaptive RAG:** Intelligente Retrieval-Runden mit iterativem Ranking
- **CRAG (Corrective RAG):** Konfidenz-basierte Re-Retrieval bei niedriger Konfidenz
- **Crawl4AI & Newspaper4k:** Besseres Web-Scraping mit Boilerplate-Entfernung
- **Self-RAG:** Selbst-kritische Verarbeitung von Evidenz

### Contradiction Detection
- **WeightedContradiction:** Typisierung + Gewichtung von Widersprüchen
- **Max 5 pro Claim:** Beste Widersprüche werden herausgefiltert
- **Trust Boundary:** VerdictAgent sieht nur strukturierte Excerpts (max 800 Zeichen)

### Kalibrierung & Confidence
- **Brier Scores:** Messbare Confidence-Calibration
- **Reliability Diagrams:** Visualisierung der Modell-Calibration
- **Consensus-Contradiction-Override:** Wenn AGREEING+FALSE oder CONTRADICTORY+TRUE → MISLEADING

### Infrastruktur
- **Analysis ID Tracing:** UUID-basierte Korrelations-IDs für durchgängiges Debugging
- **Hot-Reload:** Domain-Tiers und Scoring-Weights ohne Neustart neu laden
- **Semantic Cache:** Embedding-ähnliche Claims bei Cache-Miss (Threshold 0.92)
- **Rate Limiting:** Token-Bucket pro IP zum Schutz vor Abuse

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

---

## Agent-Verantwortungen

| Agent | Eingabe | Ausgabe | Schlüsselfunktionalität |
|-------|---------|---------|-------------------------|
| **ClaimExtractorAgent** | Rohtext | `list[ProcessedClaim]` | 6-stufige Pipeline: Splitter, Selector, Disambiguator, Decomposer, Canonicalizer, Prioritizer |
| **FactCheckerAgent** | `Claim` + Kontext | `FactCheckResult` | Facade: EvidenceBuilder + CoVe + VerdictAgent. Wertet Behauptungen gegen Webquellen |
| **EvidenceBuilderAgent** | `Claim` | `EvidencePack` | Suche (SearXNG, LangSearch, GFC) + Scraping + Trust Boundary (max 800 Zeichen pro Excerpt) |
| **CoVeProcessor** | `Claim`, `EvidencePack` | `CoVeTrace` | Chain-of-Verification: Baseline-Assessment → Verifikationsfragen → unabhängige Antworten → Reconciliation |
| **VerdictAgent** | `Claim`, `EvidencePack` | `FactCheckResult` | Faktencheck-Urteil (TRUE/MOSTLY_TRUE/MISLEADING/MOSTLY_FALSE/FALSE/UNVERIFIABLE) auf Basis strukturierter Evidenz |
| **NumberAuditorAgent** | `Claim` (STATISTICAL) | `NumberAuditResult` | Validiert Statistiken, Berechnungen, Cherry-Picking, Manipulationen (Basiseffekt, etc.) |
| **RhetoricAnalyzerAgent** | Originaltext | `RhetoricAnalysisResult` | Analysiert Manipulationstechniken: Loaded Language, Dog Whistles, Fear Appeals, Whataboutism |
| **ImageAnalyzerAgent** | `list[bytes]` | `ImageAnalysisResult` | Multimodale Bildanalyse: Manipulation-Erkennung, OCR, Content Verification |
| **SynthesizerAgent** | Alle Teilergebnisse | `SynthesisResult` | Aggregiert FactCheck-, Number-, Rhetoric-, Image-Ergebnisse → OverallRating + Confidence + Korrektionen |

**Architektur-Hinweise:**
- Alle Agenten erben von `BaseAgent` (tools/base.py)
- Facade-Pattern: FactChecker, ClaimExtractor wrappen mehrere spezialisierte Agenten
- Graceful Degradation: Alle Agenten außer ClaimExtractor nutzen `run_safe_async()` (Fehler sammeln, nicht abbrechen)
- Timeouts: 180s pro Agent (konfigurierbar via `AGENT_TIMEOUT`)
- LLM-Auswahl nach ScoutTier: LITE/PRO/MAX bestimmt Modellwahl (Gemma vs Claude)
