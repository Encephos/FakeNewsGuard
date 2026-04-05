"""Mehrstufige Claim-Processing-Pipeline.

Verarbeitet Rohtext in mehreren Stufen zu priorisierten, kanonisierten
und disambiguierten ProcessedClaim-Objekten.

Stufen:
    1. SentenceSplitter      – Rohtext → Sätze/Segmente mit Kontext
    2. ClaimSelector         – Filtert überprüfbare Behauptungen
    3. Disambiguator         – Erkennt und markiert mehrdeutige Claims
    4. ClaimDecomposer       – Zerlegt zusammengesetzte Claims in atomare
    5. ClaimCanonicalizerAgent – Kanonisierung, Normalisierung, Hash
    6. ClaimPrioritizerAgent   – Priorisierung nach Harm, Relevanz, Checkworthiness

Architekturentscheidung:
    Die Stufen sind interne Klassen in diesem Modul (kein Mikro-Agenten-Split).
    ClaimCanonicalizerAgent und ClaimPrioritizerAgent sind als eigene Agent-
    Klassen implementiert, damit sie unabhängig testbar und austauschbar sind.
    Die ClaimProcessingPipeline orchestriert alle Stufen und gibt ein
    ClaimProcessingResult zurück.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

from agents.base import BaseAgent
from config import AppConfig, ClaimQualitySignalConfig
from i18n import t
from models.schemas import (
    AmbiguityLevel,
    ArticleTopicModel,
    Claim,
    ClaimFrame,
    ClaimProcessingResult,
    ClaimSearchProfile,
    ClaimType,
    ProcessedClaim,
)
from agents.prompts.claim_prompts import (
    _CANONICALIZER_PROMPT,
    _CLAIM_SELECTOR_PROMPT,
    _DECOMPOSER_PROMPT,
    _DISAMBIGUATOR_PROMPT,
    _FRAME_EXTRACTOR_PROMPT,
    _PRIORITIZER_PROMPT,
    _TOPIC_EXTRACTOR_PROMPT,
)
from tools.llm import LLMClient
from tools.web_search import WebSearchClient


# ── Interne Hilfsfunktionen ────────────────────────────────────────────────────

def _canonical_hash(text: str) -> str:
    """SHA-256 Hash des kanonischen Textes für Cache-Keys."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]


_NEGATION_MARKERS = re.compile(
    r"\b(nicht|kein|keine|keinen|keinem|keiner|nie|niemals|weder|"
    r"war\s+kein|ist\s+kein|hat\s+nicht|wurde\s+nicht|kann\s+nicht)\b",
    re.IGNORECASE,
)


def _guard_negation(
    llm_text: str,
    original_full: str,
    seg_lookup: dict[int, str],
) -> str:
    """Detect if the LLM negated the original claim and fall back to original.

    Small LLMs sometimes invert the claim's truth direction during selection.
    If the LLM output introduces negation markers not present in any original
    segment, fall back to the closest original segment.
    """
    llm_lower = llm_text.lower()
    orig_lower = original_full.lower()

    # Check negation markers in LLM output vs original
    llm_negs = set(_NEGATION_MARKERS.findall(llm_lower))
    orig_negs = set(_NEGATION_MARKERS.findall(orig_lower))
    new_negs = llm_negs - orig_negs

    if not new_negs:
        return llm_text  # No new negation introduced

    # LLM introduced negation — fall back to best matching original segment
    best_seg = original_full.strip()
    best_overlap = 0
    llm_words = set(llm_lower.split())
    for seg_text in seg_lookup.values():
        seg_words = set(seg_text.lower().split())
        overlap = len(llm_words & seg_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_seg = seg_text

    _log(
        f"  ⚠ Negation-Guard: LLM hat Claim negiert "
        f"(neue Marker: {new_negs}), verwende Original"
    )
    return best_seg


def _split_sentences(text: str) -> list[str]:
    """Teile Text in Sätze auf – pronomen-aware (kein zu aggressives Splitting).

    Strategie: Trenne an Satzgrenzen (. ! ?), aber nicht bei:
    - Abkürzungen (z.B., d.h., etc.)
    - Zahlen (3.5 Mio.)
    - Initialen (A. Merkel)
    """
    # Schütze bekannte Abkürzungen
    protected = text
    abbreviations = [
        r"z\.B\.", r"d\.h\.", r"u\.a\.", r"etc\.", r"bzw\.", r"ggf\.",
        r"ca\.", r"inkl\.", r"exkl\.", r"Dr\.", r"Prof\.", r"Hr\.", r"Fr\.",
        r"Jan\.", r"Feb\.", r"Mär\.", r"Apr\.", r"Jun\.", r"Jul\.", r"Aug\.",
        r"Sep\.", r"Okt\.", r"Nov\.", r"Dez\.",
    ]
    placeholders: dict[str, str] = {}
    for i, abbr in enumerate(abbreviations):
        placeholder = f"__ABR{i}__"
        protected, count = re.subn(abbr, placeholder, protected)
        if count:
            placeholders[placeholder] = re.sub(r"\\", "", abbr)

    # Trenne an Satzenden (. ! ?) gefolgt von Großbuchstabe oder Zeilenumbruch
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"\'])", protected)

    # Stelle Abkürzungen wieder her
    restored = []
    for s in sentences:
        for placeholder, original in placeholders.items():
            s = s.replace(placeholder, original)
        s = s.strip()
        if s:
            restored.append(s)

    return restored if restored else [text.strip()]


def _build_sentence_context(sentences: list[str], index: int, window: int = 1) -> str:
    """Liefere Kontext-Sätze um einen Satz (±window Sätze)."""
    start = max(0, index - window)
    end = min(len(sentences), index + window + 1)
    context_parts = sentences[start:index] + sentences[index + 1:end]
    return " ".join(context_parts)


# ── Stufe 1: Sentence Splitter / Context Builder ──────────────────────────────

class SentenceSplitter:
    """Teilt Rohtext in Sätze/Segmente auf und liefert lokalen Kontext."""

    def split(
        self,
        text: str,
        metadata: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Teile Text in Segmente mit Kontext.

        Args:
            text: Rohtext.
            metadata: Optionale Metadaten (url, title, date, source).

        Returns:
            Liste von Dicts mit 'text', 'context', 'index'.
        """
        sentences = _split_sentences(text)
        segments = []
        for i, sentence in enumerate(sentences):
            context = _build_sentence_context(sentences, i)
            seg: dict[str, str] = {
                "text": sentence,
                "context": context,
                "index": str(i),
            }
            if metadata:
                seg.update(metadata)
            segments.append(seg)
        return segments


# ── Stufe 2+3+4: LLM-Stufen (Selector, Disambiguator, Decomposer) ─────────────

class _LLMStageMixin:
    """Mixin für LLM-basierte Pipeline-Stufen."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _call_llm_json(self, prompt: str, user_msg: str) -> dict:
        try:
            raw = self.llm.complete(prompt, user_msg, response_format="json")
            if isinstance(raw, str):
                return json.loads(raw)
            return raw
        except Exception as e:
            _log(f"LLM-Stufe fehlgeschlagen: {type(e).__name__}: {e}")
            return {}


class ClaimSelector(_LLMStageMixin):
    """Stufe 2: Filtert überprüfbare Behauptungen aus Segmenten."""

    def select(
        self,
        segments: list[dict[str, str]],
        full_text: str,
    ) -> tuple[list[ProcessedClaim], list[str]]:
        """Wähle überprüfbare Claims aus.

        Returns:
            (claims, implicit_claims)
        """
        # Segmente für LLM zusammenstellen
        seg_text = "\n".join(
            f"[Satz {s['index']}]: {s['text']}" for s in segments
        )
        user_msg = (
            f"## Text-Segmente zur Analyse\n\n{seg_text}\n\n"
            f"## Gesamtkontext (gekürzt)\n\n{full_text[:600]}"
        )

        raw = self._call_llm_json(_CLAIM_SELECTOR_PROMPT, user_msg)
        # Build lookup: segment index → original text
        seg_lookup = {s["index"]: s["text"] for s in segments}
        claims: list[ProcessedClaim] = []
        for c in raw.get("selected_claims", []):
            try:
                claim_text = c["text"]
                # Guard: detect if LLM negated the original claim
                claim_text = _guard_negation(claim_text, full_text, seg_lookup)
                claims.append(
                    ProcessedClaim(
                        id=c["id"],
                        text=claim_text,
                        type=ClaimType(c.get("type", "FACTUAL")),
                        context=c.get("context", ""),
                        requires_agents=c.get("requires_agents", ["fact_checker"]),
                        is_checkworthy=c.get("is_checkworthy", True),
                    )
                )
            except (KeyError, ValueError) as e:
                _log(f"Ungültiger Claim übersprungen: {e}")

        implicit = raw.get("implicit_claims", [])
        return claims, implicit


class Disambiguator(_LLMStageMixin):
    """Stufe 3: Erkennt und markiert mehrdeutige Claims."""

    def disambiguate(self, claims: list[ProcessedClaim], original_text: str = "") -> list[ProcessedClaim]:
        if not claims:
            return claims

        claims_text = "\n".join(
            f"[{c.id}]: {c.text} (Kontext: {c.context})" for c in claims
        )
        context_section = ""
        if original_text:
            context_section = f"## Originaltext (Kontext)\n\n{original_text[:600]}\n\n"
        user_msg = f"{context_section}## Claims zur Disambiguierung\n\n{claims_text}"

        raw = self._call_llm_json(_DISAMBIGUATOR_PROMPT, user_msg)
        results_by_id = {r["id"]: r for r in raw.get("results", []) if "id" in r}

        updated: list[ProcessedClaim] = []
        for claim in claims:
            r = results_by_id.get(claim.id, {})
            try:
                level = AmbiguityLevel(r.get("ambiguity_level", "NONE"))
            except ValueError:
                level = AmbiguityLevel.NONE

            # Aufgelöster Text überschreibt Originaltext wenn sinnvoll
            resolved = r.get("resolved_text", "")
            if resolved and level != AmbiguityLevel.NONE:
                # Guard: LLM darf den Claim nicht negieren (z.B. "ist Y" → "ist kein Y")
                resolved = _guard_negation(resolved, claim.text, {0: claim.text})
                new_text = resolved
            else:
                new_text = claim.text

            updated.append(
                claim.model_copy(update={
                    "text": new_text,
                    "ambiguity_level": level,
                    "ambiguity_reason": r.get("ambiguity_reason", ""),
                    "requires_more_context": r.get("requires_more_context", False),
                })
            )
        return updated


def _infer_jurisdiction(loc_lower: str, inst_lower: str) -> str:
    """Schnelle Jurisdiktions-Erkennung aus Location-/Institution-Strings.

    Verwendet generische Signale (Sprache, Behördentypen, Länder-Keywords)
    statt einzelner Städte.  Deutsche Behörden-Suffixe (-rat, -amt, -ministerium)
    und .de-Government-Domains aus domain_tiers reichen für die DE-Erkennung.
    """
    combined = f"{loc_lower} {inst_lower}"
    # DE: Länder-Keywords + generische Behördensignale
    if any(kw in combined for kw in (
        "deutschland", "deutsch", "german", "berlin", "münchen",
        "hamburg", "köln", "frankfurt", "bundesregierung", "bundestag",
        "bundesrat", "bafin", "destatis", "rki", "bundesbank",
    )):
        return "de"
    # DE: Behörden-Suffixe (Stadtrat, Landtag, Bezirksamt, Ministerium, …)
    if any(kw in inst_lower for kw in (
        "stadtrat", "landtag", "bezirksamt", "ministerium",
        "landesamt", "ordnungsamt", "bürgermeister",
    )):
        return "de"
    if any(kw in combined for kw in (
        "eu", "europäisch", "european", "eurostat", "eurozone",
        "eu-kommission", "european commission",
    )):
        return "eu"
    if any(kw in combined for kw in (
        "united kingdom", "uk", "british", "britisch", "london",
        "companies house", "hmrc", "ofcom", "fca",
    )):
        return "uk"
    if any(kw in combined for kw in (
        "united states", "usa", "american", "fda", "sec", "ftc",
        "uspto", "epa", "nih", "cdc", "washington",
    )):
        return "us"
    return "global"


def _derive_source_hints(frame: ClaimFrame) -> tuple[list[str], list[str]]:
    """Leite official-source- und fact-check-Hints generisch ab.

    Verwendet ``government_domains()`` aus ``domain_tiers.yaml`` für
    Institutions-Matching und ``SourceRegistry.by_jurisdiction_safe()``
    für jurisdiktionsbasierte Hints.  Der ClaimRouter ergänzt später
    ggf. weitere domain-spezifische Hints.
    """
    from tools.data_loader import fact_checker_domains, government_domains
    from tools.sources.registry import SourceRegistry

    official: list[str] = []

    inst_lower = (frame.institution or "").lower()
    loc_lower = (frame.location or "").lower()

    # 1. Institutions-Match: Stem einer Tier-1/Tier-2-Domain im institution-Feld
    if inst_lower:
        for domain in government_domains():
            stem = domain.split(".")[0]  # z.B. "bundesregierung" aus "bundesregierung.de"
            if len(stem) >= 5 and stem in inst_lower:
                official.append(f"site:{domain}")
                break  # ein institutioneller Hint genügt

    # 2. Jurisdiktions-Fallback: Registry-Quellen nach Jurisdiktion
    if not official:
        jurisdiction = _infer_jurisdiction(loc_lower, inst_lower)
        if jurisdiction != "global":
            for src in SourceRegistry.by_jurisdiction_safe(jurisdiction)[:2]:
                if src.classifier_domains:
                    hint = f"site:{src.classifier_domains[0]}"
                    if hint not in official:
                        official.append(hint)

    # 3. Statistik-Fallback: Claims mit Zahlen ohne andere Hints
    if frame.numbers and not official:
        if "destatis.de" in government_domains():
            official.append("site:destatis.de")

    # Fact-Check-Hints: erste 2 aus YAML tier4
    fc = fact_checker_domains()[:2]
    fact_check = [f"site:{d}" for d in fc]

    return official, fact_check


def _build_search_profile(frame: ClaimFrame) -> ClaimSearchProfile:
    """Leite ein ClaimSearchProfile aus einem ClaimFrame ab.

    Das Profil wird für frame-basierte Query-Generierung genutzt.
    Keine Queries aus freiem Claim-Text mehr – nur strukturierte Felder.
    """
    core_entities: list[str] = []
    for val in (frame.subject, frame.institution, frame.location, frame.object):
        if val and val not in core_entities:
            core_entities.append(val)

    institutions = [frame.institution] if frame.institution else []
    locations = [frame.location] if frame.location else []

    # Handlungs-Begriffe aus predicate (max. 3 Tokens)
    action_terms: list[str] = []
    if frame.predicate:
        action_terms = [w for w in frame.predicate.split() if len(w) > 4][:3]

    policy_terms = [frame.policy_context] if frame.policy_context else []

    sanction_terms: list[str] = []
    for val in (frame.sanction, frame.enforcement):
        if val:
            sanction_terms.append(val)

    # Begriffe die Off-topic-Treffer provozieren (generische Nomen)
    exclusion_terms: list[str] = []
    _offtopic_triggers = {
        "höhe", "bürger", "bürgers", "grad", "form", "art", "weise",
        "bereich", "stelle", "punkt", "rolle", "ebene",
    }
    if frame.canonical_text:
        for w in re.findall(r"\b[a-zäöü]{4,8}\b", frame.canonical_text.lower()):
            if w in _offtopic_triggers:
                exclusion_terms.append(w)

    # Official-source hints: generisch aus domain_tiers.yaml + SourceRegistry
    official_source_hints, fact_check_hints = _derive_source_hints(frame)

    return ClaimSearchProfile(
        core_entities=core_entities,
        institutions=institutions,
        locations=locations,
        action_terms=action_terms,
        policy_terms=policy_terms,
        number_terms=list(frame.numbers),
        sanction_terms=sanction_terms,
        exclusion_terms=exclusion_terms,
        official_source_hints=official_source_hints,
        fact_check_hints=fact_check_hints,
    )


def _derive_subclaim_frame(
    sub_text: str,
    parent_frame: ClaimFrame,
) -> tuple[ClaimFrame, ClaimSearchProfile]:
    """Leite einen fokussierten ClaimFrame für einen Teil-Claim ab.

    Behält nur die Frame-Felder, die im Text des Teil-Claims wirklich
    vorkommen. Verhindert, dass Teil-Claims irrelevante Kontext-Felder
    des Eltern-Claims erben (z.B. Sanktions-Frame in einem Claim über
    Fahrtenbegrenzung, der keine Sanktion erwähnt).

    Strategie: ein Frame-Feld wird behalten, wenn mindestens ein
    signifikantes Wort (> 3 Zeichen) daraus im Sub-Claim-Text auftaucht.
    """
    sub_lower = sub_text.lower()

    def _field_present(val: str) -> bool:
        if not val or len(val.strip()) < 3:
            return False
        words = [w for w in re.findall(r"[a-zäöüß]{4,}", val.lower())]
        return bool(words) and any(w in sub_lower for w in words)

    def _numbers_in_sub(numbers: list[str]) -> list[str]:
        return [n for n in numbers if re.search(re.escape(n), sub_text)]

    focused = ClaimFrame(
        raw_text=sub_text,
        subject=parent_frame.subject if _field_present(parent_frame.subject) else "",
        predicate=parent_frame.predicate if _field_present(parent_frame.predicate) else "",
        object=parent_frame.object if _field_present(parent_frame.object) else "",
        institution=parent_frame.institution if _field_present(parent_frame.institution) else "",
        location=parent_frame.location if _field_present(parent_frame.location) else "",
        time_reference=parent_frame.time_reference,  # Zeitbezug meist geteilt
        numbers=_numbers_in_sub(parent_frame.numbers),
        sanction=parent_frame.sanction if _field_present(parent_frame.sanction) else "",
        enforcement=parent_frame.enforcement if _field_present(parent_frame.enforcement) else "",
        policy_context=parent_frame.policy_context if _field_present(parent_frame.policy_context) else "",
        claim_type=parent_frame.claim_type,
        canonical_text=sub_text,
    )
    profile = _build_search_profile(focused)
    return focused, profile


class ClaimFrameExtractor(_LLMStageMixin):
    """Stufe 2.5: Extrahiert strukturierte ClaimFrames und SearchProfiles.

    Läuft NACH Selector (Stage 2) und VOR Disambiguator (Stage 3).
    Baut für jeden Claim einen semantischen Frame und ein Suchprofil.
    Der Frame ist ab hier der strukturelle Wahrheitsträger.
    """

    def extract(self, claims: list[ProcessedClaim], original_text: str = "") -> list[ProcessedClaim]:
        if not claims:
            return claims

        claims_text = "\n".join(f"[{c.id}]: {c.text}" for c in claims)
        context_section = ""
        if original_text:
            context_section = f"## Originaltext (Kontext)\n\n{original_text[:800]}\n\n"
        user_msg = f"{context_section}## Claims zur Frame-Extraktion\n\n{claims_text}"

        raw = self._call_llm_json(_FRAME_EXTRACTOR_PROMPT, user_msg)
        frames_by_id: dict[str, dict] = {
            f["id"]: f for f in raw.get("frames", []) if "id" in f
        }

        updated: list[ProcessedClaim] = []
        for claim in claims:
            fd = frames_by_id.get(claim.id, {})
            if not fd:
                # Kein Frame extrahiert → behalte Claim ohne Frame
                updated.append(claim)
                continue

            frame = ClaimFrame(
                raw_text=claim.text,
                subject=fd.get("subject", ""),
                predicate=fd.get("predicate", ""),
                object=fd.get("object", ""),
                institution=fd.get("institution", ""),
                location=fd.get("location", ""),
                time_reference=fd.get("time_reference", ""),
                numbers=[str(n) for n in fd.get("numbers", [])],
                sanction=fd.get("sanction", ""),
                enforcement=fd.get("enforcement", ""),
                policy_context=fd.get("policy_context", ""),
                claim_type=claim.type.value,
                canonical_text=fd.get("canonical_text", claim.text),
            )
            profile = _build_search_profile(frame)
            updated.append(
                claim.model_copy(update={"frame": frame, "search_profile": profile})
            )

        return updated


class ClaimDecomposer(_LLMStageMixin):
    """Stufe 4: Zerlegt zusammengesetzte Claims in atomare Claims."""

    @staticmethod
    def _has_context_integrity(text: str, original: ProcessedClaim) -> bool:
        """Prüfe ob ein (Teil-)Claim genug Kontext-Anker hat.

        Ein Split-Claim gilt als integer wenn er mindestens enthält:
        - Eine erkennbare Entität (Eigenname ≥3 Zeichen oder Zahl mit Kontext)
        - Ausreichende Länge (≥ 40 Zeichen)
        - Mindestens ein Kontext-Wort aus dem Original-Frame (falls vorhanden)
        """
        if len(text.strip()) < 40:
            return False

        # Eigenname oder Zahl vorhanden?
        has_entity = bool(
            re.search(r"[A-ZÄÖÜ][a-zäöü]{2,}", text)  # Eigenname
            or re.search(r"\d+", text)                   # Zahl
        )
        if not has_entity:
            return False

        # Frame-Kontext-Überprüfung: mindestens ein Anker-Begriff aus dem Original-Frame
        if original.frame:
            anchors: list[str] = []
            for val in (
                original.frame.institution,
                original.frame.location,
                original.frame.policy_context,
                original.frame.subject,
            ):
                if val and len(val) > 3:
                    anchors.extend(val.lower().split())

            if anchors:
                text_lower = text.lower()
                has_anchor = any(a in text_lower for a in anchors if len(a) > 3)
                if not has_anchor:
                    return False

        return True

    def decompose(
        self,
        claims: list[ProcessedClaim],
        topic_model: ArticleTopicModel | None = None,
    ) -> list[ProcessedClaim]:
        if not claims:
            return claims

        claims_text = "\n".join(f"[{c.id}]: {c.text}" for c in claims)
        user_msg = f"## Claims zur Zerlegung\n\n{claims_text}"

        # Topic-Kontext für den Decomposer: Kernentitäten als Anker
        if topic_model:
            entities = ", ".join(topic_model.key_entities[:5])
            user_msg = (
                f"## Artikelthema\n{topic_model.primary_topic}\n"
                f"Kernentitäten: {entities}\n\n{user_msg}"
            )

        raw = self._call_llm_json(_DECOMPOSER_PROMPT, user_msg)

        result: list[ProcessedClaim] = []
        claims_by_id = {c.id: c for c in claims}

        for entry in raw.get("decomposed", []):
            original_id = entry.get("original_id", "")
            original = claims_by_id.get(original_id)
            atomics = entry.get("atomic_claims", [])

            if not atomics or not original:
                if original:
                    result.append(original)
                continue

            if len(atomics) == 1:
                # Claim ist bereits atomar
                result.append(original)
                continue

            # Zerlegung: Integrity-Filter anwenden
            valid_atomics: list[ProcessedClaim] = []
            for a in atomics:
                try:
                    candidate_text = a.get("text", "")
                    # Guard: LLM darf den Claim nicht negieren
                    candidate_text = _guard_negation(
                        candidate_text, original.text, {0: original.text}
                    )
                    if not self._has_context_integrity(candidate_text, original):
                        _log(
                            f"  ✗ Mini-Claim verworfen ({original_id}): "
                            f"'{candidate_text[:60]}…'"
                        )
                        continue
                    # Fokussierten Frame ableiten statt Original zu erben
                    sub_frame: ClaimFrame | None = None
                    sub_profile: ClaimSearchProfile | None = None
                    if original.frame:
                        sub_frame, sub_profile = _derive_subclaim_frame(
                            candidate_text, original.frame
                        )
                    valid_atomics.append(
                        ProcessedClaim(
                            id=a["id"],
                            text=candidate_text,
                            type=ClaimType(a.get("type", original.type.value)),
                            context=a.get("context", original.context),
                            requires_agents=a.get("requires_agents", original.requires_agents),
                            is_checkworthy=original.is_checkworthy,
                            ambiguity_level=original.ambiguity_level,
                            ambiguity_reason=original.ambiguity_reason,
                            requires_more_context=original.requires_more_context,
                            frame=sub_frame,
                            search_profile=sub_profile,
                        )
                    )
                except (KeyError, ValueError) as e:
                    _log(f"Zerlegter Claim ungültig ({original_id}): {e}")

            if not valid_atomics:
                # Alle Teil-Claims haben Kontext verloren → Original behalten
                _log(
                    f"  ↩ Alle Teil-Claims zu '{original_id}' ohne Kontext-Integrität "
                    f"→ Original beibehalten"
                )
                result.append(original)
            else:
                result.extend(valid_atomics)

        return result if result else claims


# ── Stufe 4.5: ClaimValidator ────────────────────────────────────────────────

# Harte Filter-Muster für Meta-Claims / Recherche-Claims
_META_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(r"^es\s+gibt\s+informationen\s+darüber", re.IGNORECASE),
    re.compile(r"^es\s+gibt\s+hinweise", re.IGNORECASE),
    re.compile(r"^es\s+wird\s+behauptet,?\s+dass", re.IGNORECASE),
    re.compile(r"^es\s+gibt\s+berichte,?\s+dass", re.IGNORECASE),
    re.compile(r"^es\s+gibt\s+quellen,?\s+die", re.IGNORECASE),
    re.compile(r"^es\s+ist\s+bekannt,?\s+dass", re.IGNORECASE),
    re.compile(r"^man\s+kann\s+herausfinden", re.IGNORECASE),
    re.compile(r"^es\s+lässt\s+sich\s+(herausfinden|recherchieren|prüfen)", re.IGNORECASE),
    re.compile(r"lässt\s+sich\s+(herausfinden|recherchieren|prüfen|feststellen)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^es\s+gibt\s+daten\s+(darüber|dazu)", re.IGNORECASE),
    re.compile(r"^es\s+existieren\s+(studien|untersuchungen|berichte)", re.IGNORECASE),
    # Suchdimensionen statt Behauptungen
    re.compile(r"^(wie|wann|wo|warum|ob)\s+.{0,20}\s+(ist|war|wurde|hat|haben)", re.IGNORECASE),
    re.compile(r"^informationen\s+(über|zu|darüber)", re.IGNORECASE),
]

# Weiche Signale für niedrige Claim-Qualität
_WEAK_CLAIM_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(einige|manche|viele)\s+(leute|menschen|experten)\s+(sagen|meinen|glauben)", re.IGNORECASE),
    re.compile(r"^(es\s+heißt|angeblich|vermutlich|möglicherweise)", re.IGNORECASE),
    re.compile(r"^(die\s+frage\s+ist|zu\s+klären\s+ist|zu\s+prüfen\s+ist)", re.IGNORECASE),
]

# Evaluative/subjektive Muster → Claim wird als OPINION reklassifiziert.
# Fängt Fälle ab, in denen der LLM ein Charakterurteil oder eine Wertung
# fälschlich als FACTUAL klassifiziert hat.
_OPINION_PATTERNS: list[re.Pattern] = [
    # Charakterurteile: "X ist ein(e) Spalter(in)/Lügner(in)/Versager(in)/..."
    re.compile(
        r"\bist\s+ein(?:e)?\s+(?:spalter|lügner|betrüger|versager|heuchler|"
        r"manipulator|populist|diktator|narr|schande|katastrophe|gefahr)"
        r"(?:in)?\b",
        re.IGNORECASE,
    ),
    # "Sie/Er sind/ist [negatives Adjektiv]"
    re.compile(
        r"\b(?:ist|sind|war|waren)\s+(?:schlecht|böse|schrecklich|furchtbar|"
        r"unmoralisch|verlogen|inkompetent|unfähig|verantwortungslos|feige)\b",
        re.IGNORECASE,
    ),
    # "... wird als X in Erinnerung bleiben"
    re.compile(r"(?:wird|werden)\s+.*\bin\s+(?:erinnerung|geschichte)\s+bleiben\b", re.IGNORECASE),
    # "X ist eine Schande / ein Skandal / inakzeptabel"
    re.compile(
        r"\bist\s+(?:eine?\s+)?(?:schande|skandal|katastrophe|desaster|"
        r"inakzeptabel|untragbar|unerträglich)\b",
        re.IGNORECASE,
    ),
    # Explizite Meinungsmarker
    re.compile(r"^(?:ich\s+finde|ich\s+glaube|meiner?\s+meinung\s+nach)", re.IGNORECASE),
]

# Kontextlose Betrag-/Zahl-Aussagen ohne Akteur oder Policy-Kontext.
# Typische Mini-Claims nach Dekomposition: "Die Höhe des Bußgeldes beträgt 250 Euro."
# Diese werden stärker bestraft als normale weak signals (-0.40 statt -0.30).
_CONTEXTLESS_NUMBER_PATTERN: re.Pattern = re.compile(
    r"^(die|das|der)\s+(höhe|anzahl|zahl|menge|betrag|summe|wert|preis|kosten|dauer|länge|breite)\s+(des|der|von|beträgt|liegt|ist)\b",
    re.IGNORECASE,
)


class ClaimValidator:
    """Stufe 4.5: Validiert Claims auf Falsifizierbarkeit und Qualität.

    Filtert:
    - Meta-Claims ("Es gibt Informationen darüber, dass …")
    - Recherche-Claims / Suchdimensionen ("Wie/Wann/Wo …")
    - Nicht-falsifizierbare Pseudo-Claims

    Erkennt zusätzlich vier abstrakte Qualitätssignale (keine Sonderregeln für
    einzelne Personen, Wörter oder Testfälle – rein strukturell/statistisch):

    - missing_artifact_evidence:  Claim verweist auf ein Artefakt ohne
      verifizierbaren Anker (leerer Frame: kein Akteur, keine Institution,
      kein Zeitbezug, keine Zahlen).
    - underspecified_actor:       Akteur zu generisch – frame.subject und
      frame.institution sind kürzer als min_actor_length.
    - extraordinary_claim:        Absolutheitssprache oder Extremprozentwerte
      (konfigurierbare Schwellen, kein Themen-Hardcoding).
    - elevated_burden_of_proof:   Kausale Claims oder solche mit Sanktions-/
      Durchsetzungskontext erfordern mehr Evidenz.

    Markiert ungültige Claims mit is_valid_claim=False und invalid_reason.
    Aktive Signale senken claim_quality_score und setzen ggf. requires_more_context.
    """

    def __init__(self, signal_cfg: ClaimQualitySignalConfig | None = None) -> None:
        self._cfg = signal_cfg or ClaimQualitySignalConfig()
        # Kompiliere den absolut-Muster nur einmal
        self._extraordinary_abs_re: re.Pattern = re.compile(
            self._cfg.extraordinary_absolute_pattern, re.IGNORECASE
        )
        # Regex zum Erkennen von Prozentzahlen (z.B. "95 %", "100%", "99,5%")
        self._pct_re: re.Pattern = re.compile(
            r"(\d+(?:[.,]\d+)?)\s*%"
        )

    # ── öffentliche API ───────────────────────────────────────────────────────

    def validate(self, claims: list[ProcessedClaim]) -> list[ProcessedClaim]:
        """Validiere alle Claims. Ungültige werden markiert, nicht entfernt."""
        if not claims:
            return claims

        validated: list[ProcessedClaim] = []
        for claim in claims:
            is_valid, reason, quality, signals, more_ctx = self._check_claim(claim)

            # Post-Processing: Evaluative Muster → als OPINION reklassifizieren
            update: dict = {
                "is_valid_claim": is_valid,
                "invalid_reason": reason,
                "claim_quality_score": quality,
                "quality_signals": signals,
                "requires_more_context": claim.requires_more_context or more_ctx,
            }
            if claim.type != ClaimType.OPINION and self._is_opinion_pattern(claim.text):
                update["type"] = ClaimType.OPINION
                update["is_checkworthy"] = False
                logger.debug("Claim %s als OPINION reklassifiziert: '%s'", claim.id, claim.text[:60])

            validated.append(claim.model_copy(update=update))
        return validated

    @staticmethod
    def _is_opinion_pattern(text: str) -> bool:
        """Prüfe ob der Claim evaluative/subjektive Muster enthält."""
        return any(p.search(text) for p in _OPINION_PATTERNS)

    # ── interne Prüflogik ─────────────────────────────────────────────────────

    def _check_claim(
        self, claim: ProcessedClaim
    ) -> tuple[bool, str, float, list[str], bool]:
        """Prüfe einen einzelnen Claim.

        Returns:
            (is_valid, invalid_reason, quality_score, quality_signals, requires_more_context)
        """
        text = claim.text.strip()

        # ── Harte Filter ─────────────────────────────────────────────────────

        for pattern in _META_CLAIM_PATTERNS:
            if pattern.search(text):
                return False, f"Meta-/Recherche-Claim: '{text[:80]}…'", 0.0, [], False

        if len(text) < 15:
            return False, "Claim zu kurz für Falsifizierbarkeit", 0.1, [], False

        if text.endswith("?") and not any(
            kw in text.lower() for kw in ["stimmt es", "ist es wahr", "trifft es zu"]
        ):
            return False, "Frage statt Behauptung", 0.1, [], False

        # ── Weiche Signale (bisherige Logik) ─────────────────────────────────

        quality = 1.0

        for pattern in _WEAK_CLAIM_PATTERNS:
            if pattern.search(text):
                quality -= 0.3
                break

        if _CONTEXTLESS_NUMBER_PATTERN.search(text):
            quality -= 0.40

        has_specifics = bool(
            re.search(r"\d", text)
            or re.search(r"[A-ZÄÖÜ][a-zäöü]{2,}", text)
        )
        if not has_specifics:
            quality -= 0.15

        if text.lower().startswith(("es ist so", "es ist klar", "es stimmt")):
            quality -= 0.2

        # ── Abstrakte Qualitätssignale ────────────────────────────────────────

        signals: list[str] = []
        cfg = self._cfg

        if self._is_missing_artifact_evidence(claim):
            signals.append("missing_artifact_evidence")
            quality -= cfg.missing_artifact_penalty

        if self._is_underspecified_actor(claim):
            signals.append("underspecified_actor")
            quality -= cfg.underspecified_actor_penalty

        if self._is_extraordinary_claim(text):
            signals.append("extraordinary_claim")
            quality -= cfg.extraordinary_claim_penalty

        if self._is_elevated_burden_of_proof(claim):
            signals.append("elevated_burden_of_proof")
            quality -= cfg.elevated_burden_penalty

        quality = max(0.0, min(1.0, quality))

        requires_more_context = (
            len(signals) >= cfg.requires_context_signal_threshold
        )

        if quality < 0.3:
            return False, "Claim-Qualität zu niedrig (vage/unspezifisch)", quality, signals, requires_more_context

        return True, "", quality, signals, requires_more_context

    # ── Signaldetektoren ──────────────────────────────────────────────────────

    def _is_missing_artifact_evidence(self, claim: ProcessedClaim) -> bool:
        """True wenn der Claim keine verifizierbaren Frame-Anker enthält.

        Generalisierungsprinzip: Nicht auf bestimmte Artefakttypen (Beschluss,
        Studie, …) geprüft, sondern auf das Fehlen aller strukturellen Anker
        (Akteur, Institution, Zeitbezug, Zahlen). Ein Claim ohne jeden Anker
        ist für keine Suchstrategie auflösbar.
        """
        frame = claim.frame
        if frame is None:
            return False  # Kein Frame → Signal nicht auslösbar
        anchor_present = (
            bool(frame.subject.strip())
            or bool(frame.institution.strip())
            or bool(frame.time_reference.strip())
            or bool(frame.numbers)
        )
        return not anchor_present

    def _is_underspecified_actor(self, claim: ProcessedClaim) -> bool:
        """True wenn weder Akteur noch Institution ausreichend spezifisch ist.

        Generalisierungsprinzip: Keine Liste von "schlechten" Akteur-Namen,
        stattdessen rein längenbasiert. Ein sehr kurzes oder leeres subject +
        institution deutet auf generische Formulierungen hin ("Die Behörden",
        "Der Staat"), die nicht auf eine konkrete prüfbare Entität verweisen.
        """
        frame = claim.frame
        if frame is None:
            return False
        subject_ok = len(frame.subject.strip()) >= self._cfg.min_actor_length
        institution_ok = len(frame.institution.strip()) >= self._cfg.min_actor_length
        return not subject_ok and not institution_ok

    def _is_extraordinary_claim(self, text: str) -> bool:
        """True bei Absolutheitssprache oder extremen Prozentwerten.

        Generalisierungsprinzip: Keine Themenwörter – nur zwei universelle
        strukturelle Merkmale:
        1. Absolutheitsquantoren (konfigurierbar via extraordinary_absolute_pattern)
        2. Prozentwerte >= extraordinary_percentage_threshold (konfigurierbar)
        Beide deuten auf Claims hin, die empirisch selten wahr und schwer
        falsifizierbar sind, unabhängig vom Thema.
        """
        if self._extraordinary_abs_re.search(text):
            return True
        threshold = self._cfg.extraordinary_percentage_threshold
        for m in self._pct_re.finditer(text):
            val_str = m.group(1).replace(",", ".")
            try:
                if float(val_str) >= threshold:
                    return True
            except ValueError:
                pass
        return False

    def _is_elevated_burden_of_proof(self, claim: ProcessedClaim) -> bool:
        """True bei Kausalclaims oder wenn Sanktions-/Durchsetzungskontext vorliegt.

        Generalisierungsprinzip: Kein Themen-Hardcoding – stattdessen zwei
        strukturelle Indikatoren:
        1. ClaimType.CAUSAL → behauptete Ursache-Wirkung braucht mehr Evidenz.
        2. frame.sanction oder frame.enforcement nicht leer → behördlicher
           Durchsetzungskontext mit hohem Schadenspotenzial bei falscher Verbreitung.
        """
        if claim.type == ClaimType.CAUSAL:
            return True
        frame = claim.frame
        if frame is not None and (
            bool(frame.sanction.strip()) or bool(frame.enforcement.strip())
        ):
            return True
        return False


# ── Stufe 5: ClaimCanonicalizerAgent ─────────────────────────────────────────

class ClaimCanonicalizerAgent(BaseAgent):
    """Agent für Kanonisierung und Normalisierung von Claims.

    Verantwortlichkeiten:
    - Normalisierung von Entitäten, Datums- und Zahlenangaben
    - Erzeugung kanonischer Texte und Hashes für Cache-Keys
    - Cross-Reference-Hinweise auf ähnliche Claims
    """

    name = "Claim Canonicalizer"
    emoji = "🔤"

    def execute(
        self, input_data: Any, context: str = ""
    ) -> list[ProcessedClaim]:
        claims: list[ProcessedClaim] = input_data

        if not claims:
            return claims

        claims_text = "\n".join(
            f"[{c.id}]: {c.text}" for c in claims
        )
        user_msg = f"## Claims zur Kanonisierung\n\n{claims_text}"

        raw = self._llm_json(_CANONICALIZER_PROMPT, user_msg)

        results_by_id = {r["id"]: r for r in raw.get("canonicalized", []) if "id" in r}

        updated: list[ProcessedClaim] = []
        for claim in claims:
            r = results_by_id.get(claim.id, {})
            canonical = r.get("canonical_text", claim.text)
            if not canonical:
                canonical = claim.text

            updated.append(
                claim.model_copy(update={
                    "canonical_text": canonical,
                    "canonical_hash": _canonical_hash(canonical),
                    "normalized_entities": r.get("normalized_entities", []),
                    "normalized_dates": r.get("normalized_dates", []),
                    "normalized_numbers": r.get("normalized_numbers", []),
                })
            )

        self._log(f"{len(updated)} Claims kanonisiert")
        return updated


# ── Stufe 6: ClaimPrioritizerAgent ────────────────────────────────────────────

class ClaimPrioritizerAgent(BaseAgent):
    """Agent für Priorisierung von Claims nach Relevanz und Schadenspotenzial.

    Verantwortlichkeiten:
    - Priority-Score, Harm-Score, Checkworthiness-Score pro Claim
    - Empfohlene Verarbeitungsreihenfolge
    - Markierung von trivialen/nicht prüfenswerten Claims
    """

    name = "Claim Prioritizer"
    emoji = "📊"

    def execute(
        self, input_data: Any, context: str = ""
    ) -> list[ProcessedClaim]:
        claims: list[ProcessedClaim] = input_data

        if not claims:
            return claims

        claims_text = "\n".join(
            f"[{c.id}] [{c.type.value}]: {c.text}" for c in claims
        )
        user_msg = f"## Claims zur Priorisierung\n\n{claims_text}"

        raw = self._llm_json(_PRIORITIZER_PROMPT, user_msg)
        results_by_id = {r["id"]: r for r in raw.get("prioritized", []) if "id" in r}

        updated: list[ProcessedClaim] = []
        for claim in claims:
            r = results_by_id.get(claim.id, {})
            updated.append(
                claim.model_copy(update={
                    "priority_score": float(r.get("priority_score", 0.5)),
                    "harm_score": float(r.get("harm_score", 0.0)),
                    "checkworthiness_score": float(r.get("checkworthiness_score", 0.5)),
                    "priority_reason": r.get("priority_reason", ""),
                    "recommended_processing_order": int(r.get("recommended_processing_order", 0)),
                })
            )

        # Nach Priorisierungsreihenfolge sortieren
        updated.sort(key=lambda c: c.recommended_processing_order)
        self._log(f"{len(updated)} Claims priorisiert")
        return updated


# ── Topic Extractor ──────────────────────────────────────────────────────────


class TopicExtractor:
    """Extrahiert ein ArticleTopicModel aus dem Volltext.

    Wird einmalig in Phase 1 ausgeführt (nach Claim-Selektion, vor Decomposition).
    Das Ergebnis fließt in alle nachfolgenden Pipeline-Stufen ein.
    """

    _VALID_DOMAINS = frozenset({
        "REGULATORY", "SCIENTIFIC", "POLITICAL", "ECONOMIC",
        "SOCIAL", "HEALTH", "GENERAL",
    })

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def extract(self, text: str) -> ArticleTopicModel | None:
        """Extrahiere Topic Model aus Artikeltext.

        Returns:
            ArticleTopicModel oder None bei Fehler.
        """
        user_msg = f"## Artikeltext\n\n{text[:4000]}"
        try:
            raw = self._llm.complete_json(
                system=_TOPIC_EXTRACTOR_PROMPT,
                user=user_msg,
            )
        except Exception as e:
            _log(f"TopicExtractor LLM-Fehler: {type(e).__name__}: {e}")
            return None

        if not isinstance(raw, dict):
            _log("TopicExtractor: LLM-Antwort ist kein dict")
            return None

        domain = raw.get("domain", "GENERAL")
        if domain not in self._VALID_DOMAINS:
            domain = "GENERAL"

        try:
            return ArticleTopicModel(
                primary_topic=raw.get("primary_topic", ""),
                key_entities=raw.get("key_entities", []),
                topic_keywords=raw.get("topic_keywords", []),
                domain=domain,
                geographic_scope=raw.get("geographic_scope", ""),
                temporal_scope=raw.get("temporal_scope", ""),
                narrative_arc=raw.get("narrative_arc", ""),
            )
        except Exception as e:
            _log(f"TopicExtractor: Modell-Validierung fehlgeschlagen: {e}")
            return None


# ── ClaimProcessingPipeline ────────────────────────────────────────────────────

class ClaimProcessingPipeline:
    """Orchestriert alle 7 Claim-Processing-Stufen.

    Ablauf:
        1.   SentenceSplitter – Text → Segmente
        2.   ClaimSelector    – Segmente → prüfbare Claims (LLM)
        3.   Disambiguator    – Mehrdeutigkeiten markieren (LLM)
        4.   ClaimDecomposer  – Zusammengesetzte Claims zerlegen (LLM)
        4.5  ClaimValidator   – Meta-/Recherche-Claims filtern (regelbasiert)
        5.   ClaimCanonicalizerAgent – Kanonisierung + Hash
        6.   ClaimPrioritizerAgent   – Priorisierung + Sortierung

    Jede Stufe ist gracefully degradierbar – bei Fehler wird das
    bisherige Ergebnis weitergegeben.
    """

    def __init__(
        self,
        config: AppConfig,
        llm: LLMClient,
        search: WebSearchClient,
        llm_small: LLMClient | None = None,
    ) -> None:
        self.config = config
        _llm_small = llm_small or llm
        self._splitter = SentenceSplitter()
        self._selector = ClaimSelector(_llm_small)
        self._topic_extractor = TopicExtractor(_llm_small)
        self._frame_extractor = ClaimFrameExtractor(_llm_small)  # Stage 2.5
        self._disambiguator = Disambiguator(_llm_small)
        self._decomposer = ClaimDecomposer(_llm_small)
        self._validator = ClaimValidator(config.claim_processing.quality_signals)
        self._canonicalizer = ClaimCanonicalizerAgent(config, _llm_small, search)
        self._prioritizer = ClaimPrioritizerAgent(config, llm, search)

    def process(
        self,
        text: str,
        metadata: dict[str, str] | None = None,
    ) -> ClaimProcessingResult:
        """Führe die vollständige Pipeline aus.

        Args:
            text: Zu analysierender Rohtext.
            metadata: Optionale Metadaten (url, title, date, source).

        Returns:
            ClaimProcessingResult mit priorisierten ProcessedClaims.
        """
        notes: list[str] = []

        # Stufe 1: Sentence Splitting
        segments = self._splitter.split(text, metadata)
        notes.append(f"Stufe 1: {len(segments)} Segmente extrahiert")

        # Stufe 2: Claim Selection (LLM)
        claims, implicit_claims = self._selector.select(segments, text)
        notes.append(f"Stufe 2: {len(claims)} Claims selektiert, {len(implicit_claims)} implizite")

        if not claims:
            return ClaimProcessingResult(
                claims=[],
                implicit_claims=implicit_claims,
                processing_notes=notes,
                total_segments=len(segments),
            )

        # Topic Extraction (parallel zu den Claims, aber aus Volltext)
        topic_model: ArticleTopicModel | None = None
        try:
            topic_model = self._topic_extractor.extract(text)
            if topic_model:
                notes.append(
                    f"TopicExtractor: '{topic_model.primary_topic}' "
                    f"({topic_model.domain}, {len(topic_model.key_entities)} Entitäten)"
                )
            else:
                notes.append("TopicExtractor: Kein Topic-Modell extrahiert – übersprungen")
        except Exception as e:
            notes.append(f"TopicExtractor: Fehlgeschlagen ({type(e).__name__}) – übersprungen")

        # Stufe 2.5: Frame Extraction (LLM) – baut ClaimFrame + SearchProfile
        try:
            claims = self._frame_extractor.extract(claims, text)
            frames_built = sum(1 for c in claims if c.frame is not None)
            notes.append(f"Stufe 2.5: {frames_built}/{len(claims)} ClaimFrames extrahiert")
        except Exception as e:
            notes.append(f"Stufe 2.5: Frame-Extraktion fehlgeschlagen ({type(e).__name__}) – übersprungen")

        # Stufe 3: Disambiguation (LLM)
        try:
            claims = self._disambiguator.disambiguate(claims, text)
            notes.append("Stufe 3: Disambiguierung abgeschlossen")
        except Exception as e:
            notes.append(f"Stufe 3: Disambiguierung fehlgeschlagen ({type(e).__name__}) – übersprungen")

        # Stufe 4: Decomposition (LLM) – mit Topic-Kontext falls verfügbar
        try:
            claims = self._decomposer.decompose(claims, topic_model=topic_model)
            notes.append(f"Stufe 4: {len(claims)} Claims nach Zerlegung")
        except Exception as e:
            notes.append(f"Stufe 4: Zerlegung fehlgeschlagen ({type(e).__name__}) – übersprungen")

        # Stufe 4.5: Claim Validation (regelbasiert, kein LLM)
        try:
            claims = self._validator.validate(claims)
            valid_count = sum(1 for c in claims if c.is_valid_claim)
            invalid_count = len(claims) - valid_count
            notes.append(f"Stufe 4.5: {valid_count} gültige, {invalid_count} ungültige Claims")
            if invalid_count > 0:
                for c in claims:
                    if not c.is_valid_claim:
                        _log(f"  ✗ {c.id} ungültig: {c.invalid_reason}")
        except Exception as e:
            notes.append(f"Stufe 4.5: Validierung fehlgeschlagen ({type(e).__name__}) – übersprungen")

        # Stufe 5: Canonicalization (LLM-Agent)
        try:
            result, error = self._canonicalizer.run_safe(claims)
            if result is not None:
                claims = result
                notes.append("Stufe 5: Kanonisierung abgeschlossen")
            else:
                notes.append(f"Stufe 5: Kanonisierung fehlgeschlagen – {error}")
                # Fallback: Hashes ohne LLM-Kanonisierung
                claims = [
                    c.model_copy(update={
                        "canonical_text": c.text,
                        "canonical_hash": _canonical_hash(c.text),
                    })
                    for c in claims
                ]
        except Exception as e:
            notes.append(f"Stufe 5: Kanonisierung fehlgeschlagen ({type(e).__name__})")

        # Stufe 6: Prioritization (LLM-Agent)
        try:
            result, error = self._prioritizer.run_safe(claims)
            if result is not None:
                claims = result
                notes.append("Stufe 6: Priorisierung abgeschlossen")
            else:
                notes.append(f"Stufe 6: Priorisierung fehlgeschlagen – {error}")
        except Exception as e:
            notes.append(f"Stufe 6: Priorisierung fehlgeschlagen ({type(e).__name__})")

        return ClaimProcessingResult(
            claims=claims,
            implicit_claims=implicit_claims,
            processing_notes=notes,
            total_segments=len(segments),
            topic_model=topic_model,
        )


# ── ClaimProcessorAgent (öffentliche Schnittstelle) ───────────────────────────

class ClaimProcessorAgent(BaseAgent):
    """Öffentlicher Agent für die mehrstufige Claim-Processing-Pipeline.

    Ersetzt intern den ClaimExtractorAgent und liefert ein
    ClaimProcessingResult mit vollständig prozessierten Claims.

    Der ClaimExtractorAgent bleibt als dünne Fassade bestehen
    (Abwärtskompatibilität).
    """

    name = "Claim Processor"
    emoji = "🔬"

    def __init__(self, *args, **kwargs) -> None:
        llm_small = kwargs.pop("llm_small", None)
        super().__init__(*args, **kwargs)
        self._pipeline = ClaimProcessingPipeline(
            self.config, self.llm, self.search, llm_small=llm_small
        )

    def execute(
        self, input_data: Any, context: str = ""
    ) -> ClaimProcessingResult:
        """Verarbeite Text durch die vollständige 6-Stufen-Pipeline.

        Args:
            input_data: Text-String oder dict mit 'text' + optionalen Metadaten.
            context: Wird ignoriert (Metadaten via input_data dict übergeben).

        Returns:
            ClaimProcessingResult mit priorisierten ProcessedClaims.
        """
        if isinstance(input_data, dict):
            text = input_data.get("text", "")
            metadata = {k: v for k, v in input_data.items() if k != "text"}
        else:
            text = str(input_data)
            metadata = None

        result = self._pipeline.process(text, metadata)

        for note in result.processing_notes:
            self._log(f"  {note}")
        self._log(
            f"{len(result.claims)} ProcessedClaims, "
            f"{len(result.implicit_claims)} implizite"
        )
        return result


def _log(msg: str) -> None:
    print(f"  [ClaimProcessor] {msg}", file=sys.stderr)
