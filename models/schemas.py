"""Datenmodelle für das Multi-Agent Faktencheck-System."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Claim Extractor ──────────────────────────────────────────────


class ClaimType(str, Enum):
    FACTUAL = "FACTUAL"
    STATISTICAL = "STATISTICAL"
    CAUSAL = "CAUSAL"
    OPINION = "OPINION"
    CONTEXTUAL = "CONTEXTUAL"


class Claim(BaseModel):
    id: str = Field(description="Eindeutige ID, z.B. C1, C2")
    text: str = Field(description="Die extrahierte Behauptung")
    type: ClaimType
    context: str = Field(default="", description="Fehlender Kontext oder Ambiguität")
    requires_agents: list[str] = Field(
        default_factory=list,
        description="Welche Agenten diesen Claim prüfen sollen",
    )


class ClaimExtractionResult(BaseModel):
    claims: list[Claim]
    implicit_claims: list[str] = Field(
        default_factory=list,
        description="Implizite Behauptungen, die zwischen den Zeilen stehen",
    )


# ── Fact Checker ─────────────────────────────────────────────────


class FactRating(str, Enum):
    TRUE = "TRUE"
    MOSTLY_TRUE = "MOSTLY_TRUE"
    MISLEADING = "MISLEADING"
    MOSTLY_FALSE = "MOSTLY_FALSE"
    FALSE = "FALSE"
    UNVERIFIABLE = "UNVERIFIABLE"


class FactCheckResult(BaseModel):
    claim_id: str
    rating: FactRating
    evidence: str = Field(description="Gefundene Fakten mit Zusammenfassung")
    correction: str = Field(default="", description="Was falsch oder irreführend ist")
    missing_context: str = Field(default="", description="Absichtlich weggelassener Kontext")
    sources: list[str] = Field(default_factory=list, description="URLs der Quellen")


# ── Number Auditor ───────────────────────────────────────────────


class ManipulationType(str, Enum):
    BASE_EFFECT = "BASE_EFFECT"
    ABSOLUTE_VS_RELATIVE = "ABSOLUTE_VS_RELATIVE"
    CATEGORY_ERROR = "CATEGORY_ERROR"
    CHERRY_PICKED_TIMEFRAME = "CHERRY_PICKED_TIMEFRAME"
    CUMULATION_TRICK = "CUMULATION_TRICK"
    TREND_VS_NOISE = "TREND_VS_NOISE"
    PER_CAPITA_MISSING = "PER_CAPITA_MISSING"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    NONE = "NONE"


class NumberAuditResult(BaseModel):
    claim_id: str
    calculation_check: str = Field(description="Eigene Nachrechnung")
    methodology_issues: list[str] = Field(default_factory=list)
    correct_interpretation: str = Field(description="Korrekte Einordnung der Zahl")
    manipulation_type: ManipulationType = ManipulationType.NONE


# ── Rhetoric Analyzer ────────────────────────────────────────────


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RhetoricTechnique(BaseModel):
    technique: str = Field(description="Name der Manipulationstechnik")
    example: str = Field(description="Zitat oder Textstelle aus dem Input")
    explanation: str = Field(description="Wie die Technik hier wirkt")
    severity: Severity


class RhetoricAnalysisResult(BaseModel):
    techniques: list[RhetoricTechnique] = Field(default_factory=list)
    overall_framing: str = Field(default="", description="Gesamteinschätzung des Framings")


# ── Synthesizer ──────────────────────────────────────────────────


class OverallRating(str, Enum):
    RELIABLE = "RELIABLE"
    MOSTLY_RELIABLE = "MOSTLY_RELIABLE"
    MIXED = "MIXED"
    MISLEADING = "MISLEADING"
    HIGHLY_MISLEADING = "HIGHLY_MISLEADING"
    FABRICATED = "FABRICATED"


class SynthesisResult(BaseModel):
    overall_rating: OverallRating
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(description="3-5 Sätze Zusammenfassung")
    claims_analysis: list[FactCheckResult] = Field(default_factory=list)
    number_audits: list[NumberAuditResult] = Field(default_factory=list)
    key_corrections: list[str] = Field(default_factory=list, description="Max 5 Kernkorrekturen")
    manipulation_techniques: list[RhetoricTechnique] = Field(default_factory=list)
    fairness_notes: list[str] = Field(
        default_factory=list,
        description="Was korrekt dargestellt wurde (Fairness-Check)",
    )
    sources: list[str] = Field(default_factory=list)
    analysis_errors: list[str] = Field(
        default_factory=list,
        description="Fehler einzelner Agenten – Analyse läuft trotzdem weiter",
    )


# ── JSON-Schemata für Structured Output ──────────────────────────
# Diese Schemata werden an complete_structured() übergeben, damit
# Anthropic tool_use / OpenAI json_schema genutzt werden kann.

FACT_CHECK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claim_id": {"type": "string"},
        "rating": {
            "type": "string",
            "enum": ["TRUE", "MOSTLY_TRUE", "MISLEADING", "MOSTLY_FALSE", "FALSE", "UNVERIFIABLE"],
        },
        "evidence": {"type": "string"},
        "correction": {"type": "string"},
        "missing_context": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claim_id", "rating", "evidence"],
}

NUMBER_AUDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claim_id": {"type": "string"},
        "calculation_check": {"type": "string"},
        "methodology_issues": {"type": "array", "items": {"type": "string"}},
        "correct_interpretation": {"type": "string"},
        "manipulation_type": {
            "type": "string",
            "enum": [
                "BASE_EFFECT", "ABSOLUTE_VS_RELATIVE", "CATEGORY_ERROR",
                "CHERRY_PICKED_TIMEFRAME", "CUMULATION_TRICK", "TREND_VS_NOISE",
                "PER_CAPITA_MISSING", "CALCULATION_ERROR", "NONE",
            ],
        },
    },
    "required": ["claim_id", "calculation_check", "correct_interpretation", "manipulation_type"],
}

SYNTHESIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "overall_rating": {
            "type": "string",
            "enum": ["RELIABLE", "MOSTLY_RELIABLE", "MIXED", "MISLEADING", "HIGHLY_MISLEADING", "FABRICATED"],
        },
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "key_corrections": {"type": "array", "items": {"type": "string"}},
        "fairness_notes": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_rating", "confidence", "summary"],
}
