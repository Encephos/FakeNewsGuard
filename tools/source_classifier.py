"""Quellen-Klassifikation für Multi-Source Fact-Checking.

Klassifiziert Suchergebnis-URLs nach Glaubwürdigkeitsstufen und
berechnet einen Quellen-Konsens-Score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from urllib.parse import urlparse

from tools.web_search import SearchResult


class SourceTier(IntEnum):
    """Glaubwürdigkeitsstufen (höher = vertrauenswürdiger)."""
    UNKNOWN = 0
    USER_GENERATED = 1   # Blogs, Foren, Social Media
    MEDIA = 2            # Nachrichtenportale, Magazine
    QUALITY_JOURNALISM = 3  # Reuters, dpa, Tagesschau, SZ, Zeit
    FACT_CHECKER = 4     # Correctiv, Mimikama, dpa Faktencheck, Snopes
    OFFICIAL = 5         # Behörden, Statistikämter, Regierung


# Domain → Tier Mapping (Substring-Matching auf Hostname)
_TIER_PATTERNS: list[tuple[SourceTier, list[str]]] = [
    (SourceTier.OFFICIAL, [
        "destatis.de", "eurostat.ec.europa.eu", "ec.europa.eu/eurostat",
        "bamf.de", "bka.de", "bmi.bund.de", "bmj.de", "bundesregierung.de",
        "bundestag.de", "statistik-bw.de", "statistik.berlin-brandenburg.de",
        "who.int", "rki.de", "oecd.org", "worldbank.org", "imf.org",
        "cdc.gov", "nih.gov", "bpb.de",
    ]),
    (SourceTier.FACT_CHECKER, [
        "correctiv.org", "mimikama.org", "mimikama.at",
        "faktencheck.dpa.com", "dpa-factchecking.com",
        "faktenfinder.tagesschau.de", "snopes.com", "politifact.com",
        "factcheck.org", "fullfact.org", "leadstories.com",
        "checkyourfact.com", "reuters.com/fact-check",
        "apnews.com/hub/ap-fact-check",
    ]),
    (SourceTier.QUALITY_JOURNALISM, [
        "reuters.com", "apnews.com", "dpa.com",
        "tagesschau.de", "zdf.de", "deutschlandfunk.de", "ndr.de",
        "wdr.de", "br.de", "swr.de", "mdr.de", "rbb24.de",
        "zeit.de", "sueddeutsche.de", "spiegel.de", "faz.net",
        "handelsblatt.com", "tagesspiegel.de", "fr.de",
        "stern.de", "nzz.ch", "derstandard.at",
        "bbc.com", "bbc.co.uk", "theguardian.com", "nytimes.com",
        "washingtonpost.com", "economist.com",
    ]),
    (SourceTier.MEDIA, [
        "focus.de", "n-tv.de", "welt.de", "t-online.de",
        "rp-online.de", "merkur.de", "bild.de", "morgenpost.de",
        "ksta.de", "hna.de", "tz.de", "abendblatt.de",
        "news.de", "rtl.de", "sat1.de", "prosieben.de",
    ]),
    (SourceTier.USER_GENERATED, [
        "reddit.com", "twitter.com", "x.com", "facebook.com",
        "telegram.org", "t.me", "tiktok.com", "youtube.com",
        "medium.com", "substack.com", "wordpress.com", "blogspot.com",
    ]),
]


@dataclass
class ClassifiedSource:
    """Eine klassifizierte Quelle mit Glaubwürdigkeitsstufe."""
    url: str
    title: str
    snippet: str
    content: str
    tier: SourceTier
    tier_label: str
    domain: str


def classify_source(result: SearchResult) -> ClassifiedSource:
    """Klassifiziere eine einzelne Suchquelle nach Glaubwürdigkeit."""
    parsed = urlparse(result.url)
    domain = parsed.hostname or ""
    # www. entfernen für Matching
    if domain.startswith("www."):
        domain = domain[4:]

    tier = SourceTier.UNKNOWN
    for t, patterns in _TIER_PATTERNS:
        for pattern in patterns:
            if pattern in domain or domain.endswith(pattern):
                tier = t
                break
        if tier != SourceTier.UNKNOWN:
            break

    tier_labels = {
        SourceTier.OFFICIAL: "Offizielle Quelle",
        SourceTier.FACT_CHECKER: "Faktencheck-Organisation",
        SourceTier.QUALITY_JOURNALISM: "Qualitätsjournalismus",
        SourceTier.MEDIA: "Nachrichtenmedium",
        SourceTier.USER_GENERATED: "Nutzergeneriert",
        SourceTier.UNKNOWN: "Unbekannt",
    }

    return ClassifiedSource(
        url=result.url,
        title=result.title,
        snippet=result.snippet,
        content=result.content,
        tier=tier,
        tier_label=tier_labels[tier],
        domain=domain,
    )


def classify_results(results: list[SearchResult]) -> list[ClassifiedSource]:
    """Klassifiziere und sortiere Ergebnisse nach Tier (höchste zuerst)."""
    classified = [classify_source(r) for r in results]
    classified.sort(key=lambda c: c.tier, reverse=True)
    return classified


def deduplicate_sources(classified: list[ClassifiedSource]) -> list[ClassifiedSource]:
    """Entferne doppelte Quellen (gleiche Domain), behalte höchste Tier-Version."""
    seen_domains: set[str] = set()
    unique: list[ClassifiedSource] = []
    for src in classified:
        if src.domain not in seen_domains:
            seen_domains.add(src.domain)
            unique.append(src)
    return unique


def format_classified_for_llm(classified: list[ClassifiedSource]) -> str:
    """Formatiere klassifizierte Quellen für LLM-Kontext mit Tier-Info."""
    if not classified:
        return "Keine Suchergebnisse gefunden."

    parts: list[str] = []
    for i, src in enumerate(classified, 1):
        text = src.content if src.content else src.snippet
        parts.append(
            f"[Quelle {i}] [{src.tier_label}] {src.title}\n"
            f"URL: {src.url}\n"
            f"Inhalt: {text}\n"
        )
    return "\n---\n".join(parts)


def compute_source_consensus(classified: list[ClassifiedSource]) -> dict:
    """Berechne Quellen-Statistiken für den Fact-Checker.

    Returns:
        Dict mit Tier-Verteilung und Diversitätsbewertung.
    """
    if not classified:
        return {
            "total_sources": 0,
            "tier_counts": {},
            "highest_tier": "UNKNOWN",
            "diversity": "none",
        }

    tier_counts: dict[str, int] = {}
    for src in classified:
        label = src.tier_label
        tier_counts[label] = tier_counts.get(label, 0) + 1

    highest = max(classified, key=lambda c: c.tier)
    unique_tiers = len(set(c.tier for c in classified))

    if unique_tiers >= 3:
        diversity = "high"
    elif unique_tiers >= 2:
        diversity = "medium"
    else:
        diversity = "low"

    return {
        "total_sources": len(classified),
        "tier_counts": tier_counts,
        "highest_tier": highest.tier_label,
        "diversity": diversity,
    }
