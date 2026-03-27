"""Konfiguration – lädt API Keys aus .env und definiert Modell-Defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv


class ScoutTier(Enum):
    """Scout-Analyse-Stufen – bestimmen Modellauswahl und Qualität."""

    LITE = "lite"   # Tier 1: OpenRouter Free Tier Router (kostenlos)
    PRO = "pro"     # Tier 2: Gemma für alle Agenten
    MAX = "max"     # Tier 3: Gemma (schnell) + Qwen (mächtig)

load_dotenv()


@dataclass
class RetryConfig:
    """Konfiguration für Retry-Logik."""

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    backoff_factor: float = 2.0


@dataclass
class CacheConfig:
    """Konfiguration für den SQLite-Claim-Cache.

    Env-Vars:
        CACHE_DB_PATH – Pfad zur Cache-Datenbank (Default: .fakeguard_cache.db)
                        In Produktion/Docker auf /app/data/... setzen.
        CACHE_TTL_HOURS – TTL für Cache-Einträge in Stunden (Default: 24)
    """

    enabled: bool = True
    db_path: str = ".fakeguard_cache.db"
    ttl_hours: int = 24  # Wie lange gecachte Ergebnisse gültig sind

    def __post_init__(self) -> None:
        if env_path := os.getenv("CACHE_DB_PATH", ""):
            self.db_path = env_path
        if env_ttl := os.getenv("CACHE_TTL_HOURS", ""):
            self.ttl_hours = int(env_ttl)


@dataclass
class LLMConfig:
    """Konfiguration für den LLM-Provider."""

    provider: str = "openrouter"  # "anthropic" | "openai" | "openrouter" | "ollama"
    model: str = "qwen/qwen3-235b-a22b-thinking-2507" # "qwen/qwen3.5-397b-a17b"
    api_key: str = ""
    base_url: str | None = None  # Für Ollama / lokale Modelle
    temperature: float = 0.2  # Niedrig für Faktenprüfung
    max_tokens: int = 16384 #8192

    def __post_init__(self) -> None:
        if not self.api_key:
            if self.provider == "anthropic":
                self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
            elif self.provider == "openai":
                self.api_key = os.getenv("OPENAI_API_KEY", "")
            elif self.provider == "openrouter":
                self.api_key = os.getenv("OPENROUTER_API_KEY", "")


@dataclass
class SearchConfig:
    """Konfiguration für die Web-Suche."""

    provider: str = "searxng"  # "searxng" | "tavily" | "serper" | "brave"
    api_key: str = ""
    base_url: str = ""  # Für SearXNG: URL der Instanz (z.B. http://localhost:8888)
    engines: str = ""   # SearXNG: kommaseparierte Engine-Liste (z.B. "google,duckduckgo,bing")
    max_results: int = 15              # SearXNG ist self-hosted → großzügig
    max_concurrent_searches: int = 8  # Für async Parallelisierung (self-hosted, keine Limits)
    scrape_top_n: int = 10          # Maximale Anzahl zu scrapender Quellen pro Claim
    scrape_timeout: float = 10.0    # HTTP-Timeout pro Scrape-Request in Sekunden

    def __post_init__(self) -> None:
        if self.provider == "searxng":
            if not self.base_url:
                self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
            if not self.engines:
                self.engines = os.getenv("SEARXNG_ENGINES", "")
        elif not self.api_key:
            key_map = {
                "tavily": "TAVILY_API_KEY",
                "serper": "SERPER_API_KEY",
                "brave": "BRAVE_API_KEY",
            }
            env_var = key_map.get(self.provider, "")
            self.api_key = os.getenv(env_var, "")

        env_scrape_n = os.getenv("SCRAPE_TOP_N", "")
        if env_scrape_n:
            self.scrape_top_n = int(env_scrape_n)
        env_scrape_timeout = os.getenv("SCRAPE_TIMEOUT", "")
        if env_scrape_timeout:
            self.scrape_timeout = float(env_scrape_timeout)


@dataclass
class SearXNGConfig:
    """Konfiguration für den dedizierten SearXNG-Client.

    SearXNG dient als unterstützende Breitensuche (self-hosted, kostenlos).
    Kein Provider-Routing – explizit SearXNG-only.

    Env-Vars:
        SEARXNG_URL        – Basis-URL (Default: http://localhost:8888)
        SEARXNG_ENGINES    – kommaseparierte Engine-Liste (Default: leer = SearXNG-Default)
        SEARXNG_CATEGORIES – kommaseparierte Kategorien (Default: general,news)
        SEARXNG_LANGUAGE   – Suchsprache (Default: de)
        SEARXNG_TIME_RANGE – Zeitbereich: day/week/month/year/None (Default: leer)
    """

    base_url: str = ""
    engines: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=lambda: ["general", "news"])
    language: str = "de"
    time_range: str | None = None
    max_results: int = 15
    max_concurrent_searches: int = 8
    scrape_top_n: int = 10
    scrape_timeout: float = 10.0

    def __post_init__(self) -> None:
        if not self.base_url:
            self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
        env_engines = os.getenv("SEARXNG_ENGINES", "")
        if env_engines and not self.engines:
            self.engines = [e.strip() for e in env_engines.split(",") if e.strip()]
        env_cats = os.getenv("SEARXNG_CATEGORIES", "")
        if env_cats:
            self.categories = [c.strip() for c in env_cats.split(",") if c.strip()]
        env_lang = os.getenv("SEARXNG_LANGUAGE", "")
        if env_lang:
            self.language = env_lang
        env_tr = os.getenv("SEARXNG_TIME_RANGE", "")
        if env_tr:
            self.time_range = env_tr
        env_scrape_n = os.getenv("SCRAPE_TOP_N", "")
        if env_scrape_n:
            self.scrape_top_n = int(env_scrape_n)
        env_scrape_timeout = os.getenv("SCRAPE_TIMEOUT", "")
        if env_scrape_timeout:
            self.scrape_timeout = float(env_scrape_timeout)


@dataclass
class LangSearchConfig:
    """Konfiguration für LangSearch – semantische Websuche.

    LangSearch wird im EvidenceBuilderAgent parallel zu SearXNG genutzt
    und liefert semantisch gerankete Ergebnisse.
    Optionales Reranking über die LangSearch-API wenn verfügbar.

    Env-Vars:
        LANGSEARCH_API_KEY     – API-Key (Pflicht wenn enabled)
        LANGSEARCH_BASE_URL    – API-Basis-URL (Default: offizieller Endpunkt)
        LANGSEARCH_ENABLED     – "true"/"false" (Default: true wenn Key vorhanden)
    """

    api_key: str = ""
    base_url: str = "https://api.langsearch.com/v1"
    enabled: bool = True
    max_results: int = 10

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("LANGSEARCH_API_KEY", "")
        env_url = os.getenv("LANGSEARCH_BASE_URL", "")
        if env_url:
            self.base_url = env_url
        env_enabled = os.getenv("LANGSEARCH_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        elif not self.api_key:
            # Automatisch deaktivieren wenn kein Key
            self.enabled = False


@dataclass
class TavilyConfig:
    """Konfiguration für Tavily – KI-optimierte Websuche.

    Standardmäßig deaktiviert. SearXNG ist die primäre Suchquelle.
    Tavily kann als optionaler Content-Layer explizit zugeschaltet werden.

    Env-Vars:
        TAVILY_API_KEY      – API-Key (erforderlich wenn enabled)
        TAVILY_ENABLED      – "true" um Tavily zu aktivieren (Default: false)
        TAVILY_MAX_RESULTS  – Max. Ergebnisse pro Query (Default: 5)
        TAVILY_SEARCH_DEPTH – "basic" oder "advanced" (Default: advanced)
    """

    api_key: str = ""
    enabled: bool = False  # Standardmäßig deaktiviert – explizit via TAVILY_ENABLED=true aktivieren
    max_results: int = 5
    search_depth: str = "advanced"

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.getenv("TAVILY_API_KEY", "")
        env_enabled = os.getenv("TAVILY_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        env_max = os.getenv("TAVILY_MAX_RESULTS", "")
        if env_max:
            self.max_results = int(env_max)
        env_depth = os.getenv("TAVILY_SEARCH_DEPTH", "")
        if env_depth:
            self.search_depth = env_depth


@dataclass
class GoogleFactCheckConfig:
    """Konfiguration für die Google Fact Check Tools API.

    Env-Vars:
        GOOGLE_FACT_CHECK_API_KEY  – API-Key (kostenlos über Google Cloud Console)
        GOOGLE_FACT_CHECK_ENABLED  – "true"/"false" (Default: true wenn Key vorhanden)

    Hinweis: Der API-Key hieß früher GOOGLE_FACTCHECK_API_KEY (ohne zweites F).
    Beide Varianten werden akzeptiert für Abwärtskompatibilität.
    """

    api_key: str = ""
    enabled: bool = True
    max_results: int = 5

    def __post_init__(self) -> None:
        if not self.api_key:
            # Beide Schreibweisen akzeptieren
            self.api_key = os.getenv(
                "GOOGLE_FACT_CHECK_API_KEY",
                os.getenv("GOOGLE_FACTCHECK_API_KEY", ""),
            )
        env_enabled = os.getenv("GOOGLE_FACT_CHECK_ENABLED", "")
        if env_enabled:
            self.enabled = env_enabled.lower() in ("true", "1", "yes")
        elif not self.api_key:
            self.enabled = False


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
class UserDBConfig:
    """Konfiguration für die SQLite-Nutzerdatenbank."""

    db_path: str = ".fakeguard_users.db"

    def __post_init__(self) -> None:
        env_path = os.getenv("USERS_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class ArchiveConfig:
    """Konfiguration für das Analyse-Archiv."""

    enabled: bool = True
    db_path: str = ".fakeguard_archive.db"
    max_entries: int = 1000  # Max. Einträge, älteste werden gelöscht (0 = unbegrenzt)

    def __post_init__(self) -> None:
        # Im Docker nutzen wir /app/data/ für Persistenz
        env_path = os.getenv("ARCHIVE_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class TelegramConfig:
    """Konfiguration für den Telegram Bot."""

    bot_token: str = ""
    backend_url: str = "http://backend:8000"

    def __post_init__(self) -> None:
        if not self.bot_token:
            self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.backend_url or self.backend_url == "http://backend:8000":
            self.backend_url = os.getenv("BACKEND_URL", "http://backend:8000")


@dataclass
class GraphConfig:
    """Konfiguration für den Cross-Reference Graph."""

    enabled: bool = True
    db_path: str = ".fakeguard_graph.db"

    def __post_init__(self) -> None:
        env_path = os.getenv("GRAPH_DB_PATH", "")
        if env_path:
            self.db_path = env_path


@dataclass
class RateLimitConfig:
    """Konfiguration für API Rate-Limiting (Token-Bucket)."""

    enabled: bool = True
    requests_per_minute: int = 10  # Max. Analyse-Anfragen pro IP pro Minute
    burst: int = 3  # Max. gleichzeitige Burst-Anfragen

    def __post_init__(self) -> None:
        env_rpm = os.getenv("RATE_LIMIT_RPM", "")
        if env_rpm:
            self.requests_per_minute = int(env_rpm)
        env_burst = os.getenv("RATE_LIMIT_BURST", "")
        if env_burst:
            self.burst = int(env_burst)


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
    # Mindestanteil direkt widerlegter Claims (FALSE/MOSTLY_FALSE) für FABRICATED
    fabricated_min_refuted_ratio: float = 0.5

    # ── Rhetorik-Floors ───────────────────────────────────────────────────────
    # Rhetorik-Score ab dem MISLEADING als Mindestverdikt gilt (+ unverified_min)
    rhetoric_floor_misleading: float = 0.5
    # Rhetorik-Score ab dem HIGHLY_MISLEADING als Mindestverdikt gilt
    rhetoric_floor_highly: float = 0.7
    # Normalisierungsbasis: 3 HIGH-Techniken ergeben Score 1.0
    rhetoric_norm_base: float = 9.0

    # ── Guardrail-Schwellen für unverified/refuted Ratios ─────────────────────
    # Min. unverified_ratio damit der MISLEADING-Floor greift
    misleading_unverified_min: float = 0.4
    # Min. unverified_ratio damit der HIGHLY_MISLEADING-Floor greift
    highly_misleading_unverified_min: float = 0.5
    # Max. refuted_ratio für den HIGHLY_MISLEADING-Floor (muss niedrig sein)
    highly_misleading_refuted_max: float = 0.3

    # ── Confidence-Aggregation ────────────────────────────────────────────────
    # Puffer auf min_claim_conf beim Blending mehrerer Claim-Confidences
    claim_confidence_buffer: float = 0.10
    # Ceiling bei genau 1 Fact-Check ohne konsultierte Primärquellen
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

    langsearch_queries_simple: int = 3      # Einfache FACTUAL Claims (großzügige API-Limits)
    langsearch_queries_complex: int = 5     # STATISTICAL / CAUSAL / CONTEXTUAL Claims
    langsearch_retry_on_weak: bool = True   # Zweite LangSearch-Runde bei schwacher Evidenz
    # ── Tavily-Budgetierung ───────────────────────────────────────────────────
    tavily_primary_queries: int = 1         # Tavily-Queries in der Primärrunde (sparsam)
    tavily_max_queries_per_claim: int = 3   # Max. Tavily-Queries pro Claim inkl. Expansion
    tavily_expand_on_low_quality: bool = True  # Mehr Tavily nur bei schwacher Evidenz
    tavily_request_budget: int = 10         # Max. Tavily-Requests pro Analyse-Lauf (0 = unbegrenzt)
    # ── Schwellenwerte ────────────────────────────────────────────────────────
    weak_evidence_threshold: float = 0.25  # avg_relevance-Schwelle → LangSearch-Retry
    low_trust_confidence_penalty: float = 0.20  # Penalty-Faktor auf overall_quality
    pre_scrape_offtopic_penalty: float = 0.70   # Mindest-Penalty für Pre-Scrape-Filter
    # ── Evidence-Typing ───────────────────────────────────────────────────────
    claim_scope_min_direct: float = 0.60   # Min. claim_scope_score für "direct" evidence
    # ── Freshness / Recency ───────────────────────────────────────────────────
    stale_sources_freshness_threshold: float = 0.35  # avg_freshness < Wert → Stale-Penalty
    stale_sources_confidence_penalty: float = 0.15   # Abzug auf overall_quality bei alten Quellen
    searxng_news_categories: list[str] = field(default_factory=lambda: ["news", "general"])
    current_state_freshness_threshold: float = 0.60  # Min. avg_freshness für Aktuell-Zustand-Claims
    # SearXNG time_range für Aktuell-Zustand-Claims (Amtsinhaber, CEO, etc.)
    # "month" ist konservativ genug um Jahr-alte Artikel auszuschließen,
    # aber breit genug damit offizielle Seiten gefunden werden.
    # Mögliche Werte: "day" | "week" | "month" | "year" | None
    current_state_time_range: str = "month"

    def __post_init__(self) -> None:
        if v := os.getenv("LANGSEARCH_QUERIES_SIMPLE", ""):
            self.langsearch_queries_simple = int(v)
        if v := os.getenv("LANGSEARCH_QUERIES_COMPLEX", ""):
            self.langsearch_queries_complex = int(v)
        if v := os.getenv("LANGSEARCH_RETRY_ON_WEAK", ""):
            self.langsearch_retry_on_weak = v.lower() in ("true", "1", "yes")
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
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    langsearch: LangSearchConfig = field(default_factory=LangSearchConfig)
    tavily: TavilyConfig = field(default_factory=TavilyConfig)
    google_fact_check: GoogleFactCheckConfig = field(default_factory=GoogleFactCheckConfig)
    claim_processing: ClaimProcessingConfig = field(default_factory=ClaimProcessingConfig)
    cove: CoVeConfig = field(default_factory=CoVeConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    user_db: UserDBConfig = field(default_factory=UserDBConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    evidence_retrieval: EvidenceRetrievalConfig = field(default_factory=EvidenceRetrievalConfig)
    synthesizer: SynthesizerConfig = field(default_factory=SynthesizerConfig)
    source_layer: SourceLayerConfig = field(default_factory=SourceLayerConfig)
    tier: ScoutTier = ScoutTier.PRO  # Scout-Stufe (lite / pro / max)
    verbose: bool = True  # Zeige Agent-Wechsel und Zwischenergebnisse
    language: str = "de"  # Primärsprache der Analyse
    max_input_chars: int = 10_000  # Schutz vor übermäßig langen Inputs
    # CORS: kommaseparierte Liste erlaubter Origins.
    # Standardwert "*" erlaubt alle – in Produktion mit CORS_ORIGINS setzen.
    # Beispiel: CORS_ORIGINS=https://fakeguard.example.com,https://app.example.com
    cors_origins: list = field(
        default_factory=lambda: (
            [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
            or ["*"]
        )
    )

    def validate(self) -> None:
        """Prüft, ob alle nötigen API Keys vorhanden sind. Beendet mit Fehlermeldung wenn nicht."""
        errors: list[str] = []

        key_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
        if self.llm.provider in key_env and not self.llm.api_key:
            errors.append(f"Fehlender LLM API Key: {key_env[self.llm.provider]} nicht gesetzt")

        # SearXNG hat immer einen Default (localhost:8888) → kein Hard-Fail, nur Info
        if self.searxng.base_url == "http://localhost:8888":
            print("  ℹ SearXNG nutzt Default-URL (localhost:8888) – via SEARXNG_URL anpassbar.", file=sys.stderr)
        # Legacy-Provider (search.provider): nur prüfen wenn nicht searxng
        if self.search.provider != "searxng" and not self.search.api_key:
            key_map = {"tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY", "brave": "BRAVE_API_KEY"}
            env_var = key_map.get(self.search.provider, f"{self.search.provider.upper()}_API_KEY")
            errors.append(f"Fehlender Search API Key: {env_var} nicht gesetzt")

        # Tavily, LangSearch und Google Fact Check sind optional – nur warnen, nicht abbrechen
        if self.tavily.enabled and not self.tavily.api_key:
            print("  ⚠ Tavily aktiviert aber kein TAVILY_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.tavily.enabled = False

        if self.langsearch.enabled and not self.langsearch.api_key:
            print("  ⚠ LangSearch aktiviert aber kein LANGSEARCH_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.langsearch.enabled = False

        if self.google_fact_check.enabled and not self.google_fact_check.api_key:
            print("  ⚠ Google Fact Check aktiviert aber kein GOOGLE_FACT_CHECK_API_KEY – wird deaktiviert.", file=sys.stderr)
            self.google_fact_check.enabled = False

        if errors:
            print("❌ Konfigurationsfehler:", file=sys.stderr)
            for err in errors:
                print(f"   • {err}", file=sys.stderr)
            print("\n   Tipp: Kopiere .env.example → .env und trage deine API Keys ein.", file=sys.stderr)
            sys.exit(1)


# ── SearXNG Query-Routing-Konstanten ─────────────────────────────────────────
# Engine-Sets für claim-typ-gesteuertes Routing.
# Passen zu den in searxng/settings.yml konfigurierten Engines.

SEARXNG_WEB_ENGINES: list[str] = ["duckduckgo", "brave", "qwant"]
SEARXNG_NEWS_ENGINES: list[str] = ["duckduckgo", "brave", "tagesschau"]
SEARXNG_REFERENCE_ENGINES: list[str] = ["wikipedia", "wikidata"]
