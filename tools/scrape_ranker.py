"""Ranking- und Passage-Extraktions-Logik für die Source Scraping Pipeline.

Bestimmt, welche Suchergebnisse gescraped werden sollen und extrahiert
relevante Passagen aus Artikeltexten. Keine Abhängigkeiten zu Agents oder LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from tools.source_classifier import SourceTier, classify_source
from tools.web_search import SearchResult


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


# ── Dataclass ────────────────────────────────────────────────────

@dataclass
class RankedSource:
    result: SearchResult
    tier: SourceTier
    relevance_score: float      # 0.0–1.0, Keyword-Overlap mit Claim
    should_scrape: bool         # Finale Entscheidung
    skip_reason: str | None     # "irrelevant" | "duplicate" | "paywall" | "low_tier" | None


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


# ── Hauptfunktion ────────────────────────────────────────────────

def rank_sources(
    results_by_query: dict[str, list[SearchResult]],
    claim_text: str,
    max_scrape: int = 5,
) -> list[RankedSource]:
    """Ranke und filtere Suchergebnisse für das Scraping.

    Args:
        results_by_query: Suchergebnisse gruppiert nach Query.
        claim_text: Der zu prüfende Claim-Text.
        max_scrape: Maximale Anzahl zu scrapender Quellen.

    Returns:
        Liste aller RankedSource-Objekte, sortiert nach tier desc, relevance desc.
    """
    keywords = _extract_claim_keywords(claim_text)

    # 1. Alle SearchResults flach sammeln
    all_results: list[SearchResult] = []
    for results in results_by_query.values():
        all_results.extend(results)

    # 2. Domain-Deduplizierung: behalte pro Domain das Ergebnis mit höherem Overlap
    seen_domains: dict[str, tuple[SearchResult, float]] = {}
    for result in all_results:
        domain = _extract_domain(result.url)
        score = _keyword_overlap(keywords, result.snippet)
        if domain not in seen_domains or score > seen_domains[domain][1]:
            seen_domains[domain] = (result, score)

    # 3 + 4. Klassifizierung und Relevanz-Score
    ranked: list[RankedSource] = []
    for domain, (result, _) in seen_domains.items():
        classified = classify_source(result)
        relevance = _keyword_overlap(keywords, result.snippet)

        ranked.append(RankedSource(
            result=result,
            tier=classified.tier,
            relevance_score=relevance,
            should_scrape=False,  # wird in Schritt 5 gesetzt
            skip_reason=None,
        ))

    # 5. Scrape-Entscheidung
    for rs in ranked:
        domain = _extract_domain(rs.result.url)

        if domain in KNOWN_PAYWALLS:
            rs.should_scrape = False
            rs.skip_reason = "paywall"
        elif rs.tier <= SourceTier.USER_GENERATED:
            rs.should_scrape = False
            rs.skip_reason = "low_tier"
        elif rs.relevance_score < 0.15 and rs.tier < SourceTier.FACT_CHECKER:
            rs.should_scrape = False
            rs.skip_reason = "irrelevant"
        elif rs.tier >= SourceTier.FACT_CHECKER:
            rs.should_scrape = True
            rs.skip_reason = None
        elif rs.tier == SourceTier.QUALITY_JOURNALISM and rs.relevance_score >= 0.20:
            rs.should_scrape = True
            rs.skip_reason = None
        elif rs.tier == SourceTier.QUALITY_JOURNALISM and rs.relevance_score < 0.20:
            rs.should_scrape = False
            rs.skip_reason = "irrelevant"
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
            # Harte Paywalls bleiben immer gesperrt
            if domain in KNOWN_PAYWALLS:
                continue
            if domain in SOFT_PAYWALLS:
                rs.should_scrape = True
                rs.skip_reason = None
            elif rs.tier == SourceTier.MEDIA and rs.relevance_score >= 0.15:
                rs.should_scrape = True
                rs.skip_reason = None

    # 7. Limit auf max_scrape
    to_scrape = sorted(
        [rs for rs in ranked if rs.should_scrape],
        key=lambda rs: (rs.tier, rs.relevance_score),
        reverse=True,
    )
    for rs in to_scrape[max_scrape:]:
        rs.should_scrape = False
        rs.skip_reason = "limit_reached"

    # 8. Sortiert zurückgeben
    ranked.sort(key=lambda rs: (rs.tier, rs.relevance_score), reverse=True)
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
