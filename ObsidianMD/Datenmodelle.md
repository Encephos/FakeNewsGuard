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
    url: str
    ocr_text: str | None = None
    manipulation_indicators: list[str] = []
    emotional_framing: str
    infographic_data: dict | None = None
    context_clues: list[str] = []
    credibility_flags: list[str] = []
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
