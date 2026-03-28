"""Such-Konfigurationen – SearXNG, LangSearch, Tavily, Google Fact Check."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class SearchConfig:
    """Konfiguration für die Web-Suche."""

    provider: str = "searxng"  # "searxng" | "tavily" | "serper" | "brave"
    api_key: str = ""
    base_url: str = ""  # Für SearXNG: URL der Instanz (z.B. http://localhost:8888)
    engines: str = ""   # SearXNG: kommaseparierte Engine-Liste (z.B. "google,duckduckgo,bing")
    max_results: int = 15              # SearXNG ist self-hosted → großzügig
    max_concurrent_searches: int = 3  # Gleichzeitige Anfragen – zu hoch → Engine-Suspendierung
    scrape_top_n: int = 10          # Maximale Anzahl zu scrapender Quellen pro Claim
    scrape_timeout: float = 10.0    # HTTP-Timeout pro Scrape-Request in Sekunden

    def __post_init__(self) -> None:
        if self.provider == "searxng":
            if not self.base_url:
                self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
            if not self.engines:
                self.engines = os.getenv("SEARXNG_ENGINES", "")
        elif not self.api_key:
            key_map = {
                "tavily": "TAVILY_API_KEY",
                "serper": "SERPER_API_KEY",
                "brave": "BRAVE_API_KEY",
            }
            env_var = key_map.get(self.provider, "")
            self.api_key = os.getenv(env_var, "")

        env_scrape_n = os.getenv("SCRAPE_TOP_N", "")
        if env_scrape_n:
            self.scrape_top_n = int(env_scrape_n)
        env_scrape_timeout = os.getenv("SCRAPE_TIMEOUT", "")
        if env_scrape_timeout:
            self.scrape_timeout = float(env_scrape_timeout)


@dataclass
class SearXNGConfig:
    """Konfiguration für den dedizierten SearXNG-Client.

    SearXNG dient als unterstützende Breitensuche (self-hosted, kostenlos).
    Kein Provider-Routing – explizit SearXNG-only.

    Env-Vars:
        SEARXNG_URL        – Basis-URL (Default: http://localhost:8888)
        SEARXNG_ENGINES    – kommaseparierte Engine-Liste (Default: leer = SearXNG-Default)
        SEARXNG_CATEGORIES – kommaseparierte Kategorien (Default: general,news)
        SEARXNG_LANGUAGE   – Suchsprache (Default: de)
        SEARXNG_TIME_RANGE – Zeitbereich: day/week/month/year/None (Default: leer)
    """

    base_url: str = ""
    engines: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=lambda: ["general", "news"])
    language: str = "de"
    time_range: str | None = None
    max_results: int = 15
    max_concurrent_searches: int = 3
    scrape_top_n: int = 10
    scrape_timeout: float = 10.0
    inter_query_delay: float = 1.5
    engine_rotation_enabled: bool = True
    engines_per_query: int = 3

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
        env_engines = os.getenv("SEARXNG_ENGINES", "")
        if env_engines and not self.engines:
            self.engines = [e.strip() for e in env_engines.split(",") if e.strip()]
        env_cats = os.getenv("SEARXNG_CATEGORIES", "")
        if env_cats:
            self.categories = [c.strip() for c in env_cats.split(",") if c.strip()]
        env_lang = os.getenv("SEARXNG_LANGUAGE", "")
        if env_lang:
            self.language = env_lang
        env_tr = os.getenv("SEARXNG_TIME_RANGE", "")
        if env_tr:
            self.time_range = env_tr
        env_scrape_n = os.getenv("SCRAPE_TOP_N", "")
        if env_scrape_n:
            self.scrape_top_n = int(env_scrape_n)
        env_scrape_timeout = os.getenv("SCRAPE_TIMEOUT", "")
        if env_scrape_timeout:
            self.scrape_timeout = float(env_scrape_timeout)
        env_delay = os.getenv("SEARXNG_INTER_QUERY_DELAY", "")
        if env_delay:
            self.inter_query_delay = float(env_delay)
        env_rotation = os.getenv("SEARXNG_ENGINE_ROTATION", "")
        if env_rotation:
            self.engine_rotation_enabled = env_rotation.lower() in ("true", "1", "yes")


@dataclass
class LangSearchConfig:
    """Konfiguration für LangSearch – semantische Websuche.

    LangSearch wird im EvidenceBuilderAgent parallel zu SearXNG genutzt
    und liefert semantisch gerankete Ergebnisse.
    Optionales Reranking über die LangSearch-API wenn verfügbar.

    Env-Vars:
        LANGSEARCH_API_KEY     – API-Key (Pflicht wenn enabled)
        LANGSEARCH_BASE_URL    – API-Basis-URL (Default: offizieller Endpunkt)
        LANGSEARCH_ENABLED     – "true"/"false" (Default: true wenn Key vorhanden)
    """

    api_key: str = ""
    base_url: str = "https://api.langsearch.com/v1"
    enabled: bool = True
    max_results: int = 10

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("LANGSEARCH_API_KEY", "")
        env_url = os.getenv("LANGSEARCH_BASE_URL", "")
        if env_url:
            self.base_url = env_url
        env_enabled = os.getenv("LANGSEARCH_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        elif not self.api_key:
            # Automatisch deaktivieren wenn kein Key
            self.enabled = False


@dataclass
class TavilyConfig:
    """Konfiguration für Tavily – KI-optimierte Websuche.

    Standardmäßig deaktiviert. SearXNG ist die primäre Suchquelle.
    Tavily kann als optionaler Content-Layer explizit zugeschaltet werden.

    Env-Vars:
        TAVILY_API_KEY      – API-Key (erforderlich wenn enabled)
        TAVILY_ENABLED      – "true" um Tavily zu aktivieren (Default: false)
        TAVILY_MAX_RESULTS  – Max. Ergebnisse pro Query (Default: 5)
        TAVILY_SEARCH_DEPTH – "basic" oder "advanced" (Default: advanced)
    """

    api_key: str = ""
    enabled: bool = False  # Standardmäßig deaktiviert – explizit via TAVILY_ENABLED=true aktivieren
    max_results: int = 5
    search_depth: str = "advanced"

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("TAVILY_API_KEY", "")
        env_enabled = os.getenv("TAVILY_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        env_max = os.getenv("TAVILY_MAX_RESULTS", "")
        if env_max:
            self.max_results = int(env_max)
        env_depth = os.getenv("TAVILY_SEARCH_DEPTH", "")
        if env_depth:
            self.search_depth = env_depth


@dataclass
class GoogleFactCheckConfig:
    """Konfiguration für die Google Fact Check Tools API.

    Env-Vars:
        GOOGLE_FACT_CHECK_API_KEY  – API-Key (kostenlos über Google Cloud Console)
        GOOGLE_FACT_CHECK_ENABLED  – "true"/"false" (Default: true wenn Key vorhanden)

    Hinweis: Der API-Key hieß früher GOOGLE_FACTCHECK_API_KEY (ohne zweites F).
    Beide Varianten werden akzeptiert für Abwärtskompatibilität.
    """

    api_key: str = ""
    enabled: bool = True
    max_results: int = 5

    def __post_init__(self) -> None:
        if not self.api_key:
            # Beide Schreibweisen akzeptieren
            self.api_key = os.getenv(
                "GOOGLE_FACT_CHECK_API_KEY",
                os.getenv("GOOGLE_FACTCHECK_API_KEY", ""),
            )
        env_enabled = os.getenv("GOOGLE_FACT_CHECK_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        elif not self.api_key:
            self.enabled = False


@dataclass
class SearchCacheConfig:
    """Konfiguration für den Search-Result-Cache (Valkey oder SQLite-Fallback).

    Env-Vars:
        SEARCH_CACHE_ENABLED   – "true"/"false" (Default: true)
        SEARCH_CACHE_TTL_HOURS – TTL in Stunden (Default: 6)
    """

    enabled: bool = True
    ttl_hours: int = 6

    def __post_init__(self) -> None:
        env_enabled = os.getenv("SEARCH_CACHE_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        if env_ttl := os.getenv("SEARCH_CACHE_TTL_HOURS", ""):
            self.ttl_hours = int(env_ttl)
