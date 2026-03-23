# Index – Alle Dokumente

> Startseite: [[README]]

Vollständige Liste aller Dokumentationsdateien mit Kurzbeschreibung.

---

## Kernkonzepte

| Dokument | Inhalt |
|---|---|
| [[README]] | Projektübersicht, Quickstart, Navigationshilfe |
| [[Architektur]] | Schichtenmodell, Modulübersicht, Datenbank-Topologie |
| [[Datenfluss]] | Wie eine Anfrage von Eingabe bis Ergebnis fließt |
| [[Scout-Tiers]] | Lite / Pro / Max – Modellauswahl und Kompromisse |

## Agenten

| Dokument | Inhalt |
|---|---|
| [[Agenten]] | Übersicht aller 6 Agenten, BaseAgent, Graceful Degradation |
| [[Agent-ClaimExtractor]] | Text → atomare Behauptungen |
| [[Agent-FactChecker]] | Claim → Websuche → FactRating |
| [[Agent-NumberAuditor]] | Statistische Manipulationen erkennen |
| [[Agent-RhetoricAnalyzer]] | Rhetorik-Muster im Volltext |
| [[Agent-Synthesizer]] | Gesamturteil aus allen Teilresultaten |
| [[Agent-ImageAnalyzer]] | Bilder: OCR, Manipulation, Framing |

## Orchestrierung

| Dokument | Inhalt |
|---|---|
| [[Orchestrator]] | 4-Phasen-Workflow, asyncio.gather, on_step Callbacks |

## Datenmodelle

| Dokument | Inhalt |
|---|---|
| [[Datenmodelle]] | Alle Pydantic-Modelle, Enums, JSON-Schemas |

## Infrastruktur & Tools

| Dokument | Inhalt |
|---|---|
| [[Tools]] | Übersicht aller Werkzeuge im tools/-Ordner |
| [[LLM-Abstraktion]] | Provider, Structured Output, Vision, Timeout |
| [[Websuche]] | SearXNG, Tavily, Multi-Search, Deduplizierung |
| [[Cache]] | SQLite-Cache, TTL, SHA256-Keys |
| [[Retry]] | Exponential Backoff, Jitter, Fehlerklassen |
| [[Datenbank]] | Archive, UserDB (JWT), Cross-Reference Graph, Logger |

## Backend & API

| Dokument | Inhalt |
|---|---|
| [[API]] | FastAPI-Endpunkte, Job-Queue, Middleware, Auth |
| [[Konfiguration]] | Alle AppConfig-Unterklassen, .env-Vorlage |

## Frontend

| Dokument | Inhalt |
|---|---|
| [[Frontend]] | Next.js-Seiten, Komponenten, API-Client, Auth-Context |

## Querschnittsthemen

| Dokument | Inhalt |
|---|---|
| [[Internationalisierung]] | Backend-i18n (t()), Frontend-i18n (useI18n) |
| [[Docker]] | docker-compose, 4 Services, Volumes, Produktion |
| [[Telegram-Bot]] | Bot-Befehle, Polling, Nutzer-Verlinkung |
| [[Testing]] | pytest, asyncio_mode, Fixtures, Beispieltests |
