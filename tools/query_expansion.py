"""Query Expansion Engine – generiert diverse, kurze, fokussierte Suchanfragen.

Kernprinzip: Keyword-Suchmaschinen (DuckDuckGo, Brave, Qwant) arbeiten am besten
mit 3-5 präzisen Termen. Zu viele Terme → Recall sinkt drastisch.

7 orthogonale Strategien pro Claim:
  1. Kern-Entitäten     (hoher Recall, breit)
  2. Exakte Phrase      (hohe Precision, direkt)
  3. Zeitlich+Entität   (Aktualitäts-Filter)
  4. Offizielle Quelle  (site:-Hints, Primärquellen)
  5. Fact-Check         (Debunks, Gegenbelege)
  6. Negation/Debunk    (Falschmeldung, Hoax – Faktencheck-Vokabular)
  7. Dekomposition      (mehrdimensionale Claims → Sub-Queries)

NER-Integration: spaCy-Entitäten cross-validieren mit ClaimFrame-Feldern.
Synonym-Expansion: Token-Vektoren für Synonyme (E-Scooter ↔ Elektroroller).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools.data_loader import query_expansion_config
from tools.ner_extractor import ClaimEntities, extract_entities

if TYPE_CHECKING:
    from models.schemas import ClaimSearchProfile, ProcessedClaim
    from tools.claim_router import RouteResult


# Query-Expansion-Konstanten (aus data/scoring_weights.yaml)
_QE_CFG = query_expansion_config()
MAX_QUERY_TERMS = _QE_CFG.get("max_query_terms", 5)
MAX_PER_FAMILY = _QE_CFG.get("max_per_family", 2)
MAX_TOTAL_QUERIES = _QE_CFG.get("max_total_queries", 14)


@dataclass
class QueryVariant:
    """Einzelne Query-Variante mit Routing-Metadaten.

    Attributes:
        text:              Eigentlicher Query-String (max 5 Terme)
        family:            Strategie-Familie (entity_core, phrase, temporal, ...)
        priority:          Priorität für semantische Engines [0.0–1.0]
        engine_hint:       SearXNG Engine-Vorschlag ("brave", "duckduckgo", None)
        category_hint:     SearXNG Kategorie ("news", "general", "science")
        time_range_hint:   SearXNG Zeitraum ("day", "week", "month", None)
        page_hint:         SearXNG Seitennummer (1 oder 2 für Multi-Page)
        anchors:           Vorhandene starke Anker
    """

    text: str
    family: str
    priority: float
    engine_hint: str | None = None
    category_hint: str | None = None
    time_range_hint: str | None = None
    page_hint: int = 1
    anchors: set[str] = field(default_factory=set)


def _trim_query(terms: list[str], max_terms: int = MAX_QUERY_TERMS) -> str:
    """Kombiniert Terms zu einer Query, max max_terms Wörter gesamt.

    Priorisiert spezifischere (längere) Terme.
    """
    # Normalisieren: leere Terme und Duplikate entfernen
    clean: list[str] = []
    seen: set[str] = set()
    for t in terms:
        t = t.strip()
        if not t:
            continue
        t_lower = t.lower()
        if t_lower not in seen:
            seen.add(t_lower)
            clean.append(t)

    # Gesamtzahl Wörter zählen – terme werden als Wörter gesparsed
    result_terms: list[str] = []
    word_count = 0
    for term in clean:
        term_words = len(term.split())
        if word_count + term_words > max_terms:
            break
        result_terms.append(term)
        word_count += term_words

    return " ".join(result_terms)


def _merge_ner_with_profile(
    ner: ClaimEntities,
    profile: ClaimSearchProfile | None,
) -> dict[str, list[str]]:
    """Kombiniert NER-Entitäten mit ClaimSearchProfile-Feldern.

    NER ergänzt das Profil – ersetzt es nicht. Wenn das Profil Entitäten hat,
    die spaCy übersehen hat, bleiben sie erhalten.

    Returns:
        Dict mit Schlüsseln: locations, organizations, money, dates, misc, key_nouns
    """
    merged: dict[str, list[str]] = {
        "locations": list(ner.locations),
        "organizations": list(ner.organizations),
        "money": list(ner.money),
        "dates": list(ner.dates),
        "misc": list(ner.misc),
        "key_nouns": list(ner.key_nouns),
    }

    if profile is None:
        return merged

    def _add_unique(target: list[str], sources: list[str]) -> None:
        existing_lower = {x.lower() for x in target}
        for s in sources:
            s = s.strip()
            if s and s.lower() not in existing_lower:
                target.append(s)
                existing_lower.add(s.lower())

    # Profile-Felder in passende Kategorien einordnen
    _add_unique(merged["locations"], profile.locations)
    _add_unique(merged["organizations"], profile.institutions)

    # core_entities → misc (falls nicht bereits in LOC/ORG)
    all_specific = set(
        (x.lower() for x in merged["locations"] + merged["organizations"])
    )
    for ent in profile.core_entities:
        if ent.strip().lower() not in all_specific:
            _add_unique(merged["misc"], [ent])

    # policy_terms → key_nouns
    _add_unique(merged["key_nouns"], profile.policy_terms)
    _add_unique(merged["key_nouns"], profile.action_terms)

    # sanction_terms + number_terms → money/misc
    _add_unique(merged["money"], profile.number_terms)
    _add_unique(merged["misc"], profile.sanction_terms)

    return merged


class QueryExpansionEngine:
    """Generiert 6-10 diverse, kurze Queries aus NER + ClaimSearchProfile.

    5 orthogonale Strategien:
    1. entity_core:    Kern-Entitäten, 3-4 Terme, breiter Recall
    2. phrase:         Exakte Phrasen in Anführungszeichen, hohe Precision
    3. temporal:       Entitäten + Zeitbezug (Jahr/Datum aus Claim)
    4. official_source: site:-Hints auf Primärquellen
    5. factcheck:      Faktencheck/Debunking-Queries

    Jede Strategie produziert max 2 Queries → ~10 Queries gesamt.
    Nach Dedup und Prio-Sortierung: 6-8 finale Queries.
    """

    def expand(
        self,
        claim: ProcessedClaim,
        route_result: RouteResult,
        search_profile: ClaimSearchProfile,
    ) -> list[QueryVariant]:
        """Hauptmethode: Generiert QueryVariants aus Claim + Routing + Profil.

        Args:
            claim:          ProcessedClaim mit text und frame
            route_result:   ClaimRouter-Ergebnis (domains, jurisdiction, site_hints)
            search_profile: SearchProfile (entities, policies, locations etc.)

        Returns:
            Liste von QueryVariants, sortiert nach Priorität (höchste zuerst)
        """
        # NER auf Claim-Text
        ner = extract_entities(claim.text)

        # NER + Profil zusammenführen
        merged = _merge_ner_with_profile(ner, search_profile)

        variants: list[QueryVariant] = []

        # Strategie 1: Kern-Entitäten (hoher Recall)
        variants.extend(self._strategy_entity_core(merged, route_result))

        # Strategie 2: Exakte Phrasen (hohe Precision)
        variants.extend(self._strategy_exact_phrase(claim.text, merged))

        # Strategie 3: Zeitlich (Aktualität)
        variants.extend(self._strategy_temporal(merged))

        # Strategie 4: Offizielle Quellen (site:-Hints)
        variants.extend(self._strategy_official_source(merged, route_result, search_profile))

        # Strategie 5: Domänenspezifische Templates (REGULATORY, STATISTICAL)
        variants.extend(self._strategy_domain_templates(merged, route_result, claim))

        # Strategie 6: Fact-Check (Debunking)
        variants.extend(self._strategy_factcheck(merged))

        # Strategie 7: Negation/Debunk (Falschmeldung, Hoax)
        variants.extend(self._strategy_negation_search(merged))

        # Strategie 8: Dekomposition (mehrdimensionale Claims)
        variants.extend(self._strategy_decomposition(claim.text, merged))

        # Deduplizieren und nach Priorität sortieren
        return self._deduplicate(variants)

    # ── Strategie 1: Kern-Entitäten ───────────────────────────────────────────

    def _strategy_entity_core(
        self,
        merged: dict[str, list[str]],
        route_result: RouteResult,
    ) -> list[QueryVariant]:
        """3-4 Terme: Ort + Hauptentität + ggf. Geld/Sanktion.

        Ziel: Maximaler Recall, breite Treffer.
        Beispiel: "Hannover E-Scooter Bußgeld"
        """
        variants = []
        locs = merged["locations"]
        orgs = merged["organizations"]
        money = merged["money"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]

        # Primäre Entität: LOC > ORG > MISC > Key-Noun (Fallback)
        primary = locs[:1] or orgs[:1] or misc[:1] or nouns[:1]
        used: set[str] = {t.lower() for t in primary}
        # Sekundäre Entität: nächste nicht-duplizierte aus MISC > Key-Nouns
        secondary = [t for t in misc + nouns if t.lower() not in used][:1]
        used |= {t.lower() for t in secondary}
        # Optionale Spezifikation: Geld > nächste nicht-duplizierte Entität
        spec = money[:1] or [t for t in misc + nouns if t.lower() not in used][:1]

        # Query 1: primary + secondary + spec
        if primary and secondary:
            terms = primary + secondary + spec
            q = _trim_query(terms)
            if len(q.split()) >= 2:
                variants.append(QueryVariant(
                    text=q,
                    family="entity_core",
                    priority=1.0,
                    category_hint="general",
                    anchors={"location" if locs else "entity", "secondary"},
                ))

        # Query 2: Nur ORG/Institution + Schlüsselbegriff (für institutionelle Claims)
        if orgs and nouns:
            terms = orgs[:1] + nouns[:2]
            q = _trim_query(terms)
            if len(q.split()) >= 2 and q != (variants[0].text if variants else ""):
                variants.append(QueryVariant(
                    text=q,
                    family="entity_core",
                    priority=0.95,
                    category_hint="general",
                    anchors={"organization", "policy"},
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 2: Exakte Phrasen ────────────────────────────────────────────

    def _strategy_exact_phrase(
        self,
        claim_text: str,
        merged: dict[str, list[str]],
    ) -> list[QueryVariant]:
        """Anführungszeichen um markante Phrasen + Kontextterm.

        Ziel: Hohe Precision, direkte Matches.
        Beispiel: '"E-Scooter Bußgeld" Hannover 250'
        """
        variants = []
        locs = merged["locations"]
        money = merged["money"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]

        # Beste Phrase: längster MISC-Term oder Key-Noun (min. 2 Wörter)
        phrase_candidates = [m for m in misc + nouns if len(m.split()) >= 2]
        if not phrase_candidates:
            # Fallback: Zwei separate Terme als Phrase zusammensetzen
            combo = (misc[:1] + nouns[:1])
            if len(combo) == 2:
                phrase_candidates = [" ".join(combo)]

        context = locs[:1] + money[:1]

        if phrase_candidates:
            phrase = phrase_candidates[0]
            # Anführungszeichen für exakte Phrasensuche
            quoted = f'"{phrase}"'
            terms = [quoted] + context
            q = _trim_query(terms)
            if q and q != quoted:  # Muss mehr als nur die Phrase enthalten
                variants.append(QueryVariant(
                    text=q,
                    family="phrase",
                    priority=0.93,
                    engine_hint="duckduckgo",
                    category_hint="general",
                    anchors={"phrase", "context"},
                ))

        # Query 2: Wenn Geldbetrag vorhanden → Betrag + Kontext
        if money and (locs or misc):
            money_val = money[0]
            context2 = locs[:1] + misc[:1]
            terms = [money_val] + context2
            q = _trim_query(terms)
            if len(q.split()) >= 2:
                variants.append(QueryVariant(
                    text=q,
                    family="phrase",
                    priority=0.90,
                    category_hint="general",
                    anchors={"money", "entity"},
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 3: Zeitlich ──────────────────────────────────────────────────

    def _strategy_temporal(
        self,
        merged: dict[str, list[str]],
    ) -> list[QueryVariant]:
        """Entitäten + Zeitangabe für aktualitätssensitive Claims.

        Ziel: Artikel aus konkretem Zeitraum finden.
        Beispiel: "Hannover E-Scooter 2025 Verordnung"
        """
        variants = []
        locs = merged["locations"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]
        dates = merged["dates"]

        if not dates:
            return variants  # Keine Zeitangabe → Strategie übersprungen

        # Nur Jahreszahlen als Zeitbegriff (kürzere, präzisere Queries)
        year_dates = [d for d in dates if re.match(r"^\d{4}$", d.strip())]
        time_term = year_dates[:1] or dates[:1]

        primary = locs[:1] or misc[:1]
        secondary = misc[:1] if primary != misc[:1] else nouns[:1]

        if primary:
            terms = primary + secondary + time_term
            q = _trim_query(terms)
            if len(q.split()) >= 2:
                variants.append(QueryVariant(
                    text=q,
                    family="temporal",
                    priority=0.88,
                    category_hint="news",
                    time_range_hint="year",
                    anchors={"location", "temporal"},
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 4: Offizielle Quellen ───────────────────────────────────────

    def _strategy_official_source(
        self,
        merged: dict[str, list[str]],
        route_result: RouteResult,
        profile: ClaimSearchProfile | None,
    ) -> list[QueryVariant]:
        """site:-Hint + kompakte Keywords für offizielle Primärquellen.

        Ziel: Direkte Treffer auf Behörden/Instituts-Websites.
        Beispiel: "site:hannover.de E-Scooter Bußgeld"
        """
        variants = []
        locs = merged["locations"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]
        money = merged["money"]

        # Site-Hints aus Routing und Profil zusammenführen
        site_hints: list[str] = []
        if route_result and route_result.site_hints:
            site_hints.extend(route_result.site_hints)
        if profile and profile.official_source_hints:
            site_hints.extend(profile.official_source_hints)

        # Duplikate entfernen (Reihenfolge beibehalten)
        seen_hints: set[str] = set()
        unique_hints = []
        for h in site_hints:
            if h not in seen_hints:
                seen_hints.add(h)
                unique_hints.append(h)

        # Keywords für site:-Queries: 2-3 Terme (site: zählt als 1)
        kw_terms = (misc[:1] or locs[:1]) + nouns[:1] + money[:1]

        for hint in unique_hints[:MAX_PER_FAMILY]:
            terms = [hint] + kw_terms[:2]  # site: + max 2 weitere Terme
            q = _trim_query(terms, max_terms=4)
            if q:
                variants.append(QueryVariant(
                    text=q,
                    family="official_source",
                    priority=0.97,
                    category_hint="general",
                    anchors={"official_source"},
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 5: Domänenspezifische Templates ─────────────────────────────

    def _strategy_domain_templates(
        self,
        merged: dict[str, list[str]],
        route_result: RouteResult,
        claim: ProcessedClaim,
    ) -> list[QueryVariant]:
        """Domänenspezifische Query-Templates für REGULATORY und STATISTICAL Claims.

        Generiert gezielte Queries mit domänenspezifischen Schlüsselwörtern,
        die generische Strategien nicht abdecken.
        """
        variants: list[QueryVariant] = []
        domains = [d.value if hasattr(d, "value") else str(d) for d in (route_result.domains or [])]
        nouns = merged["key_nouns"]
        misc = merged["misc"]
        topic_terms = (nouns[:2] or misc[:2])
        topic = " ".join(topic_terms) if topic_terms else ""

        if not topic:
            return []

        jurisdiction = route_result.jurisdiction or "global"

        # ── REGULATORY: Verordnungen, Richtlinien, Strafen ──
        if "regulatory" in domains or "legal" in domains:
            # Extract dates and numbers from claim for targeted queries
            dates = merged.get("dates", [])
            numbers = merged.get("numbers", [])
            date_term = dates[0] if dates else ""
            penalty_term = numbers[0] if numbers else ""

            templates = []
            if jurisdiction == "eu":
                templates = [
                    f"EU directive {topic} regulation",
                    f"EU Verordnung {topic} Richtlinie",
                    f"EU {topic} Inkrafttreten {date_term}".strip(),
                    f"EU {topic} Strafe Bußgeld {penalty_term}".strip(),
                    f"EUR-Lex {topic} directive",
                ]
            elif jurisdiction == "de":
                templates = [
                    f"Gesetz {topic} Verordnung Deutschland",
                    f"{topic} Regelung Bußgeld Strafe",
                    f"{topic} Inkrafttreten {date_term} Deutschland".strip(),
                ]
            else:
                templates = [
                    f"regulation {topic} enforcement",
                    f"{topic} regulation penalty fine",
                ]

            for t_text in templates[:MAX_PER_FAMILY]:
                q = _trim_query(t_text.split(), max_terms=5)
                if q:
                    variants.append(QueryVariant(
                        text=q,
                        family="domain_template",
                        priority=0.93,
                        category_hint="general",
                        anchors={"domain_template"},
                    ))

        # ── STATISTICAL: Daten, Statistiken, offizielle Zahlen ──
        if "statistical" in domains or "economic" in domains:
            templates = []
            if jurisdiction == "de":
                templates = [
                    f"{topic} Statistik Deutschland Daten",
                    f"{topic} Destatis Zahlen",
                ]
            elif jurisdiction == "global":
                templates = [
                    f"{topic} statistics data {merged.get('dates', [''])[0]}".strip(),
                    f"{topic} official figures report",
                ]
            else:
                templates = [
                    f"{topic} statistics {jurisdiction} data",
                ]

            for t_text in templates[:MAX_PER_FAMILY]:
                q = _trim_query(t_text.split(), max_terms=5)
                if q:
                    variants.append(QueryVariant(
                        text=q,
                        family="domain_template",
                        priority=0.91,
                        category_hint="general",
                        anchors={"domain_template"},
                    ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 6: Fact-Check ────────────────────────────────────────────────

    def _strategy_factcheck(
        self,
        merged: dict[str, list[str]],
    ) -> list[QueryVariant]:
        """Kern-Entitäten + Faktencheck-Schlüsselwort.

        Ziel: Bestehende Fact-Checks und Debunks finden.
        Beispiel: "E-Scooter Bußgeld Faktencheck"
                  "site:correctiv.org Hannover E-Scooter"
        """
        variants = []
        locs = merged["locations"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]

        # Beste 2 Terme für Fact-Check-Query (dedupliziert)
        seen_terms: set[str] = set()
        core: list[str] = []
        for t in (locs[:1] or misc[:1]) + misc[:2] + nouns[:2]:
            if t.lower() not in seen_terms:
                seen_terms.add(t.lower())
                core.append(t)
            if len(core) >= 2:
                break
        kw = " ".join(core)

        if kw:
            # Query 1: Keywords + "Faktencheck"
            q1 = _trim_query([kw, "Faktencheck"])
            variants.append(QueryVariant(
                text=q1,
                family="factcheck",
                priority=0.92,
                engine_hint="duckduckgo",
                category_hint="news",
                anchors={"factcheck"},
            ))

            # Query 2: site:correctiv.org + Keywords
            q2 = _trim_query(["site:correctiv.org", kw], max_terms=4)
            variants.append(QueryVariant(
                text=q2,
                family="factcheck",
                priority=0.88,
                category_hint="news",
                anchors={"factcheck", "official_source"},
            ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 6: Negation/Debunk ───────────────────────────────────────────

    def _strategy_negation_search(
        self,
        merged: dict[str, list[str]],
    ) -> list[QueryVariant]:
        """Generiert Debunk-orientierte Queries mit Falschmeldung/Hoax-Termen.

        Ziel: Findet Faktenchecks die andere Terminologie verwenden als der Claim.
        Beispiel: "EU Fleisch Falschmeldung", "WhatsApp kostenpflichtig Hoax"
        """
        variants: list[QueryVariant] = []
        locs = merged["locations"]
        orgs = merged["organizations"]
        misc = merged["misc"]
        nouns = merged["key_nouns"]

        # Kern-Entitäten sammeln (dedupliziert)
        core: list[str] = []
        seen: set[str] = set()
        for t in locs[:1] + orgs[:1] + misc[:2] + nouns[:2]:
            if t.lower() not in seen:
                seen.add(t.lower())
                core.append(t)
            if len(core) >= 2:
                break

        if not core:
            return []

        entity_str = " ".join(core)
        debunk_terms = ["Falschmeldung", "Hoax", "Kettenbrief", "Faktencheck"]

        for term in debunk_terms[:2]:
            q = _trim_query([entity_str, term])
            if len(q.split()) >= 2:
                variants.append(QueryVariant(
                    text=q,
                    family="negation",
                    priority=0.82,
                    category_hint="general",
                    anchors={"debunk", "factcheck"},
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Strategie 7: Dekomposition ──────────────────────────────────────────────

    def _strategy_decomposition(
        self,
        claim_text: str,
        merged: dict[str, list[str]],
    ) -> list[QueryVariant]:
        """Zerlegt mehrdimensionale Claims in separate Sub-Queries.

        Bei Claims mit 2+ Dimensionen (z.B. rechtliche Referenz + Konsequenz)
        werden separate Queries für jede Dimension generiert.
        Beispiel: "§14a EnWG steuerbare Verbrauchseinrichtung" + "EnWG Waschmaschine"
        """
        variants: list[QueryVariant] = []
        misc = merged["misc"]
        nouns = merged["key_nouns"]
        orgs = merged["organizations"]
        locs = merged["locations"]

        # Dimension 1: Rechtliche Referenzen (§-Paragraphen, Gesetze)
        legal_refs = [m for m in misc if m.startswith("§") or re.match(r"^[A-Z]{2,}$", m)]
        # Dimension 2: Sachliche Terme (Nouns, Orgs)
        subject_terms = nouns[:3] + orgs[:1]

        has_legal = len(legal_refs) >= 1
        has_subjects = len(subject_terms) >= 2

        if has_legal and has_subjects:
            # Sub-Query 1: Rechtliche Referenz + Top-Subject
            q1 = _trim_query(legal_refs[:2] + subject_terms[:1])
            if len(q1.split()) >= 2:
                variants.append(QueryVariant(
                    text=q1,
                    family="decomposition",
                    priority=0.78,
                    category_hint="general",
                    anchors={"legal", "specific"},
                ))

            # Sub-Query 2: Location/Org + verbleibende Subjects
            context = locs[:1] or orgs[:1]
            remaining = [t for t in subject_terms if t not in subject_terms[:1]][:2]
            if context and remaining:
                q2 = _trim_query(context + remaining)
                if len(q2.split()) >= 2:
                    variants.append(QueryVariant(
                        text=q2,
                        family="decomposition",
                        priority=0.76,
                        category_hint="general",
                        anchors={"context", "subject"},
                    ))
        elif len(nouns) >= 3:
            # Kein legaler Kontext, aber viele Terme → Split in zwei Hälften
            mid = len(nouns) // 2
            q1 = _trim_query((locs[:1] or orgs[:1]) + nouns[:mid])
            q2 = _trim_query((locs[:1] or orgs[:1]) + nouns[mid:mid+2])
            if len(q1.split()) >= 2:
                variants.append(QueryVariant(
                    text=q1,
                    family="decomposition",
                    priority=0.74,
                    category_hint="general",
                ))
            if len(q2.split()) >= 2 and q2 != q1:
                variants.append(QueryVariant(
                    text=q2,
                    family="decomposition",
                    priority=0.72,
                    category_hint="general",
                ))

        return variants[:MAX_PER_FAMILY]

    # ── Deduplication & Sortierung ─────────────────────────────────────────────

    def _deduplicate(self, variants: list[QueryVariant]) -> list[QueryVariant]:
        """Entfernt Duplikate, sortiert nach Priorität, begrenzt auf MAX_TOTAL_QUERIES."""
        seen: dict[str, QueryVariant] = {}
        for v in sorted(variants, key=lambda x: x.priority, reverse=True):
            normalized = v.text.strip().lower()
            if normalized and normalized not in seen:
                seen[normalized] = v

        result = list(seen.values())[:MAX_TOTAL_QUERIES]
        return result
