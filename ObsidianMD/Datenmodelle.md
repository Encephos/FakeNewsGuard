# Datenmodelle

> Zurück: [[README]] | Siehe auch: [[Agenten]], [[LLM-Abstraktion]]

`models/schemas.py` definiert alle **Pydantic-v2-Modelle, Enums und JSON-Schemas** für das System. Die JSON-Schemas werden direkt für den Structured-Output-Mechanismus der LLM-APIs verwendet.

---

## Enums

### ClaimType

```python
class ClaimType(str, Enum):
    FACTUAL     = "factual"      # Überprüfbare Tatsache
    STATISTICAL = "statistical"  # Zahl / Statistik
    CAUSAL      = "causal"       # Ursache-Wirkung
    OPINION     = "opinion"      # Werturteil
    CONTEXTUAL  = "contextual"   # Erfordert Hintergrundwissen
```

→ [[Agent-ClaimExtractor]]

---

### FactRating

```python
class FactRating(str, Enum):
    TRUE          = "true"
    MOSTLY_TRUE   = "mostly_true"
    MISLEADING    = "misleading"
    MOSTLY_FALSE  = "mostly_false"
    FALSE         = "false"
    UNVERIFIABLE  = "unverifiable"
```

→ [[Agent-FactChecker]]

---

### ManipulationType

```python
class ManipulationType(str, Enum):
    BASE_EFFECT           = "base_effect"
    ABSOLUTE_VS_RELATIVE  = "absolute_vs_relative"
    CATEGORY_ERROR        = "category_error"
    CHERRY_PICKED_TIMEFRAME = "cherry_picked_timeframe"
    CUMULATION_TRICK      = "cumulation_trick"
    TREND_VS_NOISE        = "trend_vs_noise"
    PER_CAPITA_MISSING    = "per_capita_missing"
    CALCULATION_ERROR     = "calculation_error"
    OTHER                 = "other"
```

→ [[Agent-NumberAuditor]]

---

### Severity

```python
class Severity(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
```

→ [[Agent-RhetoricAnalyzer]]

---

### AmbiguityLevel

```python
class AmbiguityLevel(str, Enum):
    NONE   = "none"
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
```

Wird vom `ClaimProcessorAgent` für mehrdeutige Claims gesetzt.

---

### OverallRating

```python
class OverallRating(str, Enum):
    RELIABLE          = "reliable"
    MOSTLY_RELIABLE   = "mostly_reliable"
    MIXED             = "mixed"
    MISLEADING        = "misleading"
    HIGHLY_MISLEADING = "highly_misleading"
    FABRICATED        = "fabricated"
```

→ [[Agent-Synthesizer]]

---

## Claim-Modelle

### Claim

```python
class Claim(BaseModel):
    id: int
    text: str
    type: ClaimType
    context: str                      # Umgebender Satzkontext
    requires_agents: list[str] = []   # ["fact_checker", "number_auditor"]
```

### ClaimExtractionResult

```python
class ClaimExtractionResult(BaseModel):
    claims: list[Claim]
    implicit_claims: list[str]
```

### ClaimFrame

Strukturierter semantischer Rahmen, der aus dem Claim-Text extrahiert wird:

```python
class ClaimFrame(BaseModel):
    raw_text: str
    subject: str           # Handelnde Person / Akteur
    predicate: str         # Handlung / Aussage
    object: str            # Objekt der Handlung
    institution: str       # Beteiligte Institution
    location: str          # Geografischer Bezug
    time_reference: str    # Zeitlicher Bezug
    numbers: list[str]     # Enthaltene Zahlen / Prozente
    sanction: str          # Sanktions-/Strafkontext
    enforcement: str       # Durchsetzungskontext
    policy_context: str    # Politischer/regulatorischer Kontext
    claim_type: ClaimType
    canonical_text: str    # Normalisierte Formulierung
```

### ClaimSearchProfile

Aus `ClaimFrame` abgeleitete Suchparameter für `EvidenceBuilderAgent`:

```python
class ClaimSearchProfile(BaseModel):
    core_entities: list[str]
    institutions: list[str]
    locations: list[str]
    action_terms: list[str]
    policy_terms: list[str]
    number_terms: list[str]
    sanction_terms: list[str]
    exclusion_terms: list[str]
    official_source_hints: list[str]   # site:-Hints für SearXNG
    fact_check_hints: list[str]
```

### ProcessedClaim

Erweiterter `Claim` nach der sechsstufigen Verarbeitungspipeline:

```python
class ProcessedClaim(Claim):
    canonical_text: str
    canonical_hash: str                # SHA-256 (16 Zeichen)
    normalized_entities: list[str]
    normalized_dates: list[str]
    normalized_numbers: list[str]
    ambiguity_level: AmbiguityLevel
    ambiguity_reason: str | None
    requires_more_context: bool
    priority_score: float              # 0.0 – 1.0
    harm_score: float
    checkworthiness_score: float
    priority_reason: str
    recommended_processing_order: int
    is_checkworthy: bool
    frame: ClaimFrame                  # Strukturierter semantischer Rahmen
```

---

## Evidence-Modelle (`models/evidence_models.py`)

### Enums

```python
class SourceConsensus(str, Enum):
    AGREEING      = "agreeing"
    CONTRADICTORY = "contradictory"
    MIXED         = "mixed"
    INSUFFICIENT  = "insufficient"

class SourceDirection(str, Enum):
    SUPPORTS = "supports"
    REFUTES  = "refutes"
    NEUTRAL  = "neutral"
    OFFTOPIC = "offtopic"

class EvidenceType(str, Enum):
    DIRECT     = "direct"      # Direkt zur Behauptung
    CONTEXTUAL = "contextual"  # Thematisch verwandt
    WEAK       = "weak"        # Wenig relevant
```

### FactType (Source-Evidence)

`models/source_evidence.py` – Typisierung normalisierter Fakten aus institutionellen Quellen:

```python
class FactType(str, Enum):
    # Statistik / Wirtschaft:
    STATISTIC       = "statistic"
    INDICATOR       = "indicator"
    TIME_SERIES     = "time_series"

    # Wissenschaft / Publikation:
    STUDY_FINDING   = "study_finding"
    CITATION_COUNT  = "citation_count"
    PUBLICATION_METADATA = "publication_metadata"

    # Recht / Regulierung:
    LEGAL_TEXT      = "legal_text"
    PATENT_CLAIM    = "patent_claim"
    REGULATORY_ACTION = "regulatory_action"

    # Wissen / Entitäten:
    ENTITY_PROPERTY  = "entity_property"     # Direkte Eigenschaft (Wikidata)
    ENTITY_RELATION  = "entity_relation"     # Beziehung zwischen Entitäten

    # Medien / Corroboration:
    MEDIA_CORROBORATION = "media_corroboration"  # Cross-Source-Berichterstattung (GDELT)
    TONE_ANALYSIS       = "tone_analysis"        # Sentiment/Tone-Score

    # Kontext:
    CONTEXT_SUMMARY = "context_summary"    # Enzyklopädie-Zusammenfassung (Wikipedia)

    # Allgemein:
    FACT_STATEMENT  = "fact_statement"
    COMPANY_RECORD  = "company_record"
```

### ClaimDomain (Source-Routing)

`tools/sources/types.py` – Thematische Domänen für Source-Routing:

```python
class ClaimDomain(str, Enum):
    ECONOMIC       = "economic"
    STATISTICAL    = "statistical"
    SCIENTIFIC     = "scientific"
    MEDICAL        = "medical"
    LEGAL          = "legal"
    REGULATORY     = "regulatory"
    FINANCIAL      = "financial"
    PATENT         = "patent"
    TRADE          = "trade"
    BIOGRAPHICAL   = "biographical"    # Personen: Amt, Geburt, Tod
    GENERAL        = "general"         # Cross-Source-Corroboration
    GEOGRAPHIC     = "geographic"      # Orte: Hauptstädte, Einwohner
    INSTITUTIONAL  = "institutional"   # Organisationen: Gründung, Sitz
```

### EvidenceSource

```python
class EvidenceSource(BaseModel):
    url: str
    title: str
    domain: str
    domain_tier: int           # 1–5 (1=offiziell/höchstes Vertrauen)
    publication_date: str | None
    is_fact_check_org: bool
    is_primary_source: bool
```

### EvidenceItem

```python
class EvidenceItem(BaseModel):
    source: EvidenceSource
    excerpt: str               # Max. 800 Zeichen (Trust Boundary!)
    relevance_score: float
    extraction_confidence: float
    supports_claim: SourceDirection
```

> **Trust Boundary:** `excerpt` wird auf 800 Zeichen begrenzt. Der `VerdictAgent` sieht niemals rohes HTML – nur strukturierte `EvidenceItem`-Objekte.

→ Vollständiger Aufbau von `EvidencePack` in [[Agent-FactChecker]]

---

## Agenten-Output-Modelle

### FactCheckResult

```python
class FactCheckResult(BaseModel):
    claim_id: int
    rating: FactRating
    evidence: str
    correction: str | None = None
    missing_context: str | None = None
    sources: list[str] = []
    classified_sources: list[ClassifiedSource] = []
```

### ClassifiedSource

```python
class ClassifiedSource(BaseModel):
    url: str
    title: str
    tier: str          # OFFICIAL | FACT_CHECKER | ...
    relevance: float   # 0.0 – 1.0
```

### NumberAuditResult

```python
class NumberAuditResult(BaseModel):
    claim_id: int
    calculation_check: str
    methodology_issues: list[str]
    correct_interpretation: str
    manipulation_type: ManipulationType | None = None
```

### RhetoricTechnique

```python
class RhetoricTechnique(BaseModel):
    name: str
    description: str
    example: str       # Zitat aus dem Text
    severity: Severity
```

### RhetoricAnalysisResult

```python
class RhetoricAnalysisResult(BaseModel):
    techniques: list[RhetoricTechnique]
    overall_framing: str
```

### ImageAnalysisItem

```python
class ImageAnalysisItem(BaseModel):
    image_index: int                    # Index des Bildes (0-basiert)
    ocr_text: str = ""                  # Erkannter Text im Bild
    visible_elements: list[str] = []    # Personen, Orte, Logos, Symbole
    manipulation_signs: list[str] = []  # Inkonsistente Beleuchtung, Cloning-Artefakte
    emotional_framing: str = ""         # Emotionale Rahmung durch Bildwahl
    infographic_data: str = ""          # Daten aus Infografiken/Charts (Text)
    context_clues: list[str] = []       # Zeitstempel, Geo-Hinweise
```

### ImageAnalysisResult

```python
class ImageAnalysisResult(BaseModel):
    items: list[ImageAnalysisItem]
    cross_image_observations: str
    overall_assessment: str
```

---

## Synthese-Modelle

### SynthesisResult

```python
class SynthesisResult(BaseModel):
    analysis_id: str                      # Korrelations-ID (12-stelliger UUID-Kürzung)
    overall_rating: OverallRating
    confidence: float                     # 0.0 – 1.0
    summary: str
    claims_analysis: list[FactCheckResult]
    number_audits: list[NumberAuditResult]
    key_corrections: list[str]            # max. 5
    manipulation_techniques: list[RhetoricTechnique]
    fairness_notes: str
    sources: list[str]
    analysis_errors: list[str]
```

**analysis_id:** UUID-basierte Korrelations-ID (`uuid.uuid4().hex[:12]`), generiert beim Start jeder Analyse. Ermöglicht durchgängiges Tracing über alle Log-Einträge hinweg – unterstützt Debugging und Auditing in Produktionsumgebungen.

---

## JSON-Schemas für Structured Output

Für jeden Agenten-Output gibt es ein JSON-Schema, das direkt an die [[LLM-Abstraktion|`complete_structured()`-Methode]] übergeben wird:

```python
FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "rating": { "type": "string", "enum": ["true", "mostly_true", ...] },
        "evidence": { "type": "string" },
        "correction": { "type": "string" },
        ...
    },
    "required": ["rating", "evidence"]
}
```

Das LLM gibt dann **garantiert valide JSON** zurück, die direkt in Pydantic-Modelle deserialisiert werden können.

---

## TypeScript-Äquivalente

Die Python-Modelle haben TypeScript-Entsprechungen in `frontend/src/app/lib/types.ts`:

```typescript
// Beispiel:
interface SynthesisResult {
    overall_rating: OverallRating
    confidence: number
    summary: string
    claims_analysis: FactCheckResult[]
    // ...
}
```

→ [[Frontend#TypeScript-Typen]]

---

## Verwandte Dokumente

- [[Agenten]] – Wer welche Modelle produziert
- [[LLM-Abstraktion]] – JSON-Schemas für Structured Output
- [[Frontend]] – TypeScript-Äquivalente
