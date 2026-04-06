"""Dataclasses fuer Metriken und Reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepResult:
    name: str
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    search_count: int = 0
    error: str | None = None
    skipped: bool = False
    output_summary: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CaseReport:
    case_id: str
    category: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def total_latency(self) -> float:
        return sum(s.latency_s for s in self.steps)

    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.steps)

    @property
    def total_llm_calls(self) -> int:
        return sum(s.llm_calls for s in self.steps)

    @property
    def error_count(self) -> int:
        return sum(1 for s in self.steps if s.error)


@dataclass
class PipelineReport:
    tier: str = ""
    cases: list[CaseReport] = field(default_factory=list)

    @property
    def total_latency(self) -> float:
        return sum(c.total_latency for c in self.cases)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.cases)

    def step_averages(self) -> dict[str, dict]:
        """Berechne Durchschnittswerte pro Step ueber alle Cases."""
        from collections import defaultdict
        accum: dict[str, list[StepResult]] = defaultdict(list)
        for case in self.cases:
            for step in case.steps:
                if not step.skipped:
                    accum[step.name].append(step)

        averages: dict[str, dict] = {}
        for name, results in accum.items():
            n = len(results)
            averages[name] = {
                "avg_latency_s": sum(r.latency_s for r in results) / n,
                "avg_tokens": sum(r.total_tokens for r in results) / n,
                "avg_llm_calls": sum(r.llm_calls for r in results) / n,
                "error_rate": sum(1 for r in results if r.error) / n,
                "count": n,
            }
        return averages
