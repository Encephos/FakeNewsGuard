# Architektur – FakeNewsGuard

Dieses Dokument beschreibt die interne Architektur des Systems nach dem Refactoring (v2).

---

## Inhaltsverzeichnis

1. [Übersicht](#übersicht)
2. [Claim-Processing-Pipeline](#claim-processing-pipeline)
3. [Evidence-Retrieval und Trust Boundary](#evidence-retrieval-und-trust-boundary)
4. [Chain-of-Verification (CoVe)](#chain-of-verification-cove)
5. [Datenmodelle](#datenmodelle)
6. [Konfiguration](#konfiguration)
7. [Testarchitektur](#testarchitektur)

---

## Übersicht

FakeNewsGuard ist ein Multi-Agent-System. Jeder Agent hat eine klar abgegrenzte Verantwortung und kommuniziert nur über typisierte Pydantic-Modelle.

```
Orchestrator
  ├── ClaimExtractorAgent   (Facade → ClaimProcessorAgent)
  │     └── ClaimProcessingPipeline
  │           ├── SentenceSplitter
  │           ├── ClaimSelector
  │           ├── Disambiguator
  │           ├── ClaimDecomposer
  │           ├── ClaimCanonicalizerAgent
  │           └── ClaimPrioritizerAgent
  │
  ├── FactCheckerAgent       (Facade → EvidenceBuilder + CoVe + Verdict)
  │     ├── EvidenceBuilderAgent
  │     │     ├── WebSearchClient    (SearXNG)
  │     │     ├── LangSearchClient   (LangSearch API)
  │     │     └── GoogleFactCheckAPI
  │     ├── CoVeProcessor
  │     └── VerdictAgent
  │
  ├── NumberAuditorAgent
  ├── RhetoricAnalyzerAgent
  ├── ImageAnalyzerAgent
  └── SynthesizerAgent
```

---

## Claim-Processing-Pipeline

`agents/claim_processor.py` → `ClaimProcessingPipeline`

### Stufen

| # | Klasse | Eingabe | Ausgabe |
|---|--------|---------|---------|
| 1 | `SentenceSplitter` | Rohtext | `list[str]` (Segmente + Kontext-Fenster) |
| 2 | `ClaimSelector` | Segmente | `list[ProcessedClaim]` (nur prüfbare Claims) |
| 3 | `Disambiguator` | Claims | Claims mit `ambiguity_level`, `requires_more_context` |
| 4 | `ClaimDecomposer` | Claims | Atomare Claims (Komposita gesplittet) |
| 5 | `ClaimCanonicalizerAgent` | Claims | Claims mit `canonical_text`, `canonical_hash`, normalisierten Entitäten/Zahlen/Daten |
| 6 | `ClaimPrioritizerAgent` | Claims | Claims mit `priority_score`, `harm_score`, `checkworthiness_score` – sortiert |

### Graceful Degradation

Jede Stufe hat einen Try-Except-Block. Bei LLM-Fehlern gibt die Stufe die unveränderten Claims zurück und loggt den Fehler. Die Pipeline bricht nie komplett ab.

### Top-N Filtering

Der Orchestrator ruft `_select_top_claims(result)` auf, das:
1. `ClaimType.OPINION` ausschließt
2. `is_checkworthy=False` ausschließt
3. Nach `priority_score` absteigend sortiert
4. Die Top-N zurückgibt (N = `config.claim_processing.top_n`, 0 = alle)

### Canonical Hash

```python
def _canonical_hash(text: str) -> str:
    normalized = " ".join(text.lower().strip().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
```

Erlaubt cache-freundliche Lookups – semantisch identische Claims (verschiedene Formulierungen) erhalten nach Kanonisierung denselben Hash.

---

## Evidence-Retrieval und Trust Boundary

`agents/evidence_builder.py` → `EvidenceBuilderAgent`

### Retrievalpipeline

```
ProcessedClaim
    │
    ▼
Query-Generierung (LLM)
    │
    ├── SearXNG.multi_search_async()  ──┐
    ├── LangSearchClient.multi_search() ├── asyncio.gather() parallel
    └── GoogleFactCheckAPI()           ──┘
    │
    ▼
_dedup_results()        (URL-Normalisierung, Trailing-Slash, etc.)
    │
    ▼
_rank_evidence_items()  (Multi-Faktor-Ranking)
    │
    ▼
Scraping (Top-N Quellen)
    │
    ▼
EvidencePack            (Trust Boundary)
```

### Ranking-Formel

```
score = domain_tier_score * 0.40
      + relevance_score   * 0.35
      + fc_bonus          * 0.15
      + gfc_match_bonus   * 0.10
```

### Domain-Tier-System

| Tier | Beispiele | Bedeutung |
|------|-----------|-----------|
| 1 | destatis.de, eurostat.ec.europa.eu, bpb.de | Primäre Statistikquellen |
| 2 | bka.de, bundesregierung.de, rki.de | Offizielle Behörden |
| 3 | reuters.com, dpa.de, faz.net, zeit.de | Qualitätsjournalismus |
| 4 | correctiv.org, faktenfuchs.de, snopes.com | Faktenchecker-Organisationen |
| 5 | (alle anderen) | Unbekannte Quellen |

### Trust Boundary

**Problem:** Rohes HTML aus dem Web kann manipulative Inhalte, Prompt-Injection oder irrelevante Textwüste enthalten.

**Lösung:** `EvidenceBuilderAgent` ist der einzige Agent, der Web-Content sieht. Er extrahiert relevante Abschnitte und kürzt diese auf **max. 800 Zeichen** (`EvidenceItem.excerpt`). Der VerdictAgent bekommt ausschließlich das strukturierte `EvidencePack`.

```python
class EvidenceItem(BaseModel):
    excerpt: str = Field(max_length=800)  # Trust Boundary enforced
    ...

# In _build_evidence_items():
excerpt = scrape_result.content[:800]  # Hard-cut before creating EvidenceItem
```

`EvidencePack.format_for_verdict()` ist die einzige Methode, die Evidenz-Text für LLM-Prompts formatiert – sie enthält nie rohes HTML.

---

## Chain-of-Verification (CoVe)

`agents/cove_processor.py` → `CoVeProcessor`

CoVeProcessor ist **kein** BaseAgent-Subklasse, da er ausschließlich auf dem strukturierten EvidencePack arbeitet und kein eigenes Retrieval betreibt.

### Ablauf

```
EvidencePack + ProcessedClaim
    │
    ▼
Phase 1: Baseline-Assessment
    └── LLM bewertet Claim anhand EvidencePack → BaselineAssessment
        (rating, reasoning, confidence, main_evidence_urls)
    │
    ▼
Phase 2: Verifikationsfragen generieren
    └── LLM generiert 2–N Fragen, die die Baseline WIDERLEGEN könnten
        (Typen: number / timeframe / source / causality / definition / comparison / context)
    │
    ▼
Phase 3: Unabhängige Antworten
    └── LLM beantwortet JEDE Frage einzeln, OHNE die Baseline zu paraphrasieren
        (answer, contradicts_baseline, answer_found_in_evidence)
    │
    ▼
Phase 4: Reconciliation
    └── LLM reconciliert Baseline mit Verifikationsantworten
        → final_rating, final_confidence, confidence_delta, contradictions_found
    │
    ▼
CoVeTrace
```

### Budget-Kontrolle

| Config | Default | Beschreibung |
|--------|---------|--------------|
| `cove.enabled` | `false` | CoVe global aktivieren |
| `cove.max_verification_questions` | `3` | Max. Fragen pro Claim (0 = CoVe überspringen) |
| `cove.max_additional_searches` | `2` | Max. zusätzliche Web-Suchen während CoVe |

### CoVeTrace im VerdictAgent

Der VerdictAgent wertet `CoVeTrace` aus:
- `has_significant_contradictions()` → fügt Uncertainty-Signal hinzu
- `confidence_delta < -0.15` → `confidence_reduction_reason` gesetzt
- `verdict_based_on_fact_check_org` → wenn GFC-Match vorhanden

---

## Datenmodelle

### `models/schemas.py`

**`ProcessedClaim(Claim)`** – Erweitert `Claim` um alle Processing-Felder:

```python
class ProcessedClaim(Claim):
    # Canonicalization
    canonical_text: str = ""
    canonical_hash: str = ""
    normalized_entities: list[str] = []
    normalized_dates: list[str] = []
    normalized_numbers: list[str] = []

    # Disambiguation
    ambiguity_level: AmbiguityLevel = AmbiguityLevel.NONE
    ambiguity_reason: str = ""
    requires_more_context: bool = False

    # Prioritization
    priority_score: float = 0.5
    harm_score: float = 0.0
    checkworthiness_score: float = 0.5
    priority_reason: str = ""
    recommended_processing_order: int = 0
    is_checkworthy: bool = True
```

**`ClaimProcessingResult`** – Superset von `ClaimExtractionResult`:
- `claims: list[ProcessedClaim]` (statt `list[Claim]`)
- `to_extraction_result()` für Backward-Compatibility

**`FactCheckResult`** – Erweitert um optionale Felder:
- `evidence_pack: Optional[EvidencePack]`
- `cove_trace: Optional[CoVeTrace]`
- `verdict_meta: Optional[FinalVerdictMeta]`

### `models/evidence_models.py`

```
EvidencePack
  ├── google_fact_check_matches: list[GoogleFactCheckMatch]
  ├── web_results: list[EvidenceItem]
  │     └── EvidenceItem
  │           ├── source: EvidenceSource (url, domain, domain_tier, is_primary_source)
  │           ├── excerpt: str (max 800 Zeichen – Trust Boundary)
  │           ├── relevance_score: float
  │           └── extraction_confidence: float
  ├── contradictions: list[EvidenceContradiction]
  ├── evidence_quality: EvidenceQualitySignals
  └── format_for_verdict() → str  # Einziger Ausgang zum VerdictAgent
```

### `models/verdict_models.py`

```
CoVeTrace
  ├── baseline: BaselineAssessment (rating, reasoning, confidence)
  ├── verification_questions: list[VerificationQuestion]
  ├── verification_answers: list[VerificationAnswer]
  ├── contradictions_found: list[str]
  ├── confidence_delta: float
  ├── final_rating_changed: bool
  └── has_significant_contradictions() → bool

FinalVerdictMeta
  ├── uncertainty_signals: list[str]
  ├── confidence_reduction_reason: str
  ├── verdict_based_on_fact_check_org: bool
  └── source_quality_note: str
```

---

## Konfiguration

`config.py` liest alle Werte aus Umgebungsvariablen (`.env`).

```python
@dataclass
class AppConfig:
    llm: LLMConfig
    search: SearchConfig
    langsearch: LangSearchConfig          # NEU
    google_fact_check: GoogleFactCheckConfig  # NEU
    claim_processing: ClaimProcessingConfig   # NEU
    cove: CoVeConfig                          # NEU
    retry: RetryConfig
    cache: CacheConfig
    verbose: bool
    max_input_chars: int
```

LangSearch und Google Fact Check sind **optional** – fehlen die API-Keys, werden sie automatisch deaktiviert (Warning statt Fehler).

---

## Testarchitektur

Alle Tests sind mock-basiert und benötigen keine echten API-Keys.

```
tests/
├── conftest.py                    # minimal_config, sample_processed_claim, sample_evidence_pack
└── unit/
    ├── test_claim_processor.py    # 22 Tests: Pipeline, Hash, ProcessedClaim, Prioritizer
    ├── test_evidence_builder.py   # 18 Tests: Dedup, Tier, Relevanz, Qualität, Format
    ├── test_cove_processor.py     # 7 Tests: CoVeTrace, Budget, Reconciliation
    ├── test_verdict_agent.py      # 7 Tests: Verdict, GFC, CoVe-Integration, Sources
    └── test_orchestrator_v2.py    # 10 Tests: Top-N, Workflow, Fehlerbehandlung
```

**Wichtige Fixtures (`conftest.py`):**

```python
@pytest.fixture
def minimal_config():
    """AppConfig mit allen optionalen Features deaktiviert."""
    return AppConfig(
        llm=LLMConfig(provider="anthropic", api_key="test-key"),
        search=SearchConfig(provider="searxng", base_url="http://localhost:8888"),
        langsearch=LangSearchConfig(api_key="", enabled=False),
        google_fact_check=GoogleFactCheckConfig(api_key="", enabled=False),
        claim_processing=ClaimProcessingConfig(top_n=0),
        cove=CoVeConfig(enabled=False),
        ...
    )

@pytest.fixture
def sample_evidence_pack():
    """EvidencePack mit einem GFC-Match und einem destatis.de-Item."""
    ...

@pytest.fixture
def sample_processed_claim():
    """ProcessedClaim C1 mit allen neuen Feldern gesetzt."""
    ...
```

Agenten werden über `Orchestrator.__new__()` oder direkte Instanzierung mit `MagicMock`-LLM und -Search-Client getestet – keine echten HTTP-Calls.
