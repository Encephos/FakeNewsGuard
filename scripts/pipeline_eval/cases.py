"""Testfaelle fuer die Pipeline-Evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvalCase:
    id: str
    category: str
    text: str
    expect_statistical: bool = False
    expect_rhetoric: bool = False


CASES: list[EvalCase] = [
    EvalCase(
        id="eval-1",
        category="faktisch",
        text="Friedrich Merz ist seit Mai 2025 Bundeskanzler von Deutschland.",
    ),
    EvalCase(
        id="eval-2",
        category="statistisch",
        text=(
            "Die Kriminalitaetsrate in Deutschland ist seit 2015 um 50 Prozent "
            "gestiegen, besonders bei Einbruechen."
        ),
        expect_statistical=True,
    ),
    EvalCase(
        id="eval-3",
        category="regulatorisch",
        text=(
            "Die EU hat ab 2025 ein vollstaendiges Verbot von Einwegplastik-Besteck "
            "beschlossen, mit Strafen bis zu 10.000 Euro pro Verstoss."
        ),
    ),
    EvalCase(
        id="eval-4",
        category="rhetorik",
        text=(
            "Die Asylflut kostet den deutschen Steuerzahler Milliarden, waehrend "
            "deutsche Rentner hungern muessen. Willkommenswahn und Sozialtourismus "
            "zerstoeren unser Land."
        ),
        expect_rhetoric=True,
    ),
    EvalCase(
        id="eval-5",
        category="multilingual",
        text=(
            "Global CO2 emissions reached a record high of 37.4 billion tonnes "
            "in 2023 according to the IEA."
        ),
        expect_statistical=True,
    ),
]


def get_cases(ids: list[str] | None = None) -> list[EvalCase]:
    if ids is None:
        return CASES
    return [c for c in CASES if c.id in ids]
