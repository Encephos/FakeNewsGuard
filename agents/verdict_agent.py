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

from typing import Any

from agents.base import BaseAgent
from agents.verdict_calibration import (  # noqa: F401 – re-exported for backward compat
    VerdictRatingCalibrationConfig,
    _calibrate_confidence,
    _calibrate_rating,
    _is_regulatory_from_text,
    _CEILING_CONTEXTUAL_AND_LOW_TRUST,
    _CEILING_CONTEXTUAL_ONLY,
    _CEILING_CURRENT_STATE_NO_FRESH,
    _CEILING_HIGH_LOW_TRUST,
    _CEILING_HIGH_WEAK_RATE,
    _CEILING_INSUFFICIENT_CONSENSUS,
    _CEILING_LOW_AVG_RELEVANCE,
    _CEILING_NO_PRIMARY_SOURCE,
    _CEILING_OFFTOPIC_CONTAMINATION,
    _CEILING_POOR_CLAIM_QUALITY,
    _CEILING_REGULATORY_NO_DIRECT_EVIDENCE,
    _CEILING_REGULATORY_NO_OFFICIAL,
    _CEILING_REGULATORY_NOISY_CONTEXTUAL,
    _CEILING_STALE_SOURCES,
    _CEILING_VERY_LOW_AVG_RELEVANCE,
    _CEILING_WEAK_EVIDENCE,
    _CEILING_ZERO_USEFUL_EVIDENCE,
    _MIN_GOOD_SOURCES_FOR_HIGH_CONF,
    _REGULATORY_TEXT_PATTERN,
)
from models.evidence_models import EvidencePack
from models.schemas import (
    FACT_CHECK_SCHEMA,
    Claim,
    FactCheckResult,
    FactRating,
    NumberAuditResult,
    SourceInfo,
)
from models.verdict_models import CoVeTrace, FinalVerdictMeta


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
Überwachungsmaßnahme behauptet, gilt diese Stufenlogik:

**Aktive Widerlegung (direkter Beweis):**
- Mindestens eine DIREKT-klassifizierte Quelle widerlegt den Claim explizit (z.B.
  offizielle Stellungnahme: „Diese Regelung existiert nicht") → FALSE
- Ein professioneller Faktenchecker hat diesen konkreten Claim geprüft und widerlegt
  → FALSE

**Kontextuelle Widerlegung (schwacher Beweis):**
- Thematisch relevante Quellen zeigen, dass das behauptete Konzept in abgewandelter
  Form existiert, aber der Claim wesentliche Details verzerrt → MOSTLY_FALSE
- Allgemeiner Themenkontext ohne konkreten Claim-Bezug → MISLEADING (wenn naheliegend
  irreführend formuliert) oder UNVERIFIABLE (wenn keine Ableitungen möglich)

**Fehlende Evidenz (kein aktiver Beweis):**
- Keine relevante Quelle gefunden, keine direkte Widerlegung → UNVERIFIABLE
  NICHT FALSE – fehlendes Beweis ist keine Widerlegung
- Allgemeine Kontext-Quellen (KONTEXT-klassifiziert) belegen NICHT, dass eine
  spezifische Regelung nicht existiert

WICHTIG: Schließe NICHT von „keine Quelle bestätigt dies" auf FALSE.
Das Fehlen von Belegen rechtfertigt UNVERIFIABLE, nicht FALSE.

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
Das heutige Datum wird im Prompt mitgeliefert. Verwende es als Referenz.
Wenn ein Claim einen aktuellen Amts- oder Rolleninhaber beschreibt
(z.B. „X ist Bundeskanzler", „Y ist Präsident", „Z ist CEO"):
- Diese Claims sind zeitkritisch – nur Quellen aus der jüngsten Zeit zählen
- Berechne das Alter jeder Quelle relativ zum heutigen Datum
- Alte Quellen (> 1–2 Jahre vor dem heutigen Datum) können einen früheren
  Zustand beschreiben und dürfen NICHT als Beleg für den aktuellen Zustand
  gewertet werden
- Aktuelle Quellen (< 6 Monate alt), die den behaupteten Zustand bestätigen,
  sind starke Belege – auch wenn ältere Quellen einen anderen Zustand nennen
- Wenn ausschließlich veraltete Quellen vorliegen und keine aktuellen Quellen den
  behaupteten Zustand bestätigen: Wähle UNVERIFIABLE, nicht TRUE
- Wenn veraltete Quellen einen anderen Amtsinhaber nennen, ABER aktuelle Quellen
  den Claim bestätigen: Wähle TRUE (der Amtswechsel ist belegt)
- Wichtig: „Quelle von 2022 nennt Person X als Kanzler" ist KEIN Beleg für
  „Person X ist aktuell Kanzler" – prüfe das Datum jeder Quelle

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

## KRITISCH: Kein Training-Data-Override
Wenn die bereitgestellten Quellen einen bestimmten aktuellen Zustand belegen
(z.B. "Person X ist aktuell Kanzler"), aber dein Vorwissen einen anderen Zustand
kennt (z.B. "Person Y war Kanzler"): FOLGE DEN QUELLEN, NICHT DEINEM VORWISSEN.
Dein Trainingsdaten-Cutoff liegt in der Vergangenheit. Die bereitgestellten
Quellen sind aktueller als dein Wissen. Wenn Quellen und Vorwissen sich
widersprechen: Rating = TRUE oder MOSTLY_TRUE basierend auf den Quellen,
NIEMALS FALSE basierend auf veraltetem Vorwissen.

## Rhetorische Sprache im Claim-Text
Der Claim-Text kann rhetorisch manipulative Formulierungen enthalten (Alarmsprache,
emotionale Verstärkung, Framing). Das beeinflusst NICHT dein Faktenurteil:
- Beurteile ausschließlich die faktische Substanz des Claims
- Rhetorische Verstärkung (z.B. „extreme Überwachung", „skandalöse Lüge") macht
  einen unbelegten Claim nicht zu FALSE
- Eine tendenziöse Formulierung ändert UNVERIFIABLE nicht zu FALSE oder MOSTLY_FALSE
- Vermerke erkannte Rhetorik höchstens im missing_context-Feld als Hinweis

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

        import logging
        _verdict_logger = logging.getLogger("fakenewsguard.verdict")
        _verdict_logger.debug("=== VERDICT PROMPT ===\n%s", user_msg[:3000])

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

        # ── Regelbasierte Rating-Kalibrierung ──────────────────────────────────
        # Verhindert, dass fehlendes Beweis automatisch zu FALSE führt.
        # Konfiguration kann über data["rating_calibration_config"] übergeben werden.
        rating_config: VerdictRatingCalibrationConfig | None = data.get("rating_calibration_config")
        # is_regulatory wird weiter unten bestimmt; für die Rating-Kalibrierung
        # brauchen wir es bereits hier → vorab ermitteln.
        from models.schemas import ProcessedClaim as _PC_pre
        _is_regulatory_pre = False
        if isinstance(claim, _PC_pre):
            if claim.frame:
                _f_pre = claim.frame
                _is_regulatory_pre = bool(
                    _f_pre.sanction
                    or _f_pre.enforcement
                    or (_f_pre.policy_context and _f_pre.institution)
                )
            if not _is_regulatory_pre:
                _is_regulatory_pre = _is_regulatory_from_text(claim.text)
        from agents.fact_checker import _is_current_state_claim as _is_cs_pre
        _is_current_state_pre = _is_cs_pre(claim.text)
        rating, rating_calibration_reasons = _calibrate_rating(
            rating, pack, rating_config,
            is_regulatory_claim=_is_regulatory_pre,
            is_current_state_claim=_is_current_state_pre,
            claim_text=claim.text,
        )

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

        # Unsicherheitssignale aus Rating- + Confidence-Kalibrierung sammeln
        uncertainty_signals = list(rating_calibration_reasons) + list(calibration_reasons)

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

        # ── Self-RAG: Verdict Grounding Check ────────────────────────────────
        grounding_score = -1.0  # Default: nicht geprüft
        retrieval_cfg = self.config.evidence_retrieval
        if retrieval_cfg.self_rag_enabled and raw.get("evidence"):
            grounding_score = self._check_verdict_grounding(
                raw.get("evidence", ""), pack,
            )
            if grounding_score >= 0.0 and grounding_score < 0.5:
                # Schweres Grounding-Problem: Confidence-Ceiling
                penalty_reason = (
                    f"Self-RAG: Grounding-Score={grounding_score:.2f} "
                    f"(< 0.50 → Confidence begrenzt auf {retrieval_cfg.self_rag_severe_confidence_ceiling})"
                )
                calibrated_confidence = min(
                    calibrated_confidence,
                    retrieval_cfg.self_rag_severe_confidence_ceiling,
                )
                calibration_reasons.append(penalty_reason)
                uncertainty_signals.append(f"Niedrige Evidenz-Fundierung (Grounding={grounding_score:.2f})")
                self._log(f"Self-RAG {claim.id}: {penalty_reason}")
            elif grounding_score >= 0.0 and grounding_score < 0.75:
                # Moderates Grounding-Problem: Confidence-Penalty
                penalty = retrieval_cfg.self_rag_ungrounded_confidence_penalty
                calibrated_confidence = max(0.0, calibrated_confidence - penalty)
                penalty_reason = (
                    f"Self-RAG: Grounding-Score={grounding_score:.2f} "
                    f"(< 0.75 → Confidence -{penalty})"
                )
                calibration_reasons.append(penalty_reason)
                self._log(f"Self-RAG {claim.id}: {penalty_reason}")

        # FinalVerdictMeta
        all_reduction_reasons = rating_calibration_reasons + calibration_reasons
        confidence_reduction_reason = "; ".join(all_reduction_reasons) if all_reduction_reasons else ""
        verdict_meta = FinalVerdictMeta(
            cove_trace=cove_trace,
            uncertainty_signals=uncertainty_signals,
            confidence_reduction_reason=confidence_reduction_reason,
            calibrated_confidence=calibrated_confidence,
            verdict_based_on_fact_check_org=bool(pack.google_fact_check_matches),
            primary_sources_consulted=(
                pack.evidence_quality.has_primary_source_any
                if pack.evidence_quality else False
            ),
            grounding_score=grounding_score,
        )

        # Quellen aus EvidencePack + Raw-Output zusammenführen
        sources_from_pack = [i.url for i in pack.selected_sources]
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
            confidence=calibrated_confidence,
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

        raw_rating_str = raw.get("rating", "UNVERIFIABLE")
        if rating_calibration_reasons:
            self._log(
                f"Urteil {claim.id}: {result.rating.value} "
                f"(LLM: {raw_rating_str} → kalibriert: {'; '.join(rating_calibration_reasons)})"
            )
        else:
            self._log(f"Urteil {claim.id}: {result.rating.value}")
        return result

    def _build_verdict_prompt(
        self,
        claim: Claim,
        pack: EvidencePack,
        cove_trace: CoVeTrace | None,
        number_audit: NumberAuditResult | None,
    ) -> str:
        from datetime import date

        today = date.today().isoformat()

        parts: list[str] = [
            f"## Heutiges Datum: {today}\n\n"
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

    def _check_verdict_grounding(
        self,
        verdict_reasoning: str,
        pack: EvidencePack,
    ) -> float:
        """Self-RAG: Prüfe ob das Verdict-Reasoning durch Evidence-Excerpts gestützt ist.

        Extrahiert Kernaussagen aus dem Reasoning und prüft per LLM-Call,
        ob jede Aussage durch die zitierten Excerpts belegt ist.

        Returns:
            Grounding-Score (0.0–1.0): Anteil gestützter Aussagen.
            -1.0 wenn Prüfung fehlschlägt oder keine Aussagen vorhanden.
        """
        import json as _json

        # Excerpts aus dem EvidencePack sammeln
        excerpts = []
        for item in pack.web_results[:8]:
            if item.excerpt:
                excerpts.append(f"[{item.source.title}] {item.excerpt}")
        for fc in pack.google_fact_check_matches:
            excerpts.append(f"[Faktencheck: {fc.publisher}] {fc.claim_reviewed} → {fc.rating}")

        if not excerpts or not verdict_reasoning.strip():
            return -1.0

        excerpts_text = "\n---\n".join(excerpts)

        system_prompt = (
            "Du bist ein Grounding-Prüfer. Du erhältst eine Begründung (Reasoning) "
            "und eine Liste von Evidenz-Excerpts.\n\n"
            "Prüfe für jede faktische Kernaussage im Reasoning, ob sie durch "
            "mindestens einen Excerpt gestützt wird.\n\n"
            "Antwortformat: JSON-Objekt mit:\n"
            '{"statements": [{"text": "...", "supported": true/false}], '
            '"supported_count": N, "total_count": N}\n\n'
            "Ignoriere stilistische Elemente, Einleitungen und Schlussfolgerungen. "
            "Prüfe nur faktische Aussagen (Zahlen, Daten, Fakten, Quellenverweise)."
        )
        user_msg = (
            f"## Reasoning\n{verdict_reasoning}\n\n"
            f"## Evidenz-Excerpts\n{excerpts_text}"
        )

        try:
            raw = self.llm.complete(system_prompt, user_msg, response_format="json")
            result = _json.loads(raw) if isinstance(raw, str) else raw
            supported = int(result.get("supported_count", 0))
            total = int(result.get("total_count", 0))
            if total == 0:
                return -1.0
            return min(1.0, max(0.0, supported / total))
        except Exception as e:
            self._log(f"Self-RAG Grounding-Check fehlgeschlagen: {type(e).__name__}")
            return -1.0
