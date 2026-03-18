"""Orchestrator – Zentrale Steuerung des Multi-Agent-Workflows."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from config import AppConfig
from models.schemas import (
    Claim,
    ClaimType,
    FactCheckResult,
    NumberAuditResult,
    OverallRating,
    RhetoricAnalysisResult,
    SynthesisResult,
)
from agents.claim_extractor import ClaimExtractorAgent
from agents.fact_checker import FactCheckerAgent
from agents.number_auditor import NumberAuditorAgent
from agents.rhetoric_analyzer import RhetoricAnalyzerAgent
from agents.synthesizer import SynthesizerAgent
from tools.cache import ClaimCache
from tools.llm import LLMClient
from tools.web_search import WebSearchClient


class Orchestrator:
    """Steuert den gesamten Analyse-Workflow.

    Ablauf:
        1. Claim Extractor zerlegt den Text
        2. Für jeden Claim: Routing an zuständige Agenten
        3. Rhetoric Analyzer bewertet den Gesamttext
        4. Synthesizer erstellt das Gesamtverdikt
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config

        # API Keys beim Start prüfen
        config.validate()

        # Zwei LLM-Clients: schnelles Modell für Extraktion/Fact-Check,
        # mächtiges Modell für Zahlen/Rhetorik/Synthese
        from dataclasses import replace
        llm_fast = LLMClient(
            replace(config.llm, model="google/gemma-3-27b-it"),
            config.retry,
        )
        llm_powerful = LLMClient(config.llm, config.retry)

        search = WebSearchClient(config.search, config.retry)

        # Claim-Cache (optional, deaktivierbar über config.cache.enabled)
        cache = ClaimCache(config.cache)

        # Agenten initialisieren – gezielt verschiedene Modelle zuweisen
        self.claim_extractor = ClaimExtractorAgent(config, llm_fast, search)
        self.fact_checker = FactCheckerAgent(config, llm_fast, search, cache)
        self.number_auditor = NumberAuditorAgent(config, llm_powerful, search, cache)
        self.rhetoric_analyzer = RhetoricAnalyzerAgent(config, llm_powerful, search)
        self.synthesizer = SynthesizerAgent(config, llm_powerful, search)

    def analyze(self, text: str) -> SynthesisResult:
        """Analysiere einen Text vollständig.

        Args:
            text: Der zu prüfende Text (Tweet, Rede, Artikel, etc.)

        Returns:
            SynthesisResult mit Gesamtbewertung.
        """
        self._log("=" * 60)
        self._log("FAKTENCHECK GESTARTET")
        self._log("=" * 60)

        analysis_errors: list[str] = []

        # ── Phase 1: Claims extrahieren ──────────────────────────
        self._log("\n📋 PHASE 1: Claims extrahieren")
        extraction = self.claim_extractor.run(text)  # Pflicht – kein safe hier

        if not extraction.claims:
            self._log("Keine prüfbaren Claims gefunden.")
            return SynthesisResult(
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary="Es wurden keine überprüfbaren Tatsachenbehauptungen gefunden.",
                sources=[],
            )

        # Claims anzeigen
        for claim in extraction.claims:
            self._log(f"  {claim.id} [{claim.type.value}]: {claim.text}")

        # ── Phase 2: Claims an Agenten routen ────────────────────
        self._log("\n🔄 PHASE 2: Claims prüfen")

        fact_checks: list[FactCheckResult] = []
        number_audits: list[NumberAuditResult] = []

        for claim in extraction.claims:
            if claim.type == ClaimType.OPINION:
                self._log(f"  ⏭ {claim.id}: Meinung – übersprungen")
                continue

            # Fact Check – Graceful Degradation
            # Originaltext als context mitgeben, damit Suchqueries thematisch
            # angereichert werden und das LLM den Gesamtzusammenhang kennt
            self._log(f"\n  ── Fact-Check für {claim.id} ──")
            fc_result, fc_error = self.fact_checker.run_safe(claim, context=text)
            if fc_error:
                self._log(f"  ⚠ Fact-Check fehlgeschlagen: {fc_error}")
                analysis_errors.append(fc_error)
            elif fc_result is not None:
                fact_checks.append(fc_result)

            # Number Audit (für statistische Claims) – Graceful Degradation
            if "number_auditor" in claim.requires_agents or claim.type == ClaimType.STATISTICAL:
                self._log(f"  ── Number-Audit für {claim.id} ──")
                fc_context = ""
                if fc_result is not None:
                    fc_context = f"Fact-Check Ergebnis: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                na_result, na_error = self.number_auditor.run_safe(claim, context=fc_context)
                if na_error:
                    self._log(f"  ⚠ Number-Audit fehlgeschlagen: {na_error}")
                    analysis_errors.append(na_error)
                elif na_result is not None:
                    number_audits.append(na_result)

        # ── Phase 3: Rhetoric-Analyse des Gesamttexts ────────────
        self._log("\n🎭 PHASE 3: Rhetoric-Analyse")
        rhetoric_result, rhetoric_error = self.rhetoric_analyzer.run_safe(text)
        if rhetoric_error:
            self._log(f"  ⚠ Rhetoric-Analyse fehlgeschlagen: {rhetoric_error}")
            analysis_errors.append(rhetoric_error)

        # ── Phase 4: Synthese ────────────────────────────────────
        self._log("\n📊 PHASE 4: Synthese")
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
        }
        result = self.synthesizer.run(synthesis_input)

        # Fehler in Ergebnis eintragen
        if analysis_errors:
            result.analysis_errors.extend(analysis_errors)

        self._log("\n" + "=" * 60)
        self._log("FAKTENCHECK ABGESCHLOSSEN")
        self._log("=" * 60)

        return result

    async def analyze_async(self, text: str) -> SynthesisResult:
        """Async-Version von analyze() – Phase 2 läuft parallel.

        Claim Extraction (Phase 1) und Synthese (Phase 4) laufen weiterhin
        sequenziell, da sie keine Web-I/O auf Claim-Ebene benötigen.
        Phase 2 (Fact-Check + Number-Audit) und Phase 3 (Rhetoric) laufen
        gleichzeitig mit asyncio.gather.
        """
        self._log("=" * 60)
        self._log("FAKTENCHECK GESTARTET (async)")
        self._log("=" * 60)

        analysis_errors: list[str] = []

        # ── Phase 1: Claims extrahieren (sync, kein Netz) ─────────
        self._log("\n📋 PHASE 1: Claims extrahieren")
        extraction = self.claim_extractor.run(text)

        if not extraction.claims:
            self._log("Keine prüfbaren Claims gefunden.")
            return SynthesisResult(
                overall_rating=OverallRating.RELIABLE,
                confidence=0.3,
                summary="Es wurden keine überprüfbaren Tatsachenbehauptungen gefunden.",
                sources=[],
            )

        for claim in extraction.claims:
            self._log(f"  {claim.id} [{claim.type.value}]: {claim.text}")

        # ── Phase 2 + 3: Parallel ─────────────────────────────────
        self._log("\n🔄 PHASE 2+3: Claims prüfen + Rhetoric (parallel)")

        checkable = [c for c in extraction.claims if c.type != ClaimType.OPINION]
        opinion_ids = [c.id for c in extraction.claims if c.type == ClaimType.OPINION]
        for oid in opinion_ids:
            self._log(f"  ⏭ {oid}: Meinung – übersprungen")

        async def check_claim(claim: Claim) -> tuple[FactCheckResult | None, NumberAuditResult | None, list[str]]:
            errors: list[str] = []
            # Originaltext als context mitgeben für kontextualisierte Suche
            fc_result, fc_error = await self.fact_checker.run_safe_async(claim, context=text)
            if fc_error:
                self._log(f"  ⚠ Fact-Check fehlgeschlagen: {fc_error}")
                errors.append(fc_error)

            na_result: NumberAuditResult | None = None
            if "number_auditor" in claim.requires_agents or claim.type == ClaimType.STATISTICAL:
                fc_context = ""
                if fc_result is not None:
                    fc_context = f"Fact-Check Ergebnis: {fc_result.rating.value}\nEvidenz: {fc_result.evidence}"
                na_result, na_error = await self.number_auditor.run_safe_async(claim, context=fc_context)
                if na_error:
                    self._log(f"  ⚠ Number-Audit fehlgeschlagen: {na_error}")
                    errors.append(na_error)
            return fc_result, na_result, errors

        # Phase 2 (alle Claims) + Phase 3 (Rhetoric) gleichzeitig
        tasks = [check_claim(c) for c in checkable]
        tasks.append(self.rhetoric_analyzer.run_safe_async(text))  # type: ignore[arg-type]

        raw_results = await asyncio.gather(*tasks, return_exceptions=False)

        # Ergebnisse auswerten
        fact_checks: list[FactCheckResult] = []
        number_audits: list[NumberAuditResult] = []
        rhetoric_result = None

        for i, claim_result in enumerate(raw_results[:-1]):
            fc, na, errs = claim_result  # type: ignore[misc]
            if fc is not None:
                fact_checks.append(fc)
            if na is not None:
                number_audits.append(na)
            analysis_errors.extend(errs)

        rhetoric_result, rhetoric_error = raw_results[-1]  # type: ignore[misc]
        if rhetoric_error:
            self._log(f"  ⚠ Rhetoric-Analyse fehlgeschlagen: {rhetoric_error}")
            analysis_errors.append(rhetoric_error)

        # ── Phase 4: Synthese ─────────────────────────────────────
        self._log("\n📊 PHASE 4: Synthese")
        synthesis_input = {
            "original_text": text,
            "fact_checks": fact_checks,
            "number_audits": number_audits,
            "rhetoric": rhetoric_result,
        }
        result = self.synthesizer.run(synthesis_input)

        if analysis_errors:
            result.analysis_errors.extend(analysis_errors)

        self._log("\n" + "=" * 60)
        self._log("FAKTENCHECK ABGESCHLOSSEN")
        self._log("=" * 60)

        return result

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message, file=sys.stderr)
