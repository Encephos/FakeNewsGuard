# Datenbanken

> Zurück: [[Tools]] | Siehe auch: [[Cache]], [[API]]

FakeNewsGuard verwendet **vier separate SQLite-Datenbanken** für unterschiedliche Zwecke. Alle laufen im WAL-Modus für Thread-Sicherheit.

---

## Übersicht

| Datenbank | Datei | Klasse | Zweck |
|---|---|---|---|
| Claim-Cache | `.fakeguard_cache.db` | `ClaimCache` | Agenten-Ergebnisse + optionale Embeddings |
| Kalibrierung | `data/calibration.db` | `CalibrationTracker` | Confidence-Kalibrierung (Brier Scores) |
| Analyse-Archiv | `.fakeguard_archive.db` | `AnalysisArchive` | Alle abgeschlossenen Analysen |
| Nutzer-DB | `.fakeguard_users.db` | `UserDB` | Accounts, JWT, Usage-Log |
| Cross-Reference | `.fakeguard_graph.db` | `CrossReferenceGraph` | Claim-Wissens-Graph |

→ Claim-Cache: [[Cache]]
→ Kalibrierung: [[Tools#Calibration Tracker]]

---

## Analyse-Archiv (`tools/archive.py`)

Persistente Speicherung aller abgeschlossenen Analysen.

### Schema
```sql
CREATE TABLE analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT UNIQUE,        -- SHA256(text oder url)
    input_text  TEXT,
    source_url  TEXT,
    platform    TEXT,
    overall_rating TEXT,
    confidence  REAL,
    summary     TEXT,
    result_json TEXT,               -- Vollständiges SynthesisResult als JSON
    created_at  INTEGER
);

-- Volltextsuche über summary + input_text
CREATE VIRTUAL TABLE analyses_fts USING fts5(summary, input_text);
```

### Features
- **Deduplication**: Gleicher Text/URL → gleiches Archiv-Objekt (nur aktualisiert)
- **FTS5 Volltextsuche**: `SELECT * FROM analyses_fts WHERE analyses_fts MATCH 'Rente Inflation'`
- **Pagination**: `GET /api/archive?page=2&per_page=20`
- Exportierbar als JSON oder PDF

### ArchiveConfig
```python
@dataclass
class ArchiveConfig:
    enabled: bool = True
    db_path: str = ".fakeguard_archive.db"
    max_entries: int = 1000   # 0 = unbegrenzt
```

---

## Nutzer-Datenbank (`tools/user_db.py`)

Vollständige Nutzer-Verwaltung mit JWT-Authentifizierung.

### Schema
```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,     -- bcrypt (Fallback: SHA256)
    tier        TEXT DEFAULT 'lite', -- lite | pro | max
    is_admin    INTEGER DEFAULT 0,
    telegram_id TEXT,
    created_at  INTEGER,
    last_login  INTEGER,
    consent_logging INTEGER DEFAULT 0
);

CREATE TABLE usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT,                -- 'analyze', 'archive_search', ...
    tier        TEXT,
    tokens_used INTEGER,
    created_at  INTEGER
);

CREATE TABLE telegram_link_codes (
    code        TEXT PRIMARY KEY,    -- 6-stelliger alphanumerischer Code
    user_id     INTEGER REFERENCES users(id),
    expires_at  INTEGER              -- 10-Minuten-TTL
);
```

### JWT-Auth

**Access Token:** 15-Minuten-TTL, HS256-Signatur
**Refresh Token:** 7-Tage-TTL, in HttpOnly-Cookie

```python
# Token-Payload:
{ "sub": user_id, "email": email, "tier": tier, "is_admin": is_admin, "exp": ... }
```

### Passwort-Hashing
- Standard: **bcrypt** (bcrypt-Bibliothek)
- Fallback (wenn bcrypt nicht installiert): SHA256

### Telegram-Verlinkung
1. Nutzer klickt „Telegram verbinden" im Profil
2. API erstellt 6-stelligen Code mit 10-Min-TTL
3. Nutzer schickt Code an Telegram-Bot
4. Bot verknüpft Telegram-ID mit User-Account

---

## Cross-Reference Graph (`tools/cross_reference.py`)

Persistenter Wissens-Graph für Claim-Relationen.

### Schema
```sql
CREATE TABLE nodes (
    id      INTEGER PRIMARY KEY,
    type    TEXT,    -- CLAIM | SOURCE | ACTOR
    label   TEXT UNIQUE,
    data    TEXT     -- JSON
);

CREATE TABLE edges (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER REFERENCES nodes(id),
    target_id   INTEGER REFERENCES nodes(id),
    relation    TEXT,  -- supported_by | contradicted_by | mentions | related_to | cites
    weight      REAL DEFAULT 1.0,
    created_at  INTEGER
);
```

### Nutzung
Nach jeder Analyse werden Nodes und Edges hinzugefügt:
- Claim-Nodes für jede geprüfte Behauptung
- Source-Nodes für verwendete Quellen
- Edges: `supported_by` oder `contradicted_by`

Zukünftig nutzbar für: „Diese Behauptung wurde 2 Wochen ago schon widerlegt."

---

## Logging und Metriken (`tools/logger.py`)

### Strukturiertes Logging

```python
logger.info("analysis_started", {
    "job_id": job_id,
    "tier": tier,
    "text_length": len(text)
})
```

Output-Formate: **JSON** (für log aggregation) oder **Text** (für Entwicklung).

### Ring-Buffer

Letzte 500 Log-Einträge im RAM – abrufbar via Admin-API:
```
GET /api/admin/logs
```

### Metriken

Pro API-Endpunkt werden gesammelt:
- Aufruf-Anzahl
- Fehler-Anzahl
- Durchschnittliche Latenz

Abrufbar via:
```
GET /api/admin/metrics
```

### Datenschutz

**Sensitive Daten werden automatisch redaktiert:**
- API-Keys (Muster: `sk-...`, `Bearer ...`)
- Tokens
- Passwörter

---

## UserDBConfig

```python
@dataclass
class UserDBConfig:
    db_path: str = ".fakeguard_users.db"
    jwt_secret: str = ""           # Auto-generiert wenn leer (nur Dev!)
    jwt_access_ttl: int = 15       # Minuten
    jwt_refresh_ttl: int = 7       # Tage
    secure_cookies: bool = False   # True hinter HTTPS Reverse Proxy
```

→ [[Konfiguration]]

---

## Verwandte Dokumente

- [[Cache]] – Claim-Cache im Detail
- [[API]] – Auth-Endpunkte, Admin-Endpunkte
- [[Konfiguration]] – Alle DB-Configs
- [[Telegram-Bot]] – Nutzt UserDB für Telegram-Verlinkung
