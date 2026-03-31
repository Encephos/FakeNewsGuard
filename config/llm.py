"""LLM- und Retry-Konfigurationen."""

from __future__ import annotations

import os
from dataclasses import dataclass


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
