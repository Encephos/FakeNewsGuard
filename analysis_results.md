# FakeNewsGuard – Projekt-Analyse

> Vollständige Analyse des Projekts. **Kein Code wurde geändert.** Alle Findings sind als Verbesserungspotenziale dokumentiert.

---

## 🔴 Kritische Findings

### 1. API-Keys im Repository

> [!CAUTION]
> Die Datei `.env` enthält **echte API-Keys im Klartext** und ist im Git-Repository vorhanden. Obwohl `.env` in [.gitignore]( file:///Users/oskarsattler/FakeNewsGuard/.gitignore) steht, existiert die Datei bereits mit echten Keys:

| Key | Betroffen |
|-----|-----------|
| `OPENROUTER_API_KEY` | `sk-or-v1-1a2b...` |
| `TAVILY_API_KEY` | `tvly-dev-43x...` |
| `TELEGRAM_BOT_TOKEN` | `8723654668:AAH...` |
| `GOOGLE_FACTCHECK_API_KEY` | `AIzaS...` |
| `LANGSEARCH_API_KEY` | `sk-772...` |
| `SETUP_SECRET` | `zufall` |

**Empfehlung:** Alle Keys sofort rotieren. Prüfe mit `git log -- .env` ob die Datei jemals committed wurde. Falls ja, Keys in der gesamten Git-History bereinigen (BFG Repo-Cleaner oder `git filter-repo`).

---

### 2. JWT-Secret Auto-Generation

In [user_db.py#L63-69](file:///Users/oskarsattler/FakeNewsGuard/tools/user_db.py#L63-L69):

```python
def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        secret = secrets.token_hex(32)
        os.environ["JWT_SECRET"] = secret
    return secret
```

> [!WARNING]
> Ohne gesetztes `JWT_SECRET` wird bei jedem Neustart ein neues Secret generiert → **alle bestehenden Tokens werden ungültig**. In Produktion müssen sich Nutzer nach jedem Restart neu einloggen.

**Empfehlung:** `JWT_SECRET` als Pflichtfeld validieren (wie `OPENROUTER_API_KEY`).

---

### 3. SETUP_SECRET ist `zufall`

Das `SETUP_SECRET` in `.env` ist ein triviales Wort. Dieser Endpoint ([api.py#L880](file:///Users/oskarsattler/FakeNewsGuard/api.py#L880-L907)) erlaubt das Setzen von Credentials für Telegram-Accounts – ein schwaches Secret ermöglicht Account-Übernahme.

---

## 🟡 Architektur-Findings

### 4. Monolithische `api.py` (1.424 Zeilen)

[api.py](file:///Users/oskarsattler/FakeNewsGuard/api.py) enthält:
- Auth-Endpunkte (Register, Login, Refresh, Profile, Consent, Telegram-Link)
- Admin-Endpunkte
- Analyse-Job-Logik (Job-Store, Watchdog, Worker)
- Archiv-Endpunkte
- PDF-Export
- Cross-Reference Graph
- Content Extraction
- I18n-Endpunkte
- Health-Check

**Empfehlung:** Aufteilung in FastAPI-Router:
- `routers/auth.py` – Auth + Profile
- `routers/admin.py` – Admin-Dashboard
- `routers/analysis.py` – Analyse-Jobs
- `routers/archive.py` – Archiv + PDF-Export
- `routers/graph.py` – Cross-Reference Graph

### 5. Redundante `AppConfig()`-Instanziierung in `api.py`

Mindestens 7× wird `AppConfig()` als Einmalaufruf genutzt:

```python
_cors_origins = AppConfig().cors_origins    # Zeile 63
set_default_locale(AppConfig().language)     # Zeile 59
config = AppConfig()                        # Zeile 113, 125, 137, 149, 357
```

Jede Instanz liest die `.env` neu und erstellt eigene Sub-Configs. 

**Empfehlung:** Ein `_app_config`-Singleton einführen.

### 6. In-Memory Job-Store

[api.py#L104](file:///Users/oskarsattler/FakeNewsGuard/api.py#L104): `_jobs: dict[str, dict[str, Any]] = {}`

> [!IMPORTANT]
> Jobs gehen bei Restart verloren. Bei horizontal skalierten Deployments (mehrere Uvicorn-Worker) hat jeder Worker seinen eigenen isolierten Job-Store.

**Empfehlung:** Für Produktion Redis oder SQLite-basierter Job-Store. Mindestens Uvicorn auf `--workers 1` limitieren (was aktuell implizit so ist).

### 7. Code-Duplizierung: Sync vs. Async Orchestrator

[orchestrator.py](file:///Users/oskarsattler/FakeNewsGuard/orchestrator.py) enthält `analyze()` (Zeile 173-270) und `analyze_async()` (Zeile 274-378) – nahezu identische Logik mit ~95% Überschneidung.

**Empfehlung:** Nur `analyze_async()` behalten und `analyze()` als `asyncio.run(self.analyze_async(text))` Wrapper implementieren.

### 8. Duplizierter Analyse-Flow in `api.py`

[_run_job](file:///Users/oskarsattler/FakeNewsGuard/api.py#L333-L634) in `api.py` dupliziert den Orchestrator-Workflow manuell (Phase 1-4), statt `orchestrator.analyze_async()` zu nutzen.

**Grund:** Vermutlich die `push_step()` Callbacks. Die existierende `on_step`-Callback im Orchestrator würde das aber abdecken.

---

## 🟡 Konfiguration & Dependencies

### 9. Inkonsistente Default-Modelle

| Stelle | Default-Modell |
|--------|---------------|
| [config.py#L47](file:///Users/oskarsattler/FakeNewsGuard/config.py#L47) | `qwen/qwen3-235b-a22b-thinking-2507` |
| [main.py#L220](file:///Users/oskarsattler/FakeNewsGuard/main.py#L220) | `qwen/qwen3.5-397b-a17b` |
| Kommentar config.py | `qwen/qwen3.5-397b-a17b` |

Das CLI überschreibt das Modell aus `config.py` immer mit dem eigenen Default.

### 10. `requirements.txt` ohne Pinning

```
anthropic>=0.40.0
openai>=1.50.0
httpx>=0.27.0
...
```

Keine oberen Versionsgrenzen. Ein `pip install` kann Breaking Changes einführen. Fehlende Pakete:

| Fehlt | Gebraucht in |
|-------|-------------|
| `python-telegram-bot` oder `httpx` (bereits drin) | `telegram_bot.py` |
| `pytest`, `pytest-mock`, `pytest-asyncio` | `tests/` |
| `tavily-python` | `tools/web_search.py` (ggf.) |

**Empfehlung:** `pip freeze > requirements.lock` oder Tool wie `pip-compile` nutzen.

### 11. `docker-compose.yml` Version Deprecated

```yaml
version: '3.8'
```
Das `version`-Feld ist seit Docker Compose V2 [deprecated und wird ignoriert](https://docs.docker.com/compose/releases/migrate/).

### 12. Fehlende `.dockerignore`-Einträge

Die [.dockerignore](file:///Users/oskarsattler/FakeNewsGuard/.dockerignore) hat nur 68 Bytes. Folgendes sollte ergänzt werden:

```
.git/
.venv/
__pycache__/
*.db
*.db-shm
*.db-wal
.env
tests/
ObsidianMD/
frontend/node_modules/
```

Ohne das wird das Docker-Image unnötig groß (`.git/`, `node_modules/`, DB-Dateien).

---

## 🟡 Security

### 13. CORS `*` als Default

[config.py#L465](file:///Users/oskarsattler/FakeNewsGuard/config.py#L465-L470):
```python
cors_origins: list = field(
    default_factory=lambda: (
        [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
        or ["*"]
    )
)
```

Für eine Anwendung mit JWT-Auth ist `*` gefährlich – Credential-basierte Requests von jeder Origin erlaubt. `allow_credentials=True` fehlt zwar, aber trotzdem Best Practice die Origins zu beschränken.

### 14. Kein `allow_credentials` in CORS, aber Cookie-Auth

Refresh-Tokens werden als httpOnly-Cookies gesetzt ([api.py#L697](file:///Users/oskarsattler/FakeNewsGuard/api.py#L697-L705)), aber CORS fehlt `allow_credentials=True`. Das bedeutet, der Browser sendet keine Cookies bei Cross-Origin-Requests → Refresh-Token funktioniert nur mit Same-Origin.

### 15. SHA-256 Fallback für Passwort-Hashing

[user_db.py#L39-40](file:///Users/oskarsattler/FakeNewsGuard/tools/user_db.py#L39-L40):
```python
# Fallback: SHA-256 (less secure, but works without bcrypt)
return "sha256:" + hashlib.sha256(pw_bytes).hexdigest()
```

SHA-256 ohne Salt ist unsicher für Passwort-Hashing. Da `bcrypt` in `requirements.txt` steht, sollte dieser Fallback entfernt oder mit einer Warnung versehen werden.

### 16. Archiv-Endpunkte ohne Auth

[api.py#L1227](file:///Users/oskarsattler/FakeNewsGuard/api.py#L1227-L1275):
- `GET /api/archive` – Alle Analysen auflisten
- `GET /api/archive/{id}` – Detail-Ansicht
- `DELETE /api/archive/{id}` – Löschen
- `GET /api/archive-stats` – Statistiken

Alle ohne Authentifizierung. Jeder kann Analysen einsehen und löschen.

### 17. PDF-Export ohne Auth

[api.py#L1314](file:///Users/oskarsattler/FakeNewsGuard/api.py#L1314-L1338): `POST /api/export/pdf` nimmt beliebige Daten als `dict` entgegen – keine Pydantic-Validierung, kein Auth.

---

## 🔵 Performance & Robustheit

### 18. `asyncio.get_event_loop()` (deprecated)

In [agents/base.py#L61](file:///Users/oskarsattler/FakeNewsGuard/agents/base.py#L61) und [api.py#L407,435,493,510,526,578](file:///Users/oskarsattler/FakeNewsGuard/api.py#L407):

```python
await asyncio.get_event_loop().run_in_executor(...)
```

Seit Python 3.10+ deprecated. Besser: `asyncio.get_running_loop()`.

### 19. Thread-Pool-Sizing

[agents/base.py#L18](file:///Users/oskarsattler/FakeNewsGuard/agents/base.py#L18):
```python
_thread_pool = ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4))
```

Bei 8 CPU-Cores = 32 Threads. Da die Arbeit I/O-bound ist (LLM API Calls), ist das okay, aber es gibt kein Monitoring wenn der Pool voll ist.

### 20. SQLite `check_same_thread=False` ohne Connection-Pooling

[tools/cache.py](file:///Users/oskarsattler/FakeNewsGuard/tools/cache.py) nutzt eine einzige Connection mit `check_same_thread=False` und ein `threading.Lock`.

[tools/user_db.py](file:///Users/oskarsattler/FakeNewsGuard/tools/user_db.py) erstellt bei jedem Zugriff eine neue Connection (connect/close Pattern).

**Inkonsistenter Ansatz**: Cache nutzt Singleton-Connection, UserDB nutzt Per-Request-Connections.

### 21. Fehlende Graceful Shutdown

Kein `@app.on_event("shutdown")` oder Lifespan-Handler. SQLite-Connections und httpx-Clients werden nicht sauber geschlossen.

---

## 🔵 Tests

### 22. Geringe Testabdeckung

Test-Struktur:
- `tests/conftest.py` – Fixtures (gut)
- `tests/test_retrieval_refactor.py` – Integration-Test
- `tests/unit/` – Verzeichnis existiert
- `tests/tools/` – Verzeichnis existiert

`pytest.ini` ignoriert explizit 3 Testdateien:
```
addopts = --ignore=tests/unit/test_orchestrator.py 
          --ignore=tests/unit/test_orchestrator_v2.py 
          --ignore=tests/unit/test_cove_processor.py
```

**Fehlend:**
- Unit-Tests für `api.py`-Endpunkte (FastAPI `TestClient`)
- Tests für Auth-Flow (Register, Login, Token-Refresh)
- Tests für Rate-Limiter
- Tests für `telegram_bot.py`
- Tests für `tools/archive.py`, `tools/user_db.py`

### 23. `requirements-dev.txt` unvollständig

```
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-mock>=3.12.0
httpx[http2]>=0.27.0
```

Fehlt: `pytest-cov` für Coverage, `mypy` für Type-Checking, `ruff`/`black` für Linting/Formatting.

---

## 🔵 Code-Qualität

### 24. Fehlende Type-Hints an vielen Stellen

- [api.py#L253](file:///Users/oskarsattler/FakeNewsGuard/api.py#L253): `def _transform_result(result: Any, claims_map: dict[str, Any]) -> dict:` – Return-Type unspezifisch
- [api.py#L1315](file:///Users/oskarsattler/FakeNewsGuard/api.py#L1315): `async def export_pdf_from_result(req: dict)` – kein Pydantic-Modell
- `_jobs` ist `dict[str, dict[str, Any]]` – ein Pydantic-Modell wäre typsicher

### 25. Gemischte Sprache

Docstrings und Kommentare mischen Deutsch und Englisch. Endpunkt-Responses sind teils Deutsch, teils Englisch:
- `"Nicht authentifiziert."` vs. `"Node not found."`
- Config-Docstrings: Deutsch
- API-Docstrings: Teils Englisch

### 26. Unused Import-Detection

In [orchestrator.py#L25](file:///Users/oskarsattler/FakeNewsGuard/orchestrator.py#L25): `from typing import Any, Callable` – `Any` wird nicht genutzt.

### 27. Härtung der `_parse_json` Methode

[tools/llm.py#L398-433](file:///Users/oskarsattler/FakeNewsGuard/tools/llm.py#L398-L433): Die Fallback-Logik (Bracket-Zählung) berücksichtigt keine Strings mit `{`/`}`, was zu fehlerhaftem Parsing führen kann.

---

## 🟢 Positives

| Aspekt | Bewertung |
|--------|-----------|
| **Multi-Agent-Architektur** | Saubere Abstraktion über `BaseAgent`, klare Separation der Agent-Verantwortlichkeiten |
| **Graceful Degradation** | `run_safe()` Pattern verhindert Agent-Fehler den Gesamtprozess zu unterbrechen |
| **Multi-Provider LLM** | Anthropic, OpenAI, OpenRouter, Ollama – flexibel konfigurierbar |
| **Tier-System** | Gutes Pricing-Modell (Lite/Pro/Max) mit Access Control |
| **Source Classification** | Detaillierte Quellen-Kategorisierung mit Tier-System |
| **Retry-Logik** | Exponentieller Backoff mit Jitter – korrekt implementiert |
| **Claim Processing Pipeline** | Mehrstufig mit Frame-Extraction, Canonicalization, Prioritization |
| **Docker-Setup** | Docker Compose mit SearXNG, Backend, Frontend, Telegram – vollständig |
| **i18n-Support** | Internationalisierung vorhanden |
| **PDF-Export** | Analyse-Ergebnisse als PDF-Reports exportierbar |
| **Cross-Reference Graph** | Beziehungen zwischen Analysen tracken |

---

## Prioritäten-Empfehlung

| Prio | Finding | Aufwand |
|------|---------|---------|
| 🔴 **1** | API-Keys rotieren + `.env` aus Git-History entfernen | 1h |
| 🔴 **2** | `JWT_SECRET` als Pflichtfeld + `SETUP_SECRET` stärken | 30min |
| 🟡 **3** | Archiv/PDF-Endpunkte mit Auth absichern | 2h |
| 🟡 **4** | `api.py` in Router aufteilen | 4h |
| 🟡 **5** | Default-Modell-Inkonsistenz bereinigen | 30min |
| 🟡 **6** | `.dockerignore` erweitern | 15min |
| 🔵 **7** | `requirements.txt` pinnen | 30min |
| 🔵 **8** | Test-Coverage erweitern | 8h+ |
| 🔵 **9** | `asyncio.get_event_loop()` → `get_running_loop()` | 30min |
| 🔵 **10** | Graceful Shutdown implementieren | 1h |
