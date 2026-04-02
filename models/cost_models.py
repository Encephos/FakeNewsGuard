"""Kosten- und Token-Tracking-Modelle fuer LLM-Analysen."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CostSummary(BaseModel):
    """Aggregierter Token-Verbrauch und geschaetzte Kosten einer Analyse."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_per_model: dict[str, float] = Field(default_factory=dict)
    tokens_per_agent: dict[str, int] = Field(default_factory=dict)
    call_count: int = 0
