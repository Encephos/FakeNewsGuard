"""Query-Building und Suchoptimierungs-Funktionen für den Fact-Checker.

Extrahiert aus agents/fact_checker.py, um die Modulgröße zu reduzieren.
Enthält:
  - System-Prompt-Konstanten (SYSTEM_PROMPT, _QUERY_OPTIMIZER_PROMPT)
  - _ARTIFACT_TERMS
  - Alle standalone Query-Building-Funktionen
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from i18n import t
from models.schemas import Claim
from tools.llm import LLMClient
from tools.scrape_ranker import RankedSource
from tools.source_scraper import ScrapedSource

if TYPE_CHECKING:
    pass

logger = logging.getLogger("fng.fact_checker")

# ── System-Prompt-Konstanten ──────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Du bist ein Fact-Checker.  Deine EINZIGE Aufgabe: Überprüfe die gegebene Behauptung
anhand der bereitgestellten Suchergebnisse.

## Quellen-Hierarchie (in dieser Reihenfolge vertrauen)

1. Offizielle Statistikämter (Destatis, Eurostat)
2. Offizielle Behörden (BAMF, BKA, BMI)
3. Qualitätsjournalismus (Reuters, dpa, Tagesschau, Zeit, SZ)
4. Fact-Checking-Organisationen (Correctiv, dpa Faktencheck, Mimikama)
5. Akademische Quellen

NIEMALS Blogs, Telegram, X/Twitter oder Parteiseiten als Primärquelle verwenden.

## Bewertungsskala

- TRUE: Faktenkonform, korrekt kontextualisiert
- MOSTLY_TRUE: Kern stimmt, Details ungenau
- MISLEADING: Technisch korrekt, aber irreführend präsentiert
- MOSTLY_FALSE: Kernaussage falsch, enthält wahre Elemente
- FALSE: Nachweislich falsch
- UNVERIFIABLE: Kann mit verfügbaren Quellen nicht geprüft werden

## Regeln

- Wenn etwas stimmt, sag es KLAR.  Sei fair und objektiv.
- Wenn ein Claim teilweise stimmt, erkläre EXAKT was stimmt und was nicht.
- Prüfe auch den KONTEXT: Stimmt der Zeitraum? Die Bezugsgröße? Die Kategorie?
- Gib die URLs der verwendeten Quellen an.
- Wenn professionelle Faktenchecks (z.B. von Correctiv, dpa, Snopes, AFP) vorliegen,
  beziehe deren Einschätzung STARK in deine Bewertung ein. Diese Organisationen haben
  oft tiefere Recherche betrieben als aus Suchergebnissen ersichtlich.

## Output-Format (JSON)

{
  "claim_id": "C1",
  "rating": "MISLEADING",
  "evidence": "Zusammenfassung der gefundenen Fakten",
  "correction": "Was an der Behauptung falsch oder irreführend ist",
  "missing_context": "Welcher Kontext absichtlich weggelassen wird",
  "sources": ["url1", "url2"]
}
"""


_QUERY_OPTIMIZER_PROMPT = """\
Du bist ein Suchquery-Optimierer für Faktenprüfung.
Deine Aufgabe: Generiere 3-4 optimierte Suchqueries, um die gegebene Behauptung zu überprüfen.

## Regeln
- Verwende Schlüsselbegriffe, KEINE ganzen Sätze
- Query 1 (entity/policy): Kernentitäten + Policy-Kontext + Zahlen (z.B. "Hannover Stadtrat 15-Minuten-Stadt 100 Autofahrten")
- Query 2 (official-source): site:-Hint + Institution + Thema (z.B. "site:hannover.de Stadtrat Verkehr 15-Minuten-Stadt")
- Query 3 (fact-check): Faktenchecker + Kernentität + Thema (z.B. "site:correctiv.org Hannover 15-Minuten-Stadt Faktencheck")
- Query 4 (sanction/number): Entität + Zahl + Sanktion (z.B. "Hannover 250 Euro Bußgeld Kameraüberwachung")
- Behalte IMMER den Kontext-Anker (Institution/Ort/Programm) in JEDER Query
- Einzelne Zahlen ohne Kontext sind KEINE gültige Query
- Entferne Füllwörter (der, die, das, und, ist, hat, etc.)

## Wenn ein strukturierter Frame vorhanden ist, nutze dessen Felder
Frame-Felder haben Vorrang vor freiem Claim-Text.

Antworte NUR mit einem JSON-Array von 3-4 Strings. Beispiel:
["Hannover Stadtrat 15-Minuten-Stadt 100 Autofahrten", "site:hannover.de Stadtrat Verkehr 15-Minuten-Stadt", "site:correctiv.org Hannover 15-Minuten-Stadt Faktencheck", "Hannover 250 Euro Bußgeld Kameraüberwachung"]
"""


# ── Artifact-Indikatoren für missing_artifact_evidence ───────────────────────
# Generische Artefakt-Begriffe aus dem Claim-Text (keine erfundenen Behörden).
# Nur Wörter, die auf ein konkretes Dokument/Artefakt hinweisen.
_ARTIFACT_TERMS: frozenset[str] = frozenset({
    "dokument", "leak", "geleakt", "geleaktes", "intern", "interne", "internes",
    "geheim", "geheimes", "geheimakte", "protokoll", "beschluss", "studie",
    "bericht", "berichte", "papier", "entwurf", "richtlinie", "verordnung",
    "vertrag", "vereinbarung", "memo", "memorandum", "whistleblower",
    "enthüllung", "enthüllungen", "enthüllt", "leaked", "document", "secret",
    "internal", "classified",
})


# ── Standalone Query-Building-Funktionen ─────────────────────────────────────


def _build_queries_for_underspecified_claim(
    claim: "Claim",
    quality_signals: list[str],
) -> list[str]:
    """Generiere generische Query-Familien für schwach spezifizierte Claims.

    Für Claims ohne eindeutigen Akteur, mit behaupteten Dokumenten/Leaks
    oder ohne identifizierbare Institution werden 4 Query-Familien erzeugt.
    Keine Halluzinationen: Es werden ausschließlich Begriffe aus dem
    Claim-Text selbst verwendet – niemals erfundene Länder, Behörden oder
    Akteure.

    Query-Familien:
        1. direct_claim    – kompakte Keyword-Query ohne Füllwörter
        2. document/artifact – Artefakt-Keyword + Kontext-Keywords
                              (nur wenn ``missing_artifact_evidence`` Signal)
        3. fact-check      – Keywords + „Faktencheck" / Debunk-Suffix
        4. official_response – Keywords + „Stellungnahme" (ohne Institution)

    Args:
        claim: Der zu prüfende Claim (Claim oder ProcessedClaim).
        quality_signals: Liste der erkannten Qualitätssignale aus
            ProcessedClaim.quality_signals.

    Returns:
        Liste von 2–4 Queries. Leer wenn keine verwertbaren Keywords.
    """
    from tools.scrape_ranker import _extract_claim_keywords

    keywords = _extract_claim_keywords(claim.text)
    if not keywords:
        return []

    queries: list[str] = []
    kw_sorted = sorted(keywords)[:6]
    keyword_base = " ".join(kw_sorted)

    # ── Familie 1: direct claim ──────────────────────────────────────────
    queries.append(keyword_base)

    # ── Familie 2: document/artifact ────────────────────────────────────
    # Nur wenn das Signal ``missing_artifact_evidence`` vorliegt.
    # Artefakt-Begriff stammt aus dem Claim-Text – nie halluziniert.
    if "missing_artifact_evidence" in quality_signals:
        claim_lower = claim.text.lower()
        found_artifacts = [t for t in _ARTIFACT_TERMS if t in claim_lower]
        if found_artifacts:
            artifact_term = found_artifacts[0]
            # Nicht-Artefakt-Keywords als Kontext-Anker behalten
            ctx_kw = [k for k in kw_sorted if k not in _ARTIFACT_TERMS][:4]
            artifact_query = " ".join([artifact_term] + ctx_kw)
        else:
            artifact_query = f"{keyword_base} Dokument"
        if artifact_query and artifact_query not in queries:
            queries.append(artifact_query)

    # ── Familie 3: fact-check / debunk ───────────────────────────────────
    fc_query = f"{keyword_base} Faktencheck"
    if fc_query not in queries:
        queries.append(fc_query)

    # ── Familie 4: official response ─────────────────────────────────────
    # Kein konkreter Akteur: generisches „Stellungnahme" als Suffix.
    response_query = f"{keyword_base} Stellungnahme"
    if response_query not in queries:
        queries.append(response_query)

    return queries


def _count_strong_anchors(parts: list[str], profile: "ClaimSearchProfile") -> int:  # type: ignore[name-defined]
    """Zähle starke Anker in einer Query-Teilliste.

    Starke Anker sind:
        - Institution
        - Location
        - Policy-Kontext
        - konkrete Maßnahme (Sanktion/Enforcement)
        - gebundene Zahl (Zahl + Kontext-Wort, z.B. '250 Euro Bußgeld')

    Schwache Komponenten (generische Verben/Nomen, isolierte Zahlen,
    lose Hilfs-/Abstraktbegriffe) zählen NICHT.
    """
    text = " ".join(parts).lower()
    count = 0

    # Institution
    if profile.institutions and any(
        inst.lower() in text for inst in profile.institutions if inst
    ):
        count += 1
    # Location
    if profile.locations and any(
        loc.lower() in text for loc in profile.locations if loc
    ):
        count += 1
    # Policy-Kontext
    if profile.policy_terms and any(
        term.lower() in text for term in profile.policy_terms if term
    ):
        count += 1
    # Sanktion / Enforcement (konkrete Maßnahme)
    if profile.sanction_terms and any(
        term.lower() in text for term in profile.sanction_terms if term
    ):
        count += 1

    return count


def _bind_number_to_context(number: str, profile: "ClaimSearchProfile") -> str:  # type: ignore[name-defined]
    """Binde eine isolierte Zahl an ihren Kontext aus dem Profil.

    Isolierte Zahlen wie '250' oder '100' driften in generische Treffer
    (Währungsrechner, Produktseiten). Stattdessen gebundene Formen verwenden:
        '250' → '250 Euro Bußgeld'
        '100' → '100 Autofahrten'

    Falls kein sinnvoller Kontext gefunden wird, wird die Zahl NICHT verwendet.
    """
    # Prüfe ob Sanktions-Term die Zahl enthält (z.B. "250 Euro Bußgeld")
    for term in profile.sanction_terms:
        if number in term:
            return term  # Bereits gebunden

    # Prüfe ob der Frame-Rohtext eine gebundene Form enthält
    from models.schemas import ProcessedClaim as _PC2
    raw = ""
    # Versuche raw_text aus dem Frame zu bekommen
    if hasattr(profile, '_parent_frame_raw'):
        raw = profile._parent_frame_raw
    # Fallback: Suche im Claim-Text nach Zahl + Kontext-Wörtern
    # Pattern: Zahl gefolgt von oder vorangestellt mit Kontext (z.B. "100 Autofahrten", "250 Euro")
    for term in profile.policy_terms + profile.action_terms:
        if term:
            return f"{number} {term}"

    # Kein sinnvoller Kontext → Zahl nicht verwenden
    return ""


def _build_search_queries_from_profile(claim: "ProcessedClaim") -> list[str]:  # type: ignore[name-defined]
    """Baue Suchqueries aus dem strukturierten ClaimSearchProfile.

    Kein freier Claim-Text als primäre Basis. Verhindert Query-Kollaps
    auf generische Einzelbegriffe wie "Höhe", "Bürger", "Bußgeld".

    Query-Qualitätsregel:
        - Eine Query wird nur hoch priorisiert wenn sie ≥2 starke Anker enthält.
        - Zahlen nur in gebundener Form (z.B. '250 Euro Bußgeld', nicht '250').
        - Starke Anker: institution, location, policy_context, Sanktion, gebundene Zahl.
        - Schwache Komponenten: generische Verben, isolierte Zahlen, Abstraktbegriffe.

    Query-Typen:
        1. entity/policy   – Kernentitäten + Policy + gebundene Zahlen
        2. official-source – site:-Hint + Institution + Thema
        3. fact-check      – Faktenchecker + Kernentität + Policy
        4. sanction/number – Ort + gebundene Sanktion + Policy

    Returns:
        Liste von 2–4 kontextreichen Queries.
    """
    from models.schemas import ProcessedClaim as _PC
    profile = claim.search_profile
    if not profile:
        return []

    queries: list[str] = []

    # ── Query 1: entity/policy ─────────────────────────────────────────────
    # Bevorzuge Institution + Ort + Policy; falls keine Policy → action_terms
    q1_parts: list[str] = []
    q1_parts.extend(profile.institutions[:1])
    q1_parts.extend(profile.locations[:1])
    # Policy-Kontext bevorzugen; Fallback auf action_terms wenn leer
    if profile.policy_terms:
        q1_parts.extend(profile.policy_terms[:1])
    elif profile.action_terms:
        q1_parts.extend(profile.action_terms[:2])
    # Zahl nur gebunden verwenden – niemals isoliert
    if profile.number_terms:
        bound = _bind_number_to_context(profile.number_terms[0], profile)
        if bound:
            q1_parts.append(bound)
    if q1_parts:
        q1 = " ".join(p for p in q1_parts if p)
        # Nur akzeptieren wenn ≥2 starke Anker
        if len(q1.strip()) >= 6 and _count_strong_anchors(q1_parts, profile) >= 2:
            queries.append(q1.strip())
        elif len(q1.strip()) >= 6:
            # Fallback: trotzdem verwenden wenn Institution+Ort vorhanden
            if profile.institutions and profile.locations:
                queries.append(q1.strip())

    # ── Query 2: official-source ───────────────────────────────────────────
    # site:-Operator wird entfernt: SearXNG reicht ihn nicht zuverlässig an alle
    # Engines weiter. Stattdessen Domain als Keyword verwenden.
    if profile.official_source_hints:
        hint = profile.official_source_hints[0]
        if hint.startswith("site:"):
            hint = hint[5:]  # "site:hannover.de" → "hannover.de"
        q2_parts = [hint]
        q2_parts.extend(profile.institutions[:1])
        q2_parts.extend(profile.policy_terms[:1])
        # Ergänze Ort wenn noch nicht durch Institution abgedeckt
        if profile.locations and not any(
            loc.lower() in " ".join(q2_parts).lower() for loc in profile.locations
        ):
            q2_parts.extend(profile.locations[:1])
        q2 = " ".join(p for p in q2_parts if p)
        if q2.strip() not in queries:
            queries.append(q2.strip())

    # ── Query 3: fact-check ────────────────────────────────────────────────
    # site:-Operator wird entfernt: Faktencheck-Organisation als Keyword stattdessen.
    if profile.fact_check_hints:
        hint = profile.fact_check_hints[0]
        if hint.startswith("site:"):
            hint = hint[5:]  # "site:correctiv.org" → "correctiv.org"
        q3_parts = [hint]
        q3_parts.extend(profile.core_entities[:1])
        q3_parts.extend(profile.policy_terms[:1])
        q3 = " ".join(p for p in q3_parts if p)
        if q3.strip() and q3.strip() not in queries:
            queries.append(q3.strip())

    # ── Query 3b: fact-check ohne site:-Operator ─────────────────────────
    # SearXNG leitet site:-Operatoren nicht zuverlässig an alle Engines weiter.
    # Parallele Query mit expliziten Faktencheck-Keywords statt site:-Prefix.
    q3b_parts: list[str] = list(profile.core_entities[:2])
    q3b_parts.extend(profile.policy_terms[:1])
    q3b_parts.append("Faktencheck")
    q3b = " ".join(p for p in q3b_parts if p)
    if q3b.strip() and q3b.strip() not in queries:
        queries.append(q3b.strip())

    # ── Query 3c: Falschmeldung-Query für virale Claims ──────────────────
    q3c_parts = list(profile.core_entities[:2])
    q3c_parts.extend(profile.policy_terms[:1])
    q3c_parts.append("Falschmeldung")
    q3c = " ".join(p for p in q3c_parts if p)
    if q3c.strip() and q3c.strip() not in queries:
        queries.append(q3c.strip())

    # ── Query 5+6: procedural/official (Beschluss, Protokoll, Drucksache) ──────
    # Für Regelungsclaims: suche nach offiziellen Verfahrensdokumenten.
    # Ableitung ausschließlich aus Frame-Feldern des Claims – keine Halluzinationen.
    # Keine erfundenen Behörden, Städte oder Akteure: nur was Frame/Profil liefert.
    from models.schemas import ProcessedClaim as _PC_proc
    if isinstance(claim, _PC_proc) and claim.frame:
        _f = claim.frame
        _is_regulatory_q = bool(
            _f.sanction
            or _f.enforcement
            or (_f.policy_context and _f.institution)
        )
        if _is_regulatory_q:
            # Query 5: Institution + Ort + (Zeitbezug) + "Beschluss"
            _q5_parts: list[str] = []
            _q5_parts.extend(profile.institutions[:1])
            _q5_parts.extend(profile.locations[:1])
            if _f.time_reference:
                _q5_parts.append(_f.time_reference)
            _q5_parts.append("Beschluss")
            q5 = " ".join(p for p in _q5_parts if p)
            if q5.strip() and q5.strip() not in queries and _count_strong_anchors(_q5_parts, profile) >= 1:
                queries.append(q5.strip())

            # Query 6: Ort + Policy/Action + Protokoll/Drucksache
            _q6_parts: list[str] = []
            _q6_parts.extend(profile.locations[:1])
            _q6_parts.extend(profile.policy_terms[:1] or profile.action_terms[:1])
            _q6_parts.append("Ratsprotokoll Drucksache")
            q6 = " ".join(p for p in _q6_parts if p)
            if q6.strip() and q6.strip() not in queries and len(_q6_parts) >= 2:
                queries.append(q6.strip())

    # ── Query 4: sanction/number (nur bei konkreten Zahlen + Sanktionen) ──
    # Ort + Policy immer mitführen, damit Treffer wie "Bußgeld 250" ohne Kontext vermieden werden
    if profile.sanction_terms and profile.number_terms:
        q4_parts: list[str] = []
        # Ort als Kontext-Anker zuerst
        q4_parts.extend(profile.locations[:1])
        # Policy-Kontext mitführen für Verankerung
        q4_parts.extend(profile.policy_terms[:1])
        # Gebundene Sanktion verwenden statt isolierter Zahl
        q4_parts.extend(profile.sanction_terms[:1])
        q4 = " ".join(p for p in q4_parts if p)
        if q4.strip() and q4.strip() not in queries and _count_strong_anchors(q4_parts, profile) >= 2:
            queries.append(q4.strip())
    elif profile.sanction_terms:
        # Sanktion ohne Zahl: Ort + Policy + Sanktion
        q4_parts = []
        q4_parts.extend(profile.locations[:1])
        q4_parts.extend(profile.policy_terms[:1])
        q4_parts.extend(profile.sanction_terms[:1])
        q4 = " ".join(p for p in q4_parts if p)
        if q4.strip() and q4.strip() not in queries and _count_strong_anchors(q4_parts, profile) >= 2:
            queries.append(q4.strip())

    return queries


def _optimize_queries_with_llm(
    claim: Claim, llm: LLMClient, original_text: str = "",
) -> list[str] | None:
    """Nutze das LLM um optimierte Suchqueries aus dem Claim zu generieren.

    Wenn ein ClaimSearchProfile vorhanden ist, wird dessen Kontext an das
    LLM übergeben. Damit entstehen frame-basierte Queries statt reine
    Keyword-Extraktion aus freiem Text.

    Returns:
        Liste von 3-4 optimierten Queries, oder None bei Fehler.
    """
    user_msg = f"Behauptung: {claim.text}\nTyp: {claim.type.value}"
    if original_text and len(original_text) > len(claim.text) + 30:
        user_msg += f"\nOriginaltext (Kontext): {original_text[:500]}"

    # Frame-Kontext für bessere Query-Generierung mitgeben
    from models.schemas import ProcessedClaim as _PC
    if isinstance(claim, _PC) and claim.frame:
        f = claim.frame
        frame_summary_parts: list[str] = []
        if f.institution:
            frame_summary_parts.append(f"Institution: {f.institution}")
        if f.location:
            frame_summary_parts.append(f"Ort: {f.location}")
        if f.policy_context:
            frame_summary_parts.append(f"Kontext: {f.policy_context}")
        if f.numbers:
            frame_summary_parts.append(f"Zahlen: {', '.join(f.numbers)}")
        if f.sanction:
            frame_summary_parts.append(f"Sanktion: {f.sanction}")
        if frame_summary_parts:
            user_msg += "\n\n## Strukturierter Frame\n" + "\n".join(frame_summary_parts)

    try:
        raw = llm.complete(_QUERY_OPTIMIZER_PROMPT, user_msg, response_format="json")
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        # Akzeptiere sowohl direkte Liste als auch {"items": [...]}
        if isinstance(parsed, list):
            queries = [str(q).strip() for q in parsed if isinstance(q, str) and q.strip()]
        elif isinstance(parsed, dict) and "items" in parsed:
            queries = [str(q).strip() for q in parsed["items"] if isinstance(q, str) and q.strip()]
        else:
            return None
        return queries[:4] if queries else None
    except Exception as e:
        logger.warning("Query-Optimierung fehlgeschlagen: %s: %s", type(e).__name__, e)
        return None


def _build_search_queries(claim: Claim, original_text: str = "") -> list[str]:
    """Generiere Suchqueries adaptiv – bevorzuge frame-basierte Queries.

    Prioritäten:
        1. ClaimSearchProfile (frame-basiert) – außer bei Unspezifik + dünnem Profil
        2. Query-Familien für schwach spezifizierte Claims
           (``underspecified_actor`` / ``missing_artifact_evidence``):
           direct_claim | document/artifact | fact-check | official_response
        3. Adaptive Typ-Strategie aus freiem Claim-Text (Fallback)

    Query-Familien (Priorität 2) halluzinieren KEINE Akteure, Länder oder
    Behörden – sie basieren ausschließlich auf Begriffen aus dem Claim-Text.
    """
    from models.schemas import ProcessedClaim as _PC

    _underspec_signals = {"underspecified_actor", "missing_artifact_evidence"}

    # Priorität 1: Wenn ein SearchProfile vorhanden ist, nutze es primär.
    # Ausnahme: Bei Unspezifik-Signalen UND dünnem Profil (<3 Queries)
    # werden Query-Familien bevorzugt, da Profil-Felder dann meist leer sind.
    if isinstance(claim, _PC) and claim.search_profile:
        profile_queries = _build_search_queries_from_profile(claim)
        if profile_queries:
            is_underspecified = bool(_underspec_signals & set(claim.quality_signals or []))
            if not is_underspecified or len(profile_queries) >= 3:
                return profile_queries
            # Profil dünn + unterspecified → weiter zu Query-Familien

    # Priorität 2: Query-Familien für schwach spezifizierte Claims.
    # Verwendet nur Begriffe aus dem Claim-Text – keine Halluzinationen.
    if isinstance(claim, _PC) and _underspec_signals & set(claim.quality_signals or []):
        underspec_queries = _build_queries_for_underspecified_claim(claim, claim.quality_signals)
        if underspec_queries:
            return underspec_queries

    # Priorität 3 (Fallback): adaptive Strategie basierend auf Claim-Typ
    text = claim.text
    claim_type = claim.type.value

    queries = [text]  # Direktsuche mit vollem Claim-Text – immer dabei

    # ── Adaptive Strategie nach Claim-Typ ──────────────────────────

    suffix_fc = t("agents.fact_checker.search_suffix_factcheck")
    suffix_stats = t("agents.fact_checker.search_suffix_stats")
    suffix_official = t("agents.fact_checker.search_suffix_official")
    suffix_causal = t("agents.fact_checker.search_suffix_causal")

    if claim_type == "FACTUAL":
        # Einfache Fakten: Direktsuche reicht oft, Faktencheck als Ergänzung
        if len(text) > 60:
            queries.append(f"{text} {suffix_fc}")

    elif claim_type == "STATISTICAL":
        # Statistische Claims: Aggressive Suche nach Primärdaten
        queries.append(f"{text} {suffix_fc}")
        queries.append(f"{text} {suffix_stats}")
        queries.append(f"{text} {suffix_official}")
        # Kontext-Suche ist hier besonders wichtig
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    elif claim_type == "CAUSAL":
        # Kausalbehauptungen: Faktencheck + Korrelation vs. Kausalität
        queries.append(f"{text} {suffix_fc}")
        queries.append(f"{text} {suffix_causal}")

    elif claim_type == "CONTEXTUAL":
        # Kontextuelle Claims: Faktencheck + Kontext-Suche
        queries.append(f"{text} {suffix_fc}")
        if original_text and len(original_text) > len(text) + 30:
            context_query = _build_context_query(claim, original_text)
            if context_query and context_query not in queries:
                queries.append(context_query)

    else:
        # Fallback für unbekannte Typen
        queries.append(f"{text} {suffix_fc}")

    return queries


def _build_context_query(claim: Claim, original_text: str) -> str:
    """Baue eine kontextualisierte Suchanfrage aus Claim + Originaltext.

    Strategie: Nimm den Claim-Text und ergänze die wichtigsten
    thematischen Begriffe aus dem Originaltext, die im Claim fehlen.
    """
    claim_lower = claim.text.lower()

    # Extrahiere substantielle Wörter aus dem Originaltext (>4 Zeichen,
    # keine Stoppwörter), die NICHT bereits im Claim vorkommen
    stopwords = {
        "diese", "dieser", "dieses", "einen", "einem", "einer", "eines",
        "werden", "wurde", "worden", "haben", "hatte", "waren", "sind",
        "nicht", "sich", "dass", "wenn", "weil", "also", "auch", "noch",
        "schon", "immer", "durch", "nach", "über", "unter", "zwischen",
        "gegen", "damit", "dabei", "dafür", "darin", "darauf", "davon",
        "denen", "deren", "zeigen", "zeigt", "laut", "mehr", "sehr",
        "andere", "anderen", "anderer", "wieder", "bereits", "dabei",
        "beweist", "beweisen", "endgültig", "menschen", "daten",
    }

    # Alle "interessanten" Wörter aus dem Originaltext
    words = re.findall(r"[A-ZÄÖÜa-zäöüß]{4,}", original_text.lower())
    context_words = []
    seen: set[str] = set()
    for w in words:
        if w in seen or w in stopwords or w in claim_lower:
            continue
        seen.add(w)
        context_words.append(w)

    if not context_words:
        return ""

    # Nimm die ersten 3-4 Kontextbegriffe und kombiniere mit dem Claim-Kern
    # Kürze den Claim auf die ersten ~60 Zeichen für eine brauchbare Query
    claim_short = claim.text[:80].rsplit(" ", 1)[0] if len(claim.text) > 80 else claim.text
    extras = " ".join(context_words[:4])

    return f"{claim_short} {extras}"


def _evaluate_scrape_quality(
    ranked: list[RankedSource],
    scraped: list[ScrapedSource],
) -> tuple[bool, str]:
    """Prüfe ob die Scraping-Ergebnisse ausreichend sind.

    Returns:
        (needs_retry, reason) — True wenn ein Retry sinnvoll ist.
    """
    scrapable = [rs for rs in ranked if rs.should_scrape]

    # Fall A: Keine Quelle war scrapbar
    if not scrapable:
        return True, "no_scrapable_sources"

    # Fall B: Alle Scrapes fehlgeschlagen
    if scraped and all(not s.fetch_success for s in scraped):
        return True, "all_scrapes_failed"

    # Fall C: Alle erfolgreichen Scrapes haben low_relevance
    successful = [s for s in scraped if s.fetch_success]
    if successful and all(s.low_relevance for s in successful):
        return True, "all_low_relevance"

    return False, ""


def _build_fallback_queries(
    claim: Claim,
    original_queries: list[str],
) -> list[str]:
    """Generiere alternative Suchqueries für den Retry-Durchlauf.

    Strategien:
      1. Keyword-basierte Kurzquery (ohne Stoppwörter/Füllwörter)
      2. Keyword-Query + "Faktencheck"
      3. Zahlen-fokussierte Query (falls Zahlen im Claim)
    """
    from tools.scrape_ranker import _extract_claim_keywords

    keywords = _extract_claim_keywords(claim.text)
    if not keywords:
        return []

    # Strategie 1: Nur Keywords, kompakt
    keyword_query = " ".join(sorted(keywords)[:6])

    # Strategie 2: Keywords + Faktencheck
    suffix_fc = t("agents.fact_checker.search_suffix_factcheck")
    keyword_fc_query = f"{keyword_query} {suffix_fc}"

    # Strategie 3: Zahlen + Kontext-Keywords (für statistische Claims)
    numbers = re.findall(r"\d+[\.,]?\d*%?", claim.text)
    number_query = ""
    if numbers:
        number_query = f"{' '.join(numbers)} {keyword_query}"

    # Nur Queries die nicht schon im Original waren
    original_set = set(original_queries)
    fallback = []
    for q in (keyword_query, keyword_fc_query, number_query):
        if q and q not in original_set:
            fallback.append(q)

    return fallback


def _categories_for_claim(claim: Claim) -> str:
    """Bestimme SearXNG-Kategorien basierend auf dem Claim-Typ."""
    mapping = {
        "STATISTICAL": "general,science,news",
        "CAUSAL": "general,science",
        "FACTUAL": "general,news",
        "CONTEXTUAL": "general,news",
    }
    return mapping.get(claim.type.value, "general")


def _is_current_state_claim(claim_text: str) -> bool:
    """Erkennt Claims über aktuelle Amts-/Rolleninhaber (zeitkritisch).

    Prüft zwei Bedingungen:
      1. Ein Zustandsverb (ist, war, bleibt, wurde) → beschreibt aktuellen Zustand
      2. Ein Positionsbegriff (Bundeskanzler, Präsident, CEO, ...) → eine Rolle/Amt

    Beide Bedingungen müssen erfüllt sein, um False-Positives auf generische
    „ist"-Sätze zu vermeiden.
    """
    _STATE_VERBS = (
        r"\b(ist|war|ist\s+derzeit|ist\s+aktuell|ist\s+seit|bleibt|wurde\s+zum?|"
        r"amtiert|fungiert|dient|steht\s+vor|leitet|regiert)\b"
    )
    _POSITION_KEYWORDS = (
        r"\b(bundeskanzler(?:in)?|kanzler(?:in)?|pr[äa]sident(?:in)?|vizepr[äa]sident(?:in)?|"
        # Compound-fähig: (?:\w+)? erlaubt Präfix wie "Gesundheits-", "Partei-"
        r"(?:\w+)?minister(?:in)?|senator(?:in)?|"
        r"b[üu]rgermeister(?:in)?|oberbürgermeister(?:in)?|"
        r"(?:\w+)?premier(?:minister(?:in)?)?|(?:\w+)?vorsitzende[rn]?|"
        r"ceo|(?:\w+)?vorstandsvorsitzende[rn]?|(?:\w+)?vorstandschef(?:in)?|"
        r"(?:\w+)?gesch[äa]ftsf[üu]hrer(?:in)?|generalsekret[äa]r(?:in)?|"
        r"chef(?:in)?|direktor(?:in)?|leiter(?:in)?|"
        r"papst|k[öo]nig(?:in)?|monarch(?:in)?|"
        r"regierungschef(?:in)?|staatschef(?:in)?|staatsoberhaup[t]?)\b"
    )
    text_lower = claim_text.lower()
    return bool(re.search(_STATE_VERBS, text_lower, re.IGNORECASE)) and bool(
        re.search(_POSITION_KEYWORDS, text_lower, re.IGNORECASE)
    )


def _build_enriched_context(
    ranked: list[RankedSource],
    scraped: list[ScrapedSource],
) -> str:
    """Baue den angereicherten LLM-Kontext aus gerankten und gescrapten Quellen.

    Quellen mit Volltext werden bevorzugt angezeigt, Quellen ohne Volltext
    erhalten den Original-Snippet mit einem Hinweis auf den Grund.
    """
    skip_reason_labels = {
        "paywall": "Paywall",
        "low_tier": "Niedriger Quellen-Tier",
        "irrelevant": "Kein thematischer Bezug (Snippet-Analyse)",
        "limit_reached": "Scrape-Limit erreicht",
    }

    scraped_by_url: dict[str, ScrapedSource] = {s.url: s for s in scraped}

    # Sortiere: should_scrape=True zuerst
    sorted_ranked = sorted(ranked, key=lambda rs: (not rs.should_scrape, -rs.tier, -rs.relevance_score))

    from tools.source_classifier import classify_source
    parts: list[str] = []
    for i, rs in enumerate(sorted_ranked, 1):
        classified = classify_source(rs.result)
        tier_label = classified.tier_label
        title = rs.result.title
        url = rs.result.url

        sc = scraped_by_url.get(url)

        if sc and sc.fetch_success:
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Volltext-Auszug:\n  {sc.passage}"
            )
        elif sc and not sc.fetch_success:
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Snippet: {rs.result.snippet}\n"
                f"[Kein Volltext: {sc.error}]"
            )
        else:
            reason = skip_reason_labels.get(rs.skip_reason or "", rs.skip_reason or "")
            block = (
                f"[Quelle {i}] [{tier_label}] {title}\n"
                f"URL: {url}\n"
                f"Snippet: {rs.result.snippet}\n"
                f"[Kein Volltext: {reason}]"
            )

        parts.append(block)

    return "\n---\n".join(parts) if parts else "Keine Suchergebnisse gefunden."


def _adaptive_max_results(claim: Claim) -> int:
    """Bestimme die Anzahl der Suchergebnisse pro Query adaptiv.

    Einfache Claims brauchen weniger Ergebnisse, komplexe mehr.
    """
    if claim.type.value == "STATISTICAL":
        return 10  # Brauche mehr Quellen für Zahlenverifikation
    if claim.type.value in ("CAUSAL", "CONTEXTUAL"):
        return 8  # Kontext-Suche braucht etwas mehr
    return 5  # FACTUAL: weniger Ergebnisse reichen meist
