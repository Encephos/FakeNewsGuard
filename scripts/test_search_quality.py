"""Testskript: Suchqualität für Fake-News-Analyse.

Testet LangSearch + SearXNG für repräsentative Claims aus 00_testbench.json.
Bewertet: Relevanz, Themenabdeckung, Nützlichkeit für Fake-News-Analyse.

Aufruf: python3 scripts/test_search_quality.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from config import AppConfig, LangSearchConfig, SearXNGConfig
from tools.web_search import LangSearchClient, SearXNGClient, SearXNGQuery, SearchResult
from tools.ner_extractor import extract_entities
from tools.query_expansion import QueryExpansionEngine, _trim_query


# ── Testfälle – repräsentative Auswahl ────────────────────────────────────────

TEST_CLAIMS = [
    # Leicht-Fake (Erkennung durch offensichtliche Signale)
    {"id": 1,  "label": "fake", "difficulty": "leicht",
     "text": "Ab morgen kostet WhatsApp 5 Euro pro Nachricht! Mark Zuckerberg hat das gerade bestätigt!"},

    # Mittel-Fake (übertriebene Regulierung)
    {"id": 6,  "label": "fake", "difficulty": "mittel",
     "text": "Neues Gesetz aus Brüssel: Ab 2028 dürfen EU-Bürger nur noch an zwei Tagen in der Woche Fleisch essen. Bei Verstößen drohen hohe Geldstrafen."},

    # Schwer-Fake (seriöser Stil + echte Institutionen + falsche Zahlen)
    {"id": 11, "label": "fake", "difficulty": "schwer",
     "text": "Nach einem unveröffentlichten Bericht des Statistischen Bundesamtes (Destatis) sind die Reallöhne in Deutschland im letzten Quartal unerwartet um 11,4 % eingebrochen."},

    # Sehr schwer-Fake (absolute vs. relative Zahlen, echte Behörde)
    {"id": 16, "label": "fake", "difficulty": "sehr schwer",
     "text": "Offizielle Zahlen des Kraftfahrt-Bundesamtes (KBA) belegen: Die Zahl der brennenden Elektroautos auf deutschen Straßen ist von 2020 bis 2025 um 450 % gestiegen."},

    # Ultra schwer-Fake (perfekte Gesetzessprache)
    {"id": 39, "label": "fake", "difficulty": "ultra schwer (Halbwahrheit)",
     "text": "Versteckte Klausel in § 14a EnWG: Netzbetreiber können Waschmaschinen und Backöfen zentral und ohne Vorwarnung abschalten."},

    # Propaganda-Fake (False Equivalence)
    {"id": 27, "label": "fake", "difficulty": "propaganda",
     "text": "Eine interne Anfrage deckt auf: Durch den 'Integrationszuschlag' erhalten Asylbewerber 350 Euro mehr Netto als ein Rentner mit 40 Beitragsjahren."},

    # Real-Mittel (echte Behörde, echte Statistik)
    {"id": 46, "label": "real", "difficulty": "mittel",
     "text": "Nach Angaben des Statistischen Bundesamtes (Destatis) lag die Inflationsrate in Deutschland im Jahresdurchschnitt 2023 bei +5,9 Prozent im Vergleich zum Vorjahr."},

    # Real-Schwer (Recht, klingt bedrohlich)
    {"id": 51, "label": "real", "difficulty": "schwer",
     "text": "Die EU-DSGVO verpflichtet Unternehmen bei schweren Datenpannen, die Aufsichtsbehörde innerhalb von 72 Stunden zu informieren. Bußgelder bis zu 4 % des Jahresumsatzes sind möglich."},

    # Real-Schwer (kontraintuitiv aber korrekt)
    {"id": 53, "label": "real", "difficulty": "schwer",
     "text": "Obwohl der Merkur der Sonne am nächsten ist, ist die Venus aufgrund ihrer dichten Atmosphäre der heißeste Planet im Sonnensystem."},
]


@dataclass
class SearchTestResult:
    claim_id: int
    claim_text: str
    label: str
    difficulty: str
    ner_entities: dict
    queries_generated: list[str]
    langsearch_results: list[dict]
    searxng_results: list[dict]
    quality_score: float
    quality_notes: list[str]


def _assess_quality(
    claim_text: str,
    label: str,
    ls_results: list[SearchResult],
    sx_results: list[SearchResult],
) -> tuple[float, list[str]]:
    """Bewertet Suchqualität für Fake-News-Analyse.

    Kriterien:
    - Thematische Relevanz der Top-5 Ergebnisse
    - Vorhandensein von Primärquellen (Behörden, offiz. Stellen)
    - Vorhandensein von Fact-Checks
    - Vollständigkeit für Verifikation
    """
    notes = []
    all_results = ls_results + sx_results

    if not all_results:
        return 0.0, ["KEINE Suchergebnisse erhalten"]

    # Keyword-Overlap für Relevanzcheck
    claim_words = set(w.lower() for w in claim_text.split() if len(w) > 4)
    claim_words -= {"nicht", "sowie", "durch", "werden", "wurde", "haben", "einer", "einem"}

    relevant_count = 0
    factcheck_count = 0
    official_count = 0
    snippets_with_claim_context = 0

    FACTCHECK_DOMAINS = {"correctiv.org", "mimikama", "faktenfinder", "snopes", "politifact",
                         "dpa-factcheck", "volksverpetzer", "faktencheck"}
    OFFICIAL_DOMAINS = {"destatis.de", "bundesregierung.de", "bundestag.de", "kba.de",
                        "bka.de", "rki.de", "eur-lex", "ec.europa.eu", "bundesbank.de",
                        "bmwk.de", "bafin.de", "bundesnetzagentur.de"}

    for r in all_results[:15]:
        combined = f"{r.title} {r.snippet}".lower()
        url_lower = r.url.lower()

        # Relevanz: mindestens 2 Claim-Keywords im Snippet
        kw_hits = sum(1 for w in claim_words if w in combined)
        if kw_hits >= 2:
            relevant_count += 1

        # Fact-Check Domains
        if any(fc in url_lower for fc in FACTCHECK_DOMAINS):
            factcheck_count += 1

        # Offizielle Quellen
        if any(od in url_lower for od in OFFICIAL_DOMAINS):
            official_count += 1

        # Snippet enthält Claim-Kontext (Zahlen, Entitäten aus Claim)
        import re
        numbers_in_claim = re.findall(r"\d+[,.]?\d*\s*(?:%|Prozent|Euro|€)", claim_text)
        if numbers_in_claim and any(n.split()[0] in combined for n in numbers_in_claim):
            snippets_with_claim_context += 1

    total = len(all_results[:15])
    relevance_rate = relevant_count / total if total else 0

    # Score berechnen
    score = 0.0
    score += min(0.40, relevance_rate * 0.40)
    score += min(0.20, factcheck_count * 0.10)
    score += min(0.20, official_count * 0.10)
    score += min(0.20, snippets_with_claim_context * 0.07)

    # Qualitätsnotizen
    notes.append(f"Relevante Ergebnisse: {relevant_count}/{total} ({relevance_rate:.0%})")
    notes.append(f"Fact-Check Quellen: {factcheck_count}")
    notes.append(f"Offizielle Quellen: {official_count}")
    if snippets_with_claim_context > 0:
        notes.append(f"Numerischer Kontext gefunden: {snippets_with_claim_context}x")

    # Analyse-Eignung
    if label == "fake":
        if factcheck_count == 0 and official_count == 0:
            notes.append("⚠ PROBLEM: Keine Fact-Checks oder Primärquellen → Verifikation kaum möglich")
        elif factcheck_count > 0:
            notes.append("✓ Fact-Checks vorhanden → Debunking möglich")
        if official_count > 0:
            notes.append("✓ Offizielle Quellen → Zahlenvergleich möglich")
    else:  # real
        if official_count == 0:
            notes.append("⚠ Keine offiziellen Quellen → Verifikation schwierig")
        else:
            notes.append("✓ Offizielle Quellen → Bestätigung möglich")

    return min(1.0, score), notes


async def test_single_claim(
    claim: dict,
    ls_client: LangSearchClient,
    sx_client: SearXNGClient,
) -> SearchTestResult:
    """Testet Suchqualität für einen einzelnen Claim."""
    text = claim["text"]

    # NER
    entities = extract_entities(text)
    ner_info = {
        "locations": entities.locations,
        "organizations": entities.organizations,
        "money": entities.money,
        "dates": entities.dates,
        "misc": entities.misc[:3],
        "key_nouns": entities.key_nouns[:4],
    }

    # Query-Generierung (5 Strategien, direkt aus NER)
    merged = {
        "locations": entities.locations,
        "organizations": entities.organizations,
        "money": entities.money,
        "dates": entities.dates,
        "misc": entities.misc,
        "key_nouns": entities.key_nouns,
    }

    from tools.query_expansion import QueryExpansionEngine

    # Direkte Query-Generierung ohne Routing (reiner NER-Test)
    class FakeRouteResult:
        site_hints = []
        jurisdiction = "de"
        domains = []

    class FakeProfile:
        locations = entities.locations
        institutions = entities.organizations
        core_entities = entities.misc
        policy_terms = entities.key_nouns[:2]
        action_terms = []
        number_terms = entities.numbers
        sanction_terms = []
        official_source_hints = []
        fact_check_hints = []

    class FakeClaim:
        text = claim["text"]

    engine = QueryExpansionEngine()
    try:
        variants = engine.expand(FakeClaim(), FakeRouteResult(), FakeProfile())
        queries = [v.text for v in variants[:6]]
    except Exception as e:
        # Fallback: einfache Keyword-Queries
        terms = entities.query_terms(max_terms=4)
        queries = [" ".join(terms)] if terms else [text[:80]]

    if not queries:
        queries = [text[:80]]

    # LangSearch (max 3 Queries)
    ls_results_all = []
    ls_queries = queries[:3]
    try:
        ls_map = await ls_client.multi_search_async(ls_queries, max_results=5)
        for q_results in ls_map.values():
            ls_results_all.extend(q_results)
    except Exception as e:
        print(f"  LangSearch Fehler: {e}", file=sys.stderr)

    # SearXNG (alle Queries + pageno=2 für erste)
    sx_results_all = []
    sx_queries = []
    for i, q in enumerate(queries[:5]):
        sq = SearXNGQuery(query=q, pageno=1)
        sx_queries.append(sq)
        if i == 0:
            sx_queries.append(SearXNGQuery(query=q, pageno=2))
    try:
        sx_map = await sx_client.multi_search_async(sx_queries, max_results=10)
        for q_results in sx_map.values():
            sx_results_all.extend(q_results)
    except Exception as e:
        print(f"  SearXNG Fehler: {e}", file=sys.stderr)

    # Dedup
    seen_urls = set()
    ls_deduped = []
    for r in ls_results_all:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            ls_deduped.append(r)

    sx_deduped = []
    for r in sx_results_all:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            sx_deduped.append(r)

    # Qualitätsbewertung
    quality_score, quality_notes = _assess_quality(
        text, claim["label"], ls_deduped, sx_deduped
    )

    return SearchTestResult(
        claim_id=claim["id"],
        claim_text=text,
        label=claim["label"],
        difficulty=claim["difficulty"],
        ner_entities=ner_info,
        queries_generated=queries,
        langsearch_results=[
            {"title": r.title, "url": r.url, "snippet": r.snippet[:120]}
            for r in ls_deduped[:5]
        ],
        searxng_results=[
            {"title": r.title, "url": r.url, "snippet": r.snippet[:120]}
            for r in sx_deduped[:5]
        ],
        quality_score=quality_score,
        quality_notes=quality_notes,
    )


def print_result(r: SearchTestResult) -> None:
    label_icon = "🔴 FAKE" if r.label == "fake" else "🟢 REAL"
    score_bar = "█" * int(r.quality_score * 10) + "░" * (10 - int(r.quality_score * 10))
    print(f"\n{'='*80}")
    print(f"[#{r.claim_id}] {label_icon} | {r.difficulty}")
    print(f"Claim: {r.claim_text[:120]}...")
    print(f"Score: [{score_bar}] {r.quality_score:.2f}")
    print()

    print("NER-Entitäten:")
    for k, v in r.ner_entities.items():
        if v:
            print(f"  {k:12}: {v}")

    print(f"\nGenerierte Queries ({len(r.queries_generated)}):")
    for i, q in enumerate(r.queries_generated, 1):
        print(f"  {i}. {q}")

    print(f"\nLangSearch ({len(r.langsearch_results)} Ergebnisse):")
    for res in r.langsearch_results[:4]:
        print(f"  • {res['title'][:60]}")
        print(f"    {res['url'][:70]}")
        print(f"    {res['snippet'][:100]}")

    print(f"\nSearXNG ({len(r.searxng_results)} Ergebnisse):")
    for res in r.searxng_results[:4]:
        print(f"  • {res['title'][:60]}")
        print(f"    {res['url'][:70]}")
        print(f"    {res['snippet'][:100]}")

    print("\nQualitätsbewertung:")
    for note in r.quality_notes:
        print(f"  {note}")


async def main():
    cfg = AppConfig()

    ls_client = LangSearchClient(config=cfg.langsearch)
    sx_client = SearXNGClient(config=cfg.searxng)

    print(f"FakeNewsGuard – Suchqualitätstest")
    print(f"LangSearch: {'✓ aktiv' if cfg.langsearch.enabled else '✗ inaktiv'}")
    print(f"SearXNG:    {cfg.searxng.base_url}")
    print(f"Claims:     {len(TEST_CLAIMS)}")

    results = []
    for i, claim in enumerate(TEST_CLAIMS):
        print(f"\n[{i+1}/{len(TEST_CLAIMS)}] Teste Claim #{claim['id']} ({claim['difficulty']})...")
        result = await test_single_claim(claim, ls_client, sx_client)
        results.append(result)
        print_result(result)
        # Pause zwischen Claims, um Engine-Suspensions durch zu viele Anfragen zu vermeiden
        if i < len(TEST_CLAIMS) - 1:
            await asyncio.sleep(8)

    # Zusammenfassung
    print(f"\n{'='*80}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*80}")
    avg_score = sum(r.quality_score for r in results) / len(results)
    print(f"\nDurchschnittlicher Qualitätsscore: {avg_score:.2f}")
    print()
    print(f"{'ID':>3} {'Label':>6} {'Difficulty':<20} {'Score':>6} {'Analyse-Eignung'}")
    print("-" * 65)
    for r in results:
        problems = [n for n in r.quality_notes if "PROBLEM" in n]
        status = "⚠ " + problems[0][:40] if problems else "✓ OK"
        print(f"#{r.claim_id:>2} {r.label:>6} {r.difficulty:<20} {r.quality_score:>6.2f}  {status}")

    # Schwachstellen
    print("\n\nSCHWACHSTELLEN (Score < 0.35):")
    weak = [r for r in results if r.quality_score < 0.35]
    if weak:
        for r in weak:
            print(f"  #{r.claim_id} ({r.difficulty}): {r.claim_text[:80]}...")
    else:
        print("  Keine kritischen Schwachstellen")


if __name__ == "__main__":
    asyncio.run(main())
