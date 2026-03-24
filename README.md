# FakeNewsGuard

**Multi-Agent-System zur Erkennung von Fake News, Faktenverzerrung und manipulativer Rhetorik.**

Python-Backend mit FastAPI, asyncio und SQLite – läuft komplett unabhängig und nutzt LLM-APIs (Anthropic/OpenAI/OpenRouter) sowie mehrere Web-Search-Quellen (SearXNG, LangSearch, Google Fact Check API).

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
├── config.py                    # Konfiguration aus .env
├── orchestrator.py              # Zentrale Steuerung, Top-N Claim-Auswahl
│
├── agents/
│   ├── base.py                  # BaseAgent – LLM + Search + Logging
│   ├── claim_extractor.py       # Facade → ClaimProcessorAgent
│   ├── claim_processor.py       # 6-stufige Claim-Processing-Pipeline
│   ├── evidence_builder.py      # Retrieval → strukturiertes EvidencePack
│   ├── cove_processor.py        # Chain-of-Verification (CoVe)
│   ├── verdict_agent.py         # Verdikt auf Basis von EvidencePack
│   ├── fact_checker.py          # Facade: EvidenceBuilder + CoVe + Verdict
│   ├── number_auditor.py        # Zahlen- und Statistikprüfung
│   ├── rhetoric_analyzer.py     # Framing, Dog Whistles, Manipulation
│   ├── image_analyzer.py        # Bildanalyse (multimodal)
│   └── synthesizer.py           # Aggregation → Gesamtverdikt
│
├── models/
│   ├── schemas.py               # Kern-Datenmodelle (ProcessedClaim, etc.)
│   ├── evidence_models.py       # EvidencePack + Trust-Boundary-Modelle
│   └── verdict_models.py        # CoVeTrace, BaselineAssessment, etc.
│
├── tools/
│   ├── llm.py                   # LLM-Abstraktion (Anthropic/OpenAI/Ollama)
│   ├── web_search.py            # WebSearchClient + LangSearchClient
│   └── cache.py                 # SQLite-basierter Claim-Cache
│
├── tests/
│   ├── conftest.py              # Shared fixtures (minimal_config, etc.)
│   └── unit/                   # Unit-Tests (68 Tests, alle mock-basiert)
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
| `SEARCH_PROVIDER` | `searxng` | `searxng` / `tavily` / `serper` / `brave` |
| `SEARXNG_BASE_URL` | `http://localhost:8888` | SearXNG-Instanz |
| `LANGSEARCH_API_KEY` | – | LangSearch API-Key (optional) |
| `LANGSEARCH_ENABLED` | `false` | LangSearch aktivieren |
| `GOOGLE_FACT_CHECK_API_KEY` | – | Google Fact Check API-Key (optional) |
| `GOOGLE_FACT_CHECK_ENABLED` | `false` | Google Fact Check aktivieren |
| `CLAIM_TOP_N` | `0` | Max. Anzahl Claims pro Analyse (0 = unbegrenzt) |
| `COVE_ENABLED` | `false` | Chain-of-Verification aktivieren |
| `MAX_VERIFICATION_QUESTIONS` | `3` | Max. CoVe-Verifikationsfragen pro Claim |
| `USE_CANONICAL_CACHE` | `true` | Canonical Hash für Cache-Lookup nutzen |

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
