"""Konfiguration für den CommanderAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class CommanderConfig:
    """Steuerung des Commander-Orchestrierungslayers.

    Commander generiert iterativ Suchanfragen und evaluiert die
    Evidenz-Suffizienz pro Claim. Nur aktiv für PRO/MAX-Tier.
    """

    enabled: bool = field(
        default_factory=lambda: os.getenv("COMMANDER_ENABLED", "true").lower() == "true",
    )

    # Globale Hard Caps (gelten immer)
    max_prompts_cap: int = field(
        default_factory=lambda: int(os.getenv("COMMANDER_MAX_PROMPTS_CAP", "6")),
    )
    min_prompts: int = 2  # Immer mind. 1 Review nach Initial
    max_queries_per_claim_per_round: int = 4

    # Adaptive Budget Feature-Flag
    adaptive_budget: bool = field(
        default_factory=lambda: os.getenv("COMMANDER_ADAPTIVE", "true").lower() == "true",
    )

    # Per-Tier Prompt-Budgets (von difficulty gesteuert)
    easy_max_prompts: int = 2       # difficulty < 0.25
    moderate_max_prompts: int = 3   # difficulty 0.25–0.50
    hard_max_prompts: int = 4       # difficulty 0.50–0.75
    very_hard_max_prompts: int = 6  # difficulty >= 0.75

    # Per-Tier Query-Budgets
    easy_max_queries: int = 6
    moderate_max_queries: int = 8
    hard_max_queries: int = 12
    very_hard_max_queries: int = 16

    # Fallback wenn adaptive_budget=False (Verhalten wie bisher)
    max_total_queries_per_claim: int = 12
