"""AppConfig – Master-Konfiguration und ScoutTier-Enum."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class ScoutTier(Enum):
    """Scout-Analyse-Stufen – bestimmen Modellauswahl und Qualität."""

    LITE = "lite"   # Tier 1: OpenRouter Free Tier Router (kostenlos)
    PRO = "pro"     # Tier 2: Gemma für alle Agenten
    MAX = "max"     # Tier 3: Gemma (schnell) + Qwen (mächtig)


from config.database import CacheConfig, PostgreSQLConfig, ValkeyConfig  # noqa: E402
from config.infrastructure import (  # noqa: E402
    ArchiveConfig,
    GraphConfig,
    RateLimitConfig,
    TelegramConfig,
    UserDBConfig,
)
from config.llm import LLMConfig, RetryConfig  # noqa: E402
from config.processing import (  # noqa: E402
    ClaimProcessingConfig,
    CoVeConfig,
    EvidenceRetrievalConfig,
    SourceClientsConfig,
    SourceLayerConfig,
    SynthesizerConfig,
)
from config.search import (  # noqa: E402
    GoogleFactCheckConfig,
    LangSearchConfig,
    SearchCacheConfig,
    SearchConfig,
    SearXNGConfig,
    TavilyConfig,
)


@dataclass
class AppConfig:
    # ── Kern (immer erforderlich) ─────────────────────────────────────────────
    llm: LLMConfig = field(default_factory=LLMConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    # ── Suche: Primäres Backend ───────────────────────────────────────────────
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    # ── Suche: Legacy-Routing-Layer (Backward-Compat) ─────────────────────────
    search: SearchConfig = field(default_factory=SearchConfig)
    # ── Suche: Optionale Plugins ──────────────────────────────────────────────
    langsearch: LangSearchConfig = field(default_factory=LangSearchConfig)
    tavily: TavilyConfig = field(default_factory=TavilyConfig)  # disabled by default
    google_fact_check: GoogleFactCheckConfig = field(default_factory=GoogleFactCheckConfig)
    # ── Feature-Configs ───────────────────────────────────────────────────────
    claim_processing: ClaimProcessingConfig = field(default_factory=ClaimProcessingConfig)
    cove: CoVeConfig = field(default_factory=CoVeConfig)
    evidence_retrieval: EvidenceRetrievalConfig = field(default_factory=EvidenceRetrievalConfig)
    synthesizer: SynthesizerConfig = field(default_factory=SynthesizerConfig)
    # ── Source Layer (institutionelle Primärquellen) ──────────────────────────
    source_layer: SourceLayerConfig = field(default_factory=SourceLayerConfig)
    source_clients: SourceClientsConfig = field(default_factory=SourceClientsConfig)
    # ── Infrastruktur ─────────────────────────────────────────────────────────
    cache: CacheConfig = field(default_factory=CacheConfig)
    search_cache: SearchCacheConfig = field(default_factory=SearchCacheConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    user_db: UserDBConfig = field(default_factory=UserDBConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    # ── Produktions-Backends ──────────────────────────────────────────────────
    valkey: ValkeyConfig = field(default_factory=ValkeyConfig)
    postgres: PostgreSQLConfig = field(default_factory=PostgreSQLConfig)
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

        # SearXNG hat immer einen Default (localhost:8888) → kein Hard-Fail, nur Info
        if self.searxng.base_url == "http://localhost:8888":
            print("  ℹ SearXNG nutzt Default-URL (localhost:8888) – via SEARXNG_URL anpassbar.", file=sys.stderr)
        # Legacy-Provider (search.provider): nur prüfen wenn nicht searxng
        if self.search.provider != "searxng" and not self.search.api_key:
            key_map = {"tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY"}
            env_var = key_map.get(self.search.provider, f"{self.search.provider.upper()}_API_KEY")
            errors.append(f"Fehlender Search API Key: {env_var} nicht gesetzt")

        # Tavily, LangSearch und Google Fact Check sind optional – nur warnen, nicht abbrechen
        if self.tavily.enabled and not self.tavily.api_key:
            print("  ⚠ Tavily aktiviert aber kein TAVILY_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.tavily.enabled = False

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


# ── SearXNG Query-Routing-Konstanten (aus data/scoring_weights.yaml) ──────────
# Rückwärtskompatible Aliase – bevorzugt tools.data_loader.searxng_engines() verwenden.
from tools.data_loader import searxng_engines as _load_engines  # noqa: E402

_engines = _load_engines()
SEARXNG_WEB_ENGINES: list[str] = _engines.get("web", ["duckduckgo", "brave", "qwant"])
SEARXNG_NEWS_ENGINES: list[str] = _engines.get("news", ["duckduckgo", "brave", "tagesschau"])
SEARXNG_REFERENCE_ENGINES: list[str] = _engines.get("reference", ["wikipedia", "wikidata"])
