# Konfiguration

> Zurück: [[README]] | Siehe auch: [[Scout-Tiers]], [[LLM-Abstraktion]]

`config.py` enthält alle Konfigurationsklassen als Python-Dataclasses. Alle Felder können über **Umgebungsvariablen** überschrieben werden.

---

## AppConfig – Haupt-Konfiguration

`config/app.py` – 21 Felder, gruppiert nach Zuständigkeit:

```python
@dataclass
class AppConfig:
    # ── Kern (immer erforderlich) ─────────────────────────────────────
    llm: LLMConfig
    retry: RetryConfig

    # ── Primäre Suche ─────────────────────────────────────────────────
    searxng: SearXNGConfig          # Haupt-Backend (Env: SEARXNG_URL)

    # ── Optionale Suchplugins ─────────────────────────────────────────
    search: SearchConfig            # [Legacy – Backward-Compat-Routing-Layer]
    langsearch: LangSearchConfig    # auto-aktiviert wenn LANGSEARCH_API_KEY gesetzt
    tavily: TavilyConfig            # aktiviert via TAVILY_ENABLED=true
    google_fact_check: GoogleFactCheckConfig  # auto-aktiviert wenn Key gesetzt

    # ── Feature-Configs ───────────────────────────────────────────────
    claim_processing: ClaimProcessingConfig
    cove: CoVeConfig                # enabled=True by default
    evidence_retrieval: EvidenceRetrievalConfig
    synthesizer: SynthesizerConfig

    # ── Source Layer ──────────────────────────────────────────────────
    source_layer: SourceLayerConfig     # API-Keys für institutionelle Quellen
    source_clients: SourceClientsConfig # 14 Clients, Konfidenz-Schwelle, Max pro Claim

    # ── Infrastruktur ─────────────────────────────────────────────────
    cache: CacheConfig              # Claim-Cache (SQLite dev default)
    search_cache: SearchCacheConfig
    archive: ArchiveConfig
    user_db: UserDBConfig
    telegram: TelegramConfig
    rate_limit: RateLimitConfig
    graph: GraphConfig

    # ── Produktions-Backends ──────────────────────────────────────────
    valkey: ValkeyConfig            # aktiviert via CACHE_BACKEND=valkey
    postgres: PostgreSQLConfig      # aktiviert via DB_BACKEND=postgres

    # ── Skalare ───────────────────────────────────────────────────────
    tier: ScoutTier                 # lite / pro / max
    language: str = "de"
    max_input_chars: int = 10_000
    cors_origins: list[str] = ["*"]
```

---

## Alle Unter-Configs

### LLMConfig

```python
@dataclass
class LLMConfig:
    provider: str = "openrouter"    # anthropic | openai | openrouter | ollama
    model: str = "auto"
    temperature: float = 0.1
    max_tokens: int = 4096
    api_key: str = ""               # Aus Env-Var
    base_url: str = ""              # Für Ollama / Custom Endpoints
```

**Env-Vars:** `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`

→ [[LLM-Abstraktion]]

---

### SearXNGConfig (primär)

Primäres Suchbackend – self-hosted SearXNG. Wird als `config.searxng` instanziiert.

```python
@dataclass
class SearXNGConfig:
    base_url: str = ""              # Env: SEARXNG_URL (Default: http://localhost:8888)
    engines: list[str] = []         # Env: SEARXNG_ENGINES
    categories: list[str] = ["general", "news"]
    language: str = "de"            # Env: SEARXNG_LANGUAGE
    max_results: int = 15
    scrape_top_n: int = 10
    inter_query_delay: float = 1.5
    engine_rotation_enabled: bool = True
```

**Env-Vars:** `SEARXNG_URL`, `SEARXNG_ENGINES`, `SEARXNG_CATEGORIES`, `SEARXNG_LANGUAGE`

---

### SearchConfig (Legacy)

Backward-Compatibility-Routing-Layer. Primäres Backend ist `SearXNGConfig`.
Wird für optionale Cloud-Provider (Tavily, Serper, Brave) und Legacy-Codepfade verwendet.

```python
@dataclass
class SearchConfig:
    # [Legacy – Backward-Compat-Routing-Layer]
    # Primäres Backend: SearXNGConfig (config.searxng)
    provider: str = "searxng"       # searxng | tavily | serper | brave
    api_key: str = ""
    base_url: str = ""
    max_results: int = 15
    scrape_top_n: int = 10
```

→ [[Websuche]]

---

### EvidenceRetrievalConfig

Steuert die adaptive Retrieval-Strategie im `EvidenceBuilderAgent`.

```python
@dataclass
class EvidenceRetrievalConfig:
    iterative_search_enabled: bool = False  # Env: ITERATIVE_SEARCH_ENABLED
    iterative_min_quality: float = 0.6      # Env: ITERATIVE_MIN_QUALITY
    iterative_max_rounds: int = 2           # Env: ITERATIVE_MAX_ROUNDS
    langsearch_queries_simple: int = 2      # Env: LANGSEARCH_QUERIES_SIMPLE
    langsearch_queries_complex: int = 4     # Env: LANGSEARCH_QUERIES_COMPLEX
    tavily_primary_queries: int = 2         # Env: TAVILY_PRIMARY_QUERIES
    # ... weitere Freshness- und Quality-Schwellen
```

---

### RetryConfig

```python
@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 60.0
```

→ [[Retry]]

---

### CacheConfig

```python
@dataclass
class CacheConfig:
    enabled: bool = True
    db_path: str = ".fakeguard_cache.db"
    ttl_hours: int = 24
```

**Env-Vars:** `CACHE_ENABLED`, `CACHE_DB_PATH`, `CACHE_TTL_HOURS`

→ [[Cache]]

---

### ArchiveConfig

```python
@dataclass
class ArchiveConfig:
    enabled: bool = True
    db_path: str = ".fakeguard_archive.db"
    max_entries: int = 1000         # 0 = unbegrenzt
```

**Env-Vars:** `ARCHIVE_ENABLED`, `ARCHIVE_DB_PATH`, `ARCHIVE_MAX_ENTRIES`

→ [[Datenbank#Analyse-Archiv]]

---

### UserDBConfig

```python
@dataclass
class UserDBConfig:
    db_path: str = ".fakeguard_users.db"
    jwt_secret: str = ""            # Auto-generiert wenn leer (nur Dev!)
    jwt_access_ttl: int = 15        # Minuten
    jwt_refresh_ttl: int = 7        # Tage
    secure_cookies: bool = False    # True hinter HTTPS
```

**Env-Vars:** `USERS_DB_PATH`, `JWT_SECRET`, `SECURE_COOKIES`

→ [[Datenbank#Nutzer-Datenbank]]

---

### RateLimitConfig

```python
@dataclass
class RateLimitConfig:
    requests_per_minute: int = 10
    burst: int = 3
```

**Env-Vars:** `RATE_LIMIT_RPM`, `RATE_LIMIT_BURST`

---

### GraphConfig

```python
@dataclass
class GraphConfig:
    enabled: bool = True
    db_path: str = ".fakeguard_graph.db"
```

**Env-Vars:** `GRAPH_ENABLED`, `GRAPH_DB_PATH`

---

### TelegramConfig

```python
@dataclass
class TelegramConfig:
    bot_token: str = ""
    backend_url: str = "http://localhost:8000"
```

**Env-Vars:** `TELEGRAM_BOT_TOKEN`, `BACKEND_URL`

→ [[Telegram-Bot]]

---

## Vollständige .env-Vorlage

```bash
# LLM
OPENROUTER_API_KEY=sk-or-...
LLM_PROVIDER=openrouter
LLM_MODEL=auto
SCOUT_TIER=pro

# Primäre Suche (SearXNGConfig)
SEARXNG_URL=http://searxng:8888

# Optionale Suchplugins
# LANGSEARCH_API_KEY=ls-...        # aktiviert automatisch wenn gesetzt
# TAVILY_API_KEY=tvly-...
# TAVILY_ENABLED=true              # muss explizit aktiviert werden
# GOOGLE_FACT_CHECK_API_KEY=...    # aktiviert automatisch wenn gesetzt

# Sprache
LANGUAGE=de

# Auth
JWT_SECRET=dein-sicherer-schluessel
SECURE_COOKIES=false

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
BACKEND_URL=http://backend:8000

# Datenbank-Pfade (optional, Standard: /app/data/ in Docker)
USERS_DB_PATH=/app/data/users.db
ARCHIVE_DB_PATH=/app/data/archive.db
GRAPH_DB_PATH=/app/data/graph.db

# Rate-Limiting
RATE_LIMIT_RPM=10
RATE_LIMIT_BURST=3

# Produktions-Backends (optional, Default: SQLite/SQLite)
# DB_BACKEND=postgres
# POSTGRES_HOST=localhost
# POSTGRES_DB=fakeguard
# CACHE_BACKEND=valkey
# VALKEY_HOST=localhost
```

---

## Verwandte Dokumente

- [[Scout-Tiers]] – Tier-Auswahl
- [[LLM-Abstraktion]] – LLMConfig im Einsatz
- [[Websuche]] – SearchConfig im Einsatz
- [[Docker]] – Env-Vars in docker-compose
