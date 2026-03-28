"""Feature-Konfigurationen – Claim Processing, Evidence Retrieval, CoVe, Synthesizer."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ClaimQualitySignalConfig:
    """Konfiguration für abstrakte Qualitätssignale im ClaimValidator.

    Die vier Signale werden rein über strukturelle und statistische Merkmale
    erkannt – kein Hardcoding einzelner Wörter, Personen oder Testfälle.

    Signale:
        missing_artifact_evidence  – Claim referenziert ein Artefakt (Beschluss,
                                      Studie, …), aber keine verifizierbaren Anker
                                      (Akteur, Institution, Zeit, Zahl).
        underspecified_actor       – Akteur/Subject zu generisch um prüfbar zu sein
                                      (leeres frame.subject + frame.institution).
        extraordinary_claim        – Absolutheitssprache oder extreme Prozentwerte.
        elevated_burden_of_proof   – Kausaler Claim oder Sanktions-/Durchsetzungs-
                                      kontext → höhere Beweislast.

    Alle Schwellenwerte und Muster sind konfigurierbar (Env-Vars oder Instantiierung).

    Env-Vars:
        QUALITY_MISSING_ARTIFACT_PENALTY    – Penalty für missing_artifact_evidence (Default: 0.25)
        QUALITY_UNDERSPECIFIED_ACTOR_PENALTY – Penalty für underspecified_actor (Default: 0.20)
        QUALITY_EXTRAORDINARY_CLAIM_PENALTY  – Penalty für extraordinary_claim (Default: 0.20)
        QUALITY_ELEVATED_BURDEN_PENALTY      – Penalty für elevated_burden_of_proof (Default: 0.10)
        QUALITY_REQUIRES_CONTEXT_THRESHOLD   – Anzahl Signale → requires_more_context=True (Default: 2)
        QUALITY_EXTRAORDINARY_PCT_THRESHOLD  – Prozentwert ab dem extraordinary_claim feuert (Default: 90.0)
        QUALITY_MIN_ACTOR_LENGTH             – Mindestlänge von frame.subject/institution (Default: 6)
    """

    # ── Penalty-Gewichte ──────────────────────────────────────────────────────
    missing_artifact_penalty: float = 0.25
    underspecified_actor_penalty: float = 0.20
    extraordinary_claim_penalty: float = 0.20
    elevated_burden_penalty: float = 0.10

    # Ab dieser Anzahl aktiver Signale → requires_more_context=True
    requires_context_signal_threshold: int = 2

    # Regex für Absolutheitssprache (konfigurierbar, kein Themen-Hardcoding)
    extraordinary_absolute_pattern: str = (
        r"\b(alle|jeder|jede|jedes|niemand|niemals|immer|stets|"
        r"vollständig|ausnahmslos|grundsätzlich|pauschal|generell)\b"
    )

    # Prozentwert (0–100), ab dem extraordinary_claim feuert
    extraordinary_percentage_threshold: float = 90.0

    # Mindestlänge von frame.subject bzw. frame.institution für spezifischen Akteur
    min_actor_length: int = 6

    def __post_init__(self) -> None:
        if v := os.getenv("QUALITY_MISSING_ARTIFACT_PENALTY", ""):
            self.missing_artifact_penalty = float(v)
        if v := os.getenv("QUALITY_UNDERSPECIFIED_ACTOR_PENALTY", ""):
            self.underspecified_actor_penalty = float(v)
        if v := os.getenv("QUALITY_EXTRAORDINARY_CLAIM_PENALTY", ""):
            self.extraordinary_claim_penalty = float(v)
        if v := os.getenv("QUALITY_ELEVATED_BURDEN_PENALTY", ""):
            self.elevated_burden_penalty = float(v)
        if v := os.getenv("QUALITY_REQUIRES_CONTEXT_THRESHOLD", ""):
            self.requires_context_signal_threshold = int(v)
        if v := os.getenv("QUALITY_EXTRAORDINARY_PCT_THRESHOLD", ""):
            self.extraordinary_percentage_threshold = float(v)
        if v := os.getenv("QUALITY_MIN_ACTOR_LENGTH", ""):
            self.min_actor_length = int(v)


@dataclass
class ClaimProcessingConfig:
    """Konfiguration für die mehrstufige Claim-Processing-Pipeline.

    Env-Vars:
        CLAIM_TOP_N                   – Max. Claims die verarbeitet werden (0 = alle)
        USE_CANONICAL_CACHE           – Cache-Keys auf canonical_text statt Rohtext
    """

    top_n: int = 0  # 0 = alle Claims verarbeiten
    use_canonical_cache: bool = False
    # Minimale Checkworthiness-Score um einen Claim zu verarbeiten (0 = alle)
    min_checkworthiness: float = 0.0
    quality_signals: ClaimQualitySignalConfig = field(
        default_factory=ClaimQualitySignalConfig
    )

    def __post_init__(self) -> None:
        env_n = os.getenv("CLAIM_TOP_N", "")
        if env_n:
            self.top_n = int(env_n)
        env_cache = os.getenv("USE_CANONICAL_CACHE", "")
        if env_cache:
            self.use_canonical_cache = env_cache.lower() in ("true", "1", "yes")
        env_min = os.getenv("MIN_CHECKWORTHINESS", "")
        if env_min:
            self.min_checkworthiness = float(env_min)


@dataclass
class CoVeConfig:
    """Konfiguration für Chain-of-Verification (CoVe).

    Env-Vars:
        COVE_ENABLED                        – CoVe aktivieren (Default: true)
        MAX_VERIFICATION_QUESTIONS          – Max. Verifikationsfragen pro Claim
        MAX_ADDITIONAL_VERIFICATION_SEARCHES – Max. zusätzliche Suchanfragen in CoVe
    """

    enabled: bool = True
    max_verification_questions: int = 3   # 2–5 Fragen pro Claim
    max_additional_searches: int = 2      # Budget für zusätzliche Retrieval-Runden

    def __post_init__(self) -> None:
        env_enabled = os.getenv("COVE_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        env_q = os.getenv("MAX_VERIFICATION_QUESTIONS", "")
        if env_q:
            self.max_verification_questions = int(env_q)
        env_s = os.getenv("MAX_ADDITIONAL_VERIFICATION_SEARCHES", "")
        if env_s:
            self.max_additional_searches = int(env_s)


@dataclass
class SynthesizerConfig:
    """Schwellenwerte für die regelbasierte Aggregationslogik im SynthesizerAgent.

    Steuerung der drei Rating-Guardrails sowie der Confidence-Aggregation
    aus Per-Claim-Confidences.

    Env-Vars:
        SYNTH_FABRICATED_MIN_REFUTED_RATIO       – Min. Anteil widerlegter Claims für FABRICATED (Default: 0.5)
        SYNTH_RHETORIC_FLOOR_MISLEADING          – Rhetorik-Score-Schwelle für MISLEADING-Floor (Default: 0.5)
        SYNTH_RHETORIC_FLOOR_HIGHLY              – Rhetorik-Score-Schwelle für HIGHLY_MISLEADING-Floor (Default: 0.7)
        SYNTH_RHETORIC_NORM_BASE                 – Normalisierungsbasis für Rhetorik-Score (Default: 9.0)
        SYNTH_MISLEADING_UNVERIFIED_MIN          – Min. unverified_ratio für MISLEADING-Guardrail (Default: 0.4)
        SYNTH_HIGHLY_MISLEADING_UNVERIFIED_MIN   – Min. unverified_ratio für HIGHLY_MISLEADING-Guardrail (Default: 0.5)
        SYNTH_HIGHLY_MISLEADING_REFUTED_MAX      – Max. refuted_ratio für HIGHLY_MISLEADING-Guardrail (Default: 0.3)
        SYNTH_CLAIM_CONFIDENCE_BUFFER            – Puffer auf min_claim_conf bei Multi-Claim (Default: 0.10)
        SYNTH_EXTRAORDINARY_CLAIM_CONF_CEILING   – Confidence-Ceiling bei 1 Claim ohne Primärquellen (Default: 0.80)
    """

    # ── FABRICATED-Guardrail ──────────────────────────────────────────────────
    fabricated_min_refuted_ratio: float = 0.5

    # ── Rhetorik-Floors ───────────────────────────────────────────────────────
    rhetoric_floor_misleading: float = 0.5
    rhetoric_floor_highly: float = 0.7
    rhetoric_norm_base: float = 9.0

    # ── Guardrail-Schwellen für unverified/refuted Ratios ─────────────────────
    misleading_unverified_min: float = 0.4
    highly_misleading_unverified_min: float = 0.5
    highly_misleading_refuted_max: float = 0.3

    # ── Confidence-Aggregation ────────────────────────────────────────────────
    claim_confidence_buffer: float = 0.10
    extraordinary_claim_confidence_ceiling: float = 0.80

    def __post_init__(self) -> None:
        if v := os.getenv("SYNTH_FABRICATED_MIN_REFUTED_RATIO", ""):
            self.fabricated_min_refuted_ratio = float(v)
        if v := os.getenv("SYNTH_RHETORIC_FLOOR_MISLEADING", ""):
            self.rhetoric_floor_misleading = float(v)
        if v := os.getenv("SYNTH_RHETORIC_FLOOR_HIGHLY", ""):
            self.rhetoric_floor_highly = float(v)
        if v := os.getenv("SYNTH_RHETORIC_NORM_BASE", ""):
            self.rhetoric_norm_base = float(v)
        if v := os.getenv("SYNTH_MISLEADING_UNVERIFIED_MIN", ""):
            self.misleading_unverified_min = float(v)
        if v := os.getenv("SYNTH_HIGHLY_MISLEADING_UNVERIFIED_MIN", ""):
            self.highly_misleading_unverified_min = float(v)
        if v := os.getenv("SYNTH_HIGHLY_MISLEADING_REFUTED_MAX", ""):
            self.highly_misleading_refuted_max = float(v)
        if v := os.getenv("SYNTH_CLAIM_CONFIDENCE_BUFFER", ""):
            self.claim_confidence_buffer = float(v)
        if v := os.getenv("SYNTH_EXTRAORDINARY_CLAIM_CONF_CEILING", ""):
            self.extraordinary_claim_confidence_ceiling = float(v)


@dataclass
class EvidenceRetrievalConfig:
    """Konfiguration für das adaptive Retrieval im EvidenceBuilderAgent.

    Trennt die Rollen von Tavily (breit/content-stark) und LangSearch (semantisch)
    und macht Schwellenwerte konfigurierbar statt hart codiert.

    Rollen:
        SearXNG    = primäre Breitensuche (self-hosted, kostenlos, alle Queries)
        LangSearch = semantisch-präzise Ergänzung (adaptiv je nach Claim-Komplexität)
        Tavily     = optionaler Content-Layer (standardmäßig deaktiviert, budgetiert)
        GFC        = strukturierter Shortcut-Layer (kein Query-Budget nötig)

    Env-Vars:
        LANGSEARCH_QUERIES_SIMPLE     – Queries für einfache Claims (Default: 2)
        LANGSEARCH_QUERIES_COMPLEX    – Queries für komplexe/statistische Claims (Default: 4)
        LANGSEARCH_RETRY_ON_WEAK      – Zweite Runde bei schwacher erster Evidenz (Default: true)
        TAVILY_PRIMARY_QUERIES        – Tavily-Queries in Primärrunde (Default: 1)
        TAVILY_MAX_QUERIES_PER_CLAIM  – Max. Tavily-Queries pro Claim inkl. Expansion (Default: 3)
        TAVILY_EXPAND_ON_LOW_QUALITY  – Tavily-Expansion bei schwacher Evidenz (Default: true)
        TAVILY_REQUEST_BUDGET         – Max. Tavily-Requests pro Analyse-Lauf (Default: 10)
        WEAK_EVIDENCE_THRESHOLD       – Avg-Relevanz-Schwelle für LangSearch-Retry (Default: 0.25)
        LOW_TRUST_CONFIDENCE_PENALTY  – Penalty-Faktor für Low-Trust-Rate in overall_quality (Default: 0.20)
        PRE_SCRAPE_OFFTOPIC_PENALTY   – Mindest-Penalty damit Kandidat vor Scraping entfernt wird (Default: 0.70)
        CLAIM_SCOPE_MIN_DIRECT        – Min. claim_scope_score für direct evidence (Default: 0.60)
        CURRENT_STATE_TIME_RANGE      – SearXNG time_range für Aktuell-Zustand-Claims (Default: month)
    """

    langsearch_queries_simple: int = 3
    langsearch_queries_complex: int = 5
    langsearch_retry_on_weak: bool = True
    # ── Query Expansion ────────────────────────────────────────────────────────
    query_expansion_enabled: bool = True
    # ── Tavily-Budgetierung ───────────────────────────────────────────────────
    tavily_primary_queries: int = 1
    tavily_max_queries_per_claim: int = 3
    tavily_expand_on_low_quality: bool = True
    tavily_request_budget: int = 10
    # ── Schwellenwerte ────────────────────────────────────────────────────────
    weak_evidence_threshold: float = 0.25
    low_trust_confidence_penalty: float = 0.20
    pre_scrape_offtopic_penalty: float = 0.70
    # ── Evidence-Typing ───────────────────────────────────────────────────────
    claim_scope_min_direct: float = 0.60
    # ── Freshness / Recency ───────────────────────────────────────────────────
    stale_sources_freshness_threshold: float = 0.35
    stale_sources_confidence_penalty: float = 0.15
    searxng_news_categories: list[str] = field(default_factory=lambda: ["news", "general"])
    current_state_freshness_threshold: float = 0.60
    current_state_time_range: str = "month"
    # ── Iterative Search (Phase 3) ───────────────────────────────────────────
    iterative_search_enabled: bool = True
    iterative_min_quality: float = 0.45
    iterative_max_rounds: int = 2
    iterative_max_refinement_queries: int = 3

    def __post_init__(self) -> None:
        if v := os.getenv("LANGSEARCH_QUERIES_SIMPLE", ""):
            self.langsearch_queries_simple = int(v)
        if v := os.getenv("LANGSEARCH_QUERIES_COMPLEX", ""):
            self.langsearch_queries_complex = int(v)
        if v := os.getenv("LANGSEARCH_RETRY_ON_WEAK", ""):
            self.langsearch_retry_on_weak = v.lower() in ("true", "1", "yes")
        if v := os.getenv("QUERY_EXPANSION_ENABLED", ""):
            self.query_expansion_enabled = v.lower() in ("true", "1", "yes")
        if v := os.getenv("TAVILY_PRIMARY_QUERIES", ""):
            self.tavily_primary_queries = int(v)
        if v := os.getenv("TAVILY_MAX_QUERIES_PER_CLAIM", ""):
            self.tavily_max_queries_per_claim = int(v)
        if v := os.getenv("TAVILY_EXPAND_ON_LOW_QUALITY", ""):
            self.tavily_expand_on_low_quality = v.lower() in ("true", "1", "yes")
        if v := os.getenv("TAVILY_REQUEST_BUDGET", ""):
            self.tavily_request_budget = int(v)
        if v := os.getenv("WEAK_EVIDENCE_THRESHOLD", ""):
            self.weak_evidence_threshold = float(v)
        if v := os.getenv("LOW_TRUST_CONFIDENCE_PENALTY", ""):
            self.low_trust_confidence_penalty = float(v)
        if v := os.getenv("PRE_SCRAPE_OFFTOPIC_PENALTY", ""):
            self.pre_scrape_offtopic_penalty = float(v)
        if v := os.getenv("CLAIM_SCOPE_MIN_DIRECT", ""):
            self.claim_scope_min_direct = float(v)
        if v := os.getenv("CURRENT_STATE_TIME_RANGE", ""):
            self.current_state_time_range = v
        if v := os.getenv("ITERATIVE_SEARCH_ENABLED", ""):
            self.iterative_search_enabled = v.lower() in ("true", "1", "yes")
        if v := os.getenv("ITERATIVE_MIN_QUALITY", ""):
            self.iterative_min_quality = float(v)
        if v := os.getenv("ITERATIVE_MAX_ROUNDS", ""):
            self.iterative_max_rounds = int(v)
        if v := os.getenv("ITERATIVE_MAX_REFINEMENT_QUERIES", ""):
            self.iterative_max_refinement_queries = int(v)


@dataclass
class SourceLayerConfig:
    """Konfiguration für die strukturierte Source-Integration-Schicht.

    Verwaltet API-Keys, Rate-Limit-Overrides und Quellen-Aktivierung
    für die institutionellen Datenquellen in tools/sources/.

    Alle API-Keys werden aus Umgebungsvariablen geladen (Fallback: "").
    Quellen ohne Key funktionieren eingeschränkt (niedrigere Rate-Limits).
    """

    enabled: bool = True
    """Master-Schalter für die gesamte Source-Integration-Schicht."""

    # ── Optionale / pflichtlose API-Keys ──────────────────────────────────────
    companies_house_api_key: str = ""
    """UK Companies House API – Pflicht. ENV: COMPANIES_HOUSE_API_KEY."""

    openfda_api_key: str = ""
    """openFDA API – Optional (höheres Rate-Limit: 240/min statt 40/min).
    ENV: OPENFDA_API_KEY."""

    ncbi_api_key: str = ""
    """NCBI / PubMed E-utilities – Optional (10 req/s statt 3 req/s).
    ENV: NCBI_API_KEY."""

    polite_pool_email: str = ""
    """E-Mail für OpenAlex- und Crossref-Polite-Pool (User-Agent mailto).
    Kein API-Key – ermöglicht höhere Rate-Limits ohne Registrierung.
    ENV: POLITE_POOL_EMAIL."""

    # ── Laufzeit-Overrides ─────────────────────────────────────────────────────
    rate_limit_overrides: dict[str, float] = field(default_factory=dict)
    """Überschreibt rate_limit_rps pro source_id.
    Beispiel: {"pubmed": 5.0, "crossref": 10.0}
    ENV: nicht direkt konfigurierbar – nur programmatisch."""

    enabled_sources: list[str] = field(default_factory=list)
    """Whitelist aktiver source_ids. Leer = alle Quellen aktiviert.
    ENV: SOURCE_LAYER_ENABLED_SOURCES (kommasepariert)."""

    def __post_init__(self) -> None:
        if not self.companies_house_api_key:
            self.companies_house_api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "")
        if not self.openfda_api_key:
            self.openfda_api_key = os.getenv("OPENFDA_API_KEY", "")
        if not self.ncbi_api_key:
            self.ncbi_api_key = os.getenv("NCBI_API_KEY", "")
        if not self.polite_pool_email:
            self.polite_pool_email = os.getenv("POLITE_POOL_EMAIL", "")
        if not self.enabled_sources:
            raw = os.getenv("SOURCE_LAYER_ENABLED_SOURCES", "")
            if raw:
                self.enabled_sources = [s.strip() for s in raw.split(",") if s.strip()]

    def get_api_key(self, source_id: str) -> str:
        """Gibt den API-Key für eine gegebene source_id zurück (leer wenn nicht gesetzt)."""
        _key_map: dict[str, str] = {
            "companies_house": self.companies_house_api_key,
            "openfda": self.openfda_api_key,
            "pubmed": self.ncbi_api_key,
        }
        return _key_map.get(source_id, "")

    def get_rate_limit(self, source_id: str, default: float | None) -> float | None:
        """Gibt das effektive Rate-Limit für source_id zurück.

        Prüft zuerst rate_limit_overrides, fällt auf den SourceConfig-Default zurück.
        """
        return self.rate_limit_overrides.get(source_id, default)

    def is_source_enabled(self, source_id: str) -> bool:
        """Gibt ``True`` zurück wenn source_id aktiviert ist."""
        if not self.enabled:
            return False
        if not self.enabled_sources:
            return True
        return source_id in self.enabled_sources


@dataclass
class SourceClientsConfig:
    """Konfiguration für institutionelle Data Source Clients (Eurostat, openFDA, etc.).

    14 high-authority source clients (authority weight 0.70–0.97) are available:
    - Eurostat, EUR-Lex (EU statistical/legal)
    - openFDA, DailyMed, ClinicalTrials (US pharmaceutical/medical)
    - USPTO, Companies House, GLEIF (corporate/patent)
    - World Bank, OpenAlex, PubMed, Crossref, arXiv, CERN OpenData (scientific/economic)

    Source clients are instantiated per-claim if routing confidence >= min_confidence.
    Results are cached (24h default, 168h for static sources) and rate-limited per source.

    Env-Vars:
        SOURCE_CLIENTS_ENABLED – Enable source client retrieval (Default: true)
        SOURCE_CLIENTS_MIN_CONFIDENCE – Min routing confidence [0.0–1.0] (Default: 0.5)
        SOURCE_CLIENTS_MAX_PER_CLAIM – Max sources to query per claim (Default: 6)
        SOURCE_CLIENTS_MAX_RESULTS – Max results per source (Default: 3)
        SOURCE_CLIENTS_CACHE_TTL – Cache TTL in hours (Default: 24)
        SOURCE_CLIENTS_STATIC_TTL – Static source cache TTL in hours (Default: 168)
    """

    enabled: bool = True
    min_confidence: float = 0.5
    max_sources_per_claim: int = 6
    max_results_per_source: int = 3
    cache_ttl_hours: int = 24
    static_source_ttl_hours: int = 168
    circuit_breaker_failure_threshold: int = 5

    def __post_init__(self) -> None:
        if v := os.getenv("SOURCE_CLIENTS_ENABLED", ""):
            self.enabled = v.lower() in ("true", "1", "yes")
        if v := os.getenv("SOURCE_CLIENTS_MIN_CONFIDENCE", ""):
            self.min_confidence = float(v)
        if v := os.getenv("SOURCE_CLIENTS_MAX_PER_CLAIM", ""):
            self.max_sources_per_claim = int(v)
        if v := os.getenv("SOURCE_CLIENTS_MAX_RESULTS", ""):
            self.max_results_per_source = int(v)
        if v := os.getenv("SOURCE_CLIENTS_CACHE_TTL", ""):
            self.cache_ttl_hours = int(v)
        if v := os.getenv("SOURCE_CLIENTS_STATIC_TTL", ""):
            self.static_source_ttl_hours = int(v)
