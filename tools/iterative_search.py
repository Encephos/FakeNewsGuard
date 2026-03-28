"""Iterative Search mit Relevanz-Feedback.

Wenn die erste Suchrunde niedrige Qualität liefert, extrahiert dieses Modul
neue Vokabeln aus den Top-Ergebnissen und generiert verfeinerte Queries.

Typischer Ablauf:
    1. Erste Suchrunde liefert Qualität < 0.45
    2. extract_feedback_terms() analysiert Top-5-Ergebnisse auf neue Begriffe
    3. generate_refinement_queries() baut daraus gezielte Nachfragen
    4. Zweite Suchrunde mit den neuen Queries

Besonders effektiv bei:
    - Fabricated-policy Claims (Faktencheck-Vokabular: "Kettenbrief", "Hoax")
    - Claims mit ungewöhnlicher Terminologie
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.ner_extractor import ClaimEntities
    from tools.web_search import SearchResult


def extract_feedback_terms(
    claim_text: str,
    results: list["SearchResult"],
    top_k: int = 5,
    max_terms: int = 6,
) -> list[str]:
    """Extrahiere neue Terme aus Top-Ergebnissen, die nicht im Claim vorkommen.

    Strategie: Zähle Terme über die Top-K Snippets. Behalte nur Terme die:
    - In mindestens 2 Ergebnissen vorkommen (Signal, nicht Rauschen)
    - Nicht im Claim-Text enthalten sind (echtes Feedback)
    - Mindestens 5 Zeichen lang sind (keine Stoppwörter)

    Returns:
        Sortiert nach Häufigkeit (absteigend), max. max_terms Einträge.
    """
    claim_lower = claim_text.lower()
    claim_words = set(re.findall(r"[a-zäöüß]{4,}", claim_lower))

    # Stoppwörter für Feedback-Extraktion
    stop = {
        "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
        "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
        "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
        "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
        "gegen", "damit", "dabei", "mehr", "sehr", "andere", "anderen",
        "bereits", "dabei", "sollen", "können", "müssen", "dürfen",
        "lesen", "weitere", "artikel", "quelle", "seite", "inhalt",
        "beitrag", "thema", "bericht", "kommentar", "aktuell",
    }

    term_doc_count: dict[str, int] = {}
    for result in results[:top_k]:
        text = f"{result.title} {result.snippet}".lower()
        words = set(re.findall(r"[a-zäöüß]{5,}", text))
        novel = words - claim_words - stop
        for w in novel:
            term_doc_count[w] = term_doc_count.get(w, 0) + 1

    # Nur Terme die in ≥2 Ergebnissen vorkommen
    feedback = [
        (count, term)
        for term, count in term_doc_count.items()
        if count >= 2
    ]
    feedback.sort(key=lambda x: -x[0])
    return [term for _, term in feedback[:max_terms]]


def generate_refinement_queries(
    claim_text: str,
    feedback_terms: list[str],
    ner_entities: "ClaimEntities | None" = None,
    max_queries: int = 3,
) -> list[str]:
    """Generiere verfeinerte Queries mit Feedback-Vokabular.

    Strategie:
        Query 1: Top-Entitäten + Top-2 Feedback-Terme
        Query 2: Top-Entitäten + "Faktencheck" + Feedback-Term
        Query 3: Top-Entitäten + "Falschmeldung" + Feedback-Term

    Returns:
        Liste von maximal max_queries Query-Strings.
    """
    if not feedback_terms:
        return []

    # Entitäten sammeln (LOC > ORG > MISC > erste Wörter aus Claim)
    entities: list[str] = []
    if ner_entities:
        entities.extend(ner_entities.locations[:2])
        entities.extend(ner_entities.organizations[:2])
        entities.extend(ner_entities.misc[:2])

    if not entities:
        # Fallback: Großgeschriebene Wörter aus dem Claim
        entities = re.findall(r"\b[A-ZÄÖÜ][a-zäöü]{3,}\b", claim_text)[:3]

    # Entitäten deduplizieren
    seen: set[str] = set()
    unique_entities: list[str] = []
    for e in entities:
        if e.lower() not in seen:
            seen.add(e.lower())
            unique_entities.append(e)

    entity_str = " ".join(unique_entities[:2])
    queries: list[str] = []

    # Query 1: Entitäten + Feedback-Terme
    if len(feedback_terms) >= 2:
        q1 = f"{entity_str} {feedback_terms[0]} {feedback_terms[1]}"
        queries.append(q1.strip())

    # Query 2: Entitäten + "Faktencheck" + Feedback
    if feedback_terms:
        q2 = f"{entity_str} Faktencheck {feedback_terms[0]}"
        queries.append(q2.strip())

    # Query 3: Entitäten + "Falschmeldung" + Feedback
    if len(feedback_terms) >= 2:
        q3 = f"{entity_str} Falschmeldung {feedback_terms[1]}"
        queries.append(q3.strip())

    return queries[:max_queries]
