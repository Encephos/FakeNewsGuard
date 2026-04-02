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
    max_prompts: int = field(
        default_factory=lambda: int(os.getenv("COMMANDER_MAX_PROMPTS", "4")),
    )
    min_prompts: int = 2  # Immer mind. 1 Review nach Initial
    max_queries_per_claim_per_round: int = 4
    max_total_queries_per_claim: int = 12
