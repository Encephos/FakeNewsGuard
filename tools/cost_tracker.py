"""ContextVar-basierter LLM-Kosten-Akkumulator.

Einmal pro Analyse wird ein neuer Akkumulator erstellt (reset_accumulator).
Jeder LLM-Call in tools/llm.py appended automatisch einen LLMUsage-Eintrag
(record_usage). Am Ende liest der Orchestrator die Liste und berechnet
die Kosten (collect_summary).

Thread-Safety: list.append ist unter CPython GIL atomar.
ContextVar propagiert korrekt durch asyncio.gather() und run_in_executor().
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class LLMUsage:
    """Einzelner LLM-Aufruf mit Token-Zaehlung."""

    model: str
    agent: str
    input_tokens: int
    output_tokens: int
    call_type: str  # "complete" | "complete_structured" | "complete_vision"


# Shared-list-Referenz (nicht die Liste selbst!) im ContextVar
_accumulator_ref: ContextVar[list[LLMUsage] | None] = ContextVar(
    "llm_usage_accumulator", default=None
)


def reset_accumulator() -> None:
    """Einmal pro Analyse aufrufen (Orchestrator). Erstellt neue shared list."""
    _accumulator_ref.set([])


def record_usage(usage: LLMUsage) -> None:
    """Aus tools/llm.py aufrufen. Thread-safe via CPython GIL (list.append)."""
    acc = _accumulator_ref.get()
    if acc is not None:
        acc.append(usage)


def collect_summary(pricing: object) -> "CostSummary":
    """Am Ende der Analyse aufrufen. pricing = ModelPricingConfig-Instanz."""
    from models.cost_models import CostSummary

    usages = _accumulator_ref.get() or []
    s = CostSummary(call_count=len(usages))
    for u in usages:
        s.total_input_tokens += u.input_tokens
        s.total_output_tokens += u.output_tokens
        agent_tokens = u.input_tokens + u.output_tokens
        s.tokens_per_agent[u.agent] = s.tokens_per_agent.get(u.agent, 0) + agent_tokens
        p = pricing.prices.get(u.model, {"input": 0.0, "output": 0.0})
        cost = (
            u.input_tokens * p["input"] / 1_000_000
            + u.output_tokens * p["output"] / 1_000_000
        )
        s.cost_per_model[u.model] = s.cost_per_model.get(u.model, 0.0) + cost
        s.estimated_cost_usd += cost
    s.total_tokens = s.total_input_tokens + s.total_output_tokens
    return s
