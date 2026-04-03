"""Zentraler Daten-Loader für externalisierte YAML-Konfigurationsdateien.

Lädt Domain-Listen, Muster, Stoppwörter und Scoring-Gewichte aus ``data/*.yaml``.
Alle Lade-Funktionen sind mit ``@lru_cache`` dekoriert – die YAML-Dateien werden
pro Prozess nur einmal gelesen.

Falls eine YAML-Datei fehlt, wird auf Inline-Defaults zurückgefallen, damit
der Betrieb auch ohne ``data/``-Verzeichnis möglich ist.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Basisverzeichnis: Projekt-Root (eine Ebene über tools/)
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Lade eine YAML-Datei aus dem data/-Verzeichnis.

    Returns:
        Geparster Inhalt als dict, oder leeres dict bei Fehler.
    """
    path = _DATA_DIR / filename
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, yaml.YAMLError, OSError):
        return {}


# ── Domain-Tier-Listen ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def domain_tiers() -> dict[str, set[str]]:
    """Lade Domain-Tier-Zuordnungen.

    Returns:
        Dict mit Keys: tier1, tier2, tier3, tier4 → set[str] von Domains.
    """
    data = _load_yaml("domain_tiers.yaml")
    return {
        "tier1": set(data.get("tier1_official_statistics", [])),
        "tier2": set(data.get("tier2_government", [])),
        "tier3": set(data.get("tier3_quality_journalism", [])),
        "tier4": set(data.get("tier4_fact_checkers", [])),
    }


@lru_cache(maxsize=1)
def government_domains() -> frozenset[str]:
    """Tier-1 (amtliche Statistik) + Tier-2 (Regierung) Domains aus domain_tiers.yaml."""
    tiers = domain_tiers()
    return frozenset(tiers["tier1"] | tiers["tier2"])


@lru_cache(maxsize=1)
def fact_checker_domains() -> tuple[str, ...]:
    """Tier-4 Fact-Checker-Domains, sortiert für Determinismus."""
    return tuple(sorted(domain_tiers()["tier4"]))


@lru_cache(maxsize=1)
def classifier_tier_patterns() -> list[tuple[int, list[str]]]:
    """Lade Tier-Patterns für source_classifier.py.

    Returns:
        Liste von (tier_int, [domain_patterns]) Tupeln.
        Tier-Mapping: OFFICIAL=5, FACT_CHECKER=4, QUALITY_JOURNALISM=3, MEDIA=2, USER_GENERATED=1
    """
    data = _load_yaml("domain_tiers.yaml")
    classifier = data.get("classifier", {})
    # Reihenfolge: höchster Tier zuerst (OFFICIAL → USER_GENERATED)
    tier_map = [
        (5, classifier.get("official", [])),
        (4, classifier.get("fact_checker", [])),
        (3, classifier.get("quality_journalism", [])),
        (2, classifier.get("media", [])),
        (1, classifier.get("user_generated", [])),
    ]
    return [(tier, patterns) for tier, patterns in tier_map if patterns]


# ── Low-Trust Domains ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def low_trust_domains() -> frozenset[str]:
    """Lade alle Low-Trust-Domains (flach, alle Gruppen zusammen)."""
    data = _load_yaml("low_trust_domains.yaml")
    all_domains: list[str] = []
    for key in ("currency", "grammar", "legal_forums", "qa_sites"):
        all_domains.extend(data.get(key, []))
    return frozenset(all_domains)


@lru_cache(maxsize=1)
def scrape_ranker_low_trust_domains() -> frozenset[str]:
    """Lade Low-Trust-Domains für den Scrape-Ranker."""
    data = _load_yaml("low_trust_domains.yaml")
    return frozenset(data.get("scrape_ranker", []))


# ── Commercial Domains ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def commercial_domains() -> frozenset[str]:
    """Lade kommerzielle Domains."""
    data = _load_yaml("commercial_domains.yaml")
    return frozenset(data.get("domains", []))


# ── Off-topic Patterns ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def offtopic_url_patterns() -> list[re.Pattern[str]]:
    """Lade und kompiliere Off-topic URL-Muster."""
    data = _load_yaml("offtopic_patterns.yaml")
    return [
        re.compile(p, re.IGNORECASE)
        for p in data.get("url_patterns", [])
    ]


@lru_cache(maxsize=1)
def low_trust_content_patterns() -> list[re.Pattern[str]]:
    """Lade und kompiliere Low-Trust-Content-Muster."""
    data = _load_yaml("offtopic_patterns.yaml")
    return [
        re.compile(p, re.IGNORECASE)
        for p in data.get("low_trust_content_patterns", [])
    ]


@lru_cache(maxsize=1)
def commercial_snippet_patterns() -> list[re.Pattern[str]]:
    """Lade und kompiliere kommerzielle Snippet-Muster."""
    data = _load_yaml("offtopic_patterns.yaml")
    return [
        re.compile(p, re.IGNORECASE)
        for p in data.get("commercial_snippet_patterns", [])
    ]


# ── Stopwords ─────────────────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def stopwords(group: str) -> set[str]:
    """Lade Stoppwörter für eine bestimmte Gruppe.

    Args:
        group: "relevance", "scrape_ranker", oder "generic_words"
    """
    data = _load_yaml("stopwords.yaml")
    words = data.get(group, [])
    return set(words)


# ── NER Patterns ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def ner_known_orgs() -> set[str]:
    """Lade bekannte Organisations-Abkürzungen."""
    data = _load_yaml("ner_patterns.yaml")
    return set(data.get("known_orgs", []))


@lru_cache(maxsize=1)
def ner_institution_patterns() -> list[tuple[str, str]]:
    """Lade Institutions-Patterns als (regex_str, kind) Tupel."""
    data = _load_yaml("ner_patterns.yaml")
    patterns = data.get("institution_patterns", [])
    return [(p["pattern"], p["kind"]) for p in patterns if "pattern" in p and "kind" in p]


@lru_cache(maxsize=1)
def ner_law_acronyms() -> re.Pattern[str]:
    """Lade und kompiliere das Gesetzesabkürzungen-Pattern."""
    data = _load_yaml("ner_patterns.yaml")
    pattern_str = data.get("law_acronyms", r"\b(?:BGB|StGB)\b")
    return re.compile(pattern_str)


# ── Scoring Weights ───────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def scoring_weights() -> dict[str, Any]:
    """Lade alle Scoring-Gewichte als verschachteltes Dict."""
    return _load_yaml("scoring_weights.yaml")


@lru_cache(maxsize=1)
def freshness_tiers() -> list[tuple[int | None, float]]:
    """Lade Freshness-Tiers als Liste von (max_days, score) Tupeln."""
    data = scoring_weights()
    tiers = data.get("freshness_tiers", [])
    return [(t.get("max_days"), t["score"]) for t in tiers if "score" in t]


@lru_cache(maxsize=1)
def searxng_engines() -> dict[str, list[str]]:
    """Lade SearXNG Engine-Sets."""
    data = scoring_weights()
    return data.get("searxng_engines", {
        "web": ["duckduckgo", "brave", "qwant", "startpage", "google", "yahoo", "bing", "mojeek", "yep", "presearch"],
        "news": ["duckduckgo", "brave", "tagesschau"],
        "reference": ["wikipedia", "wikidata"],
    })


@lru_cache(maxsize=1)
def query_expansion_config() -> dict[str, int]:
    """Lade Query-Expansion-Konstanten."""
    data = scoring_weights()
    return data.get("query_expansion", {
        "max_query_terms": 5,
        "max_per_family": 2,
        "max_total_queries": 14,
    })


@lru_cache(maxsize=1)
def paywall_domains() -> dict[str, set[str]]:
    """Lade Paywall-Domain-Listen."""
    data = scoring_weights()
    pw = data.get("paywalls", {})
    return {
        "hard": set(pw.get("hard", [])),
        "soft": set(pw.get("soft", [])),
    }


@lru_cache(maxsize=1)
def scrape_thresholds() -> dict[str, float]:
    """Lade Scrape-Decision-Thresholds aus scoring_weights.yaml."""
    data = scoring_weights()
    sr = data.get("scrape_ranker", {})
    return sr.get("scrape_thresholds", {
        "low_trust_min": 0.35,
        "general_min": 0.10,
        "quality_journalism_min": 0.15,
        "media_min": 0.20,
        "media_fallback_min": 0.12,
    })


# ── Verdict Calibration ─────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def verdict_calibration() -> dict[str, Any]:
    """Lade Verdict-Kalibrierungsparameter."""
    return _load_yaml("verdict_calibration.yaml")


@lru_cache(maxsize=1)
def evidence_classification_thresholds() -> dict[str, float]:
    """Lade Evidence-Klassifikations-Schwellenwerte."""
    data = verdict_calibration()
    return data.get("evidence_classification", {
        "min_direct_scope": 0.60,
        "factchecker_relevance_min": 0.30,
        "direct_relevance_min": 0.35,
        "contextual_scope_min": 0.30,
        "contextual_relevance_min": 0.25,
        "max_contradictions": 5,
        "max_excerpt_chars": 800,
    })


@lru_cache(maxsize=1)
def verdict_freshness_thresholds() -> dict[str, float]:
    """Lade Freshness-Schwellenwerte fuer VerdictAgent."""
    data = verdict_calibration()
    return data.get("freshness", {
        "current_state_threshold": 0.60,
        "general_threshold": 0.40,
    })


@lru_cache(maxsize=1)
def verdict_uncertainty_thresholds() -> dict[str, float]:
    """Lade Uncertainty-Schwellenwerte fuer VerdictAgent."""
    data = verdict_calibration()
    return data.get("uncertainty", {
        "quality_threshold": 0.3,
        "offtopic_rate_threshold": 0.4,
        "grounding_severe_threshold": 0.5,
        "grounding_moderate_threshold": 0.75,
    })


@lru_cache(maxsize=1)
def excerpt_length_config() -> dict[str, int]:
    """Lade Excerpt-Laengen-Konfiguration."""
    data = verdict_calibration()
    return data.get("excerpt_length", {
        "default": 400,
        "extended": 800,
    })


# ── Claim Routing ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def claim_routing_config() -> dict[str, Any]:
    """Lade Claim-Routing Scoring-Parameter."""
    return _load_yaml("claim_routing.yaml")


# ── Source Authority ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def source_authority_weights() -> dict[str, float]:
    """Lade Authority-Weights fuer institutionelle Quellen."""
    data = _load_yaml("source_authority.yaml")
    return data.get("weights", {})


@lru_cache(maxsize=1)
def source_tier_thresholds() -> dict[str, float]:
    """Lade Tier-Schwellenwerte fuer Source-Authority."""
    data = _load_yaml("source_authority.yaml")
    return data.get("tier_thresholds", {
        "tier1_min": 0.90,
        "tier2_min": 0.75,
        "tier3_min": 0.55,
        "tier4_min": 0.35,
    })


# ── PageRank Adjustments ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def pagerank_adjustments() -> list[tuple[int, float]]:
    """Lade PageRank Tier-Adjustments als (min_rank, adjustment) Tupel."""
    data = _load_yaml("domain_tiers.yaml")
    raw = data.get("pagerank_adjustments", [
        {"min_rank": 7, "adjustment": -1.0},
        {"min_rank": 5, "adjustment": -0.5},
        {"min_rank": 3, "adjustment": 0.0},
        {"min_rank": 0, "adjustment": 0.5},
    ])
    return [(e["min_rank"], e["adjustment"]) for e in raw]


# ── Satire-Domains ───────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def satire_domains() -> frozenset[str]:
    """Lade bekannte Satire-Domains aus satire_domains.yaml."""
    data = _load_yaml("satire_domains.yaml")
    return frozenset(data.get("domains", []))


# ── Hot-Reload ───────────────────────────────────────────────────────────────

_CACHED_LOADERS = [
    domain_tiers,
    government_domains,
    fact_checker_domains,
    classifier_tier_patterns,
    low_trust_domains,
    scrape_ranker_low_trust_domains,
    commercial_domains,
    offtopic_url_patterns,
    low_trust_content_patterns,
    commercial_snippet_patterns,
    stopwords,
    ner_known_orgs,
    ner_institution_patterns,
    ner_law_acronyms,
    scoring_weights,
    freshness_tiers,
    searxng_engines,
    query_expansion_config,
    paywall_domains,
    scrape_thresholds,
    verdict_calibration,
    evidence_classification_thresholds,
    verdict_freshness_thresholds,
    verdict_uncertainty_thresholds,
    excerpt_length_config,
    claim_routing_config,
    source_authority_weights,
    source_tier_thresholds,
    pagerank_adjustments,
    satire_domains,
]


def reload_all() -> int:
    """Leere alle lru_cache-Einträge, damit YAML-Dateien neu geladen werden.

    Returns:
        Anzahl der geleerten Caches.
    """
    for fn in _CACHED_LOADERS:
        fn.cache_clear()
    return len(_CACHED_LOADERS)
