"""models – Pydantic-Datenmodelle für FakeNewsGuard.

Öffentliche API:

    Kern-Schemas (schemas.py):
        Claim, ClaimType, ClaimExtractionResult
        ProcessedClaim, ClaimProcessingResult
        ClaimFrame, ClaimSearchProfile
        FactCheckResult, FactRating, SourceInfo
        NumberAuditResult, ManipulationType
        RhetoricAnalysisResult, RhetoricTechnique, Severity
        SynthesisResult, OverallRating

    Evidence-Modelle (evidence_models.py):
        EvidenceItem, EvidenceSource, EvidencePack
        EvidenceQualitySignals, EvidenceContradiction
        EvidenceType, SourceDirection, SourceConsensus
        GoogleFactCheckMatch

    Institutionelle Quellen-Evidence (source_evidence.py):
        OfficialEvidenceItem   – einheitliches Schema für alle API-Quellen
        NormalizedFact         – atomare Fakteneinheit
        FactType               – Domänenklassifikation
        compute_recency_score  – Hilfsfunktion für Aktualitätsscore

    Urteil-Modelle (verdict_models.py):
        CoVeTrace, FinalVerdictMeta
"""

from models.evidence_models import (
    EvidenceContradiction,
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    EvidenceType,
    GoogleFactCheckMatch,
    SourceConsensus,
    SourceDirection,
)
from models.source_evidence import (
    FactType,
    NormalizedFact,
    OfficialEvidenceItem,
    compute_recency_score,
)

__all__ = [
    # evidence_models
    "EvidenceContradiction",
    "EvidenceItem",
    "EvidencePack",
    "EvidenceQualitySignals",
    "EvidenceSource",
    "EvidenceType",
    "GoogleFactCheckMatch",
    "SourceConsensus",
    "SourceDirection",
    # source_evidence
    "FactType",
    "NormalizedFact",
    "OfficialEvidenceItem",
    "compute_recency_score",
]
