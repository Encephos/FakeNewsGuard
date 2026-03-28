"""EvidenceBuilderAgent – Retrieval, Scraping und Evidenz-Strukturierung.

Verantwortlichkeiten:
    1. Query-Erstellung (LLM-optimiert)
    2. Retrieval via Tavily + LangSearch (primär) + SearXNG (unterstützend, parallel)
    3. Google Fact Check API (separate Priority-Schicht)
    4. Deduplication + Quality-Aware Ranking
    5. Scraping der Top-K Quellen
    6. Strukturierung zu einem EvidencePack

Trust Boundary:
    Dieser Agent ist die Grenze zwischen ungefilterten Webinhalten und
    strukturierter Evidenz. Rohe HTML-Inhalte verlassen diesen Agent nie.
    Nur begrenzte, strukturierte excerpt-Felder (max. 800 Zeichen) werden
    im EvidencePack weitergegeben.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from agents.base import BaseAgent
from config import AppConfig, EvidenceRetrievalConfig
from tools.data_loader import (
    domain_tiers, low_trust_domains, commercial_domains,
    offtopic_url_patterns, low_trust_content_patterns, commercial_snippet_patterns,
    scoring_weights, freshness_tiers as load_freshness_tiers, searxng_engines,
    stopwords as load_stopwords,
)
from tools.reranker import rerank, reranker_available
from tools.iterative_search import extract_feedback_terms, generate_refinement_queries
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
from models.schemas import Claim, ClaimSearchProfile, ProcessedClaim
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.llm import LLMClient
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_classifier import classify_source
from tools.source_scraper import ScrapedSource, scrape_sources
from tools.ner_extractor import entity_overlap_score
from tools.web_search import AsyncWebSearchClient, LangSearchClient, SearchResult, SearXNGClient, SearXNGQuery, WebSearchClient


# ── Deduplication ─────────────────────────────────────────────────────────────

def _dedup_results(results: list[SearchResult]) -> list[SearchResult]:
    """Dedupliziere Suchergebnisse nach URL."""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        norm_url = r.url.rstrip("/").lower()
        if norm_url not in seen:
            seen.add(norm_url)
            unique.append(r)
    return unique


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return url


# ── Domain-Tier Mapping (aus data/domain_tiers.yaml) ─────────────────────────


def _domain_tier(url: str) -> int:
    domain = _extract_domain(url)
    tiers = domain_tiers()
    if any(t in domain for t in tiers["tier1"]):
        return 1
    if any(t in domain for t in tiers["tier2"]):
        return 2
    if any(t in domain for t in tiers["tier3"]):
        return 3
    if any(t in domain for t in tiers["tier4"]):
        return 4
    return 5


def _is_fact_check_org(url: str) -> bool:
    return _domain_tier(url) == 4


# ── Stopwords für Relevanz-Berechnung (aus data/stopwords.yaml) ───────────────

_RELEVANCE_STOPWORDS: set[str] = load_stopwords("relevance")

# Off-topic Signale (aus data/offtopic_patterns.yaml)
_OFFTOPIC_URL_PATTERNS: list[re.Pattern] = offtopic_url_patterns()

# Bekannte kommerzielle/nicht-redaktionelle Domains (aus data/commercial_domains.yaml)
_COMMERCIAL_DOMAINS: frozenset[str] = commercial_domains()

# ── Low-Trust-Seitentyp-Erkennung ────────────────────────────────────────────
# Strukturelle Klasse: Seiten die kaum zur finalen Evidenzqualität beitragen
# dürfen, weil sie generisch sind und keinen direkten Claim-Bezug haben.

# Low-Trust Domains: Währungsrechner, allgemeine Grammatik-/Wörterbuchseiten,
# allgemeine Juraforen/Lexika, allgemeine Bußgeldrechner
_LOW_TRUST_DOMAINS: frozenset[str] = low_trust_domains()

# Low-Trust Titel/Snippet-Muster (aus data/offtopic_patterns.yaml)
_LOW_TRUST_CONTENT_PATTERNS: list[re.Pattern] = low_trust_content_patterns()


def _is_low_trust_site(url: str, title: str, snippet: str) -> bool:
    """Prüfe ob eine Quelle ein Low-Trust-Seitentyp ist.

    Erkennt strukturell:
        - Währungsrechner (xe.com, Umrechnungsseiten)
        - Grammatik-/Konjugationsseiten (verbformen.de, duden.de)
        - Allgemeine Juraforen/Lexika ohne Claim-Bezug
        - Allgemeine Bußgeldrechner ohne redaktionellen Kontext
        - Generische Hilfs-/Erklärseiten

    Nutzt sowohl URL/Domain als auch Titel/Snippet-Muster.

    Returns:
        True wenn die Quelle als Low-Trust eingestuft wird.
    """
    domain = _extract_domain(url)

    # 1. Domain-Match
    if domain in _LOW_TRUST_DOMAINS:
        return True
    # Subdomain-Match (z.B. de.pons.com)
    if any(lt_domain in domain for lt_domain in _LOW_TRUST_DOMAINS):
        return True

    # 2. Titel/Snippet-Muster
    combined = f"{title} {snippet}"
    if any(p.search(combined) for p in _LOW_TRUST_CONTENT_PATTERNS):
        return True

    return False


def _is_generic_reference(url: str, profile: "ClaimSearchProfile | None") -> bool:
    """Erkennt generische Wikipedia/Lexikon-Artikel ohne spezifischen Claim-Bezug.

    Ein Wikipedia-Artikel über einen generischen Begriff (z.B. "Stadtrat")
    ist keine Evidenz für einen spezifischen Claim (z.B. "Stadtrat von Hannover
    beschließt 15-Minuten-Stadt"). Die Erkennung prüft, ob der Artikel-Titel
    mindestens eine Location aus dem Profil enthält.

    Returns:
        True wenn der Artikel generisch ist (keine Location-Spezifik).
    """
    if "wikipedia.org/wiki/" not in url:
        return False
    if not profile or not profile.locations:
        return True  # Ohne Profil/Location: konservativ als generisch werten
    wiki_title = url.split("/wiki/")[-1].replace("_", " ").lower()
    return not any(loc.lower() in wiki_title for loc in profile.locations if loc)


# Kommerzielle Content-Signale (aus data/offtopic_patterns.yaml)
_COMMERCIAL_SNIPPET_PATTERNS: list[re.Pattern] = commercial_snippet_patterns()


# ── Evidence Ranking ──────────────────────────────────────────────────────────

def _extract_entities(text: str) -> set[str]:
    """Extrahiere potenzielle Entitäten (Eigennamen, Zahlen, Akronyme)."""
    # Eigennamen (Großbuchstabe gefolgt von Kleinbuchstaben, min. 3 Zeichen)
    names = set(re.findall(r"\b[A-ZÄÖÜ][a-zäöü]{2,}\b", text))
    # Zahlen (inkl. Prozent, Dezimal)
    numbers = set(re.findall(r"\d+[\.,]?\d*\s*%?", text))
    # Akronyme (2+ Großbuchstaben)
    acronyms = set(re.findall(r"\b[A-ZÄÖÜ]{2,}\b", text))
    return names | numbers | acronyms


def _entity_overlap(claim_text: str, result_text: str) -> float:
    """Berechne den Anteil der Claim-Entitäten, die im Ergebnis vorkommen."""
    claim_entities = _extract_entities(claim_text)
    if not claim_entities:
        return 0.5  # Kein Entitäts-Signal → neutral
    result_lower = result_text.lower()
    matched = sum(1 for e in claim_entities if e.lower() in result_lower)
    return matched / len(claim_entities)


def _is_offtopic_url(url: str) -> bool:
    """Prüfe ob die URL auf eine typisch irrelevante Seite hinweist."""
    domain = _extract_domain(url)
    if domain in _COMMERCIAL_DOMAINS:
        return True
    for pattern in _OFFTOPIC_URL_PATTERNS:
        if pattern.search(url):
            return True
    return False


def _has_commercial_content(title: str, snippet: str) -> bool:
    """Prüfe ob Titel/Snippet typische Kauf-/Shop-Sprache enthalten.

    Erkennt Produktseiten, Bußgeldrechner und andere kommerzielle Inhalte
    die keine redaktionellen Faktencheck-Quellen sind.
    """
    combined = f"{title} {snippet}"
    return any(p.search(combined) for p in _COMMERCIAL_SNIPPET_PATTERNS)


def _is_offtopic_content(
    title: str,
    snippet: str,
    profile: ClaimSearchProfile,
) -> tuple[bool, float]:
    """Prüfe ob Titel/Snippet inhaltlich off-topic sind, gemessen am SearchProfile.

    Geht über URL-Muster hinaus: prüft ob Kernentitäten, Institution, Ort
    und Policy-Kontext im Ergebnis vorhanden sind.

    Returns:
        (is_offtopic, penalty) — penalty 0.0–0.8 für Ranking-Abwertung.
    """
    combined = f"{title} {snippet}".lower()

    # Hat das Profil überhaupt strukturierte Anker?
    has_inst_anchor = bool(profile.institutions)
    has_loc_anchor = bool(profile.locations)
    has_policy_anchor = bool(profile.policy_terms)
    has_number_anchor = bool(profile.number_terms)
    has_sanction_anchor = bool(profile.sanction_terms)

    total_anchors = sum([has_inst_anchor, has_loc_anchor, has_policy_anchor,
                         has_number_anchor, has_sanction_anchor])

    if total_anchors == 0:
        return False, 0.0  # Kein Profil → keine strukturierte Prüfung

    # Wie viele Anker treffen zu?
    # Institution: Wort-für-Wort-Match (nicht Phrase) – "Stadtrat Hannover" matcht auch wenn
    # "Stadtrat" und "Hannover" getrennt im Text stehen
    def _inst_match(inst_list: list[str]) -> bool:
        for inst in inst_list:
            if not inst:
                continue
            words = [w for w in inst.lower().split() if len(w) > 3]
            if words and sum(1 for w in words if w in combined) >= max(1, len(words) // 2):
                return True
        return False

    inst_hit = has_inst_anchor and _inst_match(profile.institutions)
    loc_hit = has_loc_anchor and any(
        loc.lower() in combined for loc in profile.locations if loc
    )
    policy_hit = has_policy_anchor and any(
        term.lower() in combined for term in profile.policy_terms if term
    )
    number_hit = has_number_anchor and any(
        num in combined for num in profile.number_terms if num
    )
    sanction_hit = has_sanction_anchor and any(
        term.lower() in combined for term in profile.sanction_terms if term
    )

    hits = sum([inst_hit, loc_hit, policy_hit, number_hit, sanction_hit])

    # Exklusions-Begriffe: generische Tokens die Off-topic-Treffer provozieren
    has_exclusion = any(
        term.lower() in combined for term in profile.exclusion_terms if term
    )

    # Hartes Signal: kommerzieller Inhalt (Produktseite, Rechner, Shop)
    is_commercial = _has_commercial_content(title, snippet)
    if is_commercial:
        # Kommerzielle Seite die keine Institution/Ort trifft → sofort hard-offtopic
        if has_inst_anchor and has_loc_anchor and not inst_hit and not loc_hit:
            return True, 0.80
        # Kommerzielle Seite ohne Policy-Kontext → starke Abwertung
        if not policy_hit and not inst_hit:
            return True, 0.75

    # Off-topic wenn: Institution + Ort erwartet aber keiner trifft zu
    if has_inst_anchor and has_loc_anchor:
        if not inst_hit and not loc_hit and not policy_hit:
            # Zahlenübereinstimmung allein ist kein ausreichendes Relevanz-Signal
            penalty = 0.70 if number_hit else 0.70
            return True, penalty

    # Off-topic wenn: Nur Exclusion-Begriffe treffen zu, keine echten Anker
    if has_exclusion and hits == 0 and total_anchors >= 2:
        return True, 0.6

    # Policy-sensitiver Filter: Bei Regelungsclaims (Sanktion/Policy vorhanden)
    # müssen strukturelle Claim-Fit-Merkmale stärker greifen.
    is_regulatory = has_sanction_anchor or (has_policy_anchor and has_inst_anchor)

    # Verschärfter Filter: vollständige Regelungsclaims (Sanktion + Policy + Institution)
    # benötigen mindestens 2 von 3 Kernanker (Institution, Ort, Policy).
    # Dies verhindert, dass thematisch ähnliche Seiten (z.B. allgemeine DSGVO-Seiten,
    # generische Videoüberwachungsartikel) als brauchbare Evidenz verbleiben,
    # wenn sie den konkreten Claim-Kontext nicht treffen.
    is_fully_regulatory = has_sanction_anchor and has_policy_anchor and has_inst_anchor
    if is_fully_regulatory:
        key_hits = sum([inst_hit, loc_hit, policy_hit])
        if key_hits < 2:
            # Weniger als 2 Kernanker → für konkrete Regelungsclaims nicht brauchbar
            penalty = 0.80 if key_hits == 0 else 0.70
            return True, penalty

    if is_regulatory:
        # Für Regelungsclaims: ohne Institution ODER Ort → stärkere Abwertung
        if not inst_hit and not loc_hit:
            # Nur Zahl oder Sanktionsbegriff ohne Kontext → fast sicher off-topic
            penalty = 0.75 if (number_hit or sanction_hit) else 0.70
            return True, penalty
        # Institution ODER Ort trifft, aber Policy-Kontext fehlt → moderate Abwertung
        if not policy_hit and hits <= 1:
            return False, 0.45

    # Weiche Abwertung: Weniger als die Hälfte der erwarteten Anker
    if total_anchors >= 3 and hits == 0:
        return True, 0.5

    # Schwache Treffer → leichte Abwertung
    if total_anchors >= 2 and hits < total_anchors / 2:
        return False, 0.3

    return False, 0.0


def _profile_anchor_score(
    result_text: str,
    profile: ClaimSearchProfile,
) -> float:
    """Strukturierter Anchor-Score: Wie gut deckt das Ergebnis das SearchProfile ab?

    Bewertet Treffer auf institution, location, policy_terms, number_terms,
    sanction_terms. Gibt einen Wert zwischen 0.0 (kein Treffer) und 1.0 zurück.
    """
    combined = result_text.lower()

    scored_groups: list[tuple[bool, float]] = [
        # (treffer?, gewicht)
        (bool(profile.institutions) and any(
            i.lower() in combined for i in profile.institutions if i
        ), 0.30),
        (bool(profile.locations) and any(
            l.lower() in combined for l in profile.locations if l
        ), 0.25),
        (bool(profile.policy_terms) and any(
            t.lower() in combined for t in profile.policy_terms if t
        ), 0.20),
        (bool(profile.number_terms) and any(
            n in combined for n in profile.number_terms if n
        ), 0.15),
        (bool(profile.sanction_terms) and any(
            s.lower() in combined for s in profile.sanction_terms if s
        ), 0.10),
    ]

    # Nur Gruppen werten, die auch Daten im Profil haben
    total_weight = sum(w for _, w in scored_groups if _)
    if total_weight == 0:
        return 0.0

    # Normalisiert auf tatsächlich vorhandene Profil-Felder
    active_weight = sum(w for (hit, w) in scored_groups if hit is not None)
    if active_weight == 0:
        return 0.0

    hit_weight = sum(w for (hit, w) in scored_groups if hit)
    normalizer = sum(w for (hit, w) in scored_groups)
    return hit_weight / normalizer if normalizer > 0 else 0.0


def _count_anchor_hits(result_text: str, profile: ClaimSearchProfile) -> int:
    """Zähle wie viele Anchor-Gruppen im Ergebnis tatsächlich matchen."""
    combined = result_text.lower()
    hits = 0
    if profile.institutions and any(i.lower() in combined for i in profile.institutions if i):
        hits += 1
    if profile.locations and any(loc.lower() in combined for loc in profile.locations if loc):
        hits += 1
    if profile.policy_terms and any(t.lower() in combined for t in profile.policy_terms if t):
        hits += 1
    if profile.number_terms and any(n in combined for n in profile.number_terms if n):
        hits += 1
    if profile.sanction_terms and any(s.lower() in combined for s in profile.sanction_terms if s):
        hits += 1
    return hits


def _count_active_anchors(profile: ClaimSearchProfile) -> int:
    """Zähle wie viele Anchor-Gruppen im Profil befüllt sind."""
    count = 0
    if profile.institutions:
        count += 1
    if profile.locations:
        count += 1
    if profile.policy_terms:
        count += 1
    if profile.number_terms:
        count += 1
    if profile.sanction_terms:
        count += 1
    return count


def _compute_freshness(publication_date: str) -> float:
    """Berechne Freshness-Score basierend auf dem Publikationsdatum.

    Returns:
        1.0 = heute/gestern, 0.8 = letzte Woche, 0.5 = letzter Monat,
        0.3 = letztes Jahr, 0.1 = älter, 0.5 = unbekannt (neutral)
    """
    if not publication_date:
        return 0.5  # Unbekannt → neutral

    # Versuche verschiedene Datumsformate zu parsen
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%d.%m.%Y", "%Y/%m/%d", "%B %d, %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(publication_date.strip()[:20], fmt)
            break
        except (ValueError, IndexError):
            continue
    else:
        # Versuch: nur Jahreszahl
        year_match = re.search(r"\b(20\d{2})\b", publication_date)
        if year_match:
            dt = datetime(int(year_match.group(1)), 6, 15)
        else:
            return 0.5

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days_old = (now - dt).days

    for max_days, score in load_freshness_tiers():
        if max_days is None or days_old <= max_days:
            return score
    return 0.1


def _relevance_score(
    result: SearchResult,
    claim_text: str,
    profile: ClaimSearchProfile | None = None,
    ce_score: float | None = None,
) -> float:
    """Multi-Signal Relevanz-Score mit NER-basiertem Entity-Overlap.

    Ohne Profil (Gewichte):
        - Keyword-Overlap  (0.25)
        - NER Entity-Overlap (0.50)  ← spaCy + Synonym-Matching
        - Off-topic Penalty (0.25)

    Mit Profil (Gewichte):
        - Keyword-Overlap  (0.15)
        - NER Entity-Overlap (0.30)  ← spaCy + Synonym-Matching
        - Profile-Anchor   (0.35)    ← strukturierte Anker
        - Off-topic Penalty (0.20)

    NER Entity-Overlap (tools/ner_extractor.py):
        1. Exact match der erkannten Entitäten
        2. Teilstring-Match für mehrteilige Entitäten
        3. Token-Vektor-Similarität für Synonyme (E-Scooter ↔ Elektroroller)
           – nur wenn de_core_news_lg geladen ist
    """
    combined = f"{result.title} {result.snippet}".lower()
    result_text = f"{result.title} {result.snippet}"

    # Signal 1: Keyword-Overlap (ohne Stoppwörter)
    claim_words = set(re.findall(r"\b[a-zäöüA-ZÄÖÜ]{4,}\b", claim_text.lower()))
    claim_words -= _RELEVANCE_STOPWORDS
    if claim_words:
        kw_matches = sum(1 for w in claim_words if w in combined)
        kw_score = min(1.0, kw_matches / len(claim_words))
    else:
        kw_score = 0.0

    # Signal 2: NER-basierter Entity-Overlap (spaCy + Synonyme, Fallback regex)
    ner_score = entity_overlap_score(claim_text, result_text)
    # Fallback: regex-basierter Entity-Overlap wenn NER 0 liefert
    regex_entity_score = _entity_overlap(claim_text, result_text)
    entity_score = max(ner_score, regex_entity_score)

    # Signal 3: Off-topic Penalty (URL + Inhalt wenn Profil vorhanden)
    offtopic_penalty = 0.0
    if _is_low_trust_site(result.url, result.title, result.snippet):
        offtopic_penalty = 0.75
    elif _is_generic_reference(result.url, profile):
        offtopic_penalty = 0.6
    elif _is_offtopic_url(result.url):
        offtopic_penalty = 0.6
    elif profile:
        _, content_penalty = _is_offtopic_content(result.title, result.snippet, profile)
        offtopic_penalty = max(offtopic_penalty, content_penalty)
    # Schwache Überschneidung + generischer Titel → Penalty
    if kw_score < 0.2 and entity_score < 0.2:
        offtopic_penalty = max(offtopic_penalty, 0.4)

    _sw = scoring_weights().get("relevance_score", {})
    if ce_score is not None and profile:
        # Cross-Encoder verfügbar + Profil → stärkstes Scoring
        anchor_score = _profile_anchor_score(combined, profile)
        _wp = _sw.get("with_profile_ce", _sw.get("with_profile", {}))
        score = (
            kw_score * _wp.get("keyword", 0.10)
            + entity_score * _wp.get("entity", 0.20)
            + anchor_score * _wp.get("anchor", 0.25)
            + ce_score * _wp.get("cross_encoder", 0.30)
            + (1.0 - offtopic_penalty) * _wp.get("offtopic", 0.15)
        )
        total_anchors = _count_active_anchors(profile)
        if total_anchors >= 3:
            anchor_hits = _count_anchor_hits(combined, profile)
            if anchor_hits <= 1 and entity_score < 0.20 and ce_score < 0.40:
                score *= 0.4
    elif ce_score is not None:
        # Cross-Encoder ohne Profil
        _np = _sw.get("no_profile_ce", _sw.get("no_profile", {}))
        score = (
            kw_score * _np.get("keyword", 0.15)
            + entity_score * _np.get("entity", 0.25)
            + ce_score * _np.get("cross_encoder", 0.40)
            + (1.0 - offtopic_penalty) * _np.get("offtopic", 0.20)
        )
    elif profile:
        # Kein Cross-Encoder, aber Profil
        anchor_score = _profile_anchor_score(combined, profile)
        _wp = _sw.get("with_profile", {})
        score = (
            kw_score * _wp.get("keyword", 0.15)
            + entity_score * _wp.get("entity", 0.30)
            + anchor_score * _wp.get("anchor", 0.35)
            + (1.0 - offtopic_penalty) * _wp.get("offtopic", 0.20)
        )
        total_anchors = _count_active_anchors(profile)
        if total_anchors >= 3:
            anchor_hits = _count_anchor_hits(combined, profile)
            if anchor_hits <= 1 and entity_score < 0.20:
                score *= 0.4
    else:
        # Kein Cross-Encoder, kein Profil
        _np = _sw.get("no_profile", {})
        score = (
            kw_score * _np.get("keyword", 0.25)
            + entity_score * _np.get("entity", 0.50)
            + (1.0 - offtopic_penalty) * _np.get("offtopic", 0.25)
        )

    return min(1.0, max(0.0, score))


def _extract_best_excerpt(content: str, claim_text: str, max_chars: int = 800) -> str:
    """Extrahiere die relevanteste Passage statt stumpf content[:800].

    Strategie: Absätze scoren nach Entitäts- und Keyword-Overlap,
    dann die besten Absätze bis max_chars zusammenfügen.
    """
    if not content:
        return ""

    # Wenn Content kurz genug, direkt verwenden
    if len(content) <= max_chars:
        return content.strip()

    # Absätze splitten (an Doppel-Newlines oder Einzeln-Newlines bei langen Texten)
    paragraphs = [p.strip() for p in re.split(r"\n\n+|\n(?=[A-ZÄÖÜ])", content) if len(p.strip()) >= 30]
    if not paragraphs:
        return content[:max_chars].strip()

    # Phase 5: Cross-Encoder-basiertes Paragraph-Scoring wenn verfügbar
    from tools.reranker import _get_model as _get_ce_model
    ce_model = _get_ce_model()
    if ce_model is not None and len(paragraphs) <= 50:
        try:
            pairs = [(claim_text, para) for para in paragraphs]
            raw_scores = ce_model.predict(pairs, show_progress_bar=False)
            try:
                import numpy as np
                normalized = 1.0 / (1.0 + np.exp(-raw_scores))
            except ImportError:
                import math
                normalized = [1.0 / (1.0 + math.exp(-float(s))) for s in raw_scores]
            scored: list[tuple[float, str]] = list(zip(normalized, paragraphs))
        except Exception:
            ce_model = None  # Fallback auf lexikalisches Scoring

    if ce_model is None or len(paragraphs) > 50:
        claim_lower = claim_text.lower()
        claim_words = set(re.findall(r"\b[a-zäöü]{4,}\b", claim_lower)) - _RELEVANCE_STOPWORDS
        claim_entities = _extract_entities(claim_text)

        scored = []
        for para in paragraphs:
            para_lower = para.lower()
            if claim_words:
                kw_hits = sum(1 for w in claim_words if w in para_lower)
                kw_score = kw_hits / len(claim_words)
            else:
                kw_score = 0.0
            if claim_entities:
                ent_hits = sum(1 for e in claim_entities if e.lower() in para_lower)
                ent_score = ent_hits / len(claim_entities)
            else:
                ent_score = 0.0

            _ew = scoring_weights().get("excerpt_extraction", {})
            total = kw_score * _ew.get("keyword", 0.4) + ent_score * _ew.get("entity", 0.6)
            scored.append((total, para))

    # Sortiere nach Score, wähle Top-Absätze
    scored.sort(key=lambda x: -x[0])
    selected: list[str] = []
    total_len = 0
    for score, para in scored:
        if total_len + len(para) + 2 > max_chars:
            break
        selected.append(para)
        total_len += len(para) + 2

    if not selected:
        # Kein guter Absatz → erster Absatz, abgeschnitten
        return paragraphs[0][:max_chars].strip()

    return " … ".join(selected)


def _cluster_by_perspective(
    items: list[EvidenceItem],
    target: int = 8,
) -> list[EvidenceItem]:
    """Round-Robin-Selektion aus Perspektiv-Clustern für Evidenz-Diversität.

    Stellt sicher, dass verschiedene Quelltypen vertreten sind:
    - Faktenchecker (höchste Priorität bei Fake-Claims)
    - Offizielle Quellen (Tier 1-2)
    - Widerlegende Quellen
    - Bestätigende Quellen
    - Qualitätsjournalismus (Tier 3)
    - Sonstige

    Items behalten ihre ursprüngliche Reihenfolge innerhalb jedes Clusters.
    """
    if len(items) <= target:
        return items

    clusters: dict[str, list[EvidenceItem]] = {
        "fact_check": [],
        "official": [],
        "refuting": [],
        "supporting": [],
        "journalism": [],
        "other": [],
    }

    for item in items:
        tier = item.source.domain_tier
        direction = item.source_direction

        if item.source.is_fact_check_org or tier == 4:
            clusters["fact_check"].append(item)
        elif tier <= 2:
            clusters["official"].append(item)
        elif direction == SourceDirection.REFUTES:
            clusters["refuting"].append(item)
        elif direction == SourceDirection.SUPPORTS:
            clusters["supporting"].append(item)
        elif tier == 3:
            clusters["journalism"].append(item)
        else:
            clusters["other"].append(item)

    # Round-Robin: Faktenchecker zuerst, dann offizielle Quellen, etc.
    priority_order = ["fact_check", "official", "refuting", "supporting", "journalism", "other"]
    selected: list[EvidenceItem] = []
    seen_urls: set[str] = set()

    # Erste Runde: je 1 Item pro Cluster
    for key in priority_order:
        for item in clusters[key]:
            if item.source.url not in seen_urls and len(selected) < target:
                selected.append(item)
                seen_urls.add(item.source.url)
                break

    # Restliche Plätze auffüllen (Round-Robin)
    round_idx = 1
    while len(selected) < target:
        added = False
        for key in priority_order:
            if round_idx < len(clusters[key]):
                item = clusters[key][round_idx]
                if item.source.url not in seen_urls:
                    selected.append(item)
                    seen_urls.add(item.source.url)
                    added = True
                    if len(selected) >= target:
                        break
        if not added:
            break
        round_idx += 1

    return selected


def _rank_evidence_items(
    items: list[EvidenceItem],
    claim_text: str,
    google_matches: list[GoogleFactCheckMatch],
    profile: ClaimSearchProfile | None = None,
    is_current_state: bool = False,
    ce_scores: dict[str, float] | None = None,
) -> list[EvidenceItem]:
    """Ranke bestehende EvidenceItems neu – behalte alle Metadaten.

    Eingang: Bereits angereicherte EvidenceItems mit:
        - publication_date (extrahiert aus Quellen)
        - evidence_type (DIRECT/CONTEXTUAL/WEAK)
        - claim_scope_score (profile-basiert berechnet)
        - extraction_confidence (0.8/0.6/0.3 je Quelle)

    Ausgang: Gleiche Items, aber neu sortiert nach:

    Ranking-Kriterien:
        1. Domain-Tier             (0.25 Gewicht)
        2. Claim-Relevanz          (0.30 Gewicht)
        3. Profile-Anchor-Score    (0.15 Gewicht, nur wenn Profil vorhanden)
        4. Faktenchecker-Bonus     (0.15)
        5. GFC-Match-Bonus         (0.10)
        6. Off-topic Penalty       (0.05 + content-basiert)

    Filtering (Items werden entfernt):
        - URL-basierte Off-topics mit rel < 0.3
        - Inhaltliche Off-topics (Institution + Ort fehlen) mit rel < 0.25
        - Tier-5-Treffer mit rel < 0.10 ohne FC-Status

    WICHTIG: Alle Metadaten außer relevance_score bleiben unverändert.
    """
    fact_check_domains = {_extract_domain(m.url) for m in google_matches}

    ranked: list[tuple[float, EvidenceItem]] = []
    for item in items:
        # Extrahiere URL/Titel/Snippet aus dem bestehenden Item
        url = item.source.url
        title = item.source.title
        snippet = item.excerpt

        tier = _domain_tier(url)
        _ce = ce_scores.get(url) if ce_scores else None
        rel = _relevance_score(
            SearchResult(title=title, url=url, snippet=snippet),
            claim_text,
            profile,
            ce_score=_ce,
        )
        is_fc = _is_fact_check_org(url)
        has_gfc_match = _extract_domain(url) in fact_check_domains

        # ── Off-topic Detection ──────────────────────────────────
        # 0. Low-Trust-Seitentyp: fast immer verwerfen (Währungsrechner, Grammatik etc.)
        is_low_trust = _is_low_trust_site(url, title, snippet)
        if is_low_trust and not is_fc and not has_gfc_match:
            # Low-Trust-Seiten nur durchlassen wenn außergewöhnlich relevant
            if rel < 0.50:
                continue

        # 1. URL-basiert: klar irrelevante Domains
        if _is_offtopic_url(url) and rel < 0.3:
            continue

        # 2. Inhalt-basiert: Hauptentitäten fehlen komplett
        content_offtopic = False
        content_penalty = 0.0
        if profile:
            content_offtopic, content_penalty = _is_offtopic_content(
                title, snippet, profile
            )
            # Erhöhte Discard-Schwelle: 0.30 statt 0.25 – fängt mehr Randtreffer ab
            if content_offtopic and rel < 0.30 and not is_fc and not has_gfc_match:
                continue  # Klar off-topic: verwerfen

        # 3. Generisch: Tier-5 ohne Relevanz
        if rel < 0.10 and tier == 5 and not is_fc and not has_gfc_match:
            continue

        # ── Penalty-Berechnung ───────────────────────────────────
        offtopic_penalty = 0.0
        if is_low_trust:
            offtopic_penalty = 0.7  # Low-Trust: starke Abwertung
        elif _is_offtopic_url(url):
            offtopic_penalty = 0.4
        elif content_offtopic:
            offtopic_penalty = max(offtopic_penalty, content_penalty)
        elif rel < 0.15 and tier >= 4:
            offtopic_penalty = 0.2

        # ── Profil-Anchor-Score (wenn Profil vorhanden) ──────────
        anchor_bonus = 0.0
        if profile:
            combined = f"{title} {snippet}".lower()
            raw_anchor = _profile_anchor_score(combined, profile)
            # Bei Regelungsclaims (Sanktion/Policy + Institution) höheres Anchor-Gewicht
            is_regulatory_profile = bool(profile.sanction_terms) or (
                bool(profile.policy_terms) and bool(profile.institutions)
            )
            anchor_weight = 0.22 if is_regulatory_profile else 0.15
            anchor_bonus = raw_anchor * anchor_weight

        # ── Multi-Signal Ranking-Score (Gewichte aus data/scoring_weights.yaml) ──
        _rw = scoring_weights().get("ranking", {})
        score = (
            (5 - tier) / 4 * _rw.get("tier", 0.25)
            + rel * _rw.get("relevance", 0.30)
            + anchor_bonus
            + (_rw.get("factcheck", 0.15) if is_fc else 0)
            + (_rw.get("gfc", 0.10) if has_gfc_match else 0)
            + (1.0 - offtopic_penalty) * _rw.get("offtopic", 0.05)
        )

        # Current-state Claims: Nachrichten-/Behördenquellen (Tier 1-3) boosten,
        # allgemeine Hintergrundseiten leicht abwerten – frische Primärquellen
        # sollen alte Kontextseiten im Ranking überholen.
        if is_current_state:
            if tier <= 3:
                score += 0.12
            else:
                score -= 0.05

        # ── Metadaten erhalten, nur relevance_score aktualisieren ──
        updated_item = item.model_copy(
            update={"relevance_score": rel}
        )
        ranked.append((score, updated_item))

    ranked.sort(key=lambda x: -x[0])
    return [item for _, item in ranked]


def _detect_contradictions(items: list[EvidenceItem]) -> list[EvidenceContradiction]:
    """Einfache Widerspruchserkennung: suche nach expliziten Verneinungspaaren.

    Implementiert eine heuristische Prüfung auf Basis von Schlüsselwörtern.
    Für tiefere Analyse kann der VerdictAgent zusätzlich prüfen.
    """
    contradictions: list[EvidenceContradiction] = []
    negation_words = {
        "nicht", "kein", "keine", "falsch", "unwahr", "widerlegt",
        "falschaussage", "fehler", "irrtum", "gegenteil",
        "not", "false", "incorrect", "wrong", "debunked",
    }

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.source.url == b.source.url:
                continue
            words_a = set(a.excerpt.lower().split())
            words_b = set(b.excerpt.lower().split())
            # Heuristik: Ein Item enthält Verneinung, das andere nicht
            neg_in_a = bool(words_a & negation_words)
            neg_in_b = bool(words_b & negation_words)
            if neg_in_a != neg_in_b and a.relevance_score > 0.3 and b.relevance_score > 0.3:
                contradictions.append(EvidenceContradiction(
                    source_url_a=a.source.url,
                    source_url_b=b.source.url,
                    description=(
                        f"Potentieller Widerspruch: Quelle A {'enthält' if neg_in_a else 'enthält keine'} "
                        f"Verneinung, Quelle B {'enthält' if neg_in_b else 'enthält keine'} Verneinung"
                    ),
                ))
                if len(contradictions) >= 3:
                    return contradictions

    return contradictions


# ── Direktionales Quellen-Signal ──────────────────────────────────────────────

# Widerlegungsmuster (generisch, kein Claim-Bezug)
_REFUTATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p) for p in [
        r"\bnicht\s+(?:korrekt|richtig|wahr|zutreffend|belegt|nachgewiesen|bestätigt)\b",
        r"\bwiderlegt\b",
        r"\bunwahr\b",
        r"\bist\s+falsch\b",
        r"\bsind\s+falsch\b",
        r"\bunzutreffend\b",
        r"\bstimmt\s+nicht\b",
        r"\btrifft\s+nicht\s+zu\b",
        r"\bkeine?\s+(?:belege?|nachweise?)\b",
        r"\bnicht\s+belegt\b",
        r"\bnicht\s+bestätigt\b",
        r"\bkein\s+(?:beleg|nachweis)\b",
        r"\b(?:was|were|has been|have been)\s+(?:refuted|debunked)\b",
        r"\bdebunked\b",
        r"\brefuted\b",
        r"\bincorrect\b",
    ]
]

# Bestätigungsmuster (generisch, kein Claim-Bezug)
_CONFIRMATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p) for p in [
        r"\bbestätigt\b",
        r"\bbelegt\b",
        r"\bnachgewiesen\b",
        r"\btrifft\s+zu\b",
        r"\bist\s+korrekt\b",
        r"\bist\s+richtig\b",
        r"\btatsächlich\b",
        r"\bwie\s+behauptet\b",
        r"\bin\s+der\s+tat\b",
        r"\bist\s+wahr\b",
        r"\bwurde\s+festgestellt\b",
        r"\b(?:has been|have been)\s+confirmed\b",
        r"\bconfirmed\b",
        r"\bverified\b",
    ]
]


def _classify_source_direction(
    excerpt: str,
    relevance_score: float,
    evidence_type: EvidenceType,
    is_low_trust: bool,
) -> SourceDirection:
    """Bestimme die generische Aussagebeziehung einer Quelle zum Claim.

    Keine claim-spezifischen Regeln oder hartcodierten Behauptungen.
    Schwache, unzuverlässige und off-topic Quellen werden nie als
    SUPPORTS oder REFUTES klassifiziert.

    Hierarchie:
        1. Niedrige Relevanz → OFFTOPIC
        2. Low-Trust oder WEAK evidence → NEUTRAL
        3. Textanalyse auf Widerlegungs-/Bestätigungsmuster
        4. Nur DIRECT oder hochrelevantes CONTEXTUAL erhält SUPPORTS/REFUTES
    """
    # 1. Off-topic: Zu schwacher Claim-Bezug für eine Richtungsaussage
    if relevance_score < 0.20:
        return SourceDirection.OFFTOPIC

    # 2. Strukturell unzuverlässige Quellen können keine verlässliche Richtung liefern
    if is_low_trust or evidence_type == EvidenceType.WEAK:
        return SourceDirection.NEUTRAL

    # 3. Textuelles Richtungssignal – nur wenn Excerpt vorhanden
    if not excerpt:
        return SourceDirection.NEUTRAL

    text = excerpt.lower()
    refute_count = sum(1 for p in _REFUTATION_PATTERNS if p.search(text))
    support_count = sum(1 for p in _CONFIRMATION_PATTERNS if p.search(text))

    # 4. Richtung nur für DIRECT oder relevantes CONTEXTUAL vergeben
    # CONTEXTUAL mit niedriger Relevanz → NEUTRAL (verhindert "Support Leakage")
    if evidence_type != EvidenceType.DIRECT and relevance_score < 0.40:
        return SourceDirection.NEUTRAL

    if refute_count > support_count and refute_count >= 1:
        return SourceDirection.REFUTES
    if support_count > refute_count and support_count >= 1:
        return SourceDirection.SUPPORTS

    return SourceDirection.NEUTRAL


def _direction_weight(item: EvidenceItem) -> float:
    """Gewicht eines Items für die gewichtete Konsensberechnung.

    Low-Trust- und OFFTOPIC/NEUTRAL-Items tragen nicht zur Richtungsbestimmung bei.
    Tier und EvidenceType skalieren das Gewicht.
    """
    if item.source_direction in (SourceDirection.OFFTOPIC, SourceDirection.NEUTRAL):
        return 0.0
    if _is_low_trust_site(item.source.url, item.source.title, ""):
        return 0.0
    # Tier-Gewicht: Tier 1 → 1.0, Tier 5 → 0.2
    tier_weight = max(0.2, (6 - item.source.domain_tier) / 5.0)
    ev_weight = {
        EvidenceType.DIRECT: 1.0,
        EvidenceType.CONTEXTUAL: 0.5,
        EvidenceType.WEAK: 0.0,
    }
    return tier_weight * ev_weight.get(item.evidence_type, 0.0)


def _compute_quality_signals(
    items: list[EvidenceItem],
    google_matches: list[GoogleFactCheckMatch],
    low_trust_penalty_factor: float = 0.20,
    stale_threshold: float = 0.35,
    stale_penalty_factor: float = 0.15,
    is_current_state: bool = False,
) -> EvidenceQualitySignals:
    """Berechne Qualitätssignale für das Evidence-Set."""
    # ── Granulare Primary-Source-Signale ──────────────────────────────────────
    # has_primary_any: irgendeine Tier-1/2-Quelle vorhanden (schwaches Signal)
    has_primary_any = any(i.source.domain_tier <= 2 for i in items)
    # has_primary_direct: Tier-1/2-Quelle mit direktem Claim-Bezug (starkes Signal)
    # Bedingung: evidence_type=DIRECT und claim_scope_score >= 0.50
    # Allgemeine Behördenseiten ohne spezifischen Claim-Bezug zählen NICHT.
    _qw_scope = scoring_weights().get("quality_signals", {})
    _primary_direct_scope = _qw_scope.get("primary_direct_scope", 0.50)
    has_primary_direct = any(
        i.source.domain_tier <= 2
        and i.evidence_type == EvidenceType.DIRECT
        and i.claim_scope_score >= _primary_direct_scope
        for i in items
    )

    # ── Granulare Faktenchecker-Signale ───────────────────────────────────────
    # has_fc_any: irgendein Faktenchecker-Ergebnis vorhanden (schwaches Signal)
    has_fc_any = bool(google_matches) or any(i.source.is_fact_check_org for i in items)
    # has_fc_direct: Faktenchecker mit direktem Claim-Bezug (starkes Signal)
    # GFC-Matches gelten immer als direkt (API liefert Claim-spezifische Treffer).
    # Faktenchecker-Orgs nur wenn evidence_type=DIRECT (kein allgemeiner Hintergrundartikel).
    has_fc_direct = bool(google_matches) or any(
        i.source.is_fact_check_org and i.evidence_type == EvidenceType.DIRECT
        for i in items
    )

    # Rückwärtskompatible Aliase (hat_primary_sources, has_fact_check_org_result)
    has_primary = has_primary_any
    has_fc = has_fc_any

    top_tier_count = sum(1 for i in items if i.source.domain_tier <= 2)

    # Gewichteter Konsens über source_direction-Signale.
    # Low-Trust- und OFFTOPIC/NEUTRAL-Items tragen nicht bei (_direction_weight = 0).
    # Damit wird fehlende Bestätigung klar von echter Widerlegung getrennt.
    weighted_support = sum(_direction_weight(i) for i in items if i.source_direction == SourceDirection.SUPPORTS)
    weighted_refute = sum(_direction_weight(i) for i in items if i.source_direction == SourceDirection.REFUTES)
    total_signal = weighted_support + weighted_refute

    if total_signal == 0:
        consensus = SourceConsensus.INSUFFICIENT
    elif weighted_refute == 0:
        consensus = SourceConsensus.AGREEING
    elif weighted_support == 0:
        consensus = SourceConsensus.CONTRADICTORY
    else:
        support_ratio = weighted_support / total_signal
        if support_ratio >= 0.70:
            consensus = SourceConsensus.AGREEING
        elif support_ratio <= 0.30:
            consensus = SourceConsensus.CONTRADICTORY
        else:
            consensus = SourceConsensus.MIXED

    # Off-topic-Rate: Anteil schwach-relevanter Treffer in den Top-5
    # Threshold 0.30 (statt 0.20): Generische Treffer, die nur einen einzelnen
    # Keyword-Match haben (z.B. Wikipedia "Stadtrat"), entwischen sonst der Erkennung.
    top_5 = items[:5]
    if top_5:
        offtopic_count = sum(1 for i in top_5 if i.relevance_score < 0.30)
        off_topic_rate = offtopic_count / len(top_5)
    else:
        off_topic_rate = 0.0

    # Low-Trust-Rate: Anteil strukturell ungeeigneter Quellen in den Top-5
    if top_5:
        low_trust_count = sum(
            1 for i in top_5
            if _is_low_trust_site(i.source.url, i.source.title, "")
        )
        low_trust_rate = low_trust_count / len(top_5)
    else:
        low_trust_rate = 0.0

    # Echte Freshness-Berechnung (Durchschnitt der Top-Quellen)
    freshness_scores = [
        _compute_freshness(i.source.publication_date)
        for i in items[:6]
        if i.source.publication_date
    ]
    # Bei current-state Claims: Quellen ohne Datum konservativ mit 0.3 werten (statt 0.5 neutral),
    # da fehlende Datumsinfo bei zeitkritischen Claims ein Warnsignal ist.
    unknown_date_default = 0.3 if is_current_state else 0.5
    # 0.0 wenn keine Items (nicht neutral) – verhindert künstliche overall_quality-Inflation
    freshness = sum(freshness_scores) / len(freshness_scores) if freshness_scores else (unknown_date_default if items else 0.0)

    # Relevanz-Qualität: wie relevant sind die Top-Treffer?
    top_relevance = [i.relevance_score for i in items[:5]]
    avg_relevance = sum(top_relevance) / len(top_relevance) if top_relevance else 0.0

    # Gewichte aus data/scoring_weights.yaml
    _qw = scoring_weights().get("quality_signals", {})

    # Off-topic-Penalty in overall_quality einrechnen
    offtopic_penalty = off_topic_rate * _qw.get("offtopic_penalty", 0.15)
    # Low-Trust-Penalty: deckelt Confidence wenn überwiegend unzuverlässige Quellen
    low_trust_penalty = low_trust_rate * low_trust_penalty_factor
    # Freshness: bei current-state Claims stärker gewichten
    freshness_weight = _qw.get("freshness_current_state", 0.25) if is_current_state else _qw.get("freshness_normal", 0.15)
    freshness_term = freshness * freshness_weight if items else 0.0
    # Stale-Penalty: wenn alle Quellen veraltet sind (avg_freshness < stale_threshold)
    stale_penalty = stale_penalty_factor if (items and freshness < stale_threshold) else 0.0

    # Konsens-Bonus: klares Richtungssignal aus vertrauenswürdigen Quellen erhöht Qualität
    consensus_clarity_bonus = (
        _qw.get("consensus_bonus", 0.05)
        if consensus in (SourceConsensus.AGREEING, SourceConsensus.CONTRADICTORY)
        and total_signal >= 0.5
        else 0.0
    )

    # Primary-Source-Beitrag
    primary_contribution = (
        _qw.get("primary_direct", 0.25) if has_primary_direct
        else _qw.get("primary_any", 0.10) if has_primary_any
        else 0.0
    )
    # Faktenchecker-Beitrag
    fc_contribution = (
        _qw.get("fc_direct", 0.25) if has_fc_direct
        else _qw.get("fc_any", 0.10) if has_fc_any
        else 0.0
    )

    overall = (
        min(1.0, top_tier_count / 3) * _qw.get("top_tier_count", 0.30)
        + primary_contribution
        + fc_contribution
        + avg_relevance * _qw.get("avg_relevance", 0.10)
        + freshness_term
        + consensus_clarity_bonus
        - offtopic_penalty
        - low_trust_penalty
        - stale_penalty
    )
    overall = max(0.0, min(1.0, overall))

    # Evidence-Type-Statistiken für Top-5
    direct_count = sum(
        1 for i in top_5
        if getattr(i, "evidence_type", None) == EvidenceType.DIRECT
    )
    contextual_or_weak = sum(
        1 for i in top_5
        if getattr(i, "evidence_type", None) in (EvidenceType.CONTEXTUAL, EvidenceType.WEAK)
    )
    contextual_only_rate = contextual_or_weak / len(top_5) if top_5 else 0.0

    # Contextual-only-Penalty in overall_quality einrechnen
    if contextual_only_rate > 0.6 and direct_count == 0:
        overall = max(0.0, overall - _qw.get("contextual_only_penalty", 0.10))

    # Aktive direkte Widerlegung: Quellen die BOTH REFUTES + DIRECT sind.
    # Unterscheidet "Claim nicht belegt" (fehlendes Stützungssignal) von
    # "Claim aktiv durch direkte Quelle widerlegt" (echtes Widerlegungssignal).
    direct_refutation_count = sum(
        1 for i in top_5
        if (
            getattr(i, "source_direction", None) == SourceDirection.REFUTES
            and getattr(i, "evidence_type", None) == EvidenceType.DIRECT
        )
    )
    has_direct_refutation = direct_refutation_count > 0

    return EvidenceQualitySignals(
        # Granulare Signale (neue Felder)
        has_primary_source_any=has_primary_any,
        has_primary_direct_evidence=has_primary_direct,
        has_fact_check_any=has_fc_any,
        has_fact_check_direct_match=has_fc_direct,
        # Rückwärtskompatible Aliase
        has_primary_sources=has_primary_any,
        has_fact_check_org_result=has_fc_any,
        source_consensus=consensus,
        freshness_score=freshness,
        overall_quality=overall,
        top_tier_count=top_tier_count,
        off_topic_rate=off_topic_rate,
        avg_top5_relevance=avg_relevance,
        low_trust_rate=low_trust_rate,
        direct_evidence_count=direct_count,
        contextual_only_rate=contextual_only_rate,
        has_direct_refutation=has_direct_refutation,
        direct_refutation_count=direct_refutation_count,
    )


# ── Claim-Scope-Score + Evidence-Typing ──────────────────────────────────────


def _compute_claim_scope_score(
    excerpt: str,
    profile: ClaimSearchProfile | None,
) -> float:
    """Berechne wie genau eine Quelle die konkreten Claim-Details abdeckt.

    Prüft 5 Dimensionen (gewichtet):
        - institution_match   (0.25) – z.B. "Stadtrat Hannover"
        - location_match      (0.20) – z.B. "Hannover"
        - policy_context      (0.20) – z.B. "15-Minuten-Stadt", "rechtlich bindend"
        - action_regulation   (0.20) – z.B. "beschlossen", "Sitzung"
        - sanction_number     (0.15) – z.B. "250 Euro Bußgeld"

    Ohne Profil → 0.5 (neutral, kein Signal).
    Allgemeiner Konzeptbezug ohne konkrete Maßnahme → niedriger Score.
    """
    if not profile:
        return 0.5

    combined = excerpt.lower()

    dimensions: list[tuple[list[str], float]] = [
        (profile.institutions, 0.25),
        (profile.locations, 0.20),
        (profile.policy_terms, 0.20),
        (profile.action_terms if hasattr(profile, "action_terms") else [], 0.20),
        (profile.sanction_terms, 0.15),
    ]

    total_weight = 0.0
    hit_weight = 0.0
    for terms, weight in dimensions:
        if not terms:
            continue
        total_weight += weight
        if any(t.lower() in combined for t in terms if t):
            hit_weight += weight

    if total_weight == 0:
        return 0.5

    return hit_weight / total_weight


def _classify_evidence_type(
    item_relevance: float,
    claim_scope: float,
    domain_tier: int,
    is_fact_check: bool,
    is_low_trust: bool,
    min_direct_scope: float = 0.60,
) -> "EvidenceType":
    """Klassifiziere einen Treffer als DIRECT, CONTEXTUAL oder WEAK.

    DIRECT: Hoher claim_scope_score + relevance → belegt den konkreten Claim.
    CONTEXTUAL: Mittlerer Scope → erklärt Hintergrund, belegt nicht Details.
    WEAK: Niedriger Scope oder Low-Trust → kaum verwertbar.
    """
    from models.evidence_models import EvidenceType

    if is_low_trust:
        return EvidenceType.WEAK

    # Fact-Checker mit ausreichender Relevanz → immer DIRECT
    if is_fact_check and item_relevance >= 0.30:
        return EvidenceType.DIRECT

    # Offizielle Quellen (Tier 1-2) brauchen denselben Scope-Threshold wie andere Quellen.
    # Eine allgemeine Behörden-/Statistikseite ohne direkten Claim-Bezug darf nicht als
    # DIRECT gewertet werden, nur weil die Domain vertrauenswürdig ist.
    if domain_tier <= 2 and claim_scope >= min_direct_scope:
        return EvidenceType.DIRECT

    # Hoher Scope + ausreichende Relevanz → DIRECT
    if claim_scope >= min_direct_scope and item_relevance >= 0.35:
        return EvidenceType.DIRECT

    # Mittlerer Scope oder anständige Relevanz → CONTEXTUAL
    if claim_scope >= 0.30 or item_relevance >= 0.25:
        return EvidenceType.CONTEXTUAL

    return EvidenceType.WEAK


# ── Adaptive LangSearch Query Count ──────────────────────────────────────────


def _langsearch_query_count(claim: "Claim", cfg: EvidenceRetrievalConfig) -> int:
    """Bestimme adaptive LangSearch-Query-Anzahl basierend auf Claim-Komplexität.

    Einfache FACTUAL Claims → cfg.langsearch_queries_simple  (Default: 2)
    Komplexe/statistische/politische Claims → cfg.langsearch_queries_complex (Default: 4)

    Komplexitätssignale (kein Hardcoding auf einzelne Wörter):
        - Claim-Typ: STATISTICAL, CAUSAL, CONTEXTUAL
        - Claim-Länge: > 100 Zeichen
        - Reichhaltiges SearchProfile: ≥3 Institutionen/Policy-Terms
    """
    from models.schemas import ClaimType
    _COMPLEX_TYPES = {ClaimType.STATISTICAL, ClaimType.CAUSAL, ClaimType.CONTEXTUAL}

    is_complex_type = hasattr(claim, "type") and claim.type in _COMPLEX_TYPES
    is_long = len(claim.text) > 100
    has_rich_profile = (
        isinstance(claim, ProcessedClaim)
        and claim.search_profile is not None
        and (
            len(claim.search_profile.institutions)
            + len(claim.search_profile.policy_terms)
        ) >= 3
    )
    if is_complex_type or is_long or has_rich_profile:
        return cfg.langsearch_queries_complex
    return cfg.langsearch_queries_simple


# ── EvidenceBuilderAgent ──────────────────────────────────────────────────────

class EvidenceBuilderAgent(BaseAgent):
    """Baut ein strukturiertes EvidencePack für einen Claim auf.

    Ablauf:
        1. Query-Optimierung (LLM)
        2. Paralleles Retrieval: LangSearch + SearXNG
        3. Google Fact Check API (asynchron parallel)
        4. Deduplication + Ranking
        5. Scraping der Top-K Quellen
        6. Trust-Boundary: Extraktion auf max. 800 Zeichen begrenzen
        7. EvidencePack zusammenstellen
    """

    name = "Evidence Builder"
    emoji = "🔎"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Google Fact Check Client
        self._gfc_client = FactCheckDatabaseClient(
            config=FactCheckDatabaseConfig(
                google_factcheck_api_key=self.config.google_fact_check.api_key,
                enabled=self.config.google_fact_check.enabled,
            ),
            retry=self.config.retry,
        )

        # LangSearch Client (primär – semantische Hauptsuche)
        self._langsearch = LangSearchClient(
            config=self.config.langsearch,
            retry=self.config.retry,
        )

        # SearXNG Client (unterstützend – kostenlos, alle Queries, self-hosted)
        # Dedizierter Client: immer und ausschließlich SearXNG.
        # Kein Provider-Routing, keine Abhängigkeit von search.provider.
        self._searxng = SearXNGClient(
            config=self.config.searxng,
            retry=self.config.retry,
        )

    def _get_source_adapter(self, source_config: "SourceConfig"):  # type: ignore[name-defined]
        """Instanziiere oder hole Source Adapter via AdapterGuardian (mit Caching).

        AdapterGuardian handled:
        - Caching (24h default, 168h für statische Quellen)
        - Rate-Limiting (pro-Quelle Token-Bucket)
        - Circuit-Breaker (verhindert Cascade-Fehler)
        """
        if not hasattr(self, "_source_adapters"):
            self._source_adapters = {}

        if source_config.source_id not in self._source_adapters:
            try:
                from tools.sources.adapter_guardian import AdapterGuardian
                import tools.sources.clients as clients_module

                # Map source_id → adapter class
                adapter_class_map = {
                    "world_bank": clients_module.WorldBankClient,
                    "gleif": clients_module.GLEIFClient,
                    "openfda": clients_module.OpenFDAClient,
                    "openalex": clients_module.OpenAlexClient,
                    "arxiv": clients_module.ArXivClient,
                    "crossref": clients_module.CrossrefClient,
                    "cern_open_data": clients_module.CERNOpenDataClient,
                    "eurostat": clients_module.EurostatClient,
                    "eur_lex": clients_module.EURLexClient,
                    "uspto": clients_module.USPTOClient,
                    "companies_house": clients_module.CompaniesHouseClient,
                    "clinicaltrials": clients_module.ClinicalTrialsClient,
                    "dailymed": clients_module.DailyMedClient,
                    "pubmed": clients_module.PubMedClient,
                }

                adapter_class = adapter_class_map.get(source_config.source_id)
                if not adapter_class:
                    self._log(f"⚠ Source client nicht verfügbar: {source_config.source_id}")
                    return None

                adapter = adapter_class(source_config)
                self._source_adapters[source_config.source_id] = AdapterGuardian(adapter)
            except Exception as e:
                self._log(f"⚠ Source adapter init failed ({source_config.source_id}): {type(e).__name__}")
                return None

        return self._source_adapters[source_config.source_id]

    def _select_query_for_source(
        self,
        queries: list[str],
        route_result: "RouteResult",  # type: ignore[name-defined]
        source_config: "SourceConfig",  # type: ignore[name-defined]
    ) -> str | None:
        """Wähle beste Query für ein Source basierend auf Domänen-Match."""
        if not route_result.domains or not source_config.claim_domains:
            return queries[0] if queries else None

        # Finde Queries die zu Source-Domänen passen
        # (simplistically: prefer first query for now)
        return queries[0] if queries else None

    @staticmethod
    def _convert_official_evidence_to_search_results(
        source_results: dict[str, list]
    ) -> list[SearchResult]:
        """Konvertiere OfficialEvidenceItem-Lister zu SearchResult-Format.

        Source Clients liefern OfficialEvidenceItems mit title, url, excerpt, full_text.
        Diese müssen zu SearchResult konvertiert werden für Deduplication + Ranking.
        """
        results: list[SearchResult] = []

        for source_id, items in source_results.items():
            if not items:
                continue

            # items sind listen von dicts (OfficialEvidenceItem serialisiert)
            for item in items:
                try:
                    title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                    url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                    excerpt = item.get("excerpt", "") if isinstance(item, dict) else getattr(item, "excerpt", "")
                    full_text = item.get("full_text", "") if isinstance(item, dict) else getattr(item, "full_text", "")

                    result = SearchResult(
                        title=title,
                        url=url,
                        snippet=excerpt or "",
                        content=full_text or "",
                    )
                    results.append(result)
                except Exception:
                    continue  # Skip malformed items

        return results

    async def _build_source_client_tasks(
        self,
        claim: "Claim",  # type: ignore[name-defined]
        route_result: "RouteResult",  # type: ignore[name-defined]
        queries: list[str],
    ) -> tuple[list[tuple[str, Any]], dict[str, list]]:  # task list + results placeholder
        """Erstelle Source Client Tasks für hohe-confidence Routes.

        Returns:
            (task_list, results_map) wobei results_map ein dict für Ergebnisse ist
        """
        source_cfg = self.config.source_clients

        if not source_cfg.enabled:
            return [], {}

        # Nur wenn Routing-Konfidenz ausreichend ist
        if route_result.confidence < source_cfg.min_confidence:
            self._log(
                f"Source clients übersprungen (confidence: {route_result.confidence:.2f} < {source_cfg.min_confidence})"
            )
            return [], {}

        # Source-Tasks erstellen
        source_tasks: list[tuple[str, Any]] = []
        results_map: dict[str, list] = {}

        for source_config in route_result.sources[: source_cfg.max_sources_per_claim]:
            # Adapter instantiieren
            adapter = self._get_source_adapter(source_config)
            if not adapter:
                continue

            # Beste Query für diese Source
            query = self._select_query_for_source(queries, route_result, source_config)
            if not query:
                continue

            # Task erstellen
            try:
                task = adapter.search(query, max_results=source_cfg.max_results_per_source)
                source_tasks.append((source_config.source_id, task))
                results_map[source_config.source_id] = []
            except Exception as e:
                self._log(f"⚠ Source task creation failed ({source_config.source_id}): {type(e).__name__}")

        if source_tasks:
            self._log(
                f"Source clients aktiviert: {len(source_tasks)} Quellen "
                f"(Domains: {[d.value for d in route_result.domains]})"
            )

        return source_tasks, results_map

    def execute(self, input_data: Any, context: str = "") -> EvidencePack:
        """Synchrone Version – nutzt asyncio.run intern."""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                # In async context: neuen Task erstellen
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(_asyncio.run, self.execute_async(input_data, context))
                    return future.result()
            else:
                return loop.run_until_complete(self.execute_async(input_data, context))
        except RuntimeError:
            return _asyncio.run(self.execute_async(input_data, context))

    async def execute_async(self, input_data: Any, context: str = "") -> EvidencePack:
        """Async-Version – Retrieval läuft parallel.

        Retrieval-Rollen (klar getrennt, Priorität bei Fusion):
            LangSearch = semantische Hauptsuche (adaptiv 3–5 Queries, Dedup-Priorität)
            SearXNG    = unterstützende Breitensuche (alle Queries, kostenlos, self-hosted)
            Tavily     = budgetierter Content-/Discovery-Layer (1 Query primary, max 3/Claim)
            GFC        = strukturierter Shortcut-Layer (höchste Priorität)
        """
        claim: Claim = input_data
        notes: list[str] = []

        # Profil FRÜH extrahieren – wird für adaptives Retrieval + Pre-Scraping-Filter benötigt
        profile: ClaimSearchProfile | None = None
        if isinstance(claim, ProcessedClaim) and claim.search_profile:
            profile = claim.search_profile

        # ── 1. Query-Optimierung ──────────────────────────────────────────────
        queries = await self._build_queries_async(claim, context)
        notes.append(f"Queries: {queries}")

        # Unspezifik-Notiz: falls Qualitätssignale auf fehlende Spezifik hinweisen,
        # festhalten dass generische Query-Familien genutzt wurden und die
        # Verifizierbarkeit eingeschränkt sein kann.
        if isinstance(claim, ProcessedClaim):
            _underspec = {"underspecified_actor", "missing_artifact_evidence"}
            detected = _underspec & set(claim.quality_signals or [])
            if detected:
                signal_labels = ", ".join(sorted(detected))
                notes.append(
                    f"Unspezifik erkannt ({signal_labels}): Generische Query-Familien "
                    f"(direct/artifact/fact-check/official-response) verwendet. "
                    f"Claim ohne eindeutigen Akteur oder verifizierbares Artefakt – "
                    f"Verifizierung kann unvollständig bleiben."
                )

        # ── 2. Adaptives paralleles Retrieval ────────────────────────────────
        # Rollen: LangSearch = semantische Hauptsuche, Tavily = budgetiert/content-stark,
        #         SearXNG = breite kostenlose Ergänzung, GFC = Shortcut-Layer,
        #         SourceClients = hochwertige institutionelle Datenquellen (NEW)
        from agents.fact_checker import _categories_for_claim, _is_current_state_claim
        from tools.claim_router import ClaimRouter

        categories = _categories_for_claim(claim)
        retrieval_cfg = self.config.evidence_retrieval

        # Route claim für Source Client selection (NEW)
        route_result = None
        try:
            if isinstance(claim, ProcessedClaim):
                router = ClaimRouter()
                route_result, _ = router.route_and_apply(claim)
        except Exception as e:
            self._log(f"ClaimRouter error: {type(e).__name__}")

        # Recency-Override: Aktuell-Zustand-Claims (z.B. Amtsinhaber) brauchen frische Quellen
        is_current_state = _is_current_state_claim(claim.text)
        if is_current_state:
            _current_year = str(datetime.now(timezone.utc).year)
            news_cats = ",".join(retrieval_cfg.searxng_news_categories)
            if categories != news_cats:
                categories = news_cats
            # Alle Queries mit aktuellem Jahr anreichern (nicht nur erste 2)
            queries = [
                f"{q} {_current_year}" if _current_year not in q else q
                for q in queries
            ]
            # Ersten Query zusätzlich mit "aktuell" versehen (stärkstes Recency-Signal)
            if queries and "aktuell" not in queries[0].lower() and "current" not in queries[0].lower():
                queries[0] = f"{queries[0]} aktuell"
            notes.append(
                f"Recency-Override: News-Kategorien ({categories}), "
                f"Jahr {_current_year} + 'aktuell' ergänzt für Aktuell-Zustand-Claim"
            )

        # LangSearch: adaptiv nach Claim-Komplexität (2–4 Queries)
        ls_count = _langsearch_query_count(claim, retrieval_cfg)
        langsearch_queries = queries[:ls_count]

        # Parallele Tasks starten
        # SearXNG: Per-Query-Routing + Multi-Page (pageno=1 und pageno=2 für Top-Queries)
        # Faktencheck/Falschmeldung-Queries → News-Engines
        # Regulatory/Current-State-Claims → News-Engines + time_range
        # Top-2 Queries bekommen zusätzlich pageno=2 für mehr Tiefe
        searxng_queries: list[SearXNGQuery] = []
        for i, q in enumerate(queries):
            sq = SearXNGQuery(query=q, categories=categories)
            if "Faktencheck" in q or "Falschmeldung" in q:
                sq.engines = searxng_engines().get("news", ["duckduckgo", "brave", "tagesschau"])
            elif is_current_state:
                sq.engines = searxng_engines().get("news", ["duckduckgo", "brave", "tagesschau"])
                sq.time_range = retrieval_cfg.current_state_time_range
            else:
                sq.engines = searxng_engines().get("web", ["duckduckgo", "brave", "qwant"])
            searxng_queries.append(sq)
            # Multi-Page: Top-2 Queries auch auf Seite 2 suchen
            if i < 2:
                sq2 = SearXNGQuery(
                    query=q,
                    categories=categories,
                    engines=sq.engines,
                    time_range=sq.time_range,
                    pageno=2,
                )
                searxng_queries.append(sq2)
        searxng_task = self._searxng.multi_search_async(
            searxng_queries, max_results=self.config.searxng.max_results,
        )
        langsearch_task = self._langsearch.multi_search_async(
            langsearch_queries, max_results=self.config.langsearch.max_results,
        )
        gfc_task = self._gfc_client.search_async(claim.text)

        # ── Source Client Tasks (NEW) ──────────────────────────────────────────
        source_tasks = []
        source_results_map = {}
        if route_result:
            source_tasks, source_results_map = await self._build_source_client_tasks(
                claim, route_result, queries
            )

        # ── Parallele Ausführung aller Tasks ───────────────────────────────────
        gather_tasks = [searxng_task, langsearch_task, gfc_task]
        gather_tasks.extend([task for _, task in source_tasks])

        all_results = await asyncio.gather(*gather_tasks, return_exceptions=False)

        # ── Ergebnisse auspacken ──────────────────────────────────────────────
        searxng_results = all_results[0] if len(all_results) > 0 else {}
        langsearch_results = all_results[1] if len(all_results) > 1 else {}
        gfc_raw = all_results[2] if len(all_results) > 2 else []

        # Source Client Results (NEW)
        source_client_results = {}
        if source_tasks:
            for idx, (source_id, _) in enumerate(source_tasks):
                result_idx = 3 + idx
                if result_idx < len(all_results):
                    source_client_results[source_id] = all_results[result_idx] or []

        # ── 3. LangSearch-Retry bei schwacher erster Evidenz ─────────────────
        # LangSearch ist die primäre semantische Suchquelle – bei schwacher
        # erster Runde wird ZUERST LangSearch erweitert, nicht Tavily.
        if (
            retrieval_cfg.langsearch_retry_on_weak
            and self.config.langsearch.enabled
            and ls_count < len(queries)
        ):
            ls_scores = [
                _relevance_score(r, claim.text, profile)
                for q_res in langsearch_results.values()
                for r in q_res
            ]
            avg_ls = sum(ls_scores) / len(ls_scores) if ls_scores else 0.0
            if avg_ls < retrieval_cfg.weak_evidence_threshold:
                extra_queries = queries[ls_count:ls_count + 2]
                if extra_queries:
                    extra = await self._langsearch.multi_search_async(
                        extra_queries, max_results=self.config.langsearch.max_results,
                    )
                    langsearch_results.update({f"retry_{q}": v for q, v in extra.items()})
                    notes.append(
                        f"LangSearch-Retry: avg_relevance={avg_ls:.2f} < "
                        f"{retrieval_cfg.weak_evidence_threshold}, "
                        f"{len(extra_queries)} zusätzliche Queries"
                    )

        # ── 4. Ergebnisse zusammenführen + deduplizieren ─────────────────────
        # Reihenfolge: LangSearch (semantisch) → SearXNG (breit) → SourceClients (strukturiert)
        # _dedup_results() behält erstes Vorkommen → LangSearch hat Dedup-Priorität
        all_results: list[SearchResult] = []
        langsearch_count = sum(len(v) for v in langsearch_results.values())
        searxng_count = sum(len(v) for v in searxng_results.values())
        source_clients_count = 0

        for q_res in langsearch_results.values():
            all_results.extend(q_res)
        for q_res in searxng_results.values():
            all_results.extend(q_res)

        # Source Client Results (NEW) – konvertieren + hinzufügen
        if source_client_results:
            source_search_results = self._convert_official_evidence_to_search_results(
                source_client_results
            )
            source_clients_count = len(source_search_results)
            all_results.extend(source_search_results)

        unique_results = _dedup_results(all_results)

        # ── Cross-Encoder Re-Ranking (Phase 2) ─────────────────────────────
        ce_scores: dict[str, float] = {}
        if reranker_available():
            ce_ranked = rerank(claim.text, unique_results, top_k=30)
            ce_scores = {r.url: float(s) for r, s in ce_ranked}
            notes.append(f"Cross-Encoder: {len(ce_scores)} Ergebnisse bewertet")

        # Logging mit Source Client Info (NEW)
        retrieval_log = (
            f"Retrieval: {len(all_results)} Treffer → {len(unique_results)} unique "
            f"(LangSearch: {langsearch_count} semantisch [{ls_count} Queries], "
            f"SearXNG: {searxng_count} breit"
        )
        if source_clients_count > 0:
            retrieval_log += f", SourceClients: {source_clients_count} strukturiert"
        retrieval_log += ")"
        notes.append(retrieval_log)

        # ── 6. Google Fact Check Matches aufbereiten ──────────────────────────
        gfc_matches = [
            GoogleFactCheckMatch(
                claim_reviewed=fc.claim_reviewed,
                rating=fc.rating,
                publisher=fc.publisher,
                url=fc.url,
                language=fc.language,
                title=fc.title,
            )
            for fc in gfc_raw
        ]
        if gfc_matches:
            notes.append(f"Google Fact Check: {len(gfc_matches)} Treffer")

        # ── 7. Candidate Selection + Scraping ────────────────────────────────
        # Profile + Low-Trust-Signale fließen VOR dem Scraping ein:
        # Nur die wirklich besten Kandidaten werden gescraped.
        ranked, scraped = await self._rank_and_scrape(
            unique_results, claim,
            profile=profile,
        )

        # ── 8. EvidenceItems aus gescrapten Quellen bauen ─────────────────────
        evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches, profile, is_current_state=is_current_state, ce_scores=ce_scores)
        notes.append(f"Evidence Items: {len(evidence_items)} (mit Scraping)")

        # ── 9. Qualität, Widersprüche, Pack zusammenstellen ───────────────────
        contradictions = _detect_contradictions(evidence_items[:6])
        quality = _compute_quality_signals(
            evidence_items, gfc_matches,
            low_trust_penalty_factor=retrieval_cfg.low_trust_confidence_penalty,
            stale_threshold=retrieval_cfg.stale_sources_freshness_threshold,
            stale_penalty_factor=retrieval_cfg.stale_sources_confidence_penalty,
            is_current_state=is_current_state,
        )

        # ── Iterative Search: Qualitäts-basierter Retry mit Relevanz-Feedback ──
        iterative_round = 0
        while (
            retrieval_cfg.iterative_search_enabled
            and iterative_round < retrieval_cfg.iterative_max_rounds
            and quality.overall_quality < retrieval_cfg.iterative_min_quality
        ):
            iterative_round += 1

            # Feedback-Terme aus Top-Ergebnissen extrahieren
            top_results = unique_results[:10]
            feedback_terms = extract_feedback_terms(claim.text, top_results)

            if feedback_terms:
                # Verfeinerte Queries generieren
                refined_queries = generate_refinement_queries(
                    claim.text, feedback_terms,
                    max_queries=retrieval_cfg.iterative_max_refinement_queries,
                )
                notes.append(
                    f"Iterative Runde {iterative_round}: Feedback-Terme={feedback_terms[:3]}, "
                    f"Queries={len(refined_queries)}"
                )
            else:
                # Kein Feedback → klassischer Fallback
                notes.append(f"Iterative Runde {iterative_round}: Kein Feedback, Fallback-Suche")
                refined_queries = []

            # Fallback + ggf. Refinement-Queries ausführen
            fallback_results = await self._fallback_retrieval(claim, queries)
            if refined_queries:
                # Refinement-Queries über SearXNG
                from tools.web_search import SearXNGQuery
                refinement_sq = [SearXNGQuery(query=q) for q in refined_queries]
                refinement_results = await self._searxng.multi_search_async(refinement_sq)
                for results_list in refinement_results.values():
                    fallback_results.extend(results_list)

            unique_results = _dedup_results(all_results + fallback_results)
            # Re-rank mit Cross-Encoder (inkl. neue Ergebnisse)
            if reranker_available():
                ce_ranked = rerank(claim.text, unique_results, top_k=30)
                ce_scores = {r.url: float(s) for r, s in ce_ranked}
            ranked, scraped = await self._rank_and_scrape(
                unique_results, claim, profile=profile,
            )
            evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches, profile, is_current_state=is_current_state, ce_scores=ce_scores)
            quality = _compute_quality_signals(
                evidence_items, gfc_matches,
                low_trust_penalty_factor=retrieval_cfg.low_trust_confidence_penalty,
                stale_threshold=retrieval_cfg.stale_sources_freshness_threshold,
                stale_penalty_factor=retrieval_cfg.stale_sources_confidence_penalty,
                is_current_state=is_current_state,
            )
            notes.append(f"Iterative Runde {iterative_round}: {len(evidence_items)} Items, Qualität={quality.overall_quality:.2f}")

        selected_sources = [item.source for item in evidence_items[:5]]

        canonical_text = ""
        if isinstance(claim, ProcessedClaim):
            canonical_text = claim.canonical_text

        pack = EvidencePack(
            claim_id=claim.id,
            claim_text=claim.text,
            canonical_text=canonical_text,
            queries_used=queries,
            google_fact_check_matches=gfc_matches,
            web_results=evidence_items,
            selected_sources=selected_sources,
            contradictions=contradictions,
            extraction_confidence=quality.overall_quality,
            evidence_quality=quality,
            retrieval_notes=notes,
            source_count=len(unique_results),
        )

        self._log(
            f"Claim {claim.id}: {len(evidence_items)} Items, "
            f"Qualität={quality.overall_quality:.2f}, "
            f"{len(gfc_matches)} GFC-Treffer"
        )
        return pack

    async def _build_queries_async(self, claim: Claim, context: str) -> list[str]:
        """Queries aufbauen: Profile-basiert → QueryExpansionEngine → erweitert zu 6–8.

        Priorität:
            1. QueryExpansionEngine (wenn enabled) – 6–8 diverse Queries
            2. Fallback: Profile-basierte Queries (3–4)
            3. LLM nur wenn Profil weniger als 3 Queries liefert

        QueryExpansionEngine nutzt ClaimRouter für domain/jurisdiction-aware Expansion.
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()

        from agents.fact_checker import _build_search_queries, _optimize_queries_with_llm
        from tools.claim_router import ClaimRouter
        from tools.query_expansion import QueryExpansionEngine

        # Phase 1: Profile-basierte Queries (wie zuvor)
        profile_queries = _build_search_queries(claim, context)

        # Phase 2: QueryExpansionEngine (NEW – wenn Claim strukturiert und enabled)
        if self.config.evidence_retrieval.query_expansion_enabled:
            try:
                # Nur wenn claim eine ProcessedClaim mit SearchProfile ist
                if isinstance(claim, ProcessedClaim) and claim.search_profile:
                    # Route claim für Domain/Jurisdiction-aware expansion
                    router = ClaimRouter()
                    route_result, _ = router.route_and_apply(claim)

                    # Expand queries via QueryExpansionEngine
                    expander = QueryExpansionEngine()
                    query_variants = expander.expand(claim, route_result, claim.search_profile)

                    # Extrahiere Text aus Variants (6–8 diverse Queries)
                    expanded_queries = [v.text for v in query_variants]

                    if expanded_queries:
                        self._log(
                            f"QueryExpansionEngine: {len(expanded_queries)} diverse Queries "
                            f"(domains: {[d.value for d in route_result.domains]}, "
                            f"jurisdiction: {route_result.jurisdiction})"
                        )
                        # Return expanded queries (6–8 instead of 4)
                        return expanded_queries
            except Exception as e:
                # Fallback zu Profile-Queries wenn Expansion fehlschlägt
                self._log(f"QueryExpansionEngine Fehler, fallback zu Profile-Queries: {type(e).__name__}")

        # Fallback Phase 3: Profile-Queries (wie zuvor)
        if len(profile_queries) >= 3:
            return profile_queries[:4]

        # Profil unvollständig (kein Frame/Profil) → LLM als Ergänzung
        llm_queries = await loop.run_in_executor(
            None, _optimize_queries_with_llm, claim, self.llm, context
        )
        if llm_queries:
            # Merge: Profile-Queries zuerst, dann eindeutige LLM-Queries anhängen
            seen: set[str] = set(profile_queries)
            for q in llm_queries:
                if q and q not in seen:
                    profile_queries.append(q)
                    seen.add(q)

        return profile_queries[:4] if profile_queries else [claim.text]

    async def _rank_and_scrape(
        self,
        results: list[SearchResult],
        claim: Claim,
        profile: ClaimSearchProfile | None = None,
    ) -> tuple[list[RankedSource], list[ScrapedSource]]:
        """Ranke und scrape – Profile/Low-Trust-Filter greifen VOR dem Scraping.

        Ablauf:
            1. Low-Trust-Seiten und klar off-topic Kandidaten entfernen (Pre-Filter)
            2. rank_sources() auf gefilterten Kandidaten
            3. Eigentliches Scraping der Top-K Kandidaten
        """
        retrieval_cfg = self.config.evidence_retrieval

        # ── 1. Pre-Scraping-Filter ────────────────────────────────────────────
        # Low-Trust-Seiten und klar off-topic Kandidaten BEVOR rank_sources() entfernen.
        # Fact-Checker sind immer durchgelassen (tier 4 Bonus bleibt erhalten).
        filtered: list[SearchResult] = []
        removed = 0
        for r in results:
            # Low-Trust-Seitentypen (Grammatik, Währungsrechner, Juraforen …)
            if _is_low_trust_site(r.url, r.title, r.snippet) and not _is_fact_check_org(r.url):
                removed += 1
                continue
            # Profil-basierte Off-topic-Filterung: nur bei starker Penalty + geringer Relevanz
            if profile:
                is_ot, penalty = _is_offtopic_content(r.title, r.snippet, profile)
                if (
                    is_ot
                    and penalty >= retrieval_cfg.pre_scrape_offtopic_penalty
                    and _relevance_score(r, claim.text, profile) < 0.25
                    and not _is_fact_check_org(r.url)
                ):
                    removed += 1
                    continue
            filtered.append(r)

        if removed:
            self._log(f"Pre-Scraping-Filter: {removed}/{len(results)} Low-Trust/Off-topic Kandidaten entfernt")

        # ── 2. Ranking (hybrid: BM25 + semantic + profile + low-trust) ─────
        results_by_query: dict[str, list[SearchResult]] = {"_all": filtered}
        ranked = rank_sources(
            results_by_query,
            claim.text,
            max_scrape=self.config.searxng.scrape_top_n,
            profile=profile,
        )

        # ── 3. LangSearch-Content-Skip ────────────────────────────────────────
        # LangSearch liefert Summaries – bei ausreichendem Content kein Scraping nötig.
        content_skipped = 0
        for rs in ranked:
            if not rs.should_scrape or not rs.result.content:
                continue
            if len(rs.result.content) > 300:
                ls_rel = _relevance_score(rs.result, claim.text, profile)
                if ls_rel > 0.30:
                    rs.should_scrape = False
                    rs.skip_reason = "langsearch_content_sufficient"
                    content_skipped += 1
        if content_skipped:
            self._log(f"LangSearch-Content ausreichend: {content_skipped} Scraping-Requests eingespart")

        scrape_count = sum(1 for rs in ranked if rs.should_scrape)
        self._log(f"Scraping {scrape_count} von {len(ranked)} Quellen...")

        scraped = await scrape_sources(
            ranked, claim.text,
            max_concurrent=self.config.searxng.max_concurrent_searches,
            timeout=self.config.searxng.scrape_timeout,
        )
        success = sum(1 for s in scraped if s.fetch_success)
        self._log(f"Scraping: {success}/{len(scraped)} erfolgreich")
        return ranked, scraped

    def _build_evidence_items(
        self,
        ranked: list[RankedSource],
        scraped: list[ScrapedSource],
        claim_text: str,
        gfc_matches: list[GoogleFactCheckMatch],
        profile: ClaimSearchProfile | None = None,
        is_current_state: bool = False,
        ce_scores: dict[str, float] | None = None,
    ) -> list[EvidenceItem]:
        """Baue EvidenceItems aus gescrapten Quellen.

        Trust Boundary: Nur strukturierte Excerpts (max. 800 Zeichen) werden
        in EvidenceItems übernommen – kein roher HTML-Inhalt.

        Profil wird durchgereicht für strukturiertes Anchor-Ranking.
        publication_date wird aus gescrapten Quellen in EvidenceSource übernommen.
        """
        scraped_by_url = {s.url: s for s in scraped}
        items: list[EvidenceItem] = []

        for rs in ranked:
            sc = scraped_by_url.get(rs.result.url)
            url = rs.result.url
            tier = _domain_tier(url)
            domain = _extract_domain(url)

            # Excerpt-Priorisierung:
            # 1. Gescrapte Passage (höchste Qualität)
            # 2. Tavily/LangSearch-Volltext aus SearchResult.content (gut strukturiert)
            # 3. Snippet (Fallback)
            if sc and sc.fetch_success and sc.passage:
                raw_excerpt = sc.passage
                extraction_conf = 0.8
            elif rs.result.content:
                raw_excerpt = rs.result.content
                extraction_conf = 0.6
            else:
                raw_excerpt = rs.result.snippet
                extraction_conf = 0.3

            # Trust Boundary: relevante Passage statt stumpfem Cutoff
            excerpt = _extract_best_excerpt(raw_excerpt, claim_text, max_chars=800) if raw_excerpt else ""

            # publication_date aus gescraptem Inhalt übernehmen
            pub_date = ""
            if sc and sc.fetch_success:
                pub_date = getattr(sc, "publication_date", "") or ""

            source = EvidenceSource(
                url=url,
                title=rs.result.title,
                domain=domain,
                domain_tier=tier,
                is_fact_check_org=_is_fact_check_org(url),
                is_primary_source=(tier <= 2),
                publication_date=pub_date,
            )

            # Evidence-Typing: claim_scope_score + evidence_type
            rel_score = _relevance_score(rs.result, claim_text, profile)
            scope_text = excerpt if excerpt else f"{rs.result.title} {rs.result.snippet}"
            scope_score = _compute_claim_scope_score(scope_text, profile)
            is_low_trust = _is_low_trust_site(url, rs.result.title, rs.result.snippet)
            ev_type = _classify_evidence_type(
                item_relevance=rel_score,
                claim_scope=scope_score,
                domain_tier=tier,
                is_fact_check=_is_fact_check_org(url),
                is_low_trust=is_low_trust,
                min_direct_scope=self.config.evidence_retrieval.claim_scope_min_direct,
            )

            direction = _classify_source_direction(
                excerpt=excerpt,
                relevance_score=rel_score,
                evidence_type=ev_type,
                is_low_trust=is_low_trust,
            )
            # supports_claim wird aus source_direction abgeleitet (Rückwärtskompatibilität)
            supports_claim_derived: bool | None = (
                True if direction == SourceDirection.SUPPORTS
                else False if direction == SourceDirection.REFUTES
                else None
            )

            item = EvidenceItem(
                source=source,
                excerpt=excerpt,
                relevance_score=rel_score,
                extraction_confidence=extraction_conf,
                supports_claim=supports_claim_derived,
                source_direction=direction,
                evidence_type=ev_type,
                claim_scope_score=scope_score,
            )
            items.append(item)

        # Nach Ranking-Score sortieren (Tier + Relevanz + Profil-Anker + Off-topic)
        # Direktes Ranking auf EvidenceItems – Metadaten bleiben erhalten
        items = _rank_evidence_items(
            items,
            claim_text,
            gfc_matches,
            profile=profile,
            is_current_state=is_current_state,
            ce_scores=ce_scores,
        )

        # Phase 6: Perspektiv-Clustering für Evidenz-Diversität
        items = _cluster_by_perspective(items, target=8)

        return items

    async def _fallback_retrieval(
        self,
        claim: Claim,
        original_queries: list[str],
    ) -> list[SearchResult]:
        """Fallback-Suche mit alternativen Queries – LangSearch + SearXNG + ggf. Tavily.

        Priorität im Fallback:
            1. LangSearch (semantisch stark, immer aktiv wenn enabled)
            2. SearXNG (breit, kostenlos)
            3. Tavily nur wenn: Budget verfügbar UND Evidenzqualität niedrig

        Tavily wird im Fallback budgetiert: max. 1 zusätzlicher Request.
        """
        from agents.fact_checker import _build_fallback_queries
        fallback_queries = _build_fallback_queries(claim, original_queries)
        if not fallback_queries:
            return []

        # LangSearch und SearXNG parallel starten
        tasks: list = [
            self._searxng.multi_search_async(
                fallback_queries, max_results=self.config.searxng.max_results,
            )
        ]
        if self.config.langsearch.enabled:
            tasks.append(
                self._langsearch.multi_search_async(
                    fallback_queries[:2],
                    max_results=self.config.langsearch.max_results,
                )
            )

        result_maps = await asyncio.gather(*tasks, return_exceptions=True)
        combined: list[SearchResult] = []
        for rm in result_maps:
            if isinstance(rm, dict):
                combined.extend(r for results in rm.values() for r in results)
        return combined
