# FastAPI Backend

> Zurück: [[README]] | Siehe auch: [[Orchestrator]], [[Datenbank]], [[Frontend]]

`api.py` ist die HTTP-Schicht des Systems. Sie stellt RESTful Endpunkte bereit, verwaltet eine asynchrone Job-Queue und implementiert Authentication, Rate-Limiting und CORS.

---

## Start

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

In Docker: → [[Docker]]

---

## Endpunkte – Übersicht

### Analyse
| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/api/analyze` | Neue Analyse starten |
| GET | `/api/jobs/{job_id}` | Job-Status abfragen |
| POST | `/api/extract` | URL-Inhalt extrahieren |

### Archiv
| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/archive` | Analyse-Liste (paginiert) |
| GET | `/api/archive/{id}` | Einzelne Analyse |
| GET | `/api/archive/search` | Volltextsuche |

### Authentifizierung
| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/api/auth/register` | Nutzer registrieren |
| POST | `/api/auth/login` | Login → JWT |
| POST | `/api/auth/refresh` | Access Token erneuern |
| GET | `/api/auth/me` | Aktueller Nutzer |
| POST | `/api/auth/consent` | Logging-Einwilligung |

### Admin
| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/admin/users` | Nutzerliste mit Stats |
| GET | `/api/admin/metrics` | System-Metriken |
| GET | `/api/admin/logs` | Log-Ring-Buffer |
| POST | `/api/admin/reload-data` | Cache neu laden (Domain-Tiers, Config) |
| GET | `/api/admin/calibration` | Confidence-Kalibrierungsbericht (Brier Score) |
| POST | `/api/admin/calibration/ground-truth` | Ground-Truth für Kalibrierung setzen |

---

## Analyse-Workflow

### 1. Job erstellen

```http
POST /api/analyze
{
  "text": "Artikel-Text oder URL...",
  "tier": "pro",           // optional: lite | pro | max
  "url": "https://...",    // optional: Original-URL
  "agent": "all"           // optional: für zukünftige Einzelagenten-Aufrufe
}
```

**Response:**
```json
{ "job_id": "abc123" }
```

Der Job wird sofort als Hintergrund-Task gestartet. Kein Warten auf Abschluss.

### 2. Status pollen

```http
GET /api/jobs/abc123
```

**Response (laufend):**
```json
{
  "status": "running",
  "steps": [
    { "step": "extraction", "status": "done", "claims_count": 7 },
    { "step": "fact_check", "claim_id": 1, "status": "running" }
  ],
  "result": null
}
```

**Response (abgeschlossen):**
```json
{
  "status": "done",
  "steps": [...],
  "result": { ... }   // SynthesisResult
}
```

Der Client pollt alle ~1,5 Sekunden. Das Frontend zeigt Live-Updates.

---

## Job-Queue

Die Job-Queue ist ein **In-Memory-Dict**:

```python
_jobs: dict[str, Job] = {}
```

**Job-Lebenszyklus:**
```
pending → running → done | error
```

Jobs werden nach **1 Stunde** automatisch gelöscht (Background-Cleanup-Task).

**Achtung:** Kein Persistenz zwischen Server-Neustarts. Laufende Jobs gehen verloren. Für Produktionsumgebungen empfiehlt sich eine echte Queue (Redis, RabbitMQ).

---

## URL-Extraktion

```http
POST /api/extract
{ "url": "https://spiegel.de/article/..." }
```

**Response:**
```json
{
  "platform": "spiegel",
  "title": "Artikelüberschrift",
  "author": "Max Mustermann",
  "images": ["https://..."],
  "text": "Artikel-Volltext..."
}
```

Extrahierter Text wird automatisch an `/analyze` weitergeleitet wenn URL eingegeben wird.

---

## Admin-Endpunkte

### Daten neu laden

```http
POST /api/admin/reload-data
```

Setzt den LRU-Cache aller `tools/data_loader.py`-Funktionen zurück (Domain-Tiers, Scoring-Weights, etc.). Ermöglicht Hot-Reload von Konfigurationen ohne Server-Neustart.

**Response:**
```json
{
  "ok": true,
  "caches_cleared": 19
}
```

---

### Confidence-Kalibrierungsbericht

```http
GET /api/admin/calibration
```

Berechnet Kalibrierungsmetriken basierend auf gespeicherten Vorhersagen mit Ground-Truth-Labels.

**Response:**
```json
{
  "brier_score": 0.1234,
  "total_predictions": 150,
  "correct_predictions": 127,
  "accuracy": 0.847,
  "total": 150,
  "labeled": 100,
  "unlabeled": 50,
  "buckets": [
    {
      "bin_start": 0.0,
      "bin_end": 0.1,
      "predicted_mean": 0.05,
      "observed_rate": 0.2,
      "count": 10
    }
  ]
}
```

**Metriken:**
- **Brier Score:** `mean((confidence - is_correct)^2)` – 0 = perfekt, 1 = schlecht
- **Buckets (Reliability Diagram):** Zeigt ob das Modell zu selbstsicher oder zu vorsichtig ist

---

### Ground-Truth setzen

```http
POST /api/admin/calibration/ground-truth
{
  "claim_id": "claim_abc123",
  "is_correct": true
}
```

Speichert die tatsächliche (durch Menschen überprüfte) Korrektheit einer Vorhersage. Wird für Kalibrierungsberichte genutzt.

---

## Middleware

### CORS
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,  # Standard: ["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
```

### Request-Logging
Jeder Request bekommt eine Correlation-ID. Latenz wird gemessen und in [[Datenbank#Logging und Metriken|Metriken]] gespeichert.

### Rate-Limiting
Token-Bucket per IP-Adresse. Standard: 10 Requests/Minute, Burst 3.
→ [[Tools#Rate Limiter]]

### JWT-Auth
Optionale Authentifizierung. Wenn `AUTH_ENABLED=true`:
- `Authorization: Bearer <token>` Header
- Tier-Informationen aus Token
- Admin-Prüfung für Admin-Endpunkte

---

## Fehler-Format

Alle API-Fehler haben ein einheitliches Format:

```json
{
  "error": "ValidationError",
  "message": "Text überschreitet 10.000 Zeichen",
  "code": 422
}
```

---

## Umgebungsvariablen

| Variable | Standard | Beschreibung |
|---|---|---|
| `CORS_ORIGINS` | `*` | Komma-getrennte Ursprünge |
| `AUTH_ENABLED` | `false` | JWT-Auth aktivieren |
| `RATE_LIMIT_RPM` | `10` | Requests per Minute |
| `RATE_LIMIT_BURST` | `3` | Burst-Kapazität |

---

## Verwandte Dokumente

- [[Frontend]] – Wie das Frontend die API nutzt
- [[Orchestrator]] – Wird von `/analyze` aufgerufen
- [[Datenbank]] – Archive, UserDB werden von API genutzt
- [[Konfiguration]] – Alle API-relevanten Configs
- [[Docker]] – Deployment mit Reverse-Proxy
