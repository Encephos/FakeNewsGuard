"""EvidenceBuilderAgent – Retrieval, Scraping und Evidenz-Strukturierung.

Verantwortlichkeiten:
    1. Query-Erstellung (LLM-optimiert)
    2. Retrieval via LangSearch + SearXNG (parallel)
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
from config import AppConfig
from models.evidence_models import (
    EvidenceContradiction,
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    GoogleFactCheckMatch,
    SourceConsensus,
)
from models.schemas import Claim, ProcessedClaim
from tools.factcheck_databases import FactCheckDatabaseClient, FactCheckDatabaseConfig
from tools.llm import LLMClient
from tools.scrape_ranker import RankedSource, rank_sources
from tools.source_classifier import classify_source
from tools.source_scraper import ScrapedSource, scrape_sources
from tools.web_search import AsyncWebSearchClient, LangSearchClient, SearchResult, WebSearchClient


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


# ── Domain-Tier Mapping ────────────────────────────────────────────────────────

# Tier 1: Offizielle Statistikämter
_TIER1_DOMAINS = {
    "destatis.de", "eurostat.ec.europa.eu", "bpb.de", "statistik.at",
    "bfs.admin.ch", "oecd.org", "worldbank.org", "who.int", "un.org",
}
# Tier 2: Offizielle Behörden
_TIER2_DOMAINS = {
    "bamf.de", "bka.de", "bmi.bund.de", "bundesregierung.de",
    "bundestag.de", "bundesrat.de", "rki.de", "ema.europa.eu",
    "ec.europa.eu", "consilium.europa.eu", "bundesbank.de",
}
# Tier 3: Qualitätsjournalismus
_TIER3_DOMAINS = {
    "reuters.com", "dpa.com", "tagesschau.de", "zeit.de", "sueddeutsche.de",
    "faz.net", "spiegel.de", "nzz.ch", "ard.de", "zdf.de", "tagesspiegel.de",
    "welt.de", "bbc.com", "theguardian.com", "ap.org", "dw.com",
}
# Tier 4: Faktenchecker
_TIER4_DOMAINS = {
    "correctiv.org", "mimikama.org", "faktenfinder.tagesschau.de",
    "dpa-factchecking.com", "snopes.com", "factcheck.org", "politifact.com",
    "afp.com", "reuters.com/fact-check", "volksverpetzer.de",
}


def _domain_tier(url: str) -> int:
    domain = _extract_domain(url)
    if any(t in domain for t in _TIER1_DOMAINS):
        return 1
    if any(t in domain for t in _TIER2_DOMAINS):
        return 2
    if any(t in domain for t in _TIER3_DOMAINS):
        return 3
    if any(t in domain for t in _TIER4_DOMAINS):
        return 4
    return 5


def _is_fact_check_org(url: str) -> bool:
    return _domain_tier(url) == 4


# ── Stopwords für Relevanz-Berechnung ─────────────────────────────────────────

_RELEVANCE_STOPWORDS: set[str] = {
    "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
    "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
    "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
    "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
    "gegen", "damit", "dabei", "mehr", "sehr", "andere", "anderen",
    "the", "and", "for", "that", "this", "with", "from", "have", "been",
}

# Off-topic Signale: Domains/Muster die auf irrelevante Treffer hindeuten
_OFFTOPIC_URL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(rezept|recipe|kochen|restaurant|essen)", re.IGNORECASE),
    re.compile(r"(grammatik|duden|wörterbuch|dictionary|linguee|deepl)", re.IGNORECASE),
    re.compile(r"(wetter|weather|horoskop|horoscope)", re.IGNORECASE),
    re.compile(r"(shop|kaufen|bestellen|amazon|ebay)", re.IGNORECASE),
    re.compile(r"(forum|reddit\.com/r/(?!de|europe|news|worldnews))", re.IGNORECASE),
]


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
    for pattern in _OFFTOPIC_URL_PATTERNS:
        if pattern.search(url):
            return True
    return False


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

    if days_old <= 1:
        return 1.0
    elif days_old <= 7:
        return 0.9
    elif days_old <= 30:
        return 0.8
    elif days_old <= 90:
        return 0.7
    elif days_old <= 365:
        return 0.5
    elif days_old <= 730:
        return 0.3
    else:
        return 0.1


def _relevance_score(result: SearchResult, claim_text: str) -> float:
    """Multi-Signal Relevanz-Score: Keyword-Overlap + Entitäts-Match + Off-topic Penalty.

    Signale:
        - Keyword-Overlap (Gewicht 0.35)
        - Entitäts-Match (Gewicht 0.40)
        - Off-topic Penalty (Gewicht 0.25)
    """
    combined = f"{result.title} {result.snippet}".lower()

    # Signal 1: Keyword-Overlap (ohne Stoppwörter)
    claim_words = set(re.findall(r"\b[a-zäöüA-ZÄÖÜ]{4,}\b", claim_text.lower()))
    claim_words -= _RELEVANCE_STOPWORDS
    if claim_words:
        kw_matches = sum(1 for w in claim_words if w in combined)
        kw_score = min(1.0, kw_matches / len(claim_words))
    else:
        kw_score = 0.0

    # Signal 2: Entitäts-Match (Eigennamen, Zahlen, Akronyme)
    entity_score = _entity_overlap(claim_text, f"{result.title} {result.snippet}")

    # Signal 3: Off-topic Penalty
    offtopic_penalty = 0.0
    if _is_offtopic_url(result.url):
        offtopic_penalty = 0.6
    # Schwache Überschneidung + generischer Titel → Penalty
    if kw_score < 0.2 and entity_score < 0.2:
        offtopic_penalty = max(offtopic_penalty, 0.4)

    score = (
        kw_score * 0.35
        + entity_score * 0.40
        + (1.0 - offtopic_penalty) * 0.25
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

    claim_lower = claim_text.lower()
    claim_words = set(re.findall(r"\b[a-zäöü]{4,}\b", claim_lower)) - _RELEVANCE_STOPWORDS
    claim_entities = _extract_entities(claim_text)

    scored: list[tuple[float, str]] = []
    for para in paragraphs:
        para_lower = para.lower()
        # Keyword-Match
        if claim_words:
            kw_hits = sum(1 for w in claim_words if w in para_lower)
            kw_score = kw_hits / len(claim_words)
        else:
            kw_score = 0.0
        # Entity-Match
        if claim_entities:
            ent_hits = sum(1 for e in claim_entities if e.lower() in para_lower)
            ent_score = ent_hits / len(claim_entities)
        else:
            ent_score = 0.0

        total = kw_score * 0.4 + ent_score * 0.6
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


def _rank_evidence_items(
    results: list[SearchResult],
    claim_text: str,
    google_matches: list[GoogleFactCheckMatch],
) -> list[EvidenceItem]:
    """Ranke Suchergebnisse zu strukturierten EvidenceItems.

    Ranking-Kriterien:
        1. Domain-Tier (0.30 Gewicht)
        2. Claim-Relevanz inkl. Entitäten (0.30 Gewicht)
        3. Faktenchecker-Bonus (0.15)
        4. GFC-Match-Bonus (0.10)
        5. Off-topic Penalty (0.15)

    Treffer mit Relevanz < 0.10 und Tier 5 werden verworfen.
    """
    fact_check_domains = {_extract_domain(m.url) for m in google_matches}

    items: list[tuple[float, EvidenceItem]] = []
    for r in results:
        tier = _domain_tier(r.url)
        rel = _relevance_score(r, claim_text)
        is_fc = _is_fact_check_org(r.url)
        has_gfc_match = _extract_domain(r.url) in fact_check_domains

        # Off-topic Detection: verwerfe klar irrelevante Treffer
        if _is_offtopic_url(r.url) and rel < 0.3:
            continue  # Komplett verwerfen
        if rel < 0.10 and tier == 5 and not is_fc and not has_gfc_match:
            continue  # Irrelevanter Tier-5 Treffer → verwerfen

        # Off-topic Penalty für schwache Treffer
        offtopic_penalty = 0.0
        if _is_offtopic_url(r.url):
            offtopic_penalty = 0.4
        elif rel < 0.15 and tier >= 4:
            offtopic_penalty = 0.2

        # Multi-Signal Score
        score = (
            (5 - tier) / 4 * 0.30           # Tier-Bonus
            + rel * 0.30                      # Relevanz
            + (0.15 if is_fc else 0)          # Faktenchecker-Bonus
            + (0.10 if has_gfc_match else 0)  # GFC-Match-Bonus
            + (1.0 - offtopic_penalty) * 0.15 # Off-topic Penalty
        )

        source = EvidenceSource(
            url=r.url,
            title=r.title,
            domain=_extract_domain(r.url),
            domain_tier=tier,
            is_fact_check_org=is_fc,
        )

        # Bessere Excerpt-Extraktion
        content = r.content if r.content else r.snippet
        excerpt = _extract_best_excerpt(content, claim_text, max_chars=800) if content else ""

        item = EvidenceItem(
            source=source,
            excerpt=excerpt,
            relevance_score=rel,
            extraction_confidence=0.5 if excerpt else 0.0,
        )
        items.append((score, item))

    items.sort(key=lambda x: -x[0])
    return [item for _, item in items]


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


def _compute_quality_signals(
    items: list[EvidenceItem],
    google_matches: list[GoogleFactCheckMatch],
) -> EvidenceQualitySignals:
    """Berechne Qualitätssignale für das Evidence-Set."""
    has_primary = any(i.source.domain_tier <= 2 for i in items)
    has_fc = bool(google_matches) or any(i.source.is_fact_check_org for i in items)
    top_tier_count = sum(1 for i in items if i.source.domain_tier <= 2)

    if not items:
        consensus = SourceConsensus.INSUFFICIENT
    elif len(items) < 2:
        consensus = SourceConsensus.INSUFFICIENT
    else:
        support = sum(1 for i in items if i.supports_claim is True)
        oppose = sum(1 for i in items if i.supports_claim is False)
        total_assessed = support + oppose
        if total_assessed == 0:
            consensus = SourceConsensus.INSUFFICIENT
        elif support > 0 and oppose == 0:
            consensus = SourceConsensus.AGREEING
        elif oppose > 0 and support == 0:
            consensus = SourceConsensus.AGREEING  # Konsens gegen Claim
        else:
            consensus = SourceConsensus.MIXED

    # Off-topic-Rate: Anteil schwach-relevanter Treffer in den Top-5
    top_5 = items[:5]
    if top_5:
        offtopic_count = sum(1 for i in top_5 if i.relevance_score < 0.2)
        off_topic_rate = offtopic_count / len(top_5)
    else:
        off_topic_rate = 0.0

    # Echte Freshness-Berechnung (Durchschnitt der Top-Quellen)
    freshness_scores = [
        _compute_freshness(i.source.publication_date)
        for i in items[:6]
        if i.source.publication_date
    ]
    # 0.0 wenn keine Items (nicht 0.5 neutral) – verhindert künstliche overall_quality-Inflation
    freshness = sum(freshness_scores) / len(freshness_scores) if freshness_scores else (0.5 if items else 0.0)

    # Relevanz-Qualität: wie relevant sind die Top-Treffer?
    top_relevance = [i.relevance_score for i in items[:5]]
    avg_relevance = sum(top_relevance) / len(top_relevance) if top_relevance else 0.0

    # Off-topic-Penalty in overall_quality einrechnen
    offtopic_penalty = off_topic_rate * 0.15
    freshness_term = freshness * 0.10 if items else 0.0

    overall = (
        min(1.0, top_tier_count / 3) * 0.30
        + (0.25 if has_primary else 0)
        + (0.25 if has_fc else 0)
        + avg_relevance * 0.10
        + freshness_term
        - offtopic_penalty
    )
    overall = max(0.0, min(1.0, overall))

    return EvidenceQualitySignals(
        has_primary_sources=has_primary,
        has_fact_check_org_result=has_fc,
        source_consensus=consensus,
        freshness_score=freshness,
        overall_quality=overall,
        top_tier_count=top_tier_count,
        off_topic_rate=off_topic_rate,
        avg_top5_relevance=avg_relevance,
    )


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

        # LangSearch Client
        self._langsearch = LangSearchClient(
            config=self.config.langsearch,
            retry=self.config.retry,
        )

        # Async Search Clients
        self._async_search = AsyncWebSearchClient(
            self.config.search, self.config.retry
        )

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
        """Async-Version – Retrieval läuft parallel."""
        claim: Claim = input_data
        notes: list[str] = []

        # ── 1. Query-Optimierung ──────────────────────────────────────────────
        queries = await self._build_queries_async(claim, context)
        notes.append(f"Queries: {queries}")

        # ── 2. Paralleles Retrieval ───────────────────────────────────────────
        # Strategie: LangSearch = semantische Hauptsuche (Primär-Queries)
        #            SearXNG = Breite/Fallback/Discovery (alle Queries)
        # Google Fact Check = strukturierter Shortcut-Layer (höchste Priorität)
        from agents.fact_checker import _categories_for_claim
        categories = _categories_for_claim(claim)

        # LangSearch bekommt nur die ersten 2 Queries (semantisch präziser)
        langsearch_primary_queries = queries[:2] if len(queries) >= 2 else queries

        searxng_task = self._async_search.multi_search_async(
            queries, max_results=self.config.search.max_results,
            categories=categories,
        )
        langsearch_task = self._langsearch.multi_search_async(
            langsearch_primary_queries,
            max_results=self.config.langsearch.max_results,
        )
        gfc_task = self._gfc_client.search_async(claim.text)

        searxng_results, langsearch_results, gfc_raw = await asyncio.gather(
            searxng_task, langsearch_task, gfc_task, return_exceptions=False
        )

        # ── 3. Ergebnisse zusammenführen + deduplizieren ──────────────────────
        # LangSearch-Ergebnisse zuerst einfügen (semantisch priorisiert)
        all_results: list[SearchResult] = []
        for q_results in langsearch_results.values():
            all_results.extend(q_results)
        for q_results in searxng_results.values():
            all_results.extend(q_results)

        unique_results = _dedup_results(all_results)
        notes.append(
            f"Retrieval: {len(all_results)} Treffer → {len(unique_results)} unique "
            f"(LangSearch: {sum(len(v) for v in langsearch_results.values())} primär, "
            f"SearXNG: {sum(len(v) for v in searxng_results.values())} breit)"
        )

        # ── 4. Google Fact Check Matches aufbereiten ──────────────────────────
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

        # ── 5. Source Ranking + Scraping ──────────────────────────────────────
        ranked, scraped = await self._rank_and_scrape(unique_results, claim)

        # ── 6. EvidenceItems aus gescrapten Quellen bauen ─────────────────────
        evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches)
        notes.append(f"Evidence Items: {len(evidence_items)} (mit Scraping)")

        # ── 7. Qualität, Widersprüche, Pack zusammenstellen ───────────────────
        contradictions = _detect_contradictions(evidence_items[:6])
        quality = _compute_quality_signals(evidence_items, gfc_matches)

        # Retry wenn Qualität zu niedrig oder Off-topic-Rate hoch
        high_offtopic = quality.off_topic_rate > 0.6
        low_quality = quality.overall_quality < 0.2
        if (low_quality or high_offtopic) and not any(
            r.content for results_map in langsearch_results.values() for r in results_map
        ):
            reason = "Off-topic-Rate hoch" if high_offtopic else "Qualität niedrig"
            notes.append(f"{reason} – Fallback-Suche mit alternativen Queries")
            fallback_results = await self._fallback_retrieval(claim, queries)
            unique_results = _dedup_results(all_results + fallback_results)
            ranked, scraped = await self._rank_and_scrape(unique_results, claim)
            evidence_items = self._build_evidence_items(ranked, scraped, claim.text, gfc_matches)
            quality = _compute_quality_signals(evidence_items, gfc_matches)
            notes.append(f"Fallback: {len(evidence_items)} Evidence Items")

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
        """LLM-Queries asynchron generieren (Thread-Pool)."""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()

        from agents.fact_checker import _optimize_queries_with_llm, _build_search_queries
        optimized = await loop.run_in_executor(
            None, _optimize_queries_with_llm, claim, self.llm, context
        )
        if optimized:
            return optimized
        return _build_search_queries(claim, context)

    async def _rank_and_scrape(
        self,
        results: list[SearchResult],
        claim: Claim,
    ) -> tuple[list[RankedSource], list[ScrapedSource]]:
        """Ranke und scrape die relevantesten Quellen."""
        # Ergebnisse nach Queries gruppieren (für rank_sources Interface)
        results_by_query: dict[str, list[SearchResult]] = {"_all": results}

        ranked = rank_sources(
            results_by_query,
            claim.text,
            max_scrape=self.config.search.scrape_top_n,
        )
        scrape_count = sum(1 for rs in ranked if rs.should_scrape)
        self._log(f"Scraping {scrape_count} von {len(ranked)} Quellen...")

        scraped = await scrape_sources(
            ranked, claim.text,
            max_concurrent=self.config.search.max_concurrent_searches,
            timeout=self.config.search.scrape_timeout,
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
    ) -> list[EvidenceItem]:
        """Baue EvidenceItems aus gescrapten Quellen.

        Trust Boundary: Nur strukturierte Excerpts (max. 800 Zeichen) werden
        in EvidenceItems übernommen – kein roher HTML-Inhalt.
        """
        scraped_by_url = {s.url: s for s in scraped}
        items: list[EvidenceItem] = []
        gfc_domains = {_extract_domain(m.url) for m in gfc_matches}

        for rs in ranked:
            sc = scraped_by_url.get(rs.result.url)
            url = rs.result.url
            tier = _domain_tier(url)
            domain = _extract_domain(url)

            # Excerpt: bevorzuge gescrapten Passage, sonst Snippet
            if sc and sc.fetch_success and sc.passage:
                raw_excerpt = sc.passage
                extraction_conf = 0.8
            else:
                raw_excerpt = rs.result.snippet
                extraction_conf = 0.3

            # Trust Boundary: relevante Passage statt stumpfem Cutoff
            excerpt = _extract_best_excerpt(raw_excerpt, claim_text, max_chars=800) if raw_excerpt else ""

            source = EvidenceSource(
                url=url,
                title=rs.result.title,
                domain=domain,
                domain_tier=tier,
                is_fact_check_org=_is_fact_check_org(url),
                is_primary_source=(tier <= 2),
            )
            item = EvidenceItem(
                source=source,
                excerpt=excerpt,
                relevance_score=_relevance_score(rs.result, claim_text),
                extraction_confidence=extraction_conf,
                supports_claim=None,
            )
            items.append(item)

        # Nach Ranking-Score sortieren (kombiniert Tier + Relevanz)
        items = _rank_evidence_items(
            [SearchResult(
                title=i.source.title,
                url=i.source.url,
                snippet=i.excerpt,
            ) for i in items],
            claim_text,
            gfc_matches,
        )

        return items

    async def _fallback_retrieval(
        self,
        claim: Claim,
        original_queries: list[str],
    ) -> list[SearchResult]:
        """Fallback-Suche mit alternativen Queries bei niedriger Qualität."""
        from agents.fact_checker import _build_fallback_queries
        fallback_queries = _build_fallback_queries(claim, original_queries)
        if not fallback_queries:
            return []

        results_map = await self._async_search.multi_search_async(
            fallback_queries,
            max_results=self.config.search.max_results,
        )
        return [r for results in results_map.values() for r in results]
