"""CommanderAgent – LLM-gesteuerter Orchestrierungslayer für iterative Suchverfeinerung.

Commander sitzt zwischen Claim-Extraktion und Fact-Checking:
1. Generiert initiale Suchanfragen für ALLE Claims gleichzeitig (Prompt 1)
2. Delegiert Websuche an EvidenceBuilder
3. Evaluiert Evidenz-Suffizienz pro Claim (Prompt 2-4)
4. Generiert bei Bedarf neue Suchanfragen und wiederholt die Suche

Nur aktiv für PRO/MAX-Tier. LITE-Tier überspringt Commander komplett.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agents.base import BaseAgent
from agents.prompts.commander_prompts import (
    COMMANDER_INITIAL_QUERY_PROMPT,
    COMMANDER_SUFFICIENCY_REVIEW_PROMPT,
)
from config.commander import CommanderConfig
from models.commander_models import (
    CommanderClaimReview,
    CommanderResult,
    CommanderRoundLog,
)
from models.evidence_models import EvidencePack
from models.schemas import Claim, ProcessedClaim


class CommanderAgent(BaseAgent):
    """Iterativer Such-Orchestrator für Faktenprüfung."""

    name: str = "Commander"
    emoji: str = "🎖"

    def execute(self, input_data: Any, context: str = "") -> CommanderResult:
        """Sync-Wrapper – delegiert an execute_async."""
        import asyncio as _asyncio

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_asyncio.run, self.execute_async(input_data, context))
                return future.result()
        else:
            return _asyncio.run(self.execute_async(input_data, context))

    async def execute_async(self, input_data: Any, context: str = "") -> CommanderResult:
        """Haupt-Ablauf: Initiale Queries → Suche → Sufficiency-Loop."""
        claims: list[Claim] = input_data["claims"]
        article_text: str = input_data["article_text"]
        evidence_builder = input_data["evidence_builder"]

        cfg = self.config.commander
        prompt_count = 0
        round_logs: list[CommanderRoundLog] = []
        accumulated_packs: dict[str, EvidencePack] = {}
        all_queries_per_claim: dict[str, list[str]] = {c.id: [] for c in claims}

        # ── NEU: Per-Claim Difficulty + Budget ────────────────────────────────
        claim_budgets: dict[str, tuple[int, int]] = {}
        claim_difficulties: dict[str, float] = {}

        for claim in claims:
            rc = getattr(claim, "route_confidence", 0.50)
            diff = compute_claim_difficulty(claim, route_confidence=rc)
            claim_difficulties[claim.id] = diff
            if cfg.adaptive_budget:
                claim_budgets[claim.id] = difficulty_to_budget(diff, cfg)
            else:
                claim_budgets[claim.id] = (cfg.max_prompts_cap, cfg.max_total_queries_per_claim)

        overall_max_prompts = max(b[0] for b in claim_budgets.values())

        self._log(
            "Adaptive Budgets: "
            + ", ".join(
                f"{cid[:8]}→diff={claim_difficulties[cid]:.2f}/prompts={claim_budgets[cid][0]}"
                for cid in sorted(claim_budgets)
            )
        )

        # ── Prompt 1: Initiale Suchanfragen generieren ───────────────────────
        self._log("Prompt 1: Initiale Suchanfragen generieren")
        initial_queries = await self._generate_initial_queries(claims, article_text)
        prompt_count += 1

        total_queries = sum(len(qs) for qs in initial_queries.values())
        round_logs.append(CommanderRoundLog(
            round_number=1,
            prompt_type="initial",
            claims_evaluated=len(claims),
            new_queries_generated=total_queries,
        ))

        # Queries pro Claim begrenzen
        for claim_id, queries in initial_queries.items():
            limited = queries[:cfg.max_queries_per_claim_per_round]
            initial_queries[claim_id] = limited
            all_queries_per_claim.setdefault(claim_id, []).extend(limited)

        # ── Erste Suchrunde ──────────────────────────────────────────────────
        self._log(f"Suchrunde 1: {total_queries} Queries für {len(initial_queries)} Claims")
        accumulated_packs = await self._run_search_round(
            initial_queries, evidence_builder, claims,
        )

        # ── Sufficiency-Loop (Prompt 2 bis overall_max_prompts) ─────────────
        pending_claims = list(claims)  # Claims die noch nicht sufficient sind

        while prompt_count < overall_max_prompts and pending_claims:
            # Claims deren individuelles Budget bereits erschöpft ist, vorher entfernen
            budget_exhausted_ids: set[str] = set()
            for claim in pending_claims[:]:
                prompt_budget, _ = claim_budgets.get(claim.id, (cfg.max_prompts_cap, 12))
                if prompt_count >= prompt_budget:
                    budget_exhausted_ids.add(claim.id)
                    self._log(
                        f"  {claim.id[:8]} Budget erschöpft vor Review "
                        f"(difficulty={claim_difficulties.get(claim.id, '?')}, "
                        f"budget={prompt_budget}, used={prompt_count})"
                    )
                    pending_claims.remove(claim)

            if not pending_claims:
                break

            self._log(f"Prompt {prompt_count + 1}: Sufficiency-Review für {len(pending_claims)} Claims")

            review = await self._evaluate_sufficiency(
                pending_claims, accumulated_packs, article_text,
                all_queries_per_claim, prompt_count + 1,
            )
            prompt_count += 1

            # Ergebnisse auswerten
            sufficient_ids: set[str] = set()
            new_queries: dict[str, list[str]] = {}

            for claim_id, cr in review.items():
                if cr.sufficient:
                    sufficient_ids.add(claim_id)
                    self._log(f"  {claim_id}: ✓ Ausreichend")
                else:
                    # Alle Engine-Queries zusammenfassen
                    combined = []
                    for engine_queries in cr.new_queries.values():
                        combined.extend(engine_queries)
                    if combined:
                        # Begrenzen auf max_queries_per_claim_per_round
                        combined = combined[:cfg.max_queries_per_claim_per_round]
                        # Individuelles Query-Budget prüfen
                        _, query_budget = claim_budgets.get(claim_id, (cfg.max_prompts_cap, 12))
                        existing_count = len(all_queries_per_claim.get(claim_id, []))
                        remaining = query_budget - existing_count
                        combined = combined[:max(0, remaining)]
                        if combined:
                            new_queries[claim_id] = combined
                            all_queries_per_claim.setdefault(claim_id, []).extend(combined)
                    self._log(f"  {claim_id}: ✗ Unzureichend – {len(combined)} neue Queries")

            # Claims die jetzt sufficient sind, aus pending entfernen
            pending_claims = [c for c in pending_claims if c.id not in sufficient_ids]

            round_logs.append(CommanderRoundLog(
                round_number=prompt_count,
                prompt_type="review",
                claims_evaluated=len(review),
                claims_sufficient=len(sufficient_ids),
                claims_needing_more=len(new_queries),
                new_queries_generated=sum(len(qs) for qs in new_queries.values()),
                claims_budget_exhausted=len(budget_exhausted_ids),
            ))

            # Wenn keine neuen Queries oder keine pending Claims → fertig
            if not new_queries or not pending_claims:
                break

            # Min-Prompts-Check: Nach min_prompts darf die Loop enden
            if prompt_count >= cfg.min_prompts and not new_queries:
                break

            # ── Neue Suchrunde ───────────────────────────────────────────────
            self._log(f"Suchrunde {prompt_count}: {sum(len(q) for q in new_queries.values())} neue Queries")
            new_packs = await self._run_search_round(
                new_queries, evidence_builder, pending_claims,
            )

            # Neue Ergebnisse in bestehende Packs mergen
            for claim_id, new_pack in new_packs.items():
                if claim_id in accumulated_packs:
                    accumulated_packs[claim_id] = _merge_evidence_packs(
                        accumulated_packs[claim_id], new_pack,
                    )
                else:
                    accumulated_packs[claim_id] = new_pack

        self._log(
            f"Commander abgeschlossen: {prompt_count} Prompts, "
            f"{len(accumulated_packs)} EvidencePacks"
        )

        return CommanderResult(
            evidence_packs=accumulated_packs,
            rounds_completed=prompt_count,
            total_prompts_used=prompt_count,
            round_logs=round_logs,
            claim_difficulties=claim_difficulties,
            claim_budgets={cid: b[0] for cid, b in claim_budgets.items()},
        )

    # ── Interne Methoden ─────────────────────────────────────────────────────

    async def _generate_initial_queries(
        self, claims: list[Claim], article_text: str,
    ) -> dict[str, list[str]]:
        """Prompt 1: Generiere Suchanfragen für alle Claims."""
        user_message = self._build_initial_user_message(claims, article_text)
        raw = self._llm_json(COMMANDER_INITIAL_QUERY_PROMPT, user_message)

        # JSON parsen → dict[claim_id, list[str]]
        result: dict[str, list[str]] = {}
        for claim_id, data in raw.items():
            if isinstance(data, dict) and "queries" in data:
                result[claim_id] = [str(q) for q in data["queries"] if q]
            elif isinstance(data, list):
                result[claim_id] = [str(q) for q in data if q]

        # Sicherstellen dass alle Claims abgedeckt sind
        for claim in claims:
            if claim.id not in result:
                # Fallback: Claim-Text als einzelne Query
                result[claim.id] = [claim.text[:80]]

        return result

    async def _evaluate_sufficiency(
        self,
        claims: list[Claim],
        evidence_packs: dict[str, EvidencePack],
        article_text: str,
        all_queries: dict[str, list[str]],
        round_num: int,
    ) -> dict[str, CommanderClaimReview]:
        """Prompt 2-4: Evaluiere ob Evidenz pro Claim ausreicht."""
        user_message = self._build_review_user_message(
            claims, evidence_packs, article_text, all_queries,
        )
        raw = self._llm_json(COMMANDER_SUFFICIENCY_REVIEW_PROMPT, user_message)

        result: dict[str, CommanderClaimReview] = {}
        for claim_id, data in raw.items():
            if isinstance(data, dict):
                result[claim_id] = CommanderClaimReview(
                    sufficient=bool(data.get("sufficient", False)),
                    reasoning=str(data.get("reasoning", "")),
                    new_queries=_parse_new_queries(data.get("new_queries", {})),
                )
            else:
                # Fallback: als sufficient behandeln
                result[claim_id] = CommanderClaimReview(sufficient=True)

        # Claims ohne Review-Ergebnis als sufficient behandeln
        for claim in claims:
            if claim.id not in result:
                result[claim.id] = CommanderClaimReview(sufficient=True)

        return result

    async def _run_search_round(
        self,
        query_plan: dict[str, list[str]],
        evidence_builder: Any,
        claims: list[Claim],
    ) -> dict[str, EvidencePack]:
        """Führe Websuche für alle Claims parallel via EvidenceBuilder aus."""
        claim_map = {c.id: c for c in claims}
        tasks: dict[str, asyncio.Task] = {}

        for claim_id, queries in query_plan.items():
            claim = claim_map.get(claim_id)
            if claim and queries:
                tasks[claim_id] = asyncio.create_task(
                    evidence_builder.execute_with_queries_async(claim, queries),
                )

        results: dict[str, EvidencePack] = {}
        for claim_id, task in tasks.items():
            try:
                pack = await task
                results[claim_id] = pack
            except Exception as e:
                self._log(f"  Suche für {claim_id} fehlgeschlagen: {type(e).__name__}: {e}")

        return results

    # ── User-Message Builder ─────────────────────────────────────────────────

    def _build_initial_user_message(
        self, claims: list[Claim], article_text: str,
    ) -> str:
        """Baue die User-Message für Prompt 1 (initiale Query-Generierung)."""
        parts = [f"## Originaltext (gekürzt)\n{article_text[:2000]}\n"]
        parts.append("## Claims\n")

        for claim in claims:
            parts.append(f"### {claim.id}: {claim.text}")
            if isinstance(claim, ProcessedClaim) and claim.frame:
                frame = claim.frame
                frame_parts = []
                if frame.subject:
                    frame_parts.append(f"subject={frame.subject}")
                if frame.institution:
                    frame_parts.append(f"institution={frame.institution}")
                if frame.location:
                    frame_parts.append(f"location={frame.location}")
                if frame.numbers:
                    frame_parts.append(f"numbers={frame.numbers}")
                if frame.sanction:
                    frame_parts.append(f"sanction={frame.sanction}")
                if frame.policy_context:
                    frame_parts.append(f"policy_context={frame.policy_context}")
                if frame.predicate:
                    frame_parts.append(f"predicate={frame.predicate}")
                if frame_parts:
                    parts.append(f"Frame: {', '.join(frame_parts)}")

                # site:-Hints aus SearchProfile
                if isinstance(claim, ProcessedClaim) and claim.search_profile:
                    hints = claim.search_profile.official_source_hints
                    if hints:
                        parts.append(f"Source-Hints: {', '.join(hints[:3])}")
            parts.append("")

        return "\n".join(parts)

    def _build_review_user_message(
        self,
        claims: list[Claim],
        evidence_packs: dict[str, EvidencePack],
        article_text: str,
        all_queries: dict[str, list[str]],
    ) -> str:
        """Baue die User-Message für Prompts 2-4 (Sufficiency-Review)."""
        parts = [f"## Originaltext (gekürzt)\n{article_text[:1500]}\n"]

        for claim in claims:
            pack = evidence_packs.get(claim.id)
            parts.append(f"### {claim.id}: {claim.text}")

            # Frame-Info
            if isinstance(claim, ProcessedClaim) and claim.frame:
                frame = claim.frame
                frame_summary = []
                if frame.subject:
                    frame_summary.append(f"subject={frame.subject}")
                if frame.institution:
                    frame_summary.append(f"institution={frame.institution}")
                if frame.location:
                    frame_summary.append(f"location={frame.location}")
                if frame_summary:
                    parts.append(f"Frame: {', '.join(frame_summary)}")

            # Bisherige Queries
            queries_used = all_queries.get(claim.id, [])
            if queries_used:
                parts.append(f"Bisherige Suchanfragen: {json.dumps(queries_used, ensure_ascii=False)}")

            # Top-5 Ergebnisse aus EvidencePack
            if pack and pack.web_results:
                parts.append("Suchergebnisse (Top 5):")
                for item in pack.web_results[:5]:
                    direction = item.source_direction.value if hasattr(item, "source_direction") else "?"
                    ev_type = item.evidence_type.value if hasattr(item, "evidence_type") else "?"
                    excerpt = item.excerpt[:400] if item.excerpt else ""
                    parts.append(
                        f"  - [{direction}/{ev_type}] {item.source.title} "
                        f"({item.source.url})\n    {excerpt}"
                    )

                # Qualitätssignale
                if pack.evidence_quality:
                    eq = pack.evidence_quality
                    parts.append(
                        f"Qualität: overall={eq.overall_quality:.2f}, "
                        f"consensus={pack.source_consensus if hasattr(pack, 'source_consensus') else '?'}"
                    )
            else:
                parts.append("Suchergebnisse: Keine Treffer")

            parts.append("")

        return "\n".join(parts)


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


def compute_claim_difficulty(
    claim: Any,
    route_confidence: float = 0.50,
) -> float:
    """Berechnet einen Schwierigkeits-Score [0.0, 1.0] aus vorhandenen Claim-Metadaten.

    Kein LLM-Aufruf – rein regelbasiert aus vorhandenen Metadaten.

    Returns:
        0.0 = trivial (klarer Fakt, volle Struktur, gute Quellenlage)
        1.0 = extrem schwierig (ambig, vage, keine Quellen, extraordinary)
    """
    score = 0.0

    # ── 1. Ambiguität (Gewicht: 0.20) ────────────────────────────────────────
    ambiguity_weights = {"NONE": 0.0, "LOW": 0.3, "MEDIUM": 0.7, "HIGH": 1.0}
    amb_level = getattr(claim, "ambiguity_level", "NONE")
    amb_str = amb_level.value if hasattr(amb_level, "value") else str(amb_level)
    score += ambiguity_weights.get(amb_str, 0.5) * 0.20

    if getattr(claim, "requires_more_context", False):
        score += 0.05

    # ── 2. Checkworthiness (Gewicht: 0.15) ───────────────────────────────────
    # Niedrige Checkworthiness = schwerer zu prüfen
    cw = getattr(claim, "checkworthiness_score", 0.5)
    score += (1.0 - cw) * 0.15

    # ── 3. Claim-Qualität / Falsifizierbarkeit (Gewicht: 0.20) ───────────────
    quality = getattr(claim, "claim_quality_score", 0.5)
    score += (1.0 - quality) * 0.20

    # ── 4. Quality Signals (Gewicht: 0.15) ───────────────────────────────────
    hard_signals = {
        "extraordinary_claim", "elevated_burden_of_proof",
        "missing_artifact_evidence", "underspecified_actor",
    }
    signals = set(getattr(claim, "quality_signals", []))
    signal_hits = len(signals & hard_signals)
    score += min(1.0, signal_hits / 2.0) * 0.15

    # ── 5. Frame-Vollständigkeit (Gewicht: 0.15) ─────────────────────────────
    frame = getattr(claim, "frame", None)
    if frame is None:
        score += 0.15
    else:
        core_fields = ["subject", "predicate", "object", "institution"]
        empty = sum(1 for f in core_fields if not getattr(frame, f, None))
        score += (empty / len(core_fields)) * 0.15

    # ── 6. Route Confidence (Gewicht: 0.15) ──────────────────────────────────
    # Niedrige Route-Confidence = schwerer Quellen zu finden
    score += (1.0 - route_confidence) * 0.15

    return round(min(1.0, max(0.0, score)), 2)


def difficulty_to_budget(
    difficulty: float,
    cfg: CommanderConfig,
) -> tuple[int, int]:
    """Mappt Difficulty-Score auf (prompt_budget, query_budget) pro Claim.

    Schwellwerte und Budgets kommen aus CommanderConfig.

    Returns:
        (prompt_budget, query_budget)
    """
    if difficulty < 0.25:
        return (cfg.easy_max_prompts, cfg.easy_max_queries)
    elif difficulty < 0.50:
        return (cfg.moderate_max_prompts, cfg.moderate_max_queries)
    elif difficulty < 0.75:
        return (cfg.hard_max_prompts, cfg.hard_max_queries)
    else:
        return (cfg.very_hard_max_prompts, cfg.very_hard_max_queries)


def _parse_new_queries(data: Any) -> dict[str, list[str]]:
    """Parse new_queries aus dem LLM-Output, robust gegen verschiedene Formate."""
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[str]] = {}
    for engine, queries in data.items():
        if isinstance(queries, list):
            result[str(engine)] = [str(q) for q in queries if q]
    return result


def _merge_evidence_packs(existing: EvidencePack, new: EvidencePack) -> EvidencePack:
    """Merge ein neues EvidencePack in ein bestehendes (URL-Dedup)."""
    seen_urls: set[str] = {item.source.url for item in existing.web_results}

    # Neue Items nur wenn URL noch nicht vorhanden
    merged_items = list(existing.web_results)
    for item in new.web_results:
        if item.source.url not in seen_urls:
            seen_urls.add(item.source.url)
            merged_items.append(item)

    # Queries zusammenführen (Dedup)
    merged_queries = list(existing.queries_used)
    existing_queries_set = set(existing.queries_used)
    for q in new.queries_used:
        if q not in existing_queries_set:
            merged_queries.append(q)
            existing_queries_set.add(q)

    # GFC Matches mergen (URL-Dedup)
    gfc_urls = {m.url for m in existing.google_fact_check_matches}
    merged_gfc = list(existing.google_fact_check_matches)
    for m in new.google_fact_check_matches:
        if m.url not in gfc_urls:
            merged_gfc.append(m)
            gfc_urls.add(m.url)

    # Retrieval-Notes zusammenführen
    merged_notes = list(existing.retrieval_notes or [])
    merged_notes.extend(new.retrieval_notes or [])

    return EvidencePack(
        claim_id=existing.claim_id,
        claim_text=existing.claim_text,
        canonical_text=existing.canonical_text,
        queries_used=merged_queries,
        google_fact_check_matches=merged_gfc,
        web_results=merged_items,
        selected_sources=[item.source for item in merged_items[:5]],
        contradictions=existing.contradictions,
        extraction_confidence=max(
            existing.extraction_confidence,
            new.extraction_confidence,
        ),
        evidence_quality=new.evidence_quality or existing.evidence_quality,
        retrieval_notes=merged_notes,
        source_count=len(merged_items),
        official_results=(existing.official_results or []) + (new.official_results or []),
    )
