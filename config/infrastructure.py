"""Infrastruktur-Konfigurationen – Telegram, Graph, Rate-Limit, UserDB, Archive."""

from __future__ import annotations

import os
from dataclasses import dataclass


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
