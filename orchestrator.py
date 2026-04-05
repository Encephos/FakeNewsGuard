"""Orchestrator – Zentrale Steuerung des Multi-Agent-Workflows.

Neuer Ablauf (v2):
    1. Input validieren
    2. Claim Processing Pipeline (6 Stufen: Split → Canonicalize → Prioritize)
    3. Top-N Claims nach Priorität auswählen (konfigurierbar)
    4. Für jeden Claim (parallel in async):
       a. EvidenceBuilderAgent → EvidencePack
       b. CoVeProcessor → CoVeTrace (optional)
       c. VerdictAgent → FactCheckResult
       d. NumberAuditor (bei STATISTICAL)
    5. Parallel: RhetoricAnalyzer (Gesamttext)
    6. Optional: ImageAnalyzer
    7. Synthesizer → SynthesisResult

Abwärtskompatibilität:
    analyze(text) -> SynthesisResult  (unverändert)
    analyze_async(text) -> SynthesisResult  (unverändert)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Any, Callable

from config import AppConfig, ScoutTier
from i18n import set_default_locale, t
from models.schemas import (
    Claim,
    ClaimProcessingResult,
    ClaimType,
    FactCheckResult,
    ImageAnalysisResult,
    NumberAuditResult,
    OverallRating,
    RhetoricAnalysisResult,
    SynthesisResult,
)
from agents.claim_extractor import ClaimExtractorAgent
from agents.commander import CommanderAgent
from agents.fact_checker import FactCheckerAgent
from agents.image_analyzer import ImageAnalyzerAgent
from agents.number_auditor import NumberAuditorAgent
from agents.rhetoric_analyzer import RhetoricAnalyzerAgent
from agents.synthesizer import SynthesizerAgent
from tools.db.factory import create_cache
from tools.claim_router import ClaimRouter
from tools.llm import LLMClient
from tools.web_search import WebSearchClient


class InputValidationError(ValueError):
    """Wird geworfen wenn der Input die Validierung nicht besteht."""
    pass


def _format_image_analysis(result: ImageAnalysisResult) -> str:
    """Konvertiert ImageAnalysisResult in lesbaren Text-Block fuer LLM-Kontext."""
    parts: list[str] = []
    for item in result.items:
        idx = item.image_index + 1
        parts.append(f"### Bild {idx}")
        if item.ocr_text:
            parts.append(f"**Sichtbarer Text:** {item.ocr_text}")
        if item.visible_elements:
            parts.append(f"**Erkannte Elemente:** {', '.join(item.visible_elements)}")
        if item.manipulation_signs:
            parts.append(f"**Manipulationsanzeichen:** {', '.join(item.manipulation_signs)}")
        if item.emotional_framing:
            parts.append(f"**Emotionales Framing:** {item.emotional_framing}")
        if item.infographic_data:
            parts.append(f"**Infografik-Daten:** {item.infographic_data}")
        if item.context_clues:
            parts.append(f"**Kontexthinweise:** {', '.join(item.context_clues)}")
        parts.append("")
    if result.cross_image_observations:
        parts.append(f"**Zusammenspiel der Bilder:** {result.cross_image_observations}")
    if result.overall_assessment:
        parts.append(f"**Gesamteinschaetzung:** {result.overall_assessment}")
    return "\n".join(parts).strip()


def _should_run_number_auditor(claim: "Claim") -> bool:
    """Entscheide ob der NumberAuditor für diesen Claim ausgeführt werden soll.

    Regelungsclaims (Sanktion, Enforcement oder Policy+Institution im Frame)
    werden nicht als rein statistische Claims behandelt, auch wenn sie Zahlen
    enthalten. Zahlen wie "250 Euro Bußgeld" sind hier normative/politische
    Angaben, keine statistischen Messwerte.

    Explizite ``number_auditor``-Anforderung im requires_agents-Feld hat immer
    Vorrang und überschreibt die Regelungsclaim-Erkennung.

    Args:
        claim: Der zu prüfende Claim (Claim oder ProcessedClaim).

    Returns:
        True wenn der NumberAuditor ausgeführt werden soll.
    """
    from models.schemas import ProcessedClaim

    # Explizit angefordert → immer ausführen
    if "number_auditor" in claim.requires_agents:
        return True

    # Kein statistischer Claim → kein NumberAuditor
    if claim.type != ClaimType.STATISTICAL:
        return False

    # Für STATISTICAL-Claims: Regelungsclaim-Check via Frame
    if isinstance(claim, ProcessedClaim) and claim.frame:
        f = claim.frame
        is_regulatory = bool(
            f.sanction
            or f.enforcement
            or (f.policy_context and f.institution)
        )
        if is_regulatory:
            return False  # Regelungsclaim – NumberAuditor nicht geeignet

    return True


class Orchestrator:
    """Steuert den gesamten Analyse-Workflow.

    Ablauf v2:
        1. Claim Processing Pipeline (mehrstufig, mit Priorisierung)
        2. Top-N Claims auswählen
        3. Für jeden Claim: EvidenceBuilder → CoVe → VerdictAgent + ggf. NumberAuditor
        4. RhetoricAnalyzer parallel
        5. Synthesizer
    """

    def __init__(
        self,
        config: AppConfig,
        on_step: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self._on_step = on_step  # Callback für Step-Updates (z.B. API Progress)

        set_default_locale(config.language)
        config.validate()

        from dataclasses import replace

        tier = config.tier
        tier_labels = {
            ScoutTier.LITE: "Scout Lite (Free Tier Router)",
            ScoutTier.PRO: "Scout Pro (Gemma)",
            ScoutTier.MAX: "Scout Max (Gemma + Qwen)",
        }
        self._log(f"🔎 Tier: {tier_labels[tier]}")

        tm = config.tier_models

        if tier == ScoutTier.LITE:
            llm_fast = LLMClient(replace(config.llm, model=tm.model_free), config.retry)
            llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
            llm_powerful = llm_fast
        elif tier == ScoutTier.PRO:
            llm_fast = LLMClient(replace(config.llm, model=tm.model_medium), config.retry)
            llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
            llm_powerful = llm_fast
        else:
            llm_fast = LLMClient(replace(config.llm, model=tm.model_medium), config.retry)
            llm_small = LLMClient(replace(config.llm, model=tm.model_small), config.retry)
            llm_powerful = LLMClient(config.llm, config.retry)

        search = WebSearchClient(config.search, config.retry)
        cache = create_cache(config)

        self.image_analyzer = ImageAnalyzerAgent(config, llm_fast, search)
        self.claim_extractor = ClaimExtractorAgent(config, llm_fast, search, llm_small=llm_small)
        self.fact_checker = FactCheckerAgent(config, llm_fast, search, cache, llm_small=llm_small)
        self.number_auditor = NumberAuditorAgent(config, llm_powerful, search, cache)
        self.rhetoric_analyzer = RhetoricAnalyzerAgent(config, llm_powerful, search)
        self.synthesizer = SynthesizerAgent(config, llm_powerful, search)
        self._router = ClaimRouter()

        # ── Commander (nur PRO/MAX) ──────────────────────────────────────────
        if tier == ScoutTier.LITE or not config.commander.enabled:
            self.commander: CommanderAgent | None = None
        elif tier == ScoutTier.PRO:
            self.commander = CommanderAgent(config, llm_fast, search)
            self._log("  🎖 Commander Pro aktiv")
        else:  # MAX
            self.commander = CommanderAgent(config, llm_powerful, search)
            self._log("  🎖 Commander Max aktiv")

    # ── Input Validation ──────────────────────────────────────────────────────

    def _validate_input(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise InputValidationError("Kein Text zur Analyse angegeben.")
        if len(text) > self.config.max_input_chars:
            self._log(f"Input gekürzt: {len(text)} → {self.config.max_input_chars} Zeichen")
            text = text[: self.config.max_input_chars]
        return text

    # ── Top-N Claim Auswahl ───────────────────────────────────────────────────

    def _select_top_claims(self, result: ClaimProcessingResult) -> list[Claim]:
        """Wähle die Top-N Claims nach Priorität aus.

        - Ungültige Claims (is_valid_claim=False) werden herausgefiltert
        - Meinungen werden immer herausgefiltert
        - Claims mit is_checkworthy=False werden herausgefiltert
        - Wenn top_n=0: alle verbleibenden Claims zurückgeben
        - Sonst: die N Claims mit höchstem priority_score
        """
        checkable = [
            c for c in result.claims
            if c.type != ClaimType.OPINION and c.is_checkworthy and c.is_valid_claim
        ]

        for c in result.claims:
            if not c.is_valid_claim:
                self._log(f"  ⏭ {c.id}: Ungültiger Claim ({c.invalid_reason}) – übersprungen")
            elif c.type == ClaimType.OPINION:
                self._log(f"  ⏭ {c.id}: Meinung – übersprungen")
            elif not c.is_checkworthy:
                self._log(f"  ⏭ {c.id}: Nicht prüfenswert – übersprungen")

        # ── Deduplizierung via canonical_hash ──────────────────────────────
        seen_hashes: set[str] = set()
        deduped: list[Claim] = []
        for c in checkable:
            h = getattr(c, "canonical_hash", "") or ""
            if h and h in seen_hashes:
                self._log(f"  ⏭ {c.id}: Duplikat (canonical_hash) – übersprungen")
                continue
            if h:
                seen_hashes.add(h)
            deduped.append(c)
        checkable = deduped

        top_n = self.config.claim_processing.top_n
        if top_n > 0 and len(checkable) > top_n:
            # Sortiere nach priority_score (höchste zuerst)
            checkable.sort(key=lambda c: -c.priority_score)
            skipped = checkable[top_n:]
            checkable = checkable[:top_n]
            for c in skipped:
                self._log(f"  ⏭ {c.id}: Top-N Limit ({top_n}) erreicht – übersprungen")

        return checkable

    # ── Step Callbacks ────────────────────────────────────────────────────────

    def _step(self, phase: str, message: str) -> None:
        """Sendet einen Step-Update an den optionalen on_step Callback."""
        self._log(message)
        if self._on_step:
            try:
                self._on_step(phase, message)
            except Exception:
                pass

    # ── Synchrone Analyse ─────────────────────────────────────────────────────

    def analyze(self, text: str, image_urls: list[str] | None = None) -> SynthesisResult:
        """Analysiere einen Text vollständig (synchron).

        Args:
            text: Der zu prüfende Text.
            image_urls: Optionale Liste von Bild-URLs für die Bildanalyse.

        Returns:
            SynthesisResult mit Gesamtbewertung.

        Raises:
            InputValidationError: Bei leerem Input.
        """
        text = self._validate_input(text)
        self._analysis_id = uuid.uuid4().hex[:12]
        from tools.cost_tracker import reset_accumulator
        reset_accumulator()
        self._log("=" * 60)
        self._log("FAKTENCHECK GESTARTET")
        self._log("=" * 60)

        analysis_errors: list[str] = []

        # ── Phase 1: Claim Processing ─────────────────────────────────────────
        self._step("claim_processing", "\n📋 PHASE 1: Claim Processing")
        extraction, extraction_error = self.claim_extractor.run_safe(text)
        if extraction_error:
            self._log(f"  ⚠ Claim-Processing fehlgeschlagen: {extraction_error}")
            return SynthesisResult(
                analysis_id=self._analysis_id,
                overall_rating=OverallRating.MIXED,
                confidence=0.0,
                summary="Die Analyse konnte nicht durchgeführt werden: "
                        "Behauptungen konnten nicht aus dem Text extrahiert werden.",
                sources=[],
                analysis_errors=[extraction_error],
            )

        if not extraction.claims:
            self._log("Keine prüfbaren Claims gefunden.")
            return SynthesisResult(
                analysis_id=self._analysis_id,
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary="Es wurden keine überprüfbaren Tatsachenbehauptungen gefunden.",
                sources=[],
            )

        # Topic Model aus Phase 1 extrahieren
        topic_model = getattr(extraction, "topic_model", None)
        if topic_model:
            self._log(f"  🎯 Topic: {topic_model.primary_topic} [{topic_model.domain}]")

        for claim in extraction.claims:
            self._log(f"  {claim.id} [{claim.type.value}] (prio={claim.priority_score:.2f}): {claim.text}")

        # ── Top-N Auswahl ────────────────────────────────────────────────────
        checkable = self._select_top_claims(extraction)

        # Topic Model am EvidenceBuilder setzen (für topic-aware Retrieval)
        if topic_model and hasattr(self.fact_checker, "_evidence_builder"):
            self.fact_checker._evidence_builder.topic_model = topic_model

        # ── Phase 1.5: Commander (PRO/MAX) ────────────────────────────────────
        # Pre-Routing: route_confidence für Difficulty-Berechnung annotieren.
        # ClaimRouter cached intern – kein Mehraufwand bei späterer Routing-Phase.
        if self.commander:
            for claim in checkable:
                route_result, _ = self._router.route_and_apply(claim)
                if hasattr(claim, "route_confidence"):
                    claim.route_confidence = route_result.confidence

        commander_packs: dict[str, Any] = {}
        if self.commander:
            self._step("commander", "\n🎖 PHASE 1.5: Commander – Iterative Suchverfeinerung")
            cmd_result, cmd_error = self.commander.run_safe(
                {
                    "claims": checkable,
                    "article_text": text,
                    "evidence_builder": self.fact_checker._evidence_builder,
                },
                context=text,
            )
            if cmd_error:
                self._log(f"  ⚠ Commander fehlgeschlagen, Fallback auf Standard-Pipeline: {cmd_error}")
            elif cmd_result is not None:
                commander_packs = cmd_result.evidence_packs
                self._log(
                    f"  ✓ Commander: {cmd_result.rounds_completed} Runden, "
                    f"{cmd_result.total_prompts_used} Prompts, "
                    f"{len(commander_packs)} EvidencePacks"
                )

        # ── Phase 2: Claims prüfen ────────────────────────────────────────────
        self._step("fact_checking", f"\n🔄 PHASE 2: {len(checkable)} Claims prüfen")

        fact_checks: list[FactCheckResult] = []
        number_audits: list[NumberAuditResult] = []

        for claim in checkable:
            self._step("fact_checking", f"\n  ── Fact-Check für {claim.id} ──")
            route_result, routed_claim = self._router.route_and_apply(claim)
            self._log(f"  🗺 Route: {route_result.rationale}")

            # Commander hat EvidencePack vorbereitet → skip EvidenceBuilder
            if claim.id in commander_packs:
                fc_input: Any = {"claim": routed_claim, "evidence_pack": commander_packs[claim.id]}
                fc_result, fc_error = self.fact_checker.run_safe(fc_input, context=text)
            else:
                fc_result, fc_error = self.fact_checker.run_safe(routed_claim, context=text)

            if fc_error:
                self._log(f"  ⚠ Fact-Check fehlgeschlagen: {fc_error}")
                analysis_errors.append(fc_error)
            elif fc_result is not None:
                fact_checks.append(fc_result)

            if _should_run_number_auditor(routed_claim):
                self._step("number_audit", f"  ── Number-Audit für {claim.id} ──")
                fc_context = ""
                if fc_result is not None:
                    fc_context = f"Fact-Check: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                na_result, na_error = self.number_auditor.run_safe(
                    {"claim": routed_claim, "route_result": route_result},
                    context=fc_context,
                )
                if na_error:
                    self._log(f"  ⚠ Number-Audit fehlgeschlagen: {na_error}")
                    analysis_errors.append(na_error)
                elif na_result is not None:
                    number_audits.append(na_result)

        # ── Phase 3: Rhetoric-Analyse ─────────────────────────────────────────
        self._step("rhetoric", "\n🎭 PHASE 3: Rhetoric-Analyse")
        rhetoric_result, rhetoric_error = self.rhetoric_analyzer.run_safe(text)
        if rhetoric_error:
            self._log(f"  ⚠ Rhetoric-Analyse fehlgeschlagen: {rhetoric_error}")
            analysis_errors.append(rhetoric_error)

        # ── Phase 3.5: Bildanalyse (optional) ────────────────────────────────
        image_analysis_result: ImageAnalysisResult | None = None
        if image_urls:
            self._step("image_analysis", "\n🖼 PHASE 3.5: Bildanalyse")
            img_res, img_err = self.image_analyzer.run_safe(
                {"image_urls": image_urls, "post_text": text[:500]}
            )
            if img_err:
                self._log(f"  ⚠ Bildanalyse fehlgeschlagen: {img_err}")
                analysis_errors.append(img_err)
            else:
                image_analysis_result = img_res

        # ── Phase 4: Synthese ─────────────────────────────────────────────────
        self._step("synthesis", "\n📊 PHASE 4: Synthese")
        synthesis_input: dict[str, Any] = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
        }
        if image_analysis_result is not None:
            synthesis_input["image_analysis"] = _format_image_analysis(image_analysis_result)
            synthesis_input["image_analysis_result"] = image_analysis_result

        result = self.synthesizer.run(synthesis_input)
        result.analysis_id = self._analysis_id
        if analysis_errors:
            result.analysis_errors.extend(analysis_errors)

        from tools.cost_tracker import collect_summary
        result.cost_summary = collect_summary(self.config.pricing)

        self._log("\n" + "=" * 60)
        self._log("FAKTENCHECK ABGESCHLOSSEN")
        self._log("=" * 60)
        return result

    # ── Asynchrone Analyse ────────────────────────────────────────────────────

    async def analyze_async(self, text: str, image_urls: list[str] | None = None) -> SynthesisResult:
        """Async-Version von analyze() – Phase 2+3 (+optional Bildanalyse) laufen parallel.

        Claim Processing (Phase 1) und Synthese (Phase 4) laufen sequenziell.
        Phase 2 (alle Claims) + Phase 3 (Rhetoric) + Phase 3.5 (Bildanalyse) laufen parallel.

        Raises:
            InputValidationError: Bei leerem Input.
        """
        text = self._validate_input(text)
        self._analysis_id = uuid.uuid4().hex[:12]
        from tools.cost_tracker import reset_accumulator
        reset_accumulator()
        self._log("=" * 60)
        self._log("FAKTENCHECK GESTARTET (async)")
        self._log("=" * 60)

        analysis_errors: list[str] = []

        # ── Phase 1: Claim Processing ─────────────────────────────────────────
        self._step("claim_processing", "\n📋 PHASE 1: Claim Processing")
        extraction, extraction_error = self.claim_extractor.run_safe(text)
        if extraction_error:
            self._log(f"  ⚠ Claim-Processing fehlgeschlagen: {extraction_error}")
            return SynthesisResult(
                analysis_id=self._analysis_id,
                overall_rating=OverallRating.MIXED,
                confidence=0.0,
                summary="Die Analyse konnte nicht durchgeführt werden: "
                        "Behauptungen konnten nicht aus dem Text extrahiert werden.",
                sources=[],
                analysis_errors=[extraction_error],
            )

        if not extraction.claims:
            self._log("Keine prüfbaren Claims gefunden.")
            return SynthesisResult(
                analysis_id=self._analysis_id,
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary="Es wurden keine überprüfbaren Tatsachenbehauptungen gefunden.",
                sources=[],
            )

        # Topic Model aus Phase 1 extrahieren (async)
        topic_model_async = getattr(extraction, "topic_model", None)
        if topic_model_async:
            self._log(f"  🎯 Topic: {topic_model_async.primary_topic} [{topic_model_async.domain}]")

        for claim in extraction.claims:
            self._log(f"  {claim.id} [{claim.type.value}] (prio={claim.priority_score:.2f}): {claim.text}")

        checkable = self._select_top_claims(extraction)

        # Topic Model am EvidenceBuilder setzen (für topic-aware Retrieval)
        if topic_model_async and hasattr(self.fact_checker, "_evidence_builder"):
            self.fact_checker._evidence_builder.topic_model = topic_model_async

        # ── Phase 1.5: Commander (PRO/MAX) ────────────────────────────────────
        # Pre-Routing: route_confidence für Difficulty-Berechnung annotieren.
        # ClaimRouter cached intern – kein Mehraufwand bei späterer Routing-Phase.
        if self.commander:
            for claim in checkable:
                route_result, _ = self._router.route_and_apply(claim)
                if hasattr(claim, "route_confidence"):
                    claim.route_confidence = route_result.confidence

        commander_packs: dict[str, Any] = {}
        if self.commander:
            self._step("commander", "\n🎖 PHASE 1.5: Commander – Iterative Suchverfeinerung")
            cmd_result, cmd_error = await self.commander.run_safe_async(
                {
                    "claims": checkable,
                    "article_text": text,
                    "evidence_builder": self.fact_checker._evidence_builder,
                },
                context=text,
            )
            if cmd_error:
                self._log(f"  ⚠ Commander fehlgeschlagen, Fallback auf Standard-Pipeline: {cmd_error}")
            elif cmd_result is not None:
                commander_packs = cmd_result.evidence_packs
                self._log(
                    f"  ✓ Commander: {cmd_result.rounds_completed} Runden, "
                    f"{cmd_result.total_prompts_used} Prompts, "
                    f"{len(commander_packs)} EvidencePacks"
                )

        # ── Phase 2+3: Parallel ───────────────────────────────────────────────
        self._step("fact_checking", f"\n🔄 PHASE 2+3: {len(checkable)} Claims + Rhetoric (parallel)")

        async def check_claim(claim: Claim) -> tuple[FactCheckResult | None, NumberAuditResult | None, list[str]]:
            errors: list[str] = []
            self._step("fact_checking", f"  ── Fact-Check für {claim.id} ──")
            route_result, routed_claim = self._router.route_and_apply(claim)
            self._log(f"  🗺 Route: {route_result.rationale}")

            # Commander hat EvidencePack vorbereitet → skip EvidenceBuilder
            if claim.id in commander_packs:
                fc_input: Any = {"claim": routed_claim, "evidence_pack": commander_packs[claim.id]}
                fc_result, fc_error = await self.fact_checker.run_safe_async(fc_input, context=text)
            else:
                fc_result, fc_error = await self.fact_checker.run_safe_async(routed_claim, context=text)
            if fc_error:
                self._log(f"  ⚠ Fact-Check fehlgeschlagen: {fc_error}")
                errors.append(fc_error)

            na_result: NumberAuditResult | None = None
            if _should_run_number_auditor(routed_claim):
                self._step("number_audit", f"  ── Number-Audit für {claim.id} ──")
                fc_context = ""
                if fc_result is not None:
                    fc_context = f"Fact-Check: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                na_result, na_error = await self.number_auditor.run_safe_async(
                    {"claim": routed_claim, "route_result": route_result},
                    context=fc_context,
                )
                if na_error:
                    self._log(f"  ⚠ Number-Audit fehlgeschlagen: {na_error}")
                    errors.append(na_error)
            return fc_result, na_result, errors

        tasks = [check_claim(c) for c in checkable]
        tasks.append(self.rhetoric_analyzer.run_safe_async(text))  # type: ignore[arg-type]

        image_task_included = bool(image_urls)
        if image_task_included:
            tasks.append(  # type: ignore[arg-type]
                self.image_analyzer.run_safe_async(
                    {"image_urls": image_urls, "post_text": text[:500]}
                )
            )

        raw_results = await asyncio.gather(*tasks, return_exceptions=False)

        fact_checks: list[FactCheckResult] = []
        number_audits: list[NumberAuditResult] = []
        rhetoric_result = None
        n_claims = len(checkable)

        for claim_result in raw_results[:n_claims]:
            fc, na, errs = claim_result  # type: ignore[misc]
            if fc is not None:
                fact_checks.append(fc)
            if na is not None:
                number_audits.append(na)
            analysis_errors.extend(errs)

        rhetoric_result, rhetoric_error = raw_results[n_claims]  # type: ignore[misc]
        if rhetoric_error:
            self._log(f"  ⚠ Rhetoric-Analyse fehlgeschlagen: {rhetoric_error}")
            analysis_errors.append(rhetoric_error)

        image_analysis_result: ImageAnalysisResult | None = None
        if image_task_included:
            img_res, img_err = raw_results[n_claims + 1]  # type: ignore[misc]
            if img_err:
                self._log(f"  ⚠ Bildanalyse fehlgeschlagen: {img_err}")
                analysis_errors.append(img_err)
            else:
                image_analysis_result = img_res

        # ── Phase 4: Synthese ─────────────────────────────────────────────────
        self._step("synthesis", "\n📊 PHASE 4: Synthese")
        synthesis_input: dict[str, Any] = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
        }
        if image_analysis_result is not None:
            synthesis_input["image_analysis"] = _format_image_analysis(image_analysis_result)
            synthesis_input["image_analysis_result"] = image_analysis_result

        result = self.synthesizer.run(synthesis_input)
        result.analysis_id = self._analysis_id
        if analysis_errors:
            result.analysis_errors.extend(analysis_errors)

        from tools.cost_tracker import collect_summary
        result.cost_summary = collect_summary(self.config.pricing)

        self._log("\n" + "=" * 60)
        self._log("FAKTENCHECK ABGESCHLOSSEN")
        self._log("=" * 60)
        return result

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message, file=sys.stderr)
        if hasattr(self, "_analysis_id") and self._analysis_id:
            logging.getLogger("fng.orchestrator").debug(
                "[%s] %s", self._analysis_id, message,
            )
