"""Ranking- und Passage-Extraktions-Logik für die Source Scraping Pipeline.

Bestimmt, welche Suchergebnisse gescraped werden sollen und extrahiert
relevante Passagen aus Artikeltexten. Keine Abhängigkeiten zu Agents oder LLM.

Hybrid-Ranking (inspiriert von arxiv-sanity-preserver TF-IDF + SearXNG BM25):
    - Lexikalisches BM25-artiges Scoring
    - Semantisches Profil-Anchor-Scoring (wenn ClaimSearchProfile vorhanden)
    - Source-Tier / Authority-Bonus
    - Low-Trust-Penalty
    - Tavily-Content-Bonus (wenn content verfügbar)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from tools.source_classifier import SourceTier, classify_source
from tools.web_search import SearchResult

if TYPE_CHECKING:
    from models.schemas import ClaimSearchProfile


# ── Stoppwörter (konsistent mit fact_checker.py) ─────────────────

STOPWORDS: set[str] = {
    "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
    "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
    "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
    "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
    "gegen", "damit", "dabei", "dafür", "darin", "darauf", "davon",
    "denen", "deren", "zeigen", "zeigt", "laut", "mehr", "sehr",
    "andere", "anderen", "anderer", "wieder", "bereits", "dabei",
}

# Domains mit bekannten harten Paywalls
KNOWN_PAYWALLS: set[str] = {
    "bild.de", "welt.de", "faz.net", "handelsblatt.com",
    "nytimes.com", "washingtonpost.com", "economist.com",
    "ft.com", "wsj.com",
}

# Domains mit teilweiser Paywall — werden nur als Fallback gescraped
SOFT_PAYWALLS: set[str] = {
    "spiegel.de", "zeit.de", "sz.de", "sueddeutsche.de",
}

# Low-Trust Domains (re-used from evidence_builder, leichtgewichtig repliziert
# um Agent-Abhängigkeit zu vermeiden)
_LOW_TRUST_DOMAINS: frozenset[str] = frozenset({
    "xe.com", "x-rates.com", "oanda.com", "wise.com", "transferwise.com",
    "duden.de", "verbformen.de", "verbformen.com", "konjugator.de",
    "linguee.de", "linguee.com", "deepl.com", "dict.cc", "pons.com",
    "juraforum.de", "anwalt.de", "123recht.de", "frag-einen-anwalt.de",
    "gutefrage.net", "wer-weiss-was.de", "helpster.de",
    "bussgeldkatalog.de", "bussgeldkatalog.org", "bussgeldrechner.de",
})


# ── Dataclass ────────────────────────────────────────────────────

@dataclass
class RankedSource:
    result: SearchResult
    tier: SourceTier
    relevance_score: float      # 0.0–1.0, Hybrid-Score (BM25 + semantic + profile)
    should_scrape: bool         # Finale Entscheidung
    skip_reason: str | None     # "irrelevant" | "duplicate" | "paywall" | "low_tier" | "low_trust" | None
    hybrid_score: float = 0.0   # Gesamtscore aus allen Signalen


# ── Hilfsfunktionen ─────────────────────────────────────────────

def _extract_claim_keywords(claim_text: str) -> set[str]:
    """Zerlegt den Claim-Text in relevante Schlüsselwörter."""
    tokens = re.findall(r"[A-ZÄÖÜa-zäöüß]{4,}", claim_text.lower())
    return {w for w in tokens if w not in STOPWORDS}


def _keyword_overlap(keywords: set[str], text: str) -> float:
    """Berechne Anteil der keywords, die in text vorkommen."""
    if not keywords:
        return 0.0
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw in text_lower)
    return matched / len(keywords)


def _extract_domain(url: str) -> str:
    """Extrahiere Domain aus URL, ohne www.-Prefix."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _is_low_trust_domain(domain: str) -> bool:
    """Prüfe ob Domain ein bekannter Low-Trust-Seitentyp ist."""
    if domain in _LOW_TRUST_DOMAINS:
        return True
    return any(lt in domain for lt in _LOW_TRUST_DOMAINS)


# ── BM25-artiges Scoring ────────────────────────────────────────

def _bm25_score(
    keywords: set[str],
    text: str,
    k1: float = 1.5,
    b: float = 0.75,
    avg_dl: float = 80.0,
) -> float:
    """Vereinfachtes BM25-Scoring (inspiriert von SearXNG-WebSearch-AI).

    Berechnet einen normalisierten Relevanz-Score basierend auf
    Term-Frequency mit Sättigungs- und Dokumentlängen-Normalisierung.

    Args:
        keywords: Claim-Schlüsselwörter.
        text: Zu scorender Text (Titel + Snippet).
        k1: TF-Sättigungsparameter.
        b: Dokumentlängen-Normalisierung.
        avg_dl: Angenommene durchschnittliche Dokumentlänge.
    """
    if not keywords or not text:
        return 0.0

    text_lower = text.lower()
    tokens = re.findall(r"[a-zäöüß]{3,}", text_lower)
    dl = len(tokens)
    if dl == 0:
        return 0.0

    score = 0.0
    for kw in keywords:
        tf = tokens.count(kw) if len(kw) >= 4 else text_lower.count(kw)
        if tf == 0:
            continue
        # IDF-Approximation: alle Terms sind gleich wichtig (kein Korpus verfügbar)
        idf = 1.0
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * dl / avg_dl)
        score += idf * numerator / denominator

    # Normalisierung auf 0–1 Bereich
    max_possible = len(keywords) * (k1 + 1) / (1 + k1 * (1 - b + b * dl / avg_dl))
    return min(1.0, score / max_possible) if max_possible > 0 else 0.0


def _profile_anchor_fit(
    text: str,
    profile: "ClaimSearchProfile",
) -> float:
    """Strukturierter Profil-Anker-Fit für Pre-Scrape-Ranking.

    Prüft wie gut text die strukturierten Anker des ClaimSearchProfile trifft.
    Gewichtet: institution(0.30), location(0.25), policy(0.20),
    number(0.15), sanction(0.10).
    """
    combined = text.lower()

    groups: list[tuple[list[str], float]] = [
        (profile.institutions, 0.30),
        (profile.locations, 0.25),
        (profile.policy_terms, 0.20),
        (profile.number_terms, 0.15),
        (profile.sanction_terms, 0.10),
    ]

    total_weight = 0.0
    hit_weight = 0.0
    for terms, weight in groups:
        if not terms:
            continue
        total_weight += weight
        if any(t.lower() in combined for t in terms if t):
            hit_weight += weight

    return hit_weight / total_weight if total_weight > 0 else 0.0


def _hybrid_relevance_score(
    result: SearchResult,
    claim_text: str,
    keywords: set[str],
    profile: "ClaimSearchProfile | None" = None,
) -> float:
    """Hybrides Pre-Scrape-Relevanz-Scoring.

    Kombiniert (inspiriert von arxiv-sanity TF-IDF + SearXNG BM25+Semantic):
        - BM25-artiges lexikalisches Scoring (0.35)
        - Keyword-Overlap (0.20)
        - Entity/Zahlen-Match (0.15)
        - Profil-Anchor-Fit (0.30, nur wenn Profil vorhanden)

    Ohne Profil werden BM25 und Keyword-Overlap stärker gewichtet.
    """
    text = f"{result.title} {result.snippet}"
    content_text = f"{text} {result.content[:300]}" if result.content else text

    bm25 = _bm25_score(keywords, content_text)
    kw_overlap = _keyword_overlap(keywords, text)

    # Entity/Zahlen-Match
    claim_numbers = set(re.findall(r"\d+[\.,]?\d*", claim_text))
    text_lower = text.lower()
    if claim_numbers:
        num_match = sum(1 for n in claim_numbers if n in text_lower) / len(claim_numbers)
    else:
        num_match = 0.0

    if profile:
        anchor_fit = _profile_anchor_fit(content_text, profile)
        score = (
            bm25 * 0.25
            + kw_overlap * 0.15
            + num_match * 0.10
            + anchor_fit * 0.30
            + (0.20 if result.content and len(result.content) > 200 else 0.0)  # Content-Bonus
        )
    else:
        score = (
            bm25 * 0.40
            + kw_overlap * 0.30
            + num_match * 0.15
            + (0.15 if result.content and len(result.content) > 200 else 0.0)
        )

    return min(1.0, max(0.0, score))


# ── Hauptfunktion ────────────────────────────────────────────────

def rank_sources(
    results_by_query: dict[str, list[SearchResult]],
    claim_text: str,
    max_scrape: int = 5,
    profile: "ClaimSearchProfile | None" = None,
) -> list[RankedSource]:
    """Ranke und filtere Suchergebnisse für das Scraping.

    Hybrides Pre-Scrape-Ranking:
        1. BM25 + Keyword-Overlap + Profil-Anchor-Fit → hybrid_score
        2. Source-Tier-Authority-Bonus
        3. Low-Trust-Penalty (vor dem Scraping!)
        4. Tavily-Content-Bonus
        5. Scrape-Entscheidung basierend auf hybrid_score

    Args:
        results_by_query: Suchergebnisse gruppiert nach Query.
        claim_text: Der zu prüfende Claim-Text.
        max_scrape: Maximale Anzahl zu scrapender Quellen.
        profile: Optional ClaimSearchProfile für strukturiertes Ranking.

    Returns:
        Liste aller RankedSource-Objekte, sortiert nach hybrid_score desc.
    """
    keywords = _extract_claim_keywords(claim_text)

    # 1. Alle SearchResults flach sammeln
    all_results: list[SearchResult] = []
    for results in results_by_query.values():
        all_results.extend(results)

    # 2. Domain-Deduplizierung: behalte pro Domain das Ergebnis mit höherem Score
    seen_domains: dict[str, tuple[SearchResult, float]] = {}
    for result in all_results:
        domain = _extract_domain(result.url)
        score = _hybrid_relevance_score(result, claim_text, keywords, profile)
        if domain not in seen_domains or score > seen_domains[domain][1]:
            seen_domains[domain] = (result, score)

    # 3. Klassifizierung + Hybrid-Scoring
    ranked: list[RankedSource] = []
    for domain, (result, hybrid_rel) in seen_domains.items():
        classified = classify_source(result)
        is_low_trust = _is_low_trust_domain(domain)

        # ── Authority-Bonus aus Source-Tier ─────────────────────────
        tier_bonus = {
            SourceTier.OFFICIAL: 0.20,
            SourceTier.FACT_CHECKER: 0.15,
            SourceTier.QUALITY_JOURNALISM: 0.10,
            SourceTier.MEDIA: 0.03,
        }.get(classified.tier, 0.0)

        # ── Low-Trust-Penalty ──────────────────────────────────────
        low_trust_penalty = 0.25 if is_low_trust else 0.0

        # ── Finaler hybrid_score ───────────────────────────────────
        hybrid_score = hybrid_rel + tier_bonus - low_trust_penalty
        hybrid_score = max(0.0, min(1.0, hybrid_score))

        ranked.append(RankedSource(
            result=result,
            tier=classified.tier,
            relevance_score=hybrid_rel,
            should_scrape=False,
            skip_reason=None,
            hybrid_score=hybrid_score,
        ))

    # 4. Sortierung nach hybrid_score (beste zuerst)
    ranked.sort(key=lambda rs: rs.hybrid_score, reverse=True)

    # 5. Scrape-Entscheidung (jetzt basierend auf hybrid_score)
    for rs in ranked:
        domain = _extract_domain(rs.result.url)

        if domain in KNOWN_PAYWALLS:
            rs.should_scrape = False
            rs.skip_reason = "paywall"
        elif _is_low_trust_domain(domain) and rs.hybrid_score < 0.35:
            rs.should_scrape = False
            rs.skip_reason = "low_trust"
        elif rs.tier <= SourceTier.USER_GENERATED:
            rs.should_scrape = False
            rs.skip_reason = "low_tier"
        elif rs.hybrid_score < 0.10 and rs.tier < SourceTier.FACT_CHECKER:
            rs.should_scrape = False
            rs.skip_reason = "irrelevant"
        elif rs.tier >= SourceTier.FACT_CHECKER:
            rs.should_scrape = True
            rs.skip_reason = None
        elif rs.tier == SourceTier.QUALITY_JOURNALISM and rs.hybrid_score >= 0.15:
            rs.should_scrape = True
            rs.skip_reason = None
        elif rs.tier == SourceTier.QUALITY_JOURNALISM and rs.hybrid_score < 0.15:
            rs.should_scrape = False
            rs.skip_reason = "irrelevant"
        elif rs.tier == SourceTier.MEDIA and rs.hybrid_score >= 0.20:
            rs.should_scrape = True
            rs.skip_reason = None
        elif rs.tier == SourceTier.MEDIA:
            rs.should_scrape = False
            rs.skip_reason = "low_tier"

    # 6. Soft-Paywall- und Media-Fallback
    scrape_count = sum(1 for rs in ranked if rs.should_scrape)
    if scrape_count < 2:
        for rs in ranked:
            if rs.should_scrape:
                continue
            domain = _extract_domain(rs.result.url)
            if domain in KNOWN_PAYWALLS:
                continue
            if domain in SOFT_PAYWALLS:
                rs.should_scrape = True
                rs.skip_reason = None
            elif rs.tier == SourceTier.MEDIA and rs.hybrid_score >= 0.12:
                rs.should_scrape = True
                rs.skip_reason = None

    # 7. Limit auf max_scrape (nach hybrid_score sortiert)
    to_scrape = [rs for rs in ranked if rs.should_scrape]
    for rs in to_scrape[max_scrape:]:
        rs.should_scrape = False
        rs.skip_reason = "limit_reached"

    return ranked


# ── Passage-Extraktion ───────────────────────────────────────────

def extract_relevant_passages(
    article_text: str,
    claim_text: str,
    max_chars: int = 1500,
) -> tuple[str, bool]:
    """Extrahiere die relevantesten Passagen aus einem Artikeltext.

    Returns:
        (passage_text, low_relevance_flag)
        low_relevance_flag=True wenn kein Absatz einen Score > 0.1 hat.
    """
    # 1. Absätze extrahieren
    paragraphs = [p.strip() for p in article_text.split("\n\n") if len(p.strip()) >= 40]
    if len(paragraphs) < 2:
        return article_text[:max_chars], False

    # 2. Claim-Keywords und Claim-Zahlen
    keywords = _extract_claim_keywords(claim_text)
    claim_numbers = set(re.findall(r"\d+[\.,]?\d*", claim_text))

    # 3. Jeden Absatz scoren
    scored: list[tuple[int, str, float]] = []  # (original_index, text, score)
    for idx, para in enumerate(paragraphs):
        # Signal A — Keyword-Overlap (Gewicht 0.5)
        signal_a = _keyword_overlap(keywords, para)

        # Signal B — Zahlen-Match (Gewicht 0.3)
        if claim_numbers:
            para_numbers = set(re.findall(r"\d+[\.,]?\d*", para))
            signal_b = len(claim_numbers & para_numbers) / len(claim_numbers)
        else:
            signal_b = 0.0

        # Signal C — Positions-Bonus (Gewicht 0.2)
        signal_c = 1.0 - (idx / len(paragraphs)) * 0.5

        total = 0.5 * signal_a + 0.3 * signal_b + 0.2 * signal_c
        scored.append((idx, para, total))

    # 4. Top-Absätze auswählen
    max_score = max(s[2] for s in scored)
    if max_score < 0.1:
        return article_text[:500], True

    # Sortiere nach Score absteigend
    scored.sort(key=lambda s: s[2], reverse=True)

    # Wähle Absätze bis max_chars
    selected: list[tuple[int, str]] = []
    char_count = 0
    for idx, para, score in scored:
        if char_count + len(para) > max_chars:
            break
        selected.append((idx, para))
        char_count += len(para)

    # Zurück in Originalreihenfolge
    selected.sort(key=lambda s: s[0])

    return "\n\n".join(para for _, para in selected), False
