"""Konfiguration – lädt API Keys aus .env und definiert Modell-Defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv


class ScoutTier(Enum):
    """Scout-Analyse-Stufen – bestimmen Modellauswahl und Qualität."""

    LITE = "lite"   # Tier 1: OpenRouter Free Tier Router (kostenlos)
    PRO = "pro"     # Tier 2: Gemma für alle Agenten
    MAX = "max"     # Tier 3: Gemma (schnell) + Qwen (mächtig)

load_dotenv()


@dataclass
class RetryConfig:
    """Konfiguration für Retry-Logik."""

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    backoff_factor: float = 2.0


@dataclass
class CacheConfig:
    """Konfiguration für den SQLite-Claim-Cache."""

    enabled: bool = True
    db_path: str = ".fakeguard_cache.db"
    ttl_hours: int = 24  # Wie lange gecachte Ergebnisse gültig sind


@dataclass
class LLMConfig:
    """Konfiguration für den LLM-Provider."""

    provider: str = "openrouter"  # "anthropic" | "openai" | "openrouter" | "ollama"
    model: str = "qwen/qwen3-235b-a22b-thinking-2507" # "qwen/qwen3.5-397b-a17b"
    api_key: str = ""
    base_url: str | None = None  # Für Ollama / lokale Modelle
    temperature: float = 0.2  # Niedrig für Faktenprüfung
    max_tokens: int = 16384 #8192

    def __post_init__(self) -> None:
        if not self.api_key:
            if self.provider == "anthropic":
                self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
            elif self.provider == "openai":
                self.api_key = os.getenv("OPENAI_API_KEY", "")
            elif self.provider == "openrouter":
                self.api_key = os.getenv("OPENROUTER_API_KEY", "")


@dataclass
class SearchConfig:
    """Konfiguration für die Web-Suche."""

    provider: str = "searxng"  # "searxng" | "tavily" | "serper" | "brave"
    api_key: str = ""
    base_url: str = ""  # Für SearXNG: URL der Instanz (z.B. http://localhost:8888)
    engines: str = ""   # SearXNG: kommaseparierte Engine-Liste (z.B. "google,duckduckgo,bing")
    max_results: int = 10
    max_concurrent_searches: int = 5  # Für async Parallelisierung
    scrape_top_n: int = 8           # Maximale Anzahl zu scrapender Quellen pro Claim
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
class ClaimProcessingConfig:
    """Konfiguration für die mehrstufige Claim-Processing-Pipeline.

    Env-Vars:
        CLAIM_TOP_N                   – Max. Claims die verarbeitet werden (0 = alle)
        USE_CANONICAL_CACHE           – Cache-Keys auf canonical_text statt Rohtext
    """

    top_n: int = 0  # 0 = alle Claims verarbeiten
    use_canonical_cache: bool = False
    # Minimale Checkworthiness-Score um einen Claim zu verarbeiten (0 = alle)
    min_checkworthiness: float = 0.0

    def __post_init__(self) -> None:
        env_n = os.getenv("CLAIM_TOP_N", "")
        if env_n:
            self.top_n = int(env_n)
        env_cache = os.getenv("USE_CANONICAL_CACHE", "")
        if env_cache:
            self.use_canonical_cache = env_cache.lower() in ("true", "1", "yes")
        env_min = os.getenv("MIN_CHECKWORTHINESS", "")
        if env_min:
            self.min_checkworthiness = float(env_min)


@dataclass
class CoVeConfig:
    """Konfiguration für Chain-of-Verification (CoVe).

    Env-Vars:
        COVE_ENABLED                        – CoVe aktivieren (Default: true)
        MAX_VERIFICATION_QUESTIONS          – Max. Verifikationsfragen pro Claim
        MAX_ADDITIONAL_VERIFICATION_SEARCHES – Max. zusätzliche Suchanfragen in CoVe
    """

    enabled: bool = True
    max_verification_questions: int = 3   # 2–5 Fragen pro Claim
    max_additional_searches: int = 2      # Budget für zusätzliche Retrieval-Runden

    def __post_init__(self) -> None:
        env_enabled = os.getenv("COVE_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        env_q = os.getenv("MAX_VERIFICATION_QUESTIONS", "")
        if env_q:
            self.max_verification_questions = int(env_q)
        env_s = os.getenv("MAX_ADDITIONAL_VERIFICATION_SEARCHES", "")
        if env_s:
            self.max_additional_searches = int(env_s)


@dataclass
class UserDBConfig:
    """Konfiguration für die SQLite-Nutzerdatenbank."""

    db_path: str = ".fakeguard_users.db"

    def __post_init__(self) -> None:
        env_path = os.getenv("USERS_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class ArchiveConfig:
    """Konfiguration für das Analyse-Archiv."""

    enabled: bool = True
    db_path: str = ".fakeguard_archive.db"
    max_entries: int = 1000  # Max. Einträge, älteste werden gelöscht (0 = unbegrenzt)

    def __post_init__(self) -> None:
        # Im Docker nutzen wir /app/data/ für Persistenz
        env_path = os.getenv("ARCHIVE_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class TelegramConfig:
    """Konfiguration für den Telegram Bot."""

    bot_token: str = ""
    backend_url: str = "http://backend:8000"

    def __post_init__(self) -> None:
        if not self.bot_token:
            self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.backend_url or self.backend_url == "http://backend:8000":
            self.backend_url = os.getenv("BACKEND_URL", "http://backend:8000")


@dataclass
class GraphConfig:
    """Konfiguration für den Cross-Reference Graph."""

    enabled: bool = True
    db_path: str = ".fakeguard_graph.db"

    def __post_init__(self) -> None:
        env_path = os.getenv("GRAPH_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class RateLimitConfig:
    """Konfiguration für API Rate-Limiting (Token-Bucket)."""

    enabled: bool = True
    requests_per_minute: int = 10  # Max. Analyse-Anfragen pro IP pro Minute
    burst: int = 3  # Max. gleichzeitige Burst-Anfragen

    def __post_init__(self) -> None:
        env_rpm = os.getenv("RATE_LIMIT_RPM", "")
        if env_rpm:
            self.requests_per_minute = int(env_rpm)
        env_burst = os.getenv("RATE_LIMIT_BURST", "")
        if env_burst:
            self.burst = int(env_burst)


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    langsearch: LangSearchConfig = field(default_factory=LangSearchConfig)
    google_fact_check: GoogleFactCheckConfig = field(default_factory=GoogleFactCheckConfig)
    claim_processing: ClaimProcessingConfig = field(default_factory=ClaimProcessingConfig)
    cove: CoVeConfig = field(default_factory=CoVeConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    user_db: UserDBConfig = field(default_factory=UserDBConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    tier: ScoutTier = ScoutTier.PRO  # Scout-Stufe (lite / pro / max)
    verbose: bool = True  # Zeige Agent-Wechsel und Zwischenergebnisse
    language: str = "de"  # Primärsprache der Analyse
    max_input_chars: int = 10_000  # Schutz vor übermäßig langen Inputs
    # CORS: kommaseparierte Liste erlaubter Origins.
    # Standardwert "*" erlaubt alle – in Produktion mit CORS_ORIGINS setzen.
    # Beispiel: CORS_ORIGINS=https://fakeguard.example.com,https://app.example.com
    cors_origins: list = field(
        default_factory=lambda: (
            [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
            or ["*"]
        )
    )

    def validate(self) -> None:
        """Prüft, ob alle nötigen API Keys vorhanden sind. Beendet mit Fehlermeldung wenn nicht."""
        errors: list[str] = []

        key_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
        if self.llm.provider in key_env and not self.llm.api_key:
            errors.append(f"Fehlender LLM API Key: {key_env[self.llm.provider]} nicht gesetzt")

        if self.search.provider == "searxng":
            if not self.search.base_url:
                errors.append("Fehlende SearXNG URL: SEARXNG_URL nicht gesetzt")
        elif not self.search.api_key:
            key_map = {"tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY"}
            env_var = key_map.get(self.search.provider, f"{self.search.provider.upper()}_API_KEY")
            errors.append(f"Fehlender Search API Key: {env_var} nicht gesetzt")

        # LangSearch und Google Fact Check sind optional – nur warnen, nicht abbrechen
        if self.langsearch.enabled and not self.langsearch.api_key:
            print("  ⚠ LangSearch aktiviert aber kein LANGSEARCH_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.langsearch.enabled = False

        if self.google_fact_check.enabled and not self.google_fact_check.api_key:
            print("  ⚠ Google Fact Check aktiviert aber kein GOOGLE_FACT_CHECK_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.google_fact_check.enabled = False

        if errors:
            print("❌ Konfigurationsfehler:", file=sys.stderr)
            for err in errors:
                print(f"   • {err}", file=sys.stderr)
            print("\n   Tipp: Kopiere .env.example → .env und trage deine API Keys ein.", file=sys.stderr)
            sys.exit(1)
