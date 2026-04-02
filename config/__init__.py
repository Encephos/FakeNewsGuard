"""Konfiguration – lädt API Keys aus .env und definiert Modell-Defaults.

Alle Config-Klassen werden hier re-exportiert für Backward-Kompatibilität:
    from config import AppConfig, LLMConfig, SearchConfig, ...
"""

from config.app import (  # noqa: F401
    AppConfig,
    ScoutTier,
    SEARXNG_NEWS_ENGINES,
    SEARXNG_REFERENCE_ENGINES,
    SEARXNG_WEB_ENGINES,
)
from config.database import CacheConfig, PostgreSQLConfig, ValkeyConfig  # noqa: F401
from config.infrastructure import (  # noqa: F401
    ArchiveConfig,
    GraphConfig,
    RateLimitConfig,
    TelegramConfig,
    UserDBConfig,
)
from config.llm import LLMConfig, ModelPricingConfig, RetryConfig  # noqa: F401
from config.processing import (  # noqa: F401
    ClaimProcessingConfig,
    ClaimQualitySignalConfig,
    CoVeConfig,
    EvidenceRetrievalConfig,
    RetrievalStrategy,
    SourceClientsConfig,
    SourceLayerConfig,
    SynthesizerConfig,
)
from config.search import (  # noqa: F401
    GoogleFactCheckConfig,
    LangSearchConfig,
    SearchCacheConfig,
    SearchConfig,
    SearXNGConfig,
    TavilyConfig,
)

__all__ = [
    "AppConfig",
    "ArchiveConfig",
    "CacheConfig",
    "ClaimProcessingConfig",
    "ClaimQualitySignalConfig",
    "CoVeConfig",
    "EvidenceRetrievalConfig",
    "GoogleFactCheckConfig",
    "GraphConfig",
    "LLMConfig",
    "ModelPricingConfig",
    "LangSearchConfig",
    "PostgreSQLConfig",
    "RateLimitConfig",
    "RetryConfig",
    "ScoutTier",
    "SearchCacheConfig",
    "SearchConfig",
    "SearXNGConfig",
    "SourceClientsConfig",
    "SourceLayerConfig",
    "SynthesizerConfig",
    "TavilyConfig",
    "TelegramConfig",
    "UserDBConfig",
    "ValkeyConfig",
    "SEARXNG_NEWS_ENGINES",
    "SEARXNG_REFERENCE_ENGINES",
    "SEARXNG_WEB_ENGINES",
]
