"""LLM- und Retry-Konfigurationen."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class RetryConfig:
    """Konfiguration für Retry-Logik."""

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    backoff_factor: float = 2.0


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
class TierModelConfig:
    """Modellnamen pro Scout-Tier – vermeidet Hartkodierung im Orchestrator.

    Env-Vars:
        TIER_MODEL_SMALL   – Kleines Modell für schnelle Aufgaben (Default: google/gemma-3-4b-it)
        TIER_MODEL_MEDIUM  – Mittleres Modell für Pro/Max (Default: google/gemma-3-27b-it)
        TIER_MODEL_FREE    – Kostenloses Router-Modell für Lite (Default: openrouter/free)
    """

    model_small: str = "google/gemma-3-4b-it"
    model_medium: str = "google/gemma-3-27b-it"
    model_free: str = "openrouter/free"

    def __post_init__(self) -> None:
        if v := os.getenv("TIER_MODEL_SMALL", ""):
            self.model_small = v
        if v := os.getenv("TIER_MODEL_MEDIUM", ""):
            self.model_medium = v
        if v := os.getenv("TIER_MODEL_FREE", ""):
            self.model_free = v


@dataclass
class ModelPricingConfig:
    """Preise in USD pro 1 Million Tokens.

    Env-Override moeglich: PRICING_INPUT_<MODEL_SLUG> / PRICING_OUTPUT_<MODEL_SLUG>
    (MODEL_SLUG = Modell-ID mit / und - durch _ ersetzt, uppercase)
    """

    prices: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "google/gemma-3-4b-it":                {"input": 0.03, "output": 0.03},
        "google/gemma-3-27b-it":               {"input": 0.10, "output": 0.10},
        "qwen/qwen3-235b-a22b-thinking-2507":  {"input": 0.14, "output": 0.60},
        "openrouter/free":                     {"input": 0.0,  "output": 0.0},
    })

    # CO2-Emissionen in Gramm pro 1000 Tokens (operationelle Inferenz).
    # Basiert auf: TokenPowerBench-Skalierung, globaler Durchschnitts-Strommix
    # ~475 gCO2/kWh, PUE ~1.2, moderne Datacenter-GPUs (H100/A100).
    # Quellen: arxiv.org/html/2512.03024v1, arxiv.org/html/2511.05597
    co2_per_1k_tokens: dict[str, float] = field(default_factory=lambda: {
        "google/gemma-3-4b-it":                0.04,   # ~4B dense, sehr effizient
        "google/gemma-3-27b-it":               0.30,   # ~27B dense
        "qwen/qwen3-235b-a22b-thinking-2507":  0.40,   # ~235B MoE, 22B aktiv
        "openrouter/free":                     0.05,   # konservativer Schaetzwert
    })

    # CO2 pro Websuche in Gramm (konservativer Schaetzwert basierend auf
    # Google-Daten ~0.2g/Query, SearXNG-Overhead vernachlaessigbar).
    co2_per_search_query: float = 0.20
