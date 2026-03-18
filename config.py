"""Konfiguration – lädt API Keys aus .env und definiert Modell-Defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

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
    max_tokens: int = 8192

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
    max_results: int = 5
    max_concurrent_searches: int = 3  # Für async Parallelisierung

    def __post_init__(self) -> None:
        if self.provider == "searxng":
            if not self.base_url:
                self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
        elif not self.api_key:
            key_map = {
                "tavily": "TAVILY_API_KEY",
                "serper": "SERPER_API_KEY",
                "brave": "BRAVE_API_KEY",
            }
            env_var = key_map.get(self.provider, "")
            self.api_key = os.getenv(env_var, "")


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
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    verbose: bool = True  # Zeige Agent-Wechsel und Zwischenergebnisse
    language: str = "de"  # Primärsprache der Analyse
    max_input_chars: int = 10_000  # Schutz vor übermäßig langen Inputs

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

        if errors:
            print("❌ Konfigurationsfehler:", file=sys.stderr)
            for err in errors:
                print(f"   • {err}", file=sys.stderr)
            print("\n   Tipp: Kopiere .env.example → .env und trage deine API Keys ein.", file=sys.stderr)
            sys.exit(1)
