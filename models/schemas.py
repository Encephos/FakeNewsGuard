"""Datenmodelle für das Multi-Agent Faktencheck-System."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from models.cost_models import CostSummary  # noqa: F401


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


# ── Claim Processing (erweiterte Pipeline) ───────────────────────


class AmbiguityLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ClaimFrame(BaseModel):
    """Strukturierter semantischer Rahmen eines Claims.

    Der ClaimFrame ist der eigentliche Wahrheitsträger. Freier Claim-Text
    ist nur noch Render-/Ausgabeform. Alle Such- und Prüfoperationen sollen
    auf den Frame-Feldern basieren, nicht auf dem rohen Claim-Text.
    """

    raw_text: str = Field(description="Ursprünglicher Claim-Text (unveränderlich)")
    subject: str = Field(default="", description="Subjekt / Akteur der Behauptung")
    predicate: str = Field(default="", description="Handlung / Aussage / Verb-Phrase")
    object: str = Field(default="", description="Objekt / Ziel / Betroffenes")
    institution: str = Field(default="", description="Beteiligte Institution oder Behörde")
    location: str = Field(default="", description="Ort / Region / Land")
    time_reference: str = Field(default="", description="Zeitbezug (ISO oder beschreibend)")
    numbers: list[str] = Field(
        default_factory=list,
        description="Alle spezifischen Zahlen und Mengenangaben",
    )
    sanction: str = Field(default="", description="Sanktion oder Strafe (z.B. Bußgeld)")
    enforcement: str = Field(default="", description="Durchsetzungsmechanismus (z.B. Kameraüberwachung)")
    policy_context: str = Field(default="", description="Politischer/regulativer Kontext (z.B. 15-Minuten-Stadt)")
    claim_type: str = Field(default="", description="Klassifikation des Claims")
    canonical_text: str = Field(
        default="",
        description="Kanonische Formulierung rekonstruiert aus Frame-Feldern",
    )


class ClaimSearchProfile(BaseModel):
    """Suchprofil abgeleitet aus ClaimFrame. Basis für Query-Generierung.

    Kein freier Claim-Text als primäre Suchgrundlage – stattdessen
    strukturierte Felder für verschiedene Query-Typen. Verhindert
    Query-Kollaps auf generische Einzelbegriffe.
    """

    core_entities: list[str] = Field(
        default_factory=list,
        description="Kernentitäten: Personen, Institutionen, Orte",
    )
    institutions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    action_terms: list[str] = Field(
        default_factory=list,
        description="Handlungs-/Ereignis-Begriffe",
    )
    policy_terms: list[str] = Field(
        default_factory=list,
        description="Policy-/Gesetz-/Programmbegriffe",
    )
    number_terms: list[str] = Field(
        default_factory=list,
        description="Spezifische Zahlen aus dem Claim",
    )
    sanction_terms: list[str] = Field(
        default_factory=list,
        description="Sanktions- und Durchsetzungsbegriffe",
    )
    exclusion_terms: list[str] = Field(
        default_factory=list,
        description="Begriffe die Off-topic-Treffer verursachen (für Nachfilterung)",
    )
    official_source_hints: list[str] = Field(
        default_factory=list,
        description="site:-Hints für offizielle Primärquellen",
    )
    fact_check_hints: list[str] = Field(
        default_factory=list,
        description="site:-Hints für Faktenchecker-Organisationen",
    )


class ProcessedClaim(Claim):
    """Claim nach mehrstufiger Processing-Pipeline.

    Erweitert Claim um Kanonisierung, Disambiguierung und Priorisierung.
    Rückwärtskompatibel: alle Claim-Felder bleiben erhalten.
    """

    # ── Kanonisierung (ClaimCanonicalizerAgent) ──────────────────
    canonical_text: str = Field(
        default="",
        description="Normalisierte, kanonische Form des Claims",
    )
    canonical_hash: str = Field(
        default="",
        description="SHA-256 des canonical_text – für cache-freundliche Keys",
    )
    normalized_entities: list[str] = Field(
        default_factory=list,
        description="Normalisierte Entitäten (z.B. 'Deutschland' statt 'DE')",
    )
    normalized_dates: list[str] = Field(
        default_factory=list,
        description="Normalisierte Datumsangaben (ISO-Format)",
    )
    normalized_numbers: list[str] = Field(
        default_factory=list,
        description="Normalisierte Zahlenangaben (z.B. '1500' statt '1.500')",
    )

    # ── Disambiguierung ──────────────────────────────────────────
    ambiguity_level: AmbiguityLevel = AmbiguityLevel.NONE
    ambiguity_reason: str = Field(
        default="",
        description="Warum der Claim mehrdeutig ist (leer wenn eindeutig)",
    )
    requires_more_context: bool = Field(
        default=False,
        description="True wenn der Claim ohne zusätzlichen Kontext nicht prüfbar ist",
    )

    # ── Priorisierung (ClaimPrioritizerAgent) ────────────────────
    priority_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Gesamtpriorität (1.0 = höchste Priorität)",
    )
    harm_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Schadenspotenzial bei falscher Verbreitung",
    )
    checkworthiness_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Wie prüfenswert der Claim ist",
    )
    priority_reason: str = Field(
        default="",
        description="Begründung der Prioritätseinstufung",
    )
    recommended_processing_order: int = Field(
        default=0,
        description="Empfohlene Reihenfolge (0 = zuerst prüfen)",
    )
    is_checkworthy: bool = Field(
        default=True,
        description="False wenn der Claim als nicht prüfenswert eingestuft wurde",
    )

    # ── Strukturierter Frame (ClaimFrameExtractor) ───────────────
    frame: Optional["ClaimFrame"] = Field(
        default=None,
        description="Strukturierter semantischer Frame – der Wahrheitsträger",
    )
    search_profile: Optional["ClaimSearchProfile"] = Field(
        default=None,
        description="Suchprofil für frame-basierte Query-Generierung",
    )

    # ── Validierung (ClaimValidator) ──────────────────────────────
    is_valid_claim: bool = Field(
        default=True,
        description="False wenn der Claim kein echter, falsifizierbarer Claim ist (z.B. Meta-Claim, Recherche-Frage)",
    )
    invalid_reason: str = Field(
        default="",
        description="Grund warum der Claim ungültig ist (leer wenn valid)",
    )
    claim_quality_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Qualitätsscore des Claims (1.0=perfekt falsifizierbar, 0.0=kein echter Claim)",
    )
    quality_signals: list[str] = Field(
        default_factory=list,
        description=(
            "Erkannte abstrakte Qualitätssignale, z.B. 'missing_artifact_evidence', "
            "'underspecified_actor', 'extraordinary_claim', 'elevated_burden_of_proof'."
        ),
    )
    route_confidence: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Confidence des ClaimRouters für diesen Claim [0.0–1.0]",
    )


class ClaimProcessingResult(BaseModel):
    """Ergebnis der mehrstufigen Claim-Processing-Pipeline.

    Ersetzt ClaimExtractionResult als primäres Ergebnis des ClaimProcessorAgent.
    Rückwärtskompatibel: claims-Property liefert list[ProcessedClaim],
    die als list[Claim] verwendbar sind.
    """

    claims: list[ProcessedClaim]
    implicit_claims: list[str] = Field(
        default_factory=list,
        description="Implizite Behauptungen, die zwischen den Zeilen stehen",
    )
    processing_notes: list[str] = Field(
        default_factory=list,
        description="Log-Einträge aus der Processing-Pipeline",
    )
    total_segments: int = Field(
        default=0,
        description="Anzahl Sätze/Segmente im Originaltext",
    )

    def to_extraction_result(self) -> ClaimExtractionResult:
        """Konvertiere zu ClaimExtractionResult für Abwärtskompatibilität."""
        return ClaimExtractionResult(
            claims=self.claims,
            implicit_claims=self.implicit_claims,
        )


# ── Image Analyzer ───────────────────────────────────────────────


class ImageAnalysisItem(BaseModel):
    image_index: int = Field(description="Index des Bildes (0-basiert)")
    ocr_text: str = Field(default="", description="Erkannter Text im Bild (Overlays, Schlagzeilen, Wasserzeichen)")
    visible_elements: list[str] = Field(
        default_factory=list,
        description="Erkannte Elemente: Personen, Orte, Gebäude, Uniformen, Logos, Symbole",
    )
    manipulation_signs: list[str] = Field(
        default_factory=list,
        description="Anzeichen für Bildmanipulation: inkonsistente Beleuchtung, Cloning-Artefakte, Auflösungsunterschiede",
    )
    emotional_framing: str = Field(
        default="",
        description="Emotionale Rahmung durch Bildwahl, Perspektive, selektiven Ausschnitt oder Farbgebung",
    )
    infographic_data: str = Field(
        default="",
        description="Daten, Statistiken oder Aussagen aus Infografiken und Charts",
    )
    context_clues: list[str] = Field(
        default_factory=list,
        description="Kontexthinweise: sichtbare Daten/Zeitstempel, geografische Merkmale, Zeitraum-Indikatoren",
    )


class ImageAnalysisResult(BaseModel):
    items: list[ImageAnalysisItem] = Field(default_factory=list)
    cross_image_observations: str = Field(
        default="",
        description="Beobachtungen aus dem Zusammenspiel mehrerer Bilder (Widersprüche, Sequenz, Kontext)",
    )
    overall_assessment: str = Field(
        default="",
        description="Zusammenfassende Einschätzung der Bildaussagen für den Faktencheck",
    )


IMAGE_ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image_index": {"type": "integer"},
                    "ocr_text": {"type": "string"},
                    "visible_elements": {"type": "array", "items": {"type": "string"}},
                    "manipulation_signs": {"type": "array", "items": {"type": "string"}},
                    "emotional_framing": {"type": "string"},
                    "infographic_data": {"type": "string"},
                    "context_clues": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["image_index"],
            },
        },
        "cross_image_observations": {"type": "string"},
        "overall_assessment": {"type": "string"},
    },
    "required": ["items", "overall_assessment"],
}


# ── Fact Checker ─────────────────────────────────────────────────


class FactRating(str, Enum):
    TRUE = "TRUE"
    MOSTLY_TRUE = "MOSTLY_TRUE"
    MISLEADING = "MISLEADING"
    MOSTLY_FALSE = "MOSTLY_FALSE"
    FALSE = "FALSE"
    UNVERIFIABLE = "UNVERIFIABLE"


class SourceInfo(BaseModel):
    """Einzelne Quelle mit Glaubwürdigkeitsklassifikation."""
    url: str
    tier: str = Field(default="Unbekannt", description="Offizielle Quelle | Faktencheck-Organisation | Qualitätsjournalismus | Nachrichtenmedium | Nutzergeneriert | Unbekannt")
    domain: str = ""


class FactCheckResult(BaseModel):
    claim_id: str
    rating: FactRating
    evidence: str = Field(description="Gefundene Fakten mit Zusammenfassung")
    correction: str = Field(default="", description="Was falsch oder irreführend ist")
    missing_context: str = Field(default="", description="Absichtlich weggelassener Kontext")
    sources: list[str] = Field(default_factory=list, description="URLs der Quellen")
    classified_sources: list[SourceInfo] = Field(
        default_factory=list,
        description="Quellen mit Glaubwürdigkeitsklassifikation",
    )
    source_consensus: str = Field(
        default="",
        description="Zusammenfassung des Quellen-Konsens (übereinstimmend/widersprüchlich/einseitig)",
    )
    confidence: float = Field(
        default=-1.0,
        ge=-1.0,
        le=1.0,
        description="Kalibrierte Einzelclaim-Konfidenz aus VerdictAgent. -1.0 = nicht gesetzt.",
    )
    # ── Neue Felder (optional, rückwärtskompatibel) ──────────────
    # Werden von EvidenceBuilderAgent + VerdictAgent befüllt wenn aktiv
    evidence_pack: Optional["EvidencePack"] = Field(
        default=None,
        description="Strukturiertes Evidence-Pack aus EvidenceBuilderAgent",
    )
    cove_trace: Optional["CoVeTrace"] = Field(
        default=None,
        description="Chain-of-Verification Trace (None wenn CoVe nicht aktiv)",
    )
    verdict_meta: Optional["FinalVerdictMeta"] = Field(
        default=None,
        description="Metadaten zum Urteil (Unsicherheitssignale etc.)",
    )


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
    analysis_id: str = Field(
        default="",
        description="Korrelations-ID für durchgängiges Tracing der Analyse",
    )
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
    cost_summary: CostSummary | None = Field(
        default=None,
        description="Aggregierter Token-Verbrauch und geschaetzte Kosten dieser Analyse",
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
        "confidence": {"type": "number", "description": "0.0-1.0 Konfidenz in das Urteil"},
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


# ── Lazy Imports für Forward References ──────────────────────────
# Werden erst beim Zugriff aufgelöst, um Zirkelimporte zu vermeiden.
# FactCheckResult nutzt EvidencePack, CoVeTrace, FinalVerdictMeta als
# optionale Felder – diese kommen aus den neuen Modell-Modulen.

def _rebuild_models() -> None:
    """Löse Forward References in Pydantic-Modellen auf.

    Muss einmalig nach allen Imports aufgerufen werden.
    Wird automatisch beim Import von models.schemas ausgeführt.
    """
    from models.evidence_models import EvidencePack  # noqa: F401
    from models.verdict_models import CoVeTrace, FinalVerdictMeta  # noqa: F401
    FactCheckResult.model_rebuild()


# Rebuild beim Import ausführen (best-effort, kein Fehler bei Importproblemen)
try:
    _rebuild_models()
except Exception:
    pass
