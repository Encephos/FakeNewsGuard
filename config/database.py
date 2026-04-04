"""Datenbank-Konfigurationen – Valkey/Redis, PostgreSQL und SQLite-Cache."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ValkeyConfig:
    """Konfiguration für Valkey/Redis-Cache (Produktions-Backend für ClaimCache).

    Valkey ist Redis-kompatibel und bereits im Docker-Stack vorhanden.
    Wird genutzt als schneller, TTL-nativer Ersatz für den SQLite-Claim-Cache.

    Env-Vars:
        VALKEY_URL   – Redis-URL (Default: redis://valkey:6379/0)
        VALKEY_DB    – Datenbank-Index 0–15 (Default: 0)
        CACHE_BACKEND – "valkey" | "sqlite" (Default: sqlite)
    """

    url: str = "redis://valkey:6379/0"
    db: int = 0
    enabled: bool = False  # aktiviert über CACHE_BACKEND=valkey

    def __post_init__(self) -> None:
        if env_url := os.getenv("VALKEY_URL", ""):
            self.url = env_url
        if env_db := os.getenv("VALKEY_DB", ""):
            self.db = int(env_db)
        self.enabled = os.getenv("CACHE_BACKEND", "sqlite").lower() == "valkey"


@dataclass
class PostgreSQLConfig:
    """Konfiguration für PostgreSQL (Produktions-Backend für UserDB, Archive, Graph).

    Env-Vars:
        DATABASE_URL – vollständige Postgres-DSN (Vorrang vor Einzelfeldern)
                       Format: postgresql://user:password@host:port/dbname
        POSTGRES_HOST – Hostname (Default: postgres)
        POSTGRES_PORT – Port (Default: 5432)
        POSTGRES_DB   – Datenbankname (Default: fakeguard)
        POSTGRES_USER – Benutzername (Default: fakeguard)
        POSTGRES_PASSWORD – Passwort (Pflicht in Produktion)
        DB_BACKEND    – "postgres" | "sqlite" (Default: sqlite)
    """

    url: str = ""
    host: str = "postgres"
    port: int = 5432
    dbname: str = "fakeguard"
    user: str = "fakeguard"
    password: str = ""
    enabled: bool = False  # aktiviert über DB_BACKEND=postgres

    def __post_init__(self) -> None:
        # DATABASE_URL hat Vorrang
        if env_url := os.getenv("DATABASE_URL", ""):
            self.url = env_url
        else:
            if v := os.getenv("POSTGRES_HOST", ""):
                self.host = v
            if v := os.getenv("POSTGRES_PORT", ""):
                self.port = int(v)
            if v := os.getenv("POSTGRES_DB", ""):
                self.dbname = v
            if v := os.getenv("POSTGRES_USER", ""):
                self.user = v
            if v := os.getenv("POSTGRES_PASSWORD", ""):
                self.password = v
            self.url = (
                f"postgresql://{self.user}:{self.password}"
                f"@{self.host}:{self.port}/{self.dbname}"
            )
        self.enabled = os.getenv("DB_BACKEND", "sqlite").lower() == "postgres"

    @property
    def dsn(self) -> str:
        """Psycopg3-kompatible DSN (postgresql://...)."""
        return self.url


@dataclass
class CacheConfig:
    """Konfiguration für den SQLite-Claim-Cache.

    Env-Vars:
        CACHE_DB_PATH – Pfad zur Cache-Datenbank (Default: .fakeguard_cache.db)
                        In Produktion/Docker auf /app/data/... setzen.
        CACHE_TTL_HOURS – TTL für Cache-Einträge in Stunden (Default: 24)
    """

    enabled: bool = True
    db_path: str = ".fakeguard_cache.db"
    ttl_hours: int = 24  # Wie lange gecachte Ergebnisse gültig sind
    semantic_cache: bool = False  # Embedding-basierte Similarity-Suche als Fallback
    url_cache_ttl: int = 3600  # URL-Content-Cache TTL in Sekunden (Default: 1 Stunde)

    def __post_init__(self) -> None:
        if env_path := os.getenv("CACHE_DB_PATH", ""):
            self.db_path = env_path
        if env_ttl := os.getenv("CACHE_TTL_HOURS", ""):
            self.ttl_hours = int(env_ttl)
        if os.getenv("CACHE_SEMANTIC", "").lower() in ("1", "true", "yes"):
            self.semantic_cache = True
        if env_url_ttl := os.getenv("URL_CACHE_TTL", ""):
            self.url_cache_ttl = int(env_url_ttl)
