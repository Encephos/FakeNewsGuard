"""Datenmodelle für den CommanderAgent.

Commander ist ein LLM-gesteuerter Orchestrierungslayer, der iterativ
Suchanfragen generiert und die Evidenz-Suffizienz pro Claim evaluiert.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from models.evidence_models import EvidencePack


class CommanderQueryPlan(BaseModel):
    """Prompt-1-Output: Initiale Suchanfragen pro Claim."""

    claim_queries: dict[str, list[str]] = Field(
        description="claim_id → Liste von Suchanfragen",
    )


class CommanderClaimReview(BaseModel):
    """Pro-Claim Sufficiency-Entscheidung aus dem Review-Prompt."""

    sufficient: bool
    reasoning: str = ""
    new_queries: dict[str, list[str]] = Field(
        default_factory=dict,
        description='Engine → Queries, z.B. {"langsearch": [...], "searxng": [...]}',
    )


class CommanderReviewResult(BaseModel):
    """Prompt-2-bis-4-Output: Sufficiency-Review über alle Claims."""

    claim_reviews: dict[str, CommanderClaimReview]


class CommanderRoundLog(BaseModel):
    """Log einer einzelnen Commander-Iteration."""

    round_number: int
    prompt_type: str = Field(description='"initial" | "review"')
    claims_evaluated: int = 0
    claims_sufficient: int = 0
    claims_needing_more: int = 0
    new_queries_generated: int = 0
    claims_budget_exhausted: int = 0


class CommanderResult(BaseModel):
    """Gesamtausgabe des Commanders."""

    evidence_packs: dict[str, EvidencePack] = Field(
        description="claim_id → akkumuliertes EvidencePack",
    )
    rounds_completed: int = 0
    total_prompts_used: int = 0
    round_logs: list[CommanderRoundLog] = Field(default_factory=list)
    claim_difficulties: dict[str, float] = Field(
        default_factory=dict,
        description="claim_id → difficulty score [0.0–1.0]",
    )
    claim_budgets: dict[str, int] = Field(
        default_factory=dict,
        description="claim_id → zugewiesenes Prompt-Budget",
    )
