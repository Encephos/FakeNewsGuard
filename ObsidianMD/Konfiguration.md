# Konfiguration

> Zurück: [[README]] | Siehe auch: [[Scout-Tiers]], [[LLM-Abstraktion]]

`config.py` enthält alle Konfigurationsklassen als Python-Dataclasses. Alle Felder können über **Umgebungsvariablen** überschrieben werden.

---

## AppConfig – Haupt-Konfiguration

```python
@dataclass
class AppConfig:
    tier: ScoutTier = ScoutTier.PRO
    language: str = "de"              # "de" | "en"
    max_input_chars: int = 10_000
    cors_origins: list[str] = ("*",)
    auth_enabled: bool = False

    # Unter-Configs
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    user_db: UserDBConfig = field(default_factory=UserDBConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
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

### SearchConfig

```python
@dataclass
class SearchConfig:
    provider: str = "searxng"       # searxng | tavily | serper | brave
    base_url: str = "http://localhost:8888"
    api_key: str = ""
    max_results: int = 10
    scrape_top_n: int = 8
    scrape_timeout: int = 30
```

**Env-Vars:** `SEARCH_PROVIDER`, `SEARXNG_URL`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`

→ [[Websuche]]

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

# Suche
SEARCH_PROVIDER=searxng
SEARXNG_URL=http://searxng:8888
# TAVILY_API_KEY=tvly-...

# Sprache
LANGUAGE=de

# Auth
AUTH_ENABLED=false
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
```

---

## Verwandte Dokumente

- [[Scout-Tiers]] – Tier-Auswahl
- [[LLM-Abstraktion]] – LLMConfig im Einsatz
- [[Websuche]] – SearchConfig im Einsatz
- [[Docker]] – Env-Vars in docker-compose
