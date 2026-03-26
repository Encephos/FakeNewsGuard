"""VerdictAgent – Urteilsfindung auf Basis strukturierter EvidencePacks.

Verantwortlichkeiten:
    - Empfängt ein EvidencePack (Trust-Boundary-gefiltert)
    - Optional: CoVe-Trace vom CoVeProcessor
    - Optional: NumberAuditResult für statistische Claims
    - Gibt ein FactCheckResult zurück

Wichtig:
    Dieser Agent betreibt KEIN Retrieval. Er arbeitet ausschließlich
    auf strukturierten Datenstrukturen (EvidencePack, CoVeTrace).
    Rohe Webseiteninhalte erreichen ihn nie.
"""

from __future__ import annotations

import re
from typing import Any

from agents.base import BaseAgent
from models.evidence_models import EvidencePack, EvidenceType
from models.schemas import (
    FACT_CHECK_SCHEMA,
    Claim,
    FactCheckResult,
    FactRating,
    NumberAuditResult,
    SourceInfo,
)
from models.verdict_models import CoVeTrace, FinalVerdictMeta


# ── Confidence Ceilings & Calibration ─────────────────────────────────────────

# Maximale Confidence ohne Primärquelle
_CEILING_NO_PRIMARY_SOURCE = 0.82
# Maximale Confidence bei hohem Off-topic Anteil (>50%)
_CEILING_OFFTOPIC_CONTAMINATION = 0.75
# Maximale Confidence bei schwacher Evidenzqualität
_CEILING_WEAK_EVIDENCE = 0.70
# Maximale Confidence bei insufficient consensus
_CEILING_INSUFFICIENT_CONSENSUS = 0.65
# Maximale Confidence bei sehr schlechter Claim-Qualität
_CEILING_POOR_CLAIM_QUALITY = 0.72
# Ceiling bei schwacher durchschnittlicher Top-5-Relevanz (Produkte, Rechner etc.)
_CEILING_LOW_AVG_RELEVANCE = 0.68
# Ceiling bei sehr schwacher Top-5-Relevanz (fast alle Quellen unbrauchbar)
_CEILING_VERY_LOW_AVG_RELEVANCE = 0.58
# Ceiling bei hohem Low-Trust-Anteil in Top-5 (Währungsrechner, Grammatik, Juraforen)
_CEILING_HIGH_LOW_TRUST = 0.62
# Ceiling bei fehlender offizieller Quelle für Regelungsclaims
_CEILING_REGULATORY_NO_OFFICIAL = 0.72
# Ceiling bei überwiegend contextual evidence (kein direct evidence in Top-5)
_CEILING_CONTEXTUAL_ONLY = 0.65
# Ceiling bei hoher weak evidence rate (>60% WEAK in Top-5)
_CEILING_HIGH_WEAK_RATE = 0.60
# Ceiling bei Kombination aus contextual evidence UND low-trust Quellen
_CEILING_CONTEXTUAL_AND_LOW_TRUST = 0.55
# Ceiling für Regelungsclaims ohne direkte Regelungsgrundlage (strenger als ohne offizielle Quelle)
_CEILING_REGULATORY_NO_DIRECT_EVIDENCE = 0.55
# Ceiling bei veralteten Quellen (avg_freshness < Schwellwert)
_CEILING_STALE_SOURCES = 0.72
# Ceiling bei Aktuell-Zustand-Claims ohne frische Quellen
_CEILING_CURRENT_STATE_NO_FRESH = 0.55
# Ceiling wenn keinerlei brauchbare Evidenz vorliegt (kein DIRECT, kein Primary, kein FC, kein Konsens)
_CEILING_ZERO_USEFUL_EVIDENCE = 0.50
# Minimale Anzahl guter Quellen für hohe Confidence
_MIN_GOOD_SOURCES_FOR_HIGH_CONF = 2

# Textuelles Muster für Regulatory-Claims (Fallback wenn claim.frame fehlt)
_REGULATORY_TEXT_PATTERN = re.compile(
    r"\b("
    r"bu[sß]geld|geldbu[sß]e|strafe|straf(?:zahlung|gebühr)|"
    r"beschluss|beschlossen|verordnung|gesetz(?:lich|gebung)?|regelung|"
    r"[üu]berwachung|kontrolliert|[üu]berwacht|kamera(?:system)?|"
    r"pflicht|verpflichtend|verboten|verbiet|untersagt|erlaubt\s+nicht|"
    r"beh[öo]rde|amt|amtlich|offiziell\s+vorgeschrieben|"
    r"sanktion|sanktioniert|zwang|zwangsgeld|bußgeldkatalog"
    r")\b",
    re.IGNORECASE,
)


def _is_regulatory_from_text(claim_text: str) -> bool:
    """Textueller Fallback zur Regulatory-Erkennung wenn claim.frame fehlt."""
    return bool(_REGULATORY_TEXT_PATTERN.search(claim_text))


def _calibrate_confidence(
    raw_confidence: float,
    pack: "EvidencePack",
    cove_trace: "CoVeTrace | None",
    claim_quality_score: float = 1.0,
    is_regulatory_claim: bool = False,
    is_current_state_claim: bool = False,
    stale_freshness_threshold: float = 0.40,
) -> tuple[float, list[str]]:
    """Regelbasierter Confidence-Postprocessor.

    Senkt die LLM-Confidence basierend auf objektiven Pipeline-Signalen.
    LLM-Confidence wird nie direkt übernommen.

    Ceiling-Regeln:
        - Ohne Primärquelle UND ohne Fact-Check: max 0.82
        - Off-topic-Rate > 50% (aus EvidenceQualitySignals): max 0.75
        - Schwache Evidenzqualität (overall < 0.30): max 0.70
        - Schlechte Claim-Qualität (score < 0.50): max 0.72
        - Insufficient source consensus: max 0.65
        - Hoher Low-Trust-Anteil in Top-5: max 0.62
        - Regelungsclaim ohne offizielle Quelle: max 0.72
        - Veraltete Quellen (avg_freshness < Schwellwert): max 0.72
        - Aktuell-Zustand-Claim ohne frische Quellen: max 0.62

    Penalty-Regeln:
        - Zu wenige gute Quellen (Tier 1-3 oder Fact-Check): -0.10
        - CoVe-Widersprüche: dynamisch (bis -0.15)
        - Unbeantwortete CoVe-Fragen: -0.05 pro Frage (max -0.15)
        - Quellen widersprechen sich: -0.10
        - Schlechte Claim-Qualität (score < 0.70): -0.05 bis -0.10

    Args:
        raw_confidence: Rohkonfidenz des LLM (0.0–1.0)
        pack: EvidencePack mit Qualitätssignalen inkl. off_topic_rate
        cove_trace: Optionaler Chain-of-Verification Trace
        claim_quality_score: Qualität des ursprünglichen Claims (0.0–1.0)
                             aus ProcessedClaim.claim_quality_score
        is_regulatory_claim: True wenn der Claim Beschlüsse, Bußgelder,
                             Überwachung oder rechtlich bindende Regeln behauptet
        is_current_state_claim: True wenn der Claim einen aktuellen Amtsinhaber oder
                                Rolleninhaber beschreibt (zeitkritisch)
        stale_freshness_threshold: Schwellwert für avg_freshness unterhalb dessen
                                   Quellen als veraltet gelten (Default: 0.40)
    """
    confidence = raw_confidence
    reasons: list[str] = []

    quality = pack.evidence_quality

    # ── Ceilings ──────────────────────────────────────────────────────────────

    has_primary = quality.has_primary_sources if quality else False
    has_fc = quality.has_fact_check_org_result if quality else False

    # Ceiling: ohne Primärquelle oder Fact-Check
    if not has_primary and not has_fc:
        if confidence > _CEILING_NO_PRIMARY_SOURCE:
            reasons.append(f"Keine Primärquelle/Fact-Check → Ceiling {_CEILING_NO_PRIMARY_SOURCE}")
            confidence = min(confidence, _CEILING_NO_PRIMARY_SOURCE)

    # Ceiling: schwache Evidenzqualität
    if quality and quality.overall_quality < 0.3:
        if confidence > _CEILING_WEAK_EVIDENCE:
            reasons.append(f"Schwache Evidenzqualität ({quality.overall_quality:.2f}) → Ceiling {_CEILING_WEAK_EVIDENCE}")
            confidence = min(confidence, _CEILING_WEAK_EVIDENCE)

    # Ceiling: insufficient consensus
    if quality and quality.source_consensus.value == "insufficient":
        if confidence > _CEILING_INSUFFICIENT_CONSENSUS:
            reasons.append(f"Unzureichender Quellen-Konsens → Ceiling {_CEILING_INSUFFICIENT_CONSENSUS}")
            confidence = min(confidence, _CEILING_INSUFFICIENT_CONSENSUS)

    # Ceiling: off-topic contamination – aus gemessener off_topic_rate
    # (bevorzugt gegenüber der Inline-Berechnung unten, da bereits in Signals)
    if quality and quality.off_topic_rate > 0.5:
        if confidence > _CEILING_OFFTOPIC_CONTAMINATION:
            reasons.append(
                f"Off-topic-Rate {quality.off_topic_rate:.0%} → "
                f"Ceiling {_CEILING_OFFTOPIC_CONTAMINATION}"
            )
            confidence = min(confidence, _CEILING_OFFTOPIC_CONTAMINATION)
    elif pack.web_results:
        # Fallback: inline berechnen wenn off_topic_rate nicht gesetzt
        top_results = pack.web_results[:5]
        low_rel = sum(1 for r in top_results if r.relevance_score < 0.3)
        if low_rel > len(top_results) / 2:
            if confidence > _CEILING_OFFTOPIC_CONTAMINATION:
                reasons.append(
                    f"Off-topic Contamination ({low_rel}/{len(top_results)} schwach) "
                    f"→ Ceiling {_CEILING_OFFTOPIC_CONTAMINATION}"
                )
                confidence = min(confidence, _CEILING_OFFTOPIC_CONTAMINATION)

    # Ceiling: schlechte Claim-Qualität (Claim hat bei der Dekomposition Kontext verloren
    # oder war von Anfang an vage → senkt die Ceiling zusätzlich)
    if claim_quality_score < 0.50:
        if confidence > _CEILING_POOR_CLAIM_QUALITY:
            reasons.append(
                f"Niedrige Claim-Qualität ({claim_quality_score:.2f}) → "
                f"Ceiling {_CEILING_POOR_CLAIM_QUALITY}"
            )
            confidence = min(confidence, _CEILING_POOR_CLAIM_QUALITY)

    # Ceiling: schwache Top-5-Relevanz (Produkte, Rechner, allgemeine Seiten dominieren)
    # Nur anwenden wenn ein echter Messwert vorliegt: avg_top5_relevance > 0.0.
    # Der Default 0.0 ist ein Sentinel-Wert ("nicht gemessen"), kein echter Messwert.
    # In der Praxis berechnet _compute_quality_signals immer > 0 wenn web_results vorhanden.
    _avg_rel = quality.avg_top5_relevance if quality else 0.0
    if quality and _avg_rel > 0.0 and _avg_rel < 0.15:
        if confidence > _CEILING_VERY_LOW_AVG_RELEVANCE:
            reasons.append(
                f"Top-5-Quellen sehr schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_CEILING_VERY_LOW_AVG_RELEVANCE}"
            )
            confidence = min(confidence, _CEILING_VERY_LOW_AVG_RELEVANCE)
    elif quality and _avg_rel > 0.0 and _avg_rel < 0.25:
        if confidence > _CEILING_LOW_AVG_RELEVANCE:
            reasons.append(
                f"Top-5-Quellen schwach (Relevanz Ø={_avg_rel:.2f}) → "
                f"Ceiling {_CEILING_LOW_AVG_RELEVANCE}"
            )
            confidence = min(confidence, _CEILING_LOW_AVG_RELEVANCE)

    # Ceiling: hoher Low-Trust-Anteil (Währungsrechner, Grammatik, Juraforen in Top-5)
    # Verschärft: ab 20% Anteil greift das Ceiling (vorher 30%)
    _low_trust = quality.low_trust_rate if quality else 0.0
    if quality and _low_trust > 0.2:
        effective_ceiling = _CEILING_HIGH_LOW_TRUST if _low_trust > 0.3 else 0.70
        if confidence > effective_ceiling:
            reasons.append(
                f"Low-Trust-Quellen dominieren (Rate={_low_trust:.0%}) → "
                f"Ceiling {effective_ceiling}"
            )
            confidence = min(confidence, effective_ceiling)

    # Ceiling: Regelungsclaim ohne offizielle Quelle (Tier 1-2)
    if is_regulatory_claim and not has_primary and not has_fc:
        if confidence > _CEILING_REGULATORY_NO_OFFICIAL:
            reasons.append(
                f"Regelungsclaim ohne offizielle Quelle/Fact-Check → "
                f"Ceiling {_CEILING_REGULATORY_NO_OFFICIAL}"
            )
            confidence = min(confidence, _CEILING_REGULATORY_NO_OFFICIAL)

    # Ceiling: überwiegend contextual evidence (kein direct evidence in Top-5)
    # Verhindert Support Leakage: allgemeiner Kontext darf Confidence nicht hochtreiben
    _contextual_rate = quality.contextual_only_rate if quality else 0.0
    _direct_count = quality.direct_evidence_count if quality else 0
    if quality and _contextual_rate > 0.6 and _direct_count == 0:
        if confidence > _CEILING_CONTEXTUAL_ONLY:
            reasons.append(
                f"Überwiegend Kontext-Evidenz ({_contextual_rate:.0%}, "
                f"0 direkte Belege) → Ceiling {_CEILING_CONTEXTUAL_ONLY}"
            )
            confidence = min(confidence, _CEILING_CONTEXTUAL_ONLY)

    # Ceiling: Regelungsclaim ohne direkte Regelungsgrundlage (strenger)
    # Betrifft Claims über Beschlüsse, Bußgelder, Überwachung, rechtlich bindende Regeln
    if is_regulatory_claim and _direct_count == 0:
        if confidence > _CEILING_REGULATORY_NO_DIRECT_EVIDENCE:
            reasons.append(
                f"Regelungsclaim ohne direkte Evidenz (0 DIRECT in Top-5) → "
                f"Ceiling {_CEILING_REGULATORY_NO_DIRECT_EVIDENCE}"
            )
            confidence = min(confidence, _CEILING_REGULATORY_NO_DIRECT_EVIDENCE)

    # Ceiling: hohe weak evidence rate (>60% WEAK-Evidenz in Top-5)
    if pack.web_results:
        _top5 = pack.web_results[:5]
        _weak_count = sum(1 for i in _top5 if i.evidence_type == EvidenceType.WEAK)
        if _weak_count / max(1, len(_top5)) > 0.6:
            if confidence > _CEILING_HIGH_WEAK_RATE:
                reasons.append(
                    f"Hohe Weak-Evidence-Rate ({_weak_count}/{len(_top5)} WEAK) → "
                    f"Ceiling {_CEILING_HIGH_WEAK_RATE}"
                )
                confidence = min(confidence, _CEILING_HIGH_WEAK_RATE)

    # Ceiling: contextual evidence + low-trust kombiniert (verschärft)
    if quality and _contextual_rate > 0.5 and _low_trust > 0.2:
        if confidence > _CEILING_CONTEXTUAL_AND_LOW_TRUST:
            reasons.append(
                f"Kontext-Evidenz ({_contextual_rate:.0%}) + Low-Trust ({_low_trust:.0%}) → "
                f"Ceiling {_CEILING_CONTEXTUAL_AND_LOW_TRUST}"
            )
            confidence = min(confidence, _CEILING_CONTEXTUAL_AND_LOW_TRUST)

    # Ceiling: veraltete Quellen (avg_freshness unter Schwellwert)
    _freshness = quality.freshness_score if quality else 1.0
    if quality and _freshness < stale_freshness_threshold:
        if confidence > _CEILING_STALE_SOURCES:
            reasons.append(
                f"Veraltete Quellen (Freshness Ø={_freshness:.2f} < {stale_freshness_threshold}) → "
                f"Ceiling {_CEILING_STALE_SOURCES}"
            )
            confidence = min(confidence, _CEILING_STALE_SOURCES)

    # Ceiling: Aktuell-Zustand-Claim ohne frische Quellen (zeitkritisch)
    if is_current_state_claim and quality and _freshness < stale_freshness_threshold:
        if confidence > _CEILING_CURRENT_STATE_NO_FRESH:
            reasons.append(
                f"Aktuell-Zustand-Claim mit veralteten Quellen (Freshness Ø={_freshness:.2f}) → "
                f"Ceiling {_CEILING_CURRENT_STATE_NO_FRESH}"
            )
            confidence = min(confidence, _CEILING_CURRENT_STATE_NO_FRESH)

    # Ceiling: keinerlei brauchbare Evidenz
    # Greift wenn KEIN DIRECT-Evidence, keine Primärquelle, kein Fact-Check
    # UND Konsens insufficient → System hat de facto nichts Brauchbares gefunden.
    if (not has_primary and not has_fc
            and _direct_count == 0
            and quality
            and quality.source_consensus.value == "insufficient"):
        if confidence > _CEILING_ZERO_USEFUL_EVIDENCE:
            reasons.append(
                f"Keine brauchbare Evidenz (0 DIRECT, keine Primärquelle, "
                f"kein Fact-Check, Konsens insufficient) → Ceiling {_CEILING_ZERO_USEFUL_EVIDENCE}"
            )
            confidence = min(confidence, _CEILING_ZERO_USEFUL_EVIDENCE)

    # ── Penalties ─────────────────────────────────────────────────────────────

    # Penalty: zu wenige gute Quellen (Tier 1-3 oder Fact-Check)
    good_sources = sum(
        1 for r in pack.web_results
        if r.source.domain_tier <= 3 or r.source.is_fact_check_org
    )
    if good_sources < _MIN_GOOD_SOURCES_FOR_HIGH_CONF:
        penalty = 0.10
        reasons.append(f"Nur {good_sources} gute Quellen → -{penalty}")
        confidence -= penalty

    # Penalty: CoVe-Widersprüche
    if cove_trace and cove_trace.has_significant_contradictions():
        delta = abs(min(0.0, cove_trace.confidence_delta))
        if delta > 0:
            reasons.append(f"CoVe-Widersprüche (delta={cove_trace.confidence_delta:.2f}) → -{delta:.2f}")
            confidence -= delta

    # Penalty: Unbeantwortete CoVe-Kernfragen
    if cove_trace and cove_trace.unanswered_questions:
        n_unanswered = len(cove_trace.unanswered_questions)
        penalty = min(0.15, n_unanswered * 0.05)
        reasons.append(f"{n_unanswered} unbeantwortete CoVe-Fragen → -{penalty:.2f}")
        confidence -= penalty

    # Penalty: Quellen widersprechen sich
    if quality and quality.source_consensus.value == "contradictory":
        penalty = 0.10
        reasons.append(f"Quellen widersprechen sich → -{penalty}")
        confidence -= penalty

    # Penalty: Claim-Qualität unter Schwellwert
    if claim_quality_score < 0.70:
        # Sanfter gradueller Abzug: 0.05 bei 0.5–0.7, 0.10 darunter
        penalty = 0.10 if claim_quality_score < 0.50 else 0.05
        reasons.append(f"Claim-Qualität niedrig ({claim_quality_score:.2f}) → -{penalty}")
        confidence -= penalty

    confidence = max(0.0, min(1.0, confidence))
    return confidence, reasons


_VERDICT_SYSTEM_PROMPT = """\
Du bist ein Fact-Checker. Deine EINZIGE Aufgabe: Fälle ein fundiertes Urteil
über die gegebene Behauptung basierend auf den bereitgestellten Fakten.

Du erhältst strukturierte Evidenz (keine Webseiten-Rohtexte).

## Quellen-Hierarchie (in dieser Reihenfolge vertrauen)
1. Offizielle Statistikämter (Destatis, Eurostat)
2. Offizielle Behörden (BAMF, BKA, BMI)
3. Qualitätsjournalismus (Reuters, dpa, Tagesschau, Zeit, SZ)
4. Faktencheck-Organisationen (Correctiv, dpa Faktencheck, Mimikama)
5. Akademische Quellen

## Bewertungsskala
- TRUE: Faktenkonform, korrekt kontextualisiert
- MOSTLY_TRUE: Kern stimmt, Details ungenau
- MISLEADING: Technisch korrekt, aber irreführend präsentiert
- MOSTLY_FALSE: Kernaussage falsch, enthält wahre Elemente
- FALSE: Nachweislich falsch
- UNVERIFIABLE: Kann mit verfügbaren Quellen nicht geprüft werden

## Regeln
- Wenn professionelle Faktenchecks vorliegen: deren Einschätzung stark gewichten
- Sei fair: Wenn etwas stimmt, sag es klar
- Prüfe Zeitraum, Bezugsgröße, Kategorie
- Gib die URLs der verwendeten Quellen an

## Sonderregel: Claims über Beschlüsse, Bußgelder, Überwachung, Regelungen
Wenn ein Claim eine konkrete Regelung, einen Beschluss, ein Bußgeld oder eine
Überwachungsmaßnahme behauptet UND keine belastbare amtliche oder journalistische
Quelle diesen Sachverhalt bestätigt, dann:
- Bevorzuge FALSE oder MOSTLY_FALSE gegenüber MISLEADING oder UNVERIFIABLE
- MISLEADING nur, wenn ein ähnliches (nicht identisches) Konzept belegt ist,
  der Claim dieses aber verzerrt oder übertreibt
- UNVERIFIABLE nur, wenn das Thema prinzipiell nicht nachprüfbar ist (z.B. interne
  Beratungen ohne öffentliche Quellen) – NICHT als Ausweichoption bei schlechten Quellen
- Konkret: Wenn ein spezifisches Bußgeld, eine Überwachungsmaßnahme oder eine
  rechtlich bindende Regel behauptet wird und KEINE Regelungsgrundlage in den
  Quellen existiert, ist das Urteil FALSE – nicht MISLEADING

## Evidenz-Typen beachten
Jede Quelle ist als DIREKT, KONTEXT oder SCHWACH klassifiziert:
- DIREKT: Belegt den konkreten Claim (offizielle Quelle, Faktenchecker, Bericht mit Claim-Bezug)
- KONTEXT: Allgemeiner Hintergrund (erklärt das Thema, belegt aber NICHT den konkreten Claim)
- SCHWACH: Low-Trust oder nur entfernt verwandt (keine Evidenz-Kraft)

WICHTIG: KONTEXT-Quellen dürfen NICHT als Teilbeleg für konkrete Regelungsdetails zählen.
Beispiel: Eine allgemeine Seite über „15-Minuten-Stadt" belegt NICHT eine geheime Sitzung
oder ein spezifisches Bußgeld. Eine allgemeine Kameraüberwachungsseite belegt NICHT
ein konkretes 250-Euro-Bußgeld.

## Sonderregel: Aktuell-Zustand-Claims (Amtsinhaber, Rolleninhaber)
Wenn ein Claim einen aktuellen Amts- oder Rolleninhaber beschreibt
(z.B. „X ist Bundeskanzler", „Y ist Präsident", „Z ist CEO"):
- Diese Claims sind zeitkritisch – nur Quellen aus der jüngsten Zeit zählen
- Alte Quellen (> 1–2 Jahre) können einen früheren Zustand beschreiben und
  dürfen NICHT als Beleg für den aktuellen Zustand gewertet werden
- Wenn ausschließlich veraltete Quellen vorliegen und keine aktuellen Quellen den
  behaupteten Zustand bestätigen: Wähle UNVERIFIABLE, nicht TRUE
- Wenn veraltete Quellen einen anderen Amtsinhaber nennen: Wähle MISLEADING oder FALSE
- Wichtig: „Quelle von 2022 nennt Person X als Kanzler" ist KEIN Beleg für
  „Person X ist aktuell (2025/2026) Kanzler"

## Quellen-Qualitätshinweis
Wenn die Evidenzquellen überwiegend aus allgemeinen Hilfsseiten bestehen
(Währungsrechner, Grammatikseiten, Juraforen ohne Claim-Bezug, Bußgeldrechner):
- Diese Quellen belegen NICHTS über den konkreten Claim
- Behandle solche Quellen als Nicht-Evidenz (weder stützend noch widerlegend)
- Ziehe dein Urteil AUS DEM FEHLEN belastbarer Quellen, nicht aus dem Inhalt
  irrelevanter Seiten

## Evidenz-Provenienz-Pflicht
Dein Urteil MUSS sich auf die bereitgestellten Quellen stützen.
Du darfst dein eigenes Vorwissen NICHT als Ersatz für fehlende Evidenz verwenden.

Wenn die bereitgestellten Quellen keine relevante Information zum Claim enthalten:
- Setze das Rating auf UNVERIFIABLE
- Setze die Confidence auf maximal 0.50
- Erkläre im evidence-Feld ehrlich, dass keine belastbaren Quellen gefunden wurden
- Einzige Ausnahme: Wenn der Claim eine nachweislich UNMÖGLICHE Behauptung enthält
  (physikalisch unmöglich, logischer Widerspruch), darf FALSE gesetzt werden.

Im sources-Feld dürfen NUR URLs erscheinen, die tatsächlich in der bereitgestellten
Evidenz vorkommen. Erfinde KEINE URLs.

## Output-Format (JSON)
{
  "claim_id": "C1",
  "rating": "MISLEADING",
  "confidence": 0.75,
  "evidence": "Zusammenfassung der Fakten",
  "correction": "Was falsch oder irreführend ist",
  "missing_context": "Welcher Kontext fehlt",
  "sources": ["url1", "url2"]
}
"""


class VerdictAgent(BaseAgent):
    """Fällt ein Urteil auf Basis eines EvidencePack und optionalem CoVe-Trace.

    Input:
        input_data: dict mit keys:
            - claim: Claim
            - evidence_pack: EvidencePack
            - cove_trace: CoVeTrace | None
            - number_audit: NumberAuditResult | None

    Output:
        FactCheckResult (mit ausgefüllten evidence_pack, cove_trace, verdict_meta)
    """

    name = "Verdict Agent"
    emoji = "⚖️"

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        data: dict = input_data
        claim: Claim = data["claim"]
        pack: EvidencePack = data["evidence_pack"]
        cove_trace: CoVeTrace | None = data.get("cove_trace")
        number_audit: NumberAuditResult | None = data.get("number_audit")

        # Prompt aufbauen (nur strukturierte Daten, kein roher Web-Inhalt)
        user_msg = self._build_verdict_prompt(claim, pack, cove_trace, number_audit)

        raw = self._llm_structured(
            _VERDICT_SYSTEM_PROMPT,
            user_msg,
            FACT_CHECK_SCHEMA,
            tool_name="verdict",
            tool_description="Fact-Check Urteil",
        )

        try:
            rating = FactRating(raw.get("rating", "UNVERIFIABLE"))
        except ValueError:
            rating = FactRating.UNVERIFIABLE

        # ── Regelbasierte Confidence-Kalibrierung ──────────────────────────────
        # LLM-Confidence wird NICHT direkt übernommen, sondern durch
        # objektive Pipeline-Signale korrigiert.
        raw_confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.75))))

        # Claim-Qualität einbeziehen (kommt aus ProcessedClaim wenn vorhanden)
        from agents.fact_checker import _is_current_state_claim
        from models.schemas import ProcessedClaim as _PC
        claim_quality = 1.0
        is_regulatory = False
        if isinstance(claim, _PC):
            claim_quality = claim.claim_quality_score
            # Regulatory-Claim-Erkennung: primär aus Frame-Feldern
            if claim.frame:
                f = claim.frame
                is_regulatory = bool(
                    f.sanction or f.enforcement
                    or (f.policy_context and f.institution)
                )
            # Textueller Fallback wenn kein Frame vorhanden
            if not is_regulatory:
                is_regulatory = _is_regulatory_from_text(claim.text)

        is_current_state = _is_current_state_claim(claim.text)

        # Current-state Claims brauchen frischere Quellen (Threshold 0.60 statt 0.40).
        # Damit triggern Quellen ohne Datum (default 0.5) ebenfalls das Stale-Ceiling.
        stale_threshold = 0.60 if is_current_state else 0.40

        calibrated_confidence, calibration_reasons = _calibrate_confidence(
            raw_confidence, pack, cove_trace,
            claim_quality_score=claim_quality,
            is_regulatory_claim=is_regulatory,
            is_current_state_claim=is_current_state,
            stale_freshness_threshold=stale_threshold,
        )

        # Unsicherheitssignale aus Kalibrierung + eigenen Checks sammeln
        uncertainty_signals = list(calibration_reasons)

        if cove_trace:
            if cove_trace.unanswered_questions:
                uncertainty_signals.append(
                    f"Unbeantwortete Verifikationsfragen: {', '.join(cove_trace.unanswered_questions)}"
                )
            if cove_trace.has_significant_contradictions():
                uncertainty_signals.append(
                    f"CoVe: {len(cove_trace.contradictions_found)} Widersprüche gefunden"
                )

        if pack.evidence_quality:
            if pack.evidence_quality.overall_quality < 0.3:
                uncertainty_signals.append("Evidenzqualität niedrig")
            if pack.evidence_quality.source_consensus.value == "contradictory":
                uncertainty_signals.append("Quellen widersprechen sich")
            if pack.evidence_quality.off_topic_rate > 0.4:
                uncertainty_signals.append(
                    f"Hohe Off-topic-Rate: {pack.evidence_quality.off_topic_rate:.0%} der Top-Treffer irrelevant"
                )
        if claim_quality < 0.70:
            uncertainty_signals.append(f"Claim-Qualität eingeschränkt ({claim_quality:.2f})")

        # FinalVerdictMeta
        confidence_reduction_reason = "; ".join(calibration_reasons) if calibration_reasons else ""
        verdict_meta = FinalVerdictMeta(
            cove_trace=cove_trace,
            uncertainty_signals=uncertainty_signals,
            confidence_reduction_reason=confidence_reduction_reason,
            verdict_based_on_fact_check_org=bool(pack.google_fact_check_matches),
            primary_sources_consulted=(
                pack.evidence_quality.has_primary_sources
                if pack.evidence_quality else False
            ),
        )

        # Quellen aus EvidencePack + Raw-Output zusammenführen
        sources_from_pack = [i.source.url for i in pack.selected_sources]
        sources_from_llm = raw.get("sources", [])
        all_sources = list(dict.fromkeys(sources_from_pack + sources_from_llm))  # dedup, ordered

        # SourceInfo für classified_sources
        classified = [
            SourceInfo(
                url=src.url,
                tier={1: "Offizielle Quelle", 2: "Offizielle Quelle",
                      3: "Qualitätsjournalismus", 4: "Faktencheck-Organisation",
                      5: "Unbekannt"}.get(src.domain_tier, "Unbekannt"),
                domain=src.domain,
            )
            for src in pack.selected_sources
        ]

        result = FactCheckResult(
            claim_id=claim.id,
            rating=rating,
            evidence=raw.get("evidence", ""),
            correction=raw.get("correction", ""),
            missing_context=raw.get("missing_context", ""),
            sources=all_sources[:10],
            classified_sources=classified,
            source_consensus=(
                pack.evidence_quality.source_consensus.value
                if pack.evidence_quality else ""
            ),
            evidence_pack=pack,
            cove_trace=cove_trace,
            verdict_meta=verdict_meta,
        )

        self._log(f"Urteil {claim.id}: {result.rating.value}")
        return result

    def _build_verdict_prompt(
        self,
        claim: Claim,
        pack: EvidencePack,
        cove_trace: CoVeTrace | None,
        number_audit: NumberAuditResult | None,
    ) -> str:
        parts: list[str] = [
            f"## Zu prüfende Behauptung\n\n"
            f"Claim ID: {claim.id}\n"
            f"Text: {claim.text}\n"
            f"Typ: {claim.type.value}\n"
            f"Kontext-Hinweis: {claim.context}\n",
        ]

        # Warnung bei sehr niedriger Evidenzqualität
        if pack.evidence_quality and pack.evidence_quality.overall_quality < 0.3:
            parts.append(
                "\n## WARNUNG: Evidenzqualität sehr niedrig\n\n"
                "Die bereitgestellten Quellen sind überwiegend irrelevant oder generisch. "
                "Stütze dein Urteil NICHT auf eigenes Vorwissen. "
                "Wenn keine belastbaren Quellen vorliegen, wähle UNVERIFIABLE mit niedriger Confidence.\n"
            )

        # Strukturierte Evidenz (Trust-Boundary-gefiltert)
        parts.append(f"\n## Strukturierte Evidenz\n\n{pack.format_for_verdict()}")

        # CoVe-Ergebnisse
        if cove_trace:
            qa_summary = "\n".join(
                f"Q ({a.question_id}): {next((q.text for q in cove_trace.verification_questions if q.question_id == a.question_id), '?')}\n"
                f"  A: {a.answer} (Widerspruch zur Baseline: {a.contradicts_baseline})"
                for a in cove_trace.verification_answers
            )
            parts.append(
                f"\n## Chain-of-Verification Ergebnisse\n\n"
                f"Baseline: {cove_trace.baseline.rating} (Konfidenz: {cove_trace.baseline.confidence:.2f})\n"
                f"Baseline-Begründung: {cove_trace.baseline.reasoning}\n\n"
                f"Verifikations-Q&A:\n{qa_summary}\n\n"
                f"Gefundene Widersprüche: {', '.join(cove_trace.contradictions_found) or 'keine'}\n"
            )

        # Number Audit
        if number_audit and number_audit.manipulation_type.value != "NONE":
            parts.append(
                f"\n## Zahlen-Audit\n\n"
                f"Manipulationstyp: {number_audit.manipulation_type.value}\n"
                f"Nachrechnung: {number_audit.calculation_check}\n"
                f"Korrekte Einordnung: {number_audit.correct_interpretation}\n"
                f"Methodische Probleme: {'; '.join(number_audit.methodology_issues)}\n"
            )

        return "\n".join(parts)
