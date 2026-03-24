"""Urteilsmodelle für VerdictAgent und CoVe-Prozessor.

Definiert die Datenstrukturen für:
  - Chain-of-Verification (CoVe): Verifikationsfragen, -antworten, Trace
  - Finales Urteil mit Metadaten (Unsicherheitssignale, Konfidenz-Delta)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Verifikationsfragen ────────────────────────────────────────────────────────


class VerificationCategory(str, Enum):
    """Kategorie einer Verifikationsfrage."""

    NUMBER = "number"         # Genaue Zahl, Statistik, Messgröße
    TIMEFRAME = "timeframe"   # Zeitraum, Jahrgang, Aktualität
    SOURCE = "source"         # Primärquelle, Studienherkunft
    CAUSALITY = "causality"   # Kausalität vs. Korrelation
    DEFINITION = "definition" # Begriff- oder Kategoriendefinition
    COMPARISON = "comparison" # Vergleichsbasis, Referenzgröße
    CONTEXT = "context"       # Fehlender Kontext, Einschränkungen
    OTHER = "other"


class VerificationQuestion(BaseModel):
    """Eine einzelne Verifikationsfrage für den CoVe-Prozess."""

    question_id: str = Field(description="Eindeutige ID, z.B. Q1, Q2")
    text: str = Field(description="Die konkrete Verifikationsfrage")
    category: VerificationCategory = VerificationCategory.OTHER
    rationale: str = Field(
        default="",
        description="Warum diese Frage für die Claim-Verifikation relevant ist",
    )
    priority: int = Field(
        default=1,
        ge=1,
        le=3,
        description="1=hoch, 2=mittel, 3=niedrig",
    )


class VerificationAnswer(BaseModel):
    """Antwort auf eine Verifikationsfrage – unabhängig von der Baseline."""

    question_id: str
    answer: str = Field(description="Konkrete Antwort auf die Verifikationsfrage")
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Konfidenz der Antwort (0=unsicher, 1=sehr sicher)",
    )
    supporting_evidence_urls: list[str] = Field(
        default_factory=list,
        description="URLs der Quellen, die diese Antwort stützen",
    )
    supporting_excerpt: str = Field(
        default="",
        description="Relevanter Textauszug, der die Antwort belegt",
    )
    contradicts_baseline: bool = Field(
        default=False,
        description="True wenn diese Antwort der Baseline-Einschätzung widerspricht",
    )
    answer_found_in_evidence: bool = Field(
        default=True,
        description="False wenn die Frage nicht im vorhandenen Evidence-Set beantwortet werden konnte",
    )


# ── CoVe Trace ────────────────────────────────────────────────────────────────


class BaselineAssessment(BaseModel):
    """Vorläufige Einschätzung vor dem CoVe-Prozess."""

    rating: str = Field(
        description="Vorläufiges Urteil (TRUE/MOSTLY_TRUE/MISLEADING/MOSTLY_FALSE/FALSE/UNVERIFIABLE)"
    )
    reasoning: str = Field(description="Begründung der Baseline-Einschätzung")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    main_evidence_used: list[str] = Field(
        default_factory=list,
        description="URLs der Quellen, auf denen die Baseline basiert",
    )


class CoVeTrace(BaseModel):
    """Vollständiger CoVe-Trace für einen Claim.

    Enthält die Baseline, alle Verifikationsfragen und -antworten
    sowie das Reconciliation-Ergebnis.
    """

    claim_id: str

    # ── Phase 1: Baseline ────────────────────────────────────────────────────
    baseline: BaselineAssessment

    # ── Phase 2+3: Verifikation ──────────────────────────────────────────────
    verification_questions: list[VerificationQuestion] = Field(default_factory=list)
    verification_answers: list[VerificationAnswer] = Field(default_factory=list)

    # ── Phase 4: Reconciliation ──────────────────────────────────────────────
    contradictions_found: list[str] = Field(
        default_factory=list,
        description="Erkannte Widersprüche zwischen Baseline und Verifikationsantworten",
    )
    confidence_delta: float = Field(
        default=0.0,
        description="Änderung der Konfidenz durch CoVe (negativ = Konfidenz gesunken)",
    )
    final_rating_changed: bool = Field(
        default=False,
        description="True wenn das finale Urteil von der Baseline abweicht",
    )
    unanswered_questions: list[str] = Field(
        default_factory=list,
        description="Fragen, die im Evidence-Set nicht beantwortet werden konnten",
    )

    def has_significant_contradictions(self) -> bool:
        """Gibt True zurück, wenn mindestens eine hochpriorisierte Antwort die Baseline widerspricht."""
        return any(a.contradicts_baseline for a in self.verification_answers)


# ── Finales Urteil Metadaten ───────────────────────────────────────────────────


class FinalVerdictMeta(BaseModel):
    """Metadaten zum finalen Urteil des VerdictAgent.

    Enthält den CoVe-Trace (wenn aktiv) sowie Unsicherheitssignale
    und Begründungen für Konfidenz-Absenkungen.
    """

    cove_trace: Optional[CoVeTrace] = None

    # ── Unsicherheitssignale ─────────────────────────────────────────────────
    uncertainty_signals: list[str] = Field(
        default_factory=list,
        description="Konkrete Unsicherheitsfaktoren, die das Urteil erschweren",
    )
    confidence_reduction_reason: str = Field(
        default="",
        description="Begründung wenn Konfidenz durch CoVe oder Widersprüche gesenkt wurde",
    )

    # ── Urteilsqualität ──────────────────────────────────────────────────────
    verdict_based_on_fact_check_org: bool = Field(
        default=False,
        description="True wenn professionelle Faktenchecker-Ergebnisse maßgeblich waren",
    )
    primary_sources_consulted: bool = Field(
        default=False,
        description="True wenn Primärquellen (Destatis, Eurostat etc.) genutzt wurden",
    )
    evidence_gap: str = Field(
        default="",
        description="Welche Evidenz fehlt, um ein sicheres Urteil zu fällen",
    )
