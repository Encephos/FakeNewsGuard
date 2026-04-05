"""Synthesizer – Aggregiert alle Teilergebnisse zum Gesamtverdikt."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.base import BaseAgent
from i18n import t
from models.schemas import (
    SYNTHESIS_SCHEMA,
    FactCheckResult,
    FactRating,
    ImageAnalysisResult,
    NumberAuditResult,
    OverallRating,
    RhetoricAnalysisResult,
    Severity,
    SynthesisResult,
)

# Reihenfolge der Ratings für Vergleiche (kleiner = besser)
_RATING_ORDER: dict[OverallRating, int] = {
    OverallRating.RELIABLE: 0,
    OverallRating.MOSTLY_RELIABLE: 1,
    OverallRating.MIXED: 2,
    OverallRating.MISLEADING: 3,
    OverallRating.HIGHLY_MISLEADING: 4,
    OverallRating.FABRICATED: 5,
}

_SEVERITY_WEIGHT: dict[str, float] = {
    Severity.HIGH.value: 3.0,
    Severity.MEDIUM.value: 2.0,
    Severity.LOW.value: 1.0,
}


@dataclass
class AggregationSignals:
    """Abgeleitete Signale für die regelbasierte Rating-Kalibrierung."""

    n_claims: int = 0
    refuted_ratio: float = 0.0        # Anteil FALSE + MOSTLY_FALSE
    unverified_ratio: float = 0.0     # Anteil UNVERIFIABLE
    avg_claim_confidence: float = 0.0
    high_quality_evidence: bool = False  # mind. ein Claim mit Primärquellen
    rhetoric_score: float = 0.0          # 0.0–1.0, gewichtete Rhetorik-Schwere
    n_high_rhetoric: int = 0             # Anzahl HIGH-Severity-Techniken


class SynthesizerAgent(BaseAgent):
    name = "Synthesizer"
    emoji = "📊"

    # ── Öffentliche Schnittstelle ─────────────────────────────────

    def execute(self, input_data: Any, context: str = "") -> SynthesisResult:
        """Input ist ein dict mit allen Teilergebnissen."""
        data: dict = input_data

        fact_checks: list[FactCheckResult] = data.get("fact_checks", [])
        number_audits: list[NumberAuditResult] = data.get("number_audits", [])
        rhetoric: RhetoricAnalysisResult | None = data.get("rhetoric")
        original_text: str = data.get("original_text", "")
        image_analysis: str = data.get("image_analysis", "")
        image_analysis_result: ImageAnalysisResult | None = data.get("image_analysis_result")

        signals = self._compute_aggregation_signals(fact_checks, rhetoric, image_analysis_result)

        # Kontext für das LLM zusammenbauen
        parts: list[str] = [f"## Originaltext\n\n{original_text}\n"]

        if fact_checks:
            parts.append("## Fact-Check-Ergebnisse\n")
            for fc in fact_checks:
                conf_hint = (
                    f"  Kalibrierte Konfidenz: {fc.confidence:.0%}\n"
                    if fc.confidence >= 0.0 else ""
                )
                parts.append(
                    f"- Claim {fc.claim_id}: **{fc.rating.value}**\n"
                    f"  Evidenz: {fc.evidence}\n"
                    f"  Korrektur: {fc.correction}\n"
                    f"  Fehlender Kontext: {fc.missing_context}\n"
                    f"  Quellen: {', '.join(fc.sources)}\n"
                    + conf_hint
                )

        if number_audits:
            parts.append("## Number-Audit-Ergebnisse\n")
            for na in number_audits:
                parts.append(
                    f"- Claim {na.claim_id}: Manipulation = {na.manipulation_type.value}\n"
                    f"  Rechnung: {na.calculation_check}\n"
                    f"  Korrekte Einordnung: {na.correct_interpretation}\n"
                    f"  Methodische Probleme: {', '.join(na.methodology_issues)}\n"
                )

        if rhetoric:
            parts.append("## Rhetoric-Analyse\n")
            parts.append(f"Gesamtframing: {rhetoric.overall_framing}\n")
            for tech in rhetoric.techniques:
                parts.append(
                    f"- {tech.technique} ({tech.severity.value}): {tech.explanation}\n"
                    f"  Beispiel: \"{tech.example}\"\n"
                )

        if image_analysis:
            parts.append("## Bildanalyse\n")
            parts.append(image_analysis + "\n")

        # Cross-Claim Evidence Map: Quellen die mehrere Claims betreffen
        cross_claim_map: dict = data.get("cross_claim_evidence_map", {})
        if cross_claim_map:
            parts.append("## Cross-Claim Evidence (Quellen die mehrere Claims betreffen)\n")
            for url, entries in list(cross_claim_map.items())[:10]:
                claim_refs = ", ".join(
                    f"{e['claim_id']}={e['direction']}" for e in entries
                )
                parts.append(f"- {url}: {claim_refs}\n")

        # Aggregationssignale als strukturierte Entscheidungshilfe für das LLM
        parts.append(self._format_signals_section(signals))

        user_msg = "\n".join(parts)

        prompt = t("agents.synthesizer.system_prompt")
        tool_desc = t("agents.synthesizer.tool_description")
        raw = self._llm_structured(
            prompt, user_msg, SYNTHESIS_SCHEMA,
            tool_name="synthesis", tool_description=tool_desc
        )

        # Alle Quellen sammeln und deduplizieren
        all_sources: list[str] = raw.get("sources", [])
        for fc in fact_checks:
            all_sources.extend(fc.sources)
        all_sources = list(dict.fromkeys(all_sources))

        try:
            llm_rating = OverallRating(raw.get("overall_rating", "MIXED"))
        except ValueError:
            llm_rating = OverallRating.MIXED

        rating = self._apply_rating_guardrails(llm_rating, signals, fact_checks)

        # Wenn Guardrail das Rating korrigiert hat und alle Claims positiv sind,
        # ist die LLM-Summary wahrscheinlich widersprüchlich → ersetzen.
        summary = raw.get("summary", "")
        satire_count = sum(1 for fc in fact_checks if fc.is_satire)
        if satire_count:
            satire_note = f"{satire_count} Claim{'s' if satire_count > 1 else ''} als Satire identifiziert."
            summary = f"{summary} {satire_note}".strip() if summary else satire_note
        if rating != llm_rating and fact_checks:
            positive_ratings = {FactRating.TRUE, FactRating.MOSTLY_TRUE}
            if all(fc.rating in positive_ratings for fc in fact_checks):
                summary = self._build_evidence_based_summary(fact_checks)

        # Confidence: Kalibrierte Per-Claim-Confidences aus VerdictAgent verwenden.
        raw_confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.5))))

        claim_confidences: list[float] = [
            fc.confidence for fc in fact_checks if fc.confidence >= 0.0
        ]

        synth_cfg = self.config.synthesizer
        if claim_confidences:
            avg_claim_conf = sum(claim_confidences) / len(claim_confidences)
            min_claim_conf = min(claim_confidences)
            claim_aggregate = (
                avg_claim_conf * 0.7
                + min_claim_conf * 0.3
                + synth_cfg.claim_confidence_buffer * 0.5
            )
            w = synth_cfg.claim_confidence_blend_weight
            confidence = raw_confidence * (1 - w) + claim_aggregate * w
        else:
            confidence = raw_confidence

        # Ceiling: Bei nur 1 Fact-Check ohne starke Quellen
        if len(fact_checks) == 1 and not any(
            fc.verdict_meta and fc.verdict_meta.primary_sources_consulted
            for fc in fact_checks
        ):
            confidence = min(confidence, synth_cfg.extraordinary_claim_confidence_ceiling)

        confidence = min(1.0, max(0.0, confidence))

        return SynthesisResult(
            overall_rating=rating,
            confidence=confidence,
            summary=summary,
            claims_analysis=fact_checks,
            number_audits=number_audits,
            key_corrections=raw.get("key_corrections", []),
            manipulation_techniques=rhetoric.techniques if rhetoric else [],
            fairness_notes=raw.get("fairness_notes", []),
            sources=all_sources,
            image_analysis=image_analysis_result,
        )

    # ── Aggregationssignale ──────────────────────────────────────

    def _compute_aggregation_signals(
        self,
        fact_checks: list[FactCheckResult],
        rhetoric: RhetoricAnalysisResult | None,
        image_analysis: ImageAnalysisResult | None = None,
    ) -> AggregationSignals:
        """Berechnet abgeleitete Signale aus Fact-Checks und Rhetorik-Analyse."""
        signals = AggregationSignals()
        signals.n_claims = len(fact_checks)

        if fact_checks:
            refuted = sum(
                1 for fc in fact_checks
                if fc.rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE)
                and not fc.is_satire
            )
            unverified = sum(
                1 for fc in fact_checks
                if fc.rating == FactRating.UNVERIFIABLE
                and not fc.is_satire
            )
            signals.refuted_ratio = refuted / signals.n_claims
            signals.unverified_ratio = unverified / signals.n_claims

            calibrated = [fc.confidence for fc in fact_checks if fc.confidence >= 0.0]
            if calibrated:
                signals.avg_claim_confidence = sum(calibrated) / len(calibrated)

            signals.high_quality_evidence = any(
                fc.verdict_meta and fc.verdict_meta.primary_sources_consulted
                for fc in fact_checks
            )

        if rhetoric and rhetoric.techniques:
            weighted_sum = sum(
                _SEVERITY_WEIGHT.get(tech.severity.value, 1.0)
                for tech in rhetoric.techniques
            )
            signals.rhetoric_score = min(
                1.0, weighted_sum / self.config.synthesizer.rhetoric_norm_base
            )
            signals.n_high_rhetoric = sum(
                1 for tech in rhetoric.techniques
                if tech.severity == Severity.HIGH
            )

        if image_analysis:
            manipulation_count = sum(
                1 for item in image_analysis.items if item.manipulation_signs
            )
            if manipulation_count:
                signals.rhetoric_score = min(1.0, signals.rhetoric_score + manipulation_count * 1.0)

        return signals

    def _apply_rating_guardrails(
        self,
        llm_rating: OverallRating,
        signals: AggregationSignals,
        fact_checks: list[FactCheckResult] | None = None,
    ) -> OverallRating:
        """Regelbasierte Korrekturen am LLM-Vorschlag.

        Unterscheidet zwischen inhaltlicher Unsicherheit (unbelegt) und
        manipulativer Rhetorik (auch bei formal unbelegten Claims relevant).

        Keine Hardcoding einzelner Narrativtypen – nur signalbasierte Regeln.
        """
        rating = llm_rating
        cfg = self.config.synthesizer
        fact_checks = fact_checks or []

        # ── Regel 0: Evidenz-Konsistenz ──────────────────────────────
        # Wenn alle Fact-Checks TRUE/MOSTLY_TRUE sind, darf das Rating
        # nicht schlechter als MOSTLY_RELIABLE sein. Das LLM darf nicht
        # sein eigenes (möglicherweise veraltetes) Weltwissen über die
        # evidenzbasierten Pipeline-Ergebnisse stellen.
        if fact_checks:
            positive_ratings = {FactRating.TRUE, FactRating.MOSTLY_TRUE}
            all_positive = all(fc.rating in positive_ratings for fc in fact_checks)
            all_negative = all(
                fc.rating in (FactRating.FALSE, FactRating.MOSTLY_FALSE)
                for fc in fact_checks
            )

            if all_positive:
                # Cap: nie schlechter als MOSTLY_RELIABLE wenn alle Claims bestätigt
                max_allowed = OverallRating.MOSTLY_RELIABLE
                if _RATING_ORDER[rating] > _RATING_ORDER[max_allowed]:
                    rating = max_allowed

            elif all_negative and signals.n_claims > 0:
                # Floor: mindestens HIGHLY_MISLEADING wenn alle Claims widerlegt
                min_allowed = OverallRating.HIGHLY_MISLEADING
                if _RATING_ORDER[rating] < _RATING_ORDER[min_allowed]:
                    rating = min_allowed

        # ── Regel 1: FABRICATED nur bei ausreichend starker Evidenzbasis
        # → braucht: ≥ fabricated_min_refuted_ratio direkt widerlegte Claims UND Primärquellen
        if rating == OverallRating.FABRICATED:
            if (
                not signals.high_quality_evidence
                or signals.refuted_ratio < cfg.fabricated_min_refuted_ratio
            ):
                rating = OverallRating.HIGHLY_MISLEADING

        # ── Regel 2: Hohe Rhetorik-Manipulation + hoher Anteil unbelegter Claims
        # → mindestens MISLEADING, auch wenn LLM MIXED oder besser vergeben hat
        if (
            signals.rhetoric_score >= cfg.rhetoric_floor_misleading
            and signals.unverified_ratio >= cfg.misleading_unverified_min
            and signals.n_claims > 0
        ):
            if _RATING_ORDER[rating] < _RATING_ORDER[OverallRating.MISLEADING]:
                # But don't override Regel 0 — respect evidence consistency
                if not (fact_checks and all(
                    fc.rating in {FactRating.TRUE, FactRating.MOSTLY_TRUE}
                    for fc in fact_checks
                )):
                    rating = OverallRating.MISLEADING

        # ── Regel 3: Sehr starke Rhetorik + überwiegend unbelegt + kaum widerlegt
        # → Text ist stark irreführend auch ohne direkte Widerlegung
        if (
            signals.rhetoric_score >= cfg.rhetoric_floor_highly
            and signals.unverified_ratio >= cfg.highly_misleading_unverified_min
            and signals.refuted_ratio < cfg.highly_misleading_refuted_max
            and signals.n_claims > 0
        ):
            if _RATING_ORDER[rating] < _RATING_ORDER[OverallRating.HIGHLY_MISLEADING]:
                # But don't override Regel 0
                if not (fact_checks and all(
                    fc.rating in {FactRating.TRUE, FactRating.MOSTLY_TRUE}
                    for fc in fact_checks
                )):
                    rating = OverallRating.HIGHLY_MISLEADING

        return rating

    # ── Evidence-basierte Summary ─────────────────────────────────

    @staticmethod
    def _build_evidence_based_summary(fact_checks: list[FactCheckResult]) -> str:
        """Erzeugt eine Summary direkt aus den Fact-Check-Ergebnissen.

        Wird aufgerufen wenn der Guardrail das LLM-Rating korrigiert hat,
        da die LLM-Summary dann wahrscheinlich dem korrigierten Rating
        widerspricht (z.B. LLM sagt 'falsch' aber Evidenz sagt 'wahr').
        """
        parts: list[str] = []
        for fc in fact_checks:
            rating_label = {
                FactRating.TRUE: "bestätigt",
                FactRating.MOSTLY_TRUE: "größtenteils bestätigt",
            }.get(fc.rating, "geprüft")
            evidence_short = fc.evidence[:300] if fc.evidence else ""
            parts.append(
                f"Die Behauptung wurde durch die Quellenanalyse {rating_label}. "
                f"{evidence_short}"
            )
        return " ".join(parts).strip()

    # ── Hilfsmethoden ────────────────────────────────────────────

    @staticmethod
    def _format_signals_section(signals: AggregationSignals) -> str:
        """Formatiert die Aggregationssignale als lesbaren Abschnitt für das LLM."""
        if signals.n_claims == 0:
            return "## Aggregationssignale\n\nKeine Claims geprüft.\n"

        lines = [
            "## Aggregationssignale\n",
            f"- Claims geprüft: {signals.n_claims}",
            f"- Direkt widerlegte Claims (FALSE/MOSTLY_FALSE): {signals.refuted_ratio:.0%}",
            f"- Unbelegte Claims (UNVERIFIABLE): {signals.unverified_ratio:.0%}",
            f"- Ø Claim-Konfidenz: {signals.avg_claim_confidence:.0%}" if signals.avg_claim_confidence > 0 else "- Ø Claim-Konfidenz: nicht verfügbar",
            f"- Rhetorik-Manipulationsscore: {signals.rhetoric_score:.2f} / 1.00 ({signals.n_high_rhetoric} HIGH-Techniken)",
            f"- Primärquellen konsultiert: {'Ja' if signals.high_quality_evidence else 'Nein'}",
        ]
        return "\n".join(lines) + "\n"
