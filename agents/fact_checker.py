"""Fact Checker – Fassade über EvidenceBuilderAgent + CoVeProcessor + VerdictAgent.

Diese Klasse behält die bisherige öffentliche API für Abwärtskompatibilität:
    execute(claim, context) -> FactCheckResult

Intern delegiert sie an:
    1. EvidenceBuilderAgent  → EvidencePack
    2. CoVeProcessor         → CoVeTrace (optional)
    3. VerdictAgent          → FactCheckResult

Der Rückgabewert ist ein erweitertes FactCheckResult mit den neuen optionalen
Feldern evidence_pack, cove_trace, verdict_meta.

Bestehende Hilfsfunktionen (_build_search_queries, _optimize_queries_with_llm etc.)
bleiben erhalten, da sie von EvidenceBuilderAgent und ClaimProcessorAgent genutzt werden.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("fng.fact_checker")

from agents.base import BaseAgent
from models.schemas import Claim, FactCheckResult, FactRating

# ── Re-exports from agents.query_builder for backward compatibility ──────────
# All query-building functions and constants have been moved to agents/query_builder.py.
# They are re-exported here so that existing imports from agents.fact_checker continue to work.
from agents.query_builder import (  # noqa: F401
    SYSTEM_PROMPT,
    _ARTIFACT_TERMS,
    _QUERY_OPTIMIZER_PROMPT,
    _adaptive_max_results,
    _bind_number_to_context,
    _build_context_query,
    _build_enriched_context,
    _build_fallback_queries,
    _build_queries_for_underspecified_claim,
    _build_search_queries,
    _build_search_queries_from_profile,
    _categories_for_claim,
    _count_strong_anchors,
    _evaluate_scrape_quality,
    _is_current_state_claim,
    _optimize_queries_with_llm,
)


class FactCheckerAgent(BaseAgent):
    """Fassade über EvidenceBuilderAgent + CoVeProcessor + VerdictAgent.

    Öffentliche API unverändert: execute(claim) -> FactCheckResult.

    Intern:
        1. EvidenceBuilderAgent baut ein strukturiertes EvidencePack
        2. CoVeProcessor (wenn aktiviert) führt Chain-of-Verification durch
        3. VerdictAgent fällt das Urteil auf Basis des EvidencePack

    Trust Boundary:
        Rohe Webseiteninhalte verlassen den EvidenceBuilderAgent nie.
        Der VerdictAgent sieht nur das strukturierte EvidencePack.
    """

    name = "Fact Checker"
    emoji = "✅"

    def __init__(self, *args, **kwargs) -> None:
        llm_small = kwargs.pop("llm_small", None)
        super().__init__(*args, **kwargs)
        # Lazy-Imports um Zirkelimporte zu vermeiden
        from agents.evidence_builder import EvidenceBuilderAgent
        from agents.verdict_agent import VerdictAgent
        from agents.cove_processor import CoVeProcessor

        _llm_small = llm_small or self.llm
        # EvidenceBuilderAgent: Gemma 4B (reine Query-Optimierung, kein tiefes Reasoning)
        self._evidence_builder = EvidenceBuilderAgent(self.config, _llm_small, self.search)
        # VerdictAgent: bleibt auf 27B (Herzstück des Faktenchecks)
        self._verdict_agent = VerdictAgent(self.config, self.llm, self.search, self.cache)
        # CoVeProcessor: llm_small für Fragen/Antworten (RAG), llm für Baseline/Reconciliation
        self._cove_processor = CoVeProcessor(
            llm=self.llm,
            llm_small=_llm_small,
            config=self.config.cove,
        )

    def _check_cache(self, claim: Claim, context: str) -> FactCheckResult | None:
        """Cache-Lookup mit Kontext für kollisionsfreie Keys."""
        cached = self._cache_get(claim.text, context)
        if cached is not None:
            try:
                return FactCheckResult(**cached)
            except Exception:
                pass
        return None

    def execute(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Synchrone Fact-Check-Pipeline: EvidenceBuilder → CoVe → VerdictAgent.

        input_data kann ein Claim oder ein dict sein:
        - Claim: Normale Pipeline (EvidenceBuilder → CoVe → Verdict)
        - dict mit "claim" + "evidence_pack": EvidenceBuilder wird übersprungen
          (Commander hat EvidencePack bereits vorbereitet)
        """
        # ── Commander-Override: EvidencePack bereits vorhanden ────────────────
        pack = None
        if isinstance(input_data, dict) and "evidence_pack" in input_data:
            claim = input_data["claim"]
            pack = input_data["evidence_pack"]
            self._log(f"EvidencePack-Override: {len(pack.web_results)} Items vom Commander")
        else:
            claim = input_data

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        # ── 1. Evidence Builder (nur wenn kein Override) ─────────────────────
        if pack is None:
            pack, pack_error = self._evidence_builder.run_safe(claim, context=context)
            if pack_error or pack is None:
                self._log(f"EvidenceBuilder fehlgeschlagen: {pack_error}")
                return self._unverifiable_fallback(claim, pack_error or "EvidenceBuilder fehlgeschlagen")

        # ── 2. CoVe (optional) ────────────────────────────────────────────────
        cove_trace = None
        if self.config.cove.enabled:
            try:
                cove_trace = self._cove_processor.process(claim, pack)
            except Exception as e:
                self._log(f"CoVe fehlgeschlagen: {type(e).__name__}: {e}")

        # ── 3. VerdictAgent ───────────────────────────────────────────────────
        # topic_model vom EvidenceBuilder durchreichen (für kontextbewusste Urteilsfindung)
        _tm = getattr(self._evidence_builder, "topic_model", None)
        result, verdict_error = self._verdict_agent.run_safe(
            {"claim": claim, "evidence_pack": pack, "cove_trace": cove_trace, "topic_model": _tm},
            context=context,
        )
        if verdict_error or result is None:
            self._log(f"VerdictAgent fehlgeschlagen: {verdict_error}")
            return self._unverifiable_fallback(claim, verdict_error or "VerdictAgent fehlgeschlagen")

        self._cache_set(claim.text, result.model_dump(exclude={"evidence_pack", "cove_trace", "verdict_meta"}), context)
        return result

    async def execute_async(self, input_data: Any, context: str = "") -> FactCheckResult:
        """Async Fact-Check-Pipeline."""
        import asyncio

        # ── Commander-Override: EvidencePack bereits vorhanden ────────────────
        pack = None
        if isinstance(input_data, dict) and "evidence_pack" in input_data:
            claim = input_data["claim"]
            pack = input_data["evidence_pack"]
            self._log(f"EvidencePack-Override (async): {len(pack.web_results)} Items vom Commander")
        else:
            claim = input_data

        cached = self._check_cache(claim, context)
        if cached is not None:
            return cached

        # ── 1. Evidence Builder (async, nur wenn kein Override) ──────────────
        if pack is None:
            try:
                pack = await self._evidence_builder.execute_async(claim, context=context)
            except Exception as e:
                self._log(f"EvidenceBuilder async fehlgeschlagen: {type(e).__name__}: {e}")
                return self._unverifiable_fallback(claim, f"EvidenceBuilder: {e}")

        # ── 2. CoVe im Thread-Pool (blockiert nicht den Event Loop) ──────────
        cove_trace = None
        if self.config.cove.enabled:
            try:
                loop = asyncio.get_event_loop()
                cove_trace = await loop.run_in_executor(
                    None, self._cove_processor.process, claim, pack
                )
            except Exception as e:
                self._log(f"CoVe fehlgeschlagen: {type(e).__name__}: {e}")

        # ── 3. VerdictAgent im Thread-Pool ────────────────────────────────────
        _tm = getattr(self._evidence_builder, "topic_model", None)
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._verdict_agent.execute,
                {"claim": claim, "evidence_pack": pack, "cove_trace": cove_trace, "topic_model": _tm},
                context,
            )
        except Exception as e:
            self._log(f"VerdictAgent async fehlgeschlagen: {type(e).__name__}: {e}")
            return self._unverifiable_fallback(claim, f"VerdictAgent: {e}")

        self._cache_set(claim.text, result.model_dump(exclude={"evidence_pack", "cove_trace", "verdict_meta"}), context)
        return result

    # ── Fallback ──────────────────────────────────────────────────────────────

    @staticmethod
    def _unverifiable_fallback(claim: Claim, reason: str) -> FactCheckResult:
        """Erzeuge ein UNVERIFIABLE-Ergebnis bei Pipeline-Fehler."""
        return FactCheckResult(
            claim_id=claim.id,
            rating=FactRating.UNVERIFIABLE,
            evidence=f"Automatische Prüfung fehlgeschlagen: {reason}",
            sources=[],
        )
