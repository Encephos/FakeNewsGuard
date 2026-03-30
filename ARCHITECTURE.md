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
  │     │     ├── SearXNGClient      (primäres Suchbackend)
  │     │     ├── LangSearchClient   (LangSearch API)
  │     │     ├── GoogleFactCheckAPI
  │     │     └── ClaimRouter → SourceClients (14 institutionelle Quellen)
  │     ├── CoVeProcessor
  │     └── VerdictAgent
  │
  ├── NumberAuditorAgent
  ├── RhetoricAnalyzerAgent
  ├── ImageAnalyzerAgent
  └── SynthesizerAgent
```

---

## Wichtige Implementierungsmerkmale

### Entfernte Komponenten (v2 Stabilisierung)
- **`agents/_legacy_fallback.py` (entfernt):** Pre-v2-Monolith-Fallback wurde gelöscht. FactChecker nutzt jetzt ausschließlich die moderne Pipeline (EvidenceBuilder → CoVe → Verdict).
- **Legacy-Tests (re-aktiviert):** `test_orchestrator_v2.py` und `test_cove_processor.py` sind jetzt aktiv und validieren die gesamte v2-Pipeline.

### Moderne Merkmale
- **Analysis-ID Tracing:** UUID-basierte Korrelations-IDs für durchgängiges Debugging
- **Claim-Deduplizierung:** Semantisch identische Claims (unterschiedliche Formulierungen) werden nach Kanonisierung dedupliziert
- **Semantic Cache (optional):** Embedding-ähnliche Claims bei Cache-Miss (0.92 Threshold)
- **Confidence-Kalibrierung:** Brier Scores, Reliability Diagrams, Consensus-Contradiction-Override
- **Hot-Reload:** Domain-Tiers und Scoring-Weights können ohne Neustart neu geladen werden

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

### Top-N Filtering und Deduplication

Der Orchestrator ruft `_select_top_claims(result)` auf, das:
1. Dedupliziert Claims nach `canonical_hash` (nur der erste wird beibehalten)
2. `ClaimType.OPINION` ausschließt
3. `is_checkworthy=False` ausschließt
4. Nach `priority_score` absteigend sortiert
5. Die Top-N zurückgibt (N = `config.claim_processing.top_n`, 0 = alle)

**Canonical Hash Deduplication:**
Semantisch identische Claims (verschiedene Formulierungen) erhalten nach Kanonisierung denselben `canonical_hash`. Dies verhindert doppelte Analysen und spart LLM-Kosten.

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
    ├── SearXNGClient.multi_search_async()  ──┐
    ├── LangSearchClient.multi_search_async() ├── asyncio.gather() parallel
    ├── GoogleFactCheckAPI()               ──┘
    └── ClaimRouter → SourceClients (institutional, optional)
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

### Source Layer (Institutionelle Primärquellen)

`tools/claim_router.py` → `ClaimRouter` · `tools/sources/registry.py` → `SourceRegistry`

`ClaimRouter.route_and_apply(claim)` mappt Claim-Signale **heuristisch** (kein LLM) auf eine
priorisierte Liste kommerziell sicherer Quellen aus der `SourceRegistry`. Bei Routing-Konfidenz
≥ `source_clients.min_confidence` (default: 0.5) werden bis zu `max_sources_per_claim` Clients
aufgerufen und die Ergebnisse in das `EvidencePack` integriert.

14 verfügbare Clients (authority weight 0.70–0.97):

| Gruppe | Quellen |
|--------|---------|
| EU/Statistik | Eurostat (0.92), EUR-Lex (0.94) |
| US Regulatorik | openFDA (0.95), DailyMed (0.90), ClinicalTrials (0.89), USPTO (0.90) |
| Korporativ | Companies House (0.91), GLEIF (0.92) |
| Global/Wissenschaft | World Bank (0.88), OpenAlex (0.78), Crossref, CERN OpenData |
| Wissenschaftlich* | arXiv (0.70), PubMed (0.82) |

\* arXiv und PubMed sind `CommercialUsePolicy.CHECK_TERMS` → zur Laufzeit ausgeschlossen
(`SourceRegistry.by_jurisdiction_safe()` filtert sie heraus).

Ergebnisse werden via `SourceCache` zwischengespeichert (24 h default, 168 h für statische Quellen).

---

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
| `cove.enabled` | `true` | CoVe global aktivieren |
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
  │     └── EvidenceContradiction
  │           ├── type: ContradictionType (negation, numeric, temporal, tier, direction)
  │           ├── severity: ContradictionSeverity (low, medium, high)
  │           ├── source1, source2: str (URLs)
  │           ├── claim1, claim2: str (betroffene Aussagen)
  │           └── reasoning: str
  ├── evidence_quality: EvidenceQualitySignals
  └── format_for_verdict() → str  # Einziger Ausgang zum VerdictAgent
```

**Widerspruch-Erkennung:**
- **Negation:** "ist", "ist nicht" (einfache Verneinung)
- **Numeric:** Zahlenwerte differieren um ≥1,5x mit gleichem Einheit
- **Temporal:** Zeitpunkt-Konflikte (z.B. "2020" vs "2021")
- **Tier:** Konflikte zwischen Quellen unterschiedlicher Qualitäts-Tier
- **Direction:** SUPPORTS vs REFUTES für dieselbe Quelle

Schweregrad wird von der Qualitäts-Tier der betroffenen Quellen bestimmt (Tier 1/2 = HIGH, Tier 3/4 = MEDIUM, Tier 5 = LOW). Max. 5 Widersprüche pro EvidencePack, sortiert nach Schweregrad.

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

SynthesisResult
  ├── analysis_id: str  # Korrelations-ID für Tracing (UUID-kürzung)
  ├── overall_rating: FactRating
  └── ...weitere Felder
```

**Consensus-Contradiction Override:**
- Wenn **alle Fact-Check-Agenten denselben Rating haben** (AGREEING) aber die **Evidenz widerspricht** (CONTRADICTORY), wird das Rating zu **MISLEADING** downgraded
- Wenn die Evidenz **widerspricht** (CONTRADICTORY) aber das Rating **TRUE** ist und **kein direkter FactChecker-Match** vorhanden, wird downgraded zu **MISLEADING**
- Dies verhindert, dass falsche Einigkeit zu falscher Bewertung führt

---

## Konfiguration

`config/` (Package) liest alle Werte aus Umgebungsvariablen (`.env`).
Module: `app.py`, `llm.py`, `search.py`, `processing.py`, `database.py`, `infrastructure.py`

```python
@dataclass
class AppConfig:
    # ── Kern (immer erforderlich) ─────────────────────────────────────
    llm: LLMConfig
    retry: RetryConfig

    # ── Primäre Suche ─────────────────────────────────────────────────
    searxng: SearXNGConfig          # Haupt-Backend (self-hosted, Env: SEARXNG_URL)

    # ── Optionale Suchplugins ─────────────────────────────────────────
    search: SearchConfig            # [Legacy – Backward-Compat-Routing-Layer]
    langsearch: LangSearchConfig    # auto-aktiviert wenn LANGSEARCH_API_KEY gesetzt
    tavily: TavilyConfig            # disabled by default, aktiviert via TAVILY_ENABLED=true
    google_fact_check: GoogleFactCheckConfig  # auto-aktiviert wenn Key gesetzt

    # ── Feature-Configs ───────────────────────────────────────────────
    claim_processing: ClaimProcessingConfig
    cove: CoVeConfig                # enabled=True by default
    evidence_retrieval: EvidenceRetrievalConfig
    synthesizer: SynthesizerConfig

    # ── Source Layer ──────────────────────────────────────────────────
    source_layer: SourceLayerConfig     # API-Keys für institutionelle Quellen
    source_clients: SourceClientsConfig # 14 Clients, min_confidence, max_sources_per_claim

    # ── Infrastruktur ─────────────────────────────────────────────────
    cache: CacheConfig              # Claim-Cache (SQLite dev default)
    search_cache: SearchCacheConfig
    archive: ArchiveConfig
    user_db: UserDBConfig
    telegram: TelegramConfig
    rate_limit: RateLimitConfig
    graph: GraphConfig

    # ── Produktions-Backends ──────────────────────────────────────────
    valkey: ValkeyConfig            # aktiviert via CACHE_BACKEND=valkey
    postgres: PostgreSQLConfig      # aktiviert via DB_BACKEND=postgres

    # ── Skalare ───────────────────────────────────────────────────────
    tier: ScoutTier                 # lite / pro / max
    verbose: bool
    language: str
    max_input_chars: int
    cors_origins: list[str]
```

Optionale Features (LangSearch, Tavily, Google Fact Check) werden automatisch deaktiviert wenn
kein API-Key gesetzt ist (Warning statt Fehler). Produktions-Backends (PostgreSQL, Valkey) müssen
explizit via `DB_BACKEND=postgres` / `CACHE_BACKEND=valkey` aktiviert werden; SQLite ist der
Dev-Default.

---

## Testarchitektur

Alle Unit-Tests sind mock-basiert und benötigen keine echten API-Keys. Netzwerkaufrufe werden
im `tests/unit/conftest.py` via `autouse`-Fixture global gemockt.

```
tests/
├── conftest.py                        # AppConfig-Fixtures, sample_processed_claim, etc.
├── test_retrieval_refactor.py         # Hybrid-Ranking, Evidence-Typing, Tavily-Budget
└── unit/
    ├── test_calibration_tracker.py    # Brier Score, Ground-Truth, Reliability Diagramme
    ├── test_claim_processor.py        # Pipeline, Hash, ProcessedClaim, Prioritizer
    ├── test_claim_router.py           # ClaimRouter, Heuristiken, Jurisdiktion
    ├── test_claim_validator.py
    ├── test_cove_processor.py         # CoVe Baseline, Verifikationsfragen, Reconciliation [AKTIV]
    ├── test_evidence_builder.py       # Widerspruchserkennung (Typ/Schweregrad), Dedup, Tier, Relevanz, Qualität
    ├── test_evidence_quality.py
    ├── test_evidence_rating_integrity.py
    ├── test_fact_checker.py           # v2-Pipeline, UNVERIFIABLE Fallback (kein Legacy-Pfad mehr)
    ├── test_hint_generation.py        # _derive_source_hints, _infer_jurisdiction
    ├── test_orchestrator_v2.py        # Deduplizierung, Analyse-ID, Top-N Auswahl [AKTIV]
    ├── test_source_adapters.py        # SourceRegistry, SourceCache, CircuitBreaker
    ├── test_source_policy_enforcement.py  # CHECK_TERMS, Display-Limits, Policies
    ├── test_synthesizer_aggregation.py
    ├── test_verdict_agent.py          # Verdikt, CoVe-Integration, Confidence-Ceilings
    ├── test_verdict_rating_calibration.py  # Consensus-Contradiction Override
    └── tools/
        └── test_cache.py              # Semantic Cache mit Embedding-Similarity Fallback
```

**Autouse-Mock-Fixture (`tests/unit/conftest.py`):**

```python
@pytest.fixture(autouse=True)
def mock_network_calls(mocker):
    """Mockt alle externen Aufrufe in unit/-Tests automatisch."""
    mocker.patch.object(LangSearchClient,  "multi_search_async", return_value={})
    mocker.patch.object(SearXNGClient,     "multi_search_async", return_value={})
    mocker.patch.object(FactCheckDatabaseClient, "search_async", return_value=[])
    mocker.patch("tools.source_scraper.scrape_sources", return_value=[])
    mocker.patch("tools.scrape_ranker.rank_sources",   return_value=[])
```

**Wichtige Fixtures (`tests/conftest.py`):**

```python
minimal_config()          # AppConfig mit deaktivierten optionalen Features
sample_processed_claim()  # ProcessedClaim mit allen Feldern gesetzt
sample_evidence_pack()    # EvidencePack mit GFC-Match + destatis.de-Item
mock_llm_client()         # Gibt festes Rating zurück (MISLEADING)
mock_search_client()      # Gibt SearchResult mit Testdaten zurück
```
