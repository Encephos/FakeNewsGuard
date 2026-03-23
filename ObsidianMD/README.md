# FakeNewsGuard – Projektdokumentation

> **FakeNewsGuard** ist ein mehrstufiges, agentenbasiertes Faktenchecking-System. Es zerlegt Behauptungen aus Texten oder URLs, prüft sie gegen Webquellen, analysiert rhetorische Manipulationen und synthetisiert ein abschliessendes Urteil.

---

## Schnellnavigation

| Bereich | Beschreibung |
|---|---|
| [[Architektur]] | Systemüberblick, Schichtenmodell, Modulstruktur |
| [[Datenfluss]] | Wie eine Analyse von Eingabe bis Ergebnis läuft |
| [[Agenten]] | Übersicht aller 6 Agenten |
| [[Orchestrator]] | 4-Phasen-Workflow & Parallelisierung |
| [[Datenmodelle]] | Pydantic-Schemas, Enums, JSON-Schemas |
| [[API]] | FastAPI-Endpunkte, Job-Queue, Middleware |
| [[Frontend]] | Next.js-Architektur, Seiten, Komponenten |
| [[Scout-Tiers]] | Lite / Pro / Max – Modellauswahl |
| [[Konfiguration]] | AppConfig und alle Unter-Configs |
| [[Tools]] | Werkzeugübersicht (LLM, Suche, Cache, Retry …) |
| [[Internationalisierung]] | Backend- und Frontend-i18n |
| [[Docker]] | docker-compose, Services, Volumes |
| [[Telegram-Bot]] | Telegram-Integration |
| [[Testing]] | pytest-Suite, Fixtures, asyncio |

---

## Was ist FakeNewsGuard?

FakeNewsGuard ist ein automatisiertes Nachprüfungssystem, das:

1. **Behauptungen extrahiert** – aus Artikeln, Social-Media-Posts, Videos-Transkripten oder beliebigem Text
2. **Fakten prüft** – durch adaptive Websuche, Quellenranking und Scraping mit LLM-Auswertung
3. **Statistiken auditiert** – erkennt typische Datentricks (Basiseffekte, fehlende Pro-Kopf-Normierung, Cherry-Picking …)
4. **Rhetorik analysiert** – identifiziert Manipulationstechniken wie Angstappelle, Whataboutismus, Dog Whistles
5. **Bilder analysiert** – OCR, Manipulationserkennung, emotionales Framing (Vision-Agent)
6. **Ein Urteil synthetisiert** – mit Konfidenzscore, Korrekturen, Fairness-Anmerkungen und Quellenangaben

### Technologie-Stack

| Schicht | Technologie |
|---|---|
| Backend | Python 3.12, FastAPI, asyncio |
| LLM-Abstraktion | Anthropic / OpenAI / OpenRouter / Ollama |
| Websuche | SearXNG (self-hosted), Tavily, Serper, Brave |
| Datenbank | SQLite (WAL-Modus) – mehrere Datenbanken |
| Frontend | Next.js 14, TypeScript, React |
| Deployment | Docker Compose (4 Services) |
| Bots | python-telegram-bot |

---

## Verzeichnisstruktur

```
FakeNewsGuard/
├── agents/                  # 6 Agenten
│   ├── base.py              # Abstrakte Basisklasse
│   ├── claim_extractor.py
│   ├── fact_checker.py
│   ├── number_auditor.py
│   ├── rhetoric_analyzer.py
│   ├── synthesizer.py
│   └── image_analyzer.py
├── tools/                   # Infrastruktur-Werkzeuge
│   ├── llm.py               # LLM-Abstraktion
│   ├── web_search.py        # Suchmaschinen-Abstraktion
│   ├── cache.py             # SQLite-Cache
│   ├── retry.py             # Retry-Logik
│   ├── archive.py           # Analyse-Archiv
│   ├── user_db.py           # Nutzer-Datenbank
│   ├── logger.py            # Strukturiertes Logging
│   ├── source_classifier.py # Quellenklassifikation
│   ├── scrape_ranker.py     # Scraping-Priorisierung
│   ├── source_scraper.py    # Async-HTTP-Scraper
│   ├── cross_reference.py   # Wissens-Graph
│   ├── rate_limiter.py      # Token-Bucket
│   ├── pdf_export.py        # PDF-Export
│   ├── factcheck_databases.py
│   └── content_extractor.py
├── models/
│   └── schemas.py           # Alle Pydantic-Modelle
├── i18n/                    # Backend-Übersetzungen
│   └── locales/
│       ├── de.py
│       └── en.py
├── frontend/                # Next.js-App
│   └── src/app/
│       ├── page.tsx
│       ├── admin/
│       ├── login/
│       ├── profile/
│       ├── archiv/
│       ├── components/
│       └── lib/
├── tests/                   # pytest-Suite
├── orchestrator.py          # Analyse-Workflow
├── api.py                   # FastAPI-App
├── config.py                # Konfiguration
├── main.py                  # CLI
├── telegram_bot.py          # Telegram-Bot
├── Dockerfile
└── docker-compose.yml
```

---

## Einstiegspunkte

### CLI
```bash
python main.py "Angela Merkel hat die Rente um 20% gekürzt."
python main.py --file artikel.txt --tier max --json
python main.py --interactive
```

### API-Server
```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

### Docker (empfohlen)
```bash
docker compose up -d
```

### Telegram-Bot
```bash
python telegram_bot.py
```

---

## Analyse-Lebenszyklus (Kurzfassung)

```
POST /api/analyze  →  job_id
                         ↓ polling
GET /api/jobs/{id}  →  steps[], status, result
```

Intern läuft dabei der [[Datenfluss|4-Phasen-Workflow]] ab:

```
Phase 1  →  ClaimExtractor   (sequenziell)
Phase 2  →  FactChecker      (parallel, je Claim)
         →  NumberAuditor    (parallel, nur stat. Claims)
Phase 3  →  RhetoricAnalyzer (parallel zu Phase 2)
Phase 4  →  Synthesizer      (sequenziell, nach 2+3)
```

---

## Bewertungsskala

| Rating | Bedeutung |
|---|---|
| `RELIABLE` | Verlässlich |
| `MOSTLY_RELIABLE` | Überwiegend verlässlich |
| `MIXED` | Gemischt |
| `MISLEADING` | Irreführend |
| `HIGHLY_MISLEADING` | Stark irreführend |
| `FABRICATED` | Erfunden |

---

## Weiterführende Dokumente

- Für Agenten-Details: [[Agenten]], [[Agent-ClaimExtractor]], [[Agent-FactChecker]], [[Agent-NumberAuditor]], [[Agent-RhetoricAnalyzer]], [[Agent-Synthesizer]], [[Agent-ImageAnalyzer]]
- Für Infrastruktur: [[Tools]], [[LLM-Abstraktion]], [[Websuche]], [[Cache]], [[Retry]], [[Datenbank]]
- Für Deployment: [[Docker]], [[Konfiguration]]
