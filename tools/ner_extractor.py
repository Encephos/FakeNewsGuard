"""NER-Extractor – spaCy-basierte Entitätsextraktion für Claims.

Nutzt de_core_news_lg für:
- Named Entity Recognition (PER, ORG, LOC, MONEY, DATE, MISC)
- Token-level Vektoren für Synonym-Erkennung (E-Scooter ↔ Elektroroller)
- Cross-Validierung mit ClaimFrame-Feldern

Fallback auf regex-basierte Extraktion wenn spaCy nicht verfügbar.

Modell-Installation:
    pip install spacy
    python -m spacy download de_core_news_lg
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tools.data_loader import (
    ner_known_orgs, ner_institution_patterns, ner_law_acronyms,
    stopwords as load_stopwords,
)

if TYPE_CHECKING:
    pass

# Lazy-loaded spaCy model – wird einmal geladen und gecacht
_NLP = None
_NLP_LOCK = threading.Lock()
_SPACY_AVAILABLE: bool | None = None  # None = noch nicht geprüft


def _get_nlp():
    """Gibt das spaCy-Modell zurück (lazy, thread-safe). None wenn nicht verfügbar."""
    global _NLP, _SPACY_AVAILABLE
    if _SPACY_AVAILABLE is False:
        return None
    with _NLP_LOCK:
        if _NLP is None:
            try:
                import spacy
                _NLP = spacy.load("de_core_news_lg")
                _SPACY_AVAILABLE = True
            except (ImportError, OSError):
                _SPACY_AVAILABLE = False
                return None
    return _NLP


@dataclass
class ClaimEntities:
    """Extrahierte Entitäten aus einem Claim-Text.

    Attributes:
        persons:      Personennamen (spaCy PER)
        organizations: Organisationen & Institutionen (spaCy ORG)
        locations:    Orte, Länder, Regionen (spaCy LOC + GPE)
        money:        Geldbeträge mit Währung (spaCy MONEY → "250 Euro")
        dates:        Zeitangaben (spaCy DATE → "2025", "ab Januar")
        misc:         Sonstige Named Entities (spaCy MISC)
        numbers:      Isolierte Zahlen/Prozentwerte (regex-basiert)
        key_nouns:    Wichtige Substantive ohne Stoppwörter (für Query-Terme)
    """

    persons: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    money: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    misc: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    key_nouns: list[str] = field(default_factory=list)

    def all_named_entities(self) -> list[str]:
        """Alle benannten Entitäten als flache Liste (ohne Duplikate)."""
        seen: set[str] = set()
        result = []
        for ent in (
            self.persons
            + self.organizations
            + self.locations
            + self.money
            + self.dates
            + self.misc
        ):
            normalized = ent.strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                result.append(normalized)
        return result

    def query_terms(self, max_terms: int = 5) -> list[str]:
        """Beste Terme für Query-Konstruktion – priorisiert spezifische Entitäten.

        Reihenfolge: LOC > ORG > MONEY > MISC > PER > KEY_NOUNS
        Rationale: Für Fact-Checking sind Ort und Institution die stärksten Anker.
        """
        candidates: list[tuple[int, str]] = []  # (priority, term)
        for loc in self.locations:
            candidates.append((0, loc))
        for org in self.organizations:
            candidates.append((1, org))
        for money in self.money:
            candidates.append((2, money))
        for misc in self.misc:
            candidates.append((3, misc))
        for per in self.persons:
            candidates.append((4, per))
        for num in self.numbers:
            candidates.append((5, num))
        for noun in self.key_nouns:
            candidates.append((6, noun))

        seen: set[str] = set()
        result = []
        for _, term in sorted(candidates, key=lambda x: x[0]):
            norm = term.strip()
            if norm and norm.lower() not in seen:
                seen.add(norm.lower())
                result.append(norm)
            if len(result) >= max_terms:
                break
        return result


# Stoppwörter für Key-Noun-Extraktion (keine Query-Terme)
_KEY_NOUN_STOPWORDS = frozenset({
    "der", "die", "das", "des", "dem", "den",
    "ein", "eine", "einer", "eines", "einem", "einen",
    "und", "oder", "aber", "dass", "weil", "wenn", "als",
    "ist", "sind", "war", "waren", "hat", "haben", "hatte",
    "soll", "sollte", "kann", "könnte", "muss", "müsste",
    "mit", "von", "für", "auf", "bei", "nach", "seit",
    "über", "unter", "durch", "gegen", "zwischen",
    "auch", "noch", "schon", "nur", "nicht", "mehr", "sehr",
    "man", "sich", "wir", "sie", "er", "es", "ihm", "ihr",
    "jahr", "euro", "prozent",  # oft zu generisch als alleiniger Term
})


def extract_entities(text: str) -> ClaimEntities:
    """Extrahiert benannte Entitäten aus einem Claim-Text.

    Nutzt spaCy wenn verfügbar, sonst regex-basierter Fallback.

    Args:
        text: Claim-Text (beliebige Länge)

    Returns:
        ClaimEntities mit allen erkannten Entitäten
    """
    nlp = _get_nlp()
    if nlp is not None:
        return _extract_spacy(nlp, text)
    return _extract_regex_fallback(text)


def _extract_spacy(nlp, text: str) -> ClaimEntities:
    """spaCy-basierte NER-Extraktion."""
    doc = nlp(text)
    result = ClaimEntities()

    for ent in doc.ents:
        label = ent.label_
        value = ent.text.strip()
        if not value:
            continue
        if label == "PER":
            result.persons.append(value)
        elif label == "ORG":
            result.organizations.append(value)
        elif label in ("LOC", "GPE"):
            result.locations.append(value)
        elif label == "MONEY":
            result.money.append(value)
        elif label == "DATE":
            # Nur konkrete Zeitangaben (Jahreszahlen, Monatsnamen etc.)
            if re.search(r"\d{4}|januar|februar|märz|april|mai|juni|juli|august|"
                         r"september|oktober|november|dezember|heute|morgen|gestern",
                         value.lower()):
                result.dates.append(value)
        elif label == "MISC":
            result.misc.append(value)

    # Zahlen + Prozente via Regex (spaCy CARDINAL oft unzuverlässig für DE)
    number_pattern = re.compile(
        r"\b\d+(?:[.,]\d+)?(?:\s*(?:Euro|EUR|€|Prozent|%|Mrd\.|Mio\.|Tsd\.))?\b"
    )
    for m in number_pattern.finditer(text):
        val = m.group().strip()
        if val and val not in result.numbers:
            result.numbers.append(val)

    # Key-Nouns: Substantive (NOUN/PROPN) ohne Stoppwörter, min. 5 Zeichen
    seen_nouns: set[str] = set()
    for token in doc:
        if token.pos_ in ("NOUN", "PROPN") and not token.is_stop:
            lemma = token.lemma_.lower()
            surface = token.text
            if (
                len(surface) >= 5
                and lemma not in _KEY_NOUN_STOPWORDS
                and surface.lower() not in seen_nouns
                # Kein reines Duplikat einer bereits erkannten NE
                and not any(surface.lower() in ent.lower()
                            for ent in result.all_named_entities())
            ):
                seen_nouns.add(surface.lower())
                result.key_nouns.append(surface)

    return result


def _extract_regex_fallback(text: str) -> ClaimEntities:
    """Erweiterter Regex-Fallback wenn spaCy nicht installiert ist.

    Verbesserungen gegenüber naiver Großschreibungs-Heuristik:
    - Abkürzungen in Klammern extrahieren: "(KBA)", "(Destatis)"
    - Bindestrich-Komposita normalisieren: "Kraftfahrt-Bundesamt" → "Kraftfahrt-Bundesamt"
    - Zahlen ohne Einheit erfassen: "450", "11,4"
    - Key-Nouns: Nomen-Kandidaten (Großschreibung im Satz, min. 6 Zeichen, kein Satzanfang)
    - Bekannte deutsche Institutionen via Wörterbuch
    """
    result = ClaimEntities()

    # ── 1. Abkürzungen in Klammern (höchste Präzision) ────────────────────
    abbrev_pattern = re.compile(r"\(([A-ZÄÖÜ][A-Za-zÄÖÜäöü0-9]{1,12})\)")
    for m in abbrev_pattern.finditer(text):
        abbrev = m.group(1)
        # Bekannte Institutionen → organizations
        _KNOWN_ORGS = ner_known_orgs()
        if abbrev in _KNOWN_ORGS or abbrev.isupper():
            result.organizations.append(abbrev)

    # ── 2. Bekannte Institutionen im Fließtext ────────────────────────────
    _INSTITUTION_PATTERNS = ner_institution_patterns()
    # Wörter die zu gematchten Institutionsnamen gehören → aus key_nouns ausschließen
    _institution_matched_words: set[str] = set()
    for pattern, kind in _INSTITUTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            match = re.search(pattern, text, re.IGNORECASE)
            val = match.group().strip()
            if kind == "org" and val not in result.organizations:
                result.organizations.append(val)
            elif kind == "loc" and val not in result.locations:
                result.locations.append(val)
            # Alle Wörter des Matches als "bekannt" markieren
            for word in re.findall(r"[A-ZÄÖÜ][a-zäöüA-ZÄÖÜ\-]{3,}", val):
                _institution_matched_words.add(word.lower())

    # ── 2b. Legale Referenzen (§-Paragraphen, Gesetzes-Akronyme) ─────────
    # §14a, § 14a, §§ 14-16
    para_pattern = re.compile(r"§+\s*\d+[a-z]?(?:\s*(?:Abs\.|Abs)\s*\d+)?")
    for m in para_pattern.finditer(text):
        val = m.group().strip()
        if val not in result.misc:
            result.misc.append(val)

    # Bekannte Gesetzesabkürzungen
    _LAW_ACRONYMS = ner_law_acronyms()
    for m in _LAW_ACRONYMS.finditer(text):
        val = m.group()
        if val not in result.misc:
            result.misc.append(val)

    # ── 3. Geldbeiträge ───────────────────────────────────────────────────
    money_pattern = re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:Euro|EUR|€|Dollar|USD|\$|Pfund|GBP)\b",
        re.IGNORECASE,
    )
    for m in money_pattern.finditer(text):
        result.money.append(m.group().strip())

    # ── 4. Prozentwerte und Dezimalzahlen ─────────────────────────────────
    pct_pattern = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:Prozent|%)\b")
    for m in pct_pattern.finditer(text):
        result.numbers.append(m.group().strip())

    # Große Zahlen ohne Einheit (> 10, relevant für Statistiken)
    num_pattern = re.compile(r"\b([1-9]\d{1,}(?:[.,]\d+)?)\b")
    for m in num_pattern.finditer(text):
        val = m.group()
        # Nicht als Jahreszahl oder Geld-Zahl nochmal hinzufügen
        if not re.match(r"^(19|20)\d{2}$", val) and val not in result.numbers:
            result.numbers.append(val)

    # ── 5. Jahreszahlen ───────────────────────────────────────────────────
    year_pattern = re.compile(r"\b(19|20)\d{2}\b")
    for m in year_pattern.finditer(text):
        result.dates.append(m.group())

    # ── 6. Key-Nouns: Großgeschriebene Nomen (kein Satzanfang, min. 5 Zeichen) ──
    # Wörter die mit Großbuchstabe beginnen, nicht Satzanfang sind, min. 5 Zeichen
    # und keine reinen Satzzeichen-Artefakte
    _GENERIC_WORDS = load_stopwords("generic_words")
    seen_nouns: set[str] = set(_institution_matched_words)  # Institutionswörter ausschließen
    words = re.findall(r"\b[A-ZÄÖÜ][a-zäöüA-ZÄÖÜ\-]{4,}\b", text)
    # Erste Wort im Text (Satzanfang) ignorieren
    first_word = text.split()[0].rstrip("!?.,") if text.split() else ""
    for word in words:
        if word == first_word:
            continue
        if word in _GENERIC_WORDS:
            continue
        # Kein Duplikat von bekannten Entitäten oder Misc-Einträgen
        all_known = (result.organizations + result.locations
                     + result.misc + [m.split()[0] for m in result.money])
        if any(word.lower() in k.lower() or k.lower() in word.lower() for k in all_known):
            continue
        if word.lower() not in seen_nouns:
            seen_nouns.add(word.lower())
            result.key_nouns.append(word)

    return result


def entity_overlap_score(claim_text: str, result_text: str) -> float:
    """Berechnet Entity-Overlap zwischen Claim und Suchergebnis-Text.

    Vergleicht:
    1. Exact matches der erkannten Entitäten (normalisiert)
    2. Token-Vektoren für Synonym-Erkennung wenn spaCy verfügbar
       (z.B. "E-Scooter" ↔ "Elektroroller", "Bußgeld" ↔ "Geldstrafe")

    Args:
        claim_text: Originaltext des Claims
        result_text: Titel + Snippet des Suchergebnisses

    Returns:
        float [0.0, 1.0] – Anteil gematschter Claim-Entitäten
    """
    claim_ents = extract_entities(claim_text)
    named = claim_ents.all_named_entities()
    if not named:
        return 0.0

    result_lower = result_text.lower()
    hits = 0

    nlp = _get_nlp()

    for ent in named:
        ent_lower = ent.lower()
        # 1. Exact match
        if ent_lower in result_lower:
            hits += 1
            continue

        # 2. Teilstring-Match (für mehrteilige Entitäten wie "Bundesministerium für Umwelt")
        parts = ent_lower.split()
        if len(parts) > 1 and any(p in result_lower for p in parts if len(p) > 4):
            hits += 0.5
            continue

        # 3. Token-Vektor-Similarität (Synonyme) – nur wenn spaCy mit Vektoren verfügbar
        if nlp is not None and nlp.vocab.vectors.shape[0] > 0:
            synonym_hit = _check_synonym_match(nlp, ent, result_text)
            if synonym_hit:
                hits += 0.7  # Synonym-Match etwas geringer gewichten

    return min(1.0, hits / len(named))


def _check_synonym_match(nlp, entity: str, result_text: str, threshold: float = 0.78) -> bool:
    """Prüft ob ein Synonym der Entität im result_text vorkommt.

    Vergleicht jeden Token der Entität mit jedem Token des result_text
    via word-vector cosine similarity.

    Args:
        threshold: Minimale Cosinus-Ähnlichkeit für Synonym-Match (0.78 = konservativ)
    """
    ent_doc = nlp(entity)
    result_doc = nlp(result_text)

    ent_tokens = [t for t in ent_doc if not t.is_stop and t.has_vector and len(t.text) > 3]
    result_tokens = [t for t in result_doc if not t.is_stop and t.has_vector and len(t.text) > 3]

    if not ent_tokens or not result_tokens:
        return False

    for et in ent_tokens:
        for rt in result_tokens:
            # Kein exact match (das ist schon oben geprüft)
            if et.lower_ == rt.lower_:
                continue
            if et.similarity(rt) >= threshold:
                return True
    return False


def spacy_available() -> bool:
    """Gibt True zurück wenn spaCy + de_core_news_lg geladen werden können."""
    return _get_nlp() is not None
