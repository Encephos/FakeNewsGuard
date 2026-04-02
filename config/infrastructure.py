"""Infrastruktur-Konfigurationen – Telegram, Graph, Rate-Limit, UserDB, Archive."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TelemetryConfig:
    """Konfiguration fuer Observability (OpenTelemetry + Prometheus).

    Env-Vars:
        OTEL_ENABLED        – OpenTelemetry Tracing aktivieren (Default: false)
        OTLP_ENDPOINT       – OTLP gRPC Collector Endpoint (Default: http://localhost:4317)
        OTEL_SERVICE_NAME   – Service-Name in Traces (Default: fakeguard-api)
        PROMETHEUS_ENABLED  – /metrics Endpoint aktivieren (Default: true)
    """

    otel_enabled: bool = False
    otlp_endpoint: str = "http://localhost:4317"
    prometheus_enabled: bool = True
    service_name: str = "fakeguard-api"

    def __post_init__(self) -> None:
        if os.getenv("OTEL_ENABLED", "").lower() in ("1", "true"):
            self.otel_enabled = True
        if v := os.getenv("OTLP_ENDPOINT", ""):
            self.otlp_endpoint = v
        if os.getenv("PROMETHEUS_ENABLED", "").lower() in ("0", "false"):
            self.prometheus_enabled = False
        if v := os.getenv("OTEL_SERVICE_NAME", ""):
            self.service_name = v


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
    poll_interval: float = 2.0
    max_poll_attempts: int = 960
    message_chunk_size: int = 4000
    http_timeout: float = 30.0
    poll_timeout: float = 15.0

    def __post_init__(self) -> None:
        if not self.bot_token:
            self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.backend_url or self.backend_url == "http://backend:8000":
            self.backend_url = os.getenv("BACKEND_URL", "http://backend:8000")
        if v := os.getenv("TELEGRAM_POLL_INTERVAL", ""):
            self.poll_interval = float(v)
        if v := os.getenv("TELEGRAM_MAX_POLL_ATTEMPTS", ""):
            self.max_poll_attempts = int(v)
        if v := os.getenv("TELEGRAM_MSG_CHUNK_SIZE", ""):
            self.message_chunk_size = int(v)
        if v := os.getenv("TELEGRAM_HTTP_TIMEOUT", ""):
            self.http_timeout = float(v)
        if v := os.getenv("TELEGRAM_POLL_TIMEOUT", ""):
            self.poll_timeout = float(v)


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
    """Konfiguration für API Rate-Limiting (Token-Bucket).

    Env-Vars:
        RATE_LIMIT_RPM              – Max Anfragen pro IP pro Minute (Default: 10)
        RATE_LIMIT_BURST            – Max Burst-Anfragen (Default: 3)
        RATE_LIMIT_AUTH_RPM         – Auth-Endpunkt RPM (Default: 5)
        RATE_LIMIT_AUTH_BURST       – Auth-Endpunkt Burst (Default: 2)
        RATE_LIMIT_CLEANUP_INTERVAL – Bucket-Cleanup-Intervall in Sekunden (Default: 300)
        RATE_LIMIT_INACTIVITY_CUTOFF – Bucket-Inaktivitäts-Cutoff in Sekunden (Default: 600)
    """

    enabled: bool = True
    requests_per_minute: int = 10
    burst: int = 3
    auth_requests_per_minute: int = 5
    auth_burst: int = 2
    cleanup_interval_s: float = 300.0
    inactivity_cutoff_s: float = 600.0

    def __post_init__(self) -> None:
        if v := os.getenv("RATE_LIMIT_RPM", ""):
            self.requests_per_minute = int(v)
        if v := os.getenv("RATE_LIMIT_BURST", ""):
            self.burst = int(v)
        if v := os.getenv("RATE_LIMIT_AUTH_RPM", ""):
            self.auth_requests_per_minute = int(v)
        if v := os.getenv("RATE_LIMIT_AUTH_BURST", ""):
            self.auth_burst = int(v)
        if v := os.getenv("RATE_LIMIT_CLEANUP_INTERVAL", ""):
            self.cleanup_interval_s = float(v)
        if v := os.getenv("RATE_LIMIT_INACTIVITY_CUTOFF", ""):
            self.inactivity_cutoff_s = float(v)


@dataclass
class HTTPTimeoutsConfig:
    """Zentrale HTTP-Timeout-Konfiguration für alle Module.

    Env-Vars:
        TIMEOUT_LLM            – LLM-API Timeout in Sekunden (Default: 120.0)
        TIMEOUT_LLM_CONNECT    – LLM-API Connect-Timeout (Default: 10.0)
        TIMEOUT_SCRAPE         – Scraping-Timeout (Default: 20.0)
        TIMEOUT_SEARCH         – Such-API Timeout (Default: 30.0)
        TIMEOUT_SOURCE_CLIENT  – Institutionelle Quellen Timeout (Default: 15.0)
        TIMEOUT_AGENT          – Agent-Gesamt-Timeout (Default: 180.0)
        TIMEOUT_DOMAIN_TRUST   – Domain-Trust-API Timeout (Default: 10.0)
    """

    llm: float = 120.0
    llm_connect: float = 10.0
    scrape: float = 20.0
    search: float = 30.0
    source_client: float = 15.0
    agent: float = 180.0
    domain_trust: float = 10.0

    def __post_init__(self) -> None:
        if v := os.getenv("TIMEOUT_LLM", ""):
            self.llm = float(v)
        if v := os.getenv("TIMEOUT_LLM_CONNECT", ""):
            self.llm_connect = float(v)
        if v := os.getenv("TIMEOUT_SCRAPE", ""):
            self.scrape = float(v)
        if v := os.getenv("TIMEOUT_SEARCH", ""):
            self.search = float(v)
        if v := os.getenv("TIMEOUT_SOURCE_CLIENT", ""):
            self.source_client = float(v)
        if v := os.getenv("TIMEOUT_AGENT", ""):
            self.agent = float(v)
        if v := os.getenv("TIMEOUT_DOMAIN_TRUST", ""):
            self.domain_trust = float(v)


@dataclass
class JobConfig:
    """Konfiguration für Job-Management (API-Background-Tasks).

    Env-Vars:
        JOB_TTL_SECONDS           – Job-Cleanup nach Abschluss (Default: 3600)
        JOB_TIMEOUT_SECONDS       – Max. Job-Laufzeit (Default: 1800)
        JOB_INACTIVITY_TIMEOUT    – Inaktivitäts-Timeout (Default: 300)
        JOB_CLAIM_BATCH_SIZE      – Claims pro Batch (Default: 4)
        JOB_INACTIVITY_SCALING    – Sekunden Inaktivitäts-Skalierung pro Claim (Default: 30)
    """

    ttl_seconds: int = 3600
    timeout_seconds: int = 1800
    inactivity_timeout: int = 300
    claim_batch_size: int = 4
    inactivity_scaling_per_claim: int = 30

    def __post_init__(self) -> None:
        if v := os.getenv("JOB_TTL_SECONDS", ""):
            self.ttl_seconds = int(v)
        if v := os.getenv("JOB_TIMEOUT_SECONDS", ""):
            self.timeout_seconds = int(v)
        if v := os.getenv("JOB_INACTIVITY_TIMEOUT", ""):
            self.inactivity_timeout = int(v)
        if v := os.getenv("JOB_CLAIM_BATCH_SIZE", ""):
            self.claim_batch_size = int(v)
        if v := os.getenv("JOB_INACTIVITY_SCALING", ""):
            self.inactivity_scaling_per_claim = int(v)


@dataclass
class CeleryConfig:
    """Konfiguration fuer Celery Task-Queue (Broker + Backend via Valkey/Redis).

    Env-Vars:
        CELERY_BROKER_URL         – Broker-URL (Default: redis://valkey:6379/1)
        CELERY_RESULT_BACKEND     – Result-Backend-URL (Default: redis://valkey:6379/1)
        CELERY_TASK_TIME_LIMIT    – Hard-Kill nach N Sekunden (Default: 1800)
        CELERY_TASK_SOFT_TIME_LIMIT – SoftTimeLimitExceeded nach N Sekunden (Default: 1740)
        CELERY_WORKER_CONCURRENCY – Worker-Parallelitaet (Default: 4)
    """

    broker_url: str = "redis://valkey:6379/1"
    result_backend: str = "redis://valkey:6379/1"
    task_time_limit: int = 1800
    task_soft_time_limit: int = 1740
    worker_concurrency: int = 4

    def __post_init__(self) -> None:
        if v := os.getenv("CELERY_BROKER_URL", ""):
            self.broker_url = v
        if v := os.getenv("CELERY_RESULT_BACKEND", ""):
            self.result_backend = v
        if v := os.getenv("CELERY_TASK_TIME_LIMIT", ""):
            self.task_time_limit = int(v)
        if v := os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", ""):
            self.task_soft_time_limit = int(v)
        if v := os.getenv("CELERY_WORKER_CONCURRENCY", ""):
            self.worker_concurrency = int(v)


@dataclass
class AuthConfig:
    """Konfiguration für Authentifizierung.

    Env-Vars:
        AUTH_REFRESH_TOKEN_MAX_AGE  – Refresh-Token Gültigkeit in Sekunden (Default: 604800 = 7 Tage)
        AUTH_REMEMBER_ME_MAX_AGE    – Remember-Me Gültigkeit in Sekunden (Default: 2592000 = 30 Tage)
        AUTH_MIN_PASSWORD_LENGTH    – Minimale Passwort-Länge (Default: 8)
        AUTH_LINK_CODE_EXPIRATION   – Link-Code Ablauf in Sekunden (Default: 600)
        AUTH_MAX_DISPLAY_NAME       – Max. Display-Name-Länge (Default: 50)
    """

    refresh_token_max_age: int = 7 * 86400
    remember_me_max_age: int = 30 * 86400
    min_password_length: int = 8
    link_code_expiration: int = 600
    max_display_name_length: int = 50

    def __post_init__(self) -> None:
        if v := os.getenv("AUTH_REFRESH_TOKEN_MAX_AGE", ""):
            self.refresh_token_max_age = int(v)
        if v := os.getenv("AUTH_REMEMBER_ME_MAX_AGE", ""):
            self.remember_me_max_age = int(v)
        if v := os.getenv("AUTH_MIN_PASSWORD_LENGTH", ""):
            self.min_password_length = int(v)
        if v := os.getenv("AUTH_LINK_CODE_EXPIRATION", ""):
            self.link_code_expiration = int(v)
        if v := os.getenv("AUTH_MAX_DISPLAY_NAME", ""):
            self.max_display_name_length = int(v)
