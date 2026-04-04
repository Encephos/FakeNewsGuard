"""Unit-Tests für agents/commander.py – CommanderAgent und Hilfsfunktionen.

Zwei Schichten:
1. Reine Hilfsfunktionen (compute_claim_difficulty, difficulty_to_budget,
   _parse_new_queries, _merge_evidence_packs) – kein Mock nötig.
2. CommanderAgent.execute_async – gemockter LLM und evidence_builder (AsyncMock).
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _make_claim_ns(**kwargs) -> types.SimpleNamespace:
    """Minimaler Claim-ähnlicher Namespace für compute_claim_difficulty."""
    defaults = {
        "ambiguity_level": types.SimpleNamespace(value="NONE"),
        "requires_more_context": False,
        "checkworthiness_score": 0.9,
        "claim_quality_score": 0.9,
        "quality_signals": [],
        "frame": types.SimpleNamespace(
            subject="Bund",
            predicate="plant",
            object="Reform",
            institution="Bundesregierung",
        ),
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _default_commander_config():
    from config.commander import CommanderConfig
    return CommanderConfig(
        adaptive_budget=True,
        easy_max_prompts=2,
        easy_max_queries=6,
        moderate_max_prompts=3,
        moderate_max_queries=8,
        hard_max_prompts=4,
        hard_max_queries=12,
        very_hard_max_prompts=6,
        very_hard_max_queries=16,
    )


# ── compute_claim_difficulty ──────────────────────────────────────────────────


class TestComputeClaimDifficulty:
    def test_high_ambiguity_increases_score(self):
        from agents.commander import compute_claim_difficulty
        low = _make_claim_ns(ambiguity_level=types.SimpleNamespace(value="NONE"))
        high = _make_claim_ns(ambiguity_level=types.SimpleNamespace(value="HIGH"))
        assert compute_claim_difficulty(high) > compute_claim_difficulty(low)

    def test_no_frame_adds_max_frame_penalty(self):
        from agents.commander import compute_claim_difficulty
        no_frame = _make_claim_ns(frame=None)
        with_frame = _make_claim_ns()
        # frame=None → +0.15 penalty
        assert compute_claim_difficulty(no_frame) > compute_claim_difficulty(with_frame)

    def test_full_frame_adds_zero_frame_penalty(self):
        from agents.commander import compute_claim_difficulty
        full_frame = _make_claim_ns(
            frame=types.SimpleNamespace(
                subject="A", predicate="B", object="C", institution="D"
            )
        )
        score = compute_claim_difficulty(full_frame)
        # Alle 4 core_fields gesetzt → frame-Beitrag = 0
        assert score < 0.20  # Nur andere Faktoren tragen bei (alle minimal)

    def test_hard_quality_signals_increase_score(self):
        from agents.commander import compute_claim_difficulty
        no_signals = _make_claim_ns(quality_signals=[])
        with_signals = _make_claim_ns(
            quality_signals=["extraordinary_claim", "elevated_burden_of_proof"]
        )
        assert compute_claim_difficulty(with_signals) > compute_claim_difficulty(no_signals)

    def test_score_clamped_to_zero_one(self):
        from agents.commander import compute_claim_difficulty
        extreme = _make_claim_ns(
            ambiguity_level=types.SimpleNamespace(value="HIGH"),
            requires_more_context=True,
            checkworthiness_score=0.0,
            claim_quality_score=0.0,
            quality_signals=["extraordinary_claim", "elevated_burden_of_proof",
                             "missing_artifact_evidence", "underspecified_actor"],
            frame=None,
        )
        score = compute_claim_difficulty(extreme, route_confidence=0.0)
        assert 0.0 <= score <= 1.0


# ── difficulty_to_budget ──────────────────────────────────────────────────────


class TestDifficultyToBudget:
    def test_easy_difficulty_returns_easy_budget(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        prompts, queries = difficulty_to_budget(0.10, cfg)
        assert prompts == cfg.easy_max_prompts
        assert queries == cfg.easy_max_queries

    def test_moderate_difficulty_returns_moderate_budget(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        prompts, queries = difficulty_to_budget(0.35, cfg)
        assert prompts == cfg.moderate_max_prompts
        assert queries == cfg.moderate_max_queries

    def test_hard_difficulty_returns_hard_budget(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        prompts, queries = difficulty_to_budget(0.60, cfg)
        assert prompts == cfg.hard_max_prompts
        assert queries == cfg.hard_max_queries

    def test_very_hard_difficulty_returns_very_hard_budget(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        prompts, queries = difficulty_to_budget(0.80, cfg)
        assert prompts == cfg.very_hard_max_prompts
        assert queries == cfg.very_hard_max_queries

    def test_boundary_value_at_0_25(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        # 0.25 ist ≥ 0.25 → moderate
        prompts, _ = difficulty_to_budget(0.25, cfg)
        assert prompts == cfg.moderate_max_prompts

    def test_boundary_value_at_0_50(self):
        from agents.commander import difficulty_to_budget
        cfg = _default_commander_config()
        # 0.50 ist ≥ 0.50 → hard
        prompts, _ = difficulty_to_budget(0.50, cfg)
        assert prompts == cfg.hard_max_prompts


# ── _parse_new_queries ────────────────────────────────────────────────────────


class TestParseNewQueries:
    def test_valid_dict_returns_queries(self):
        from agents.commander import _parse_new_queries
        data = {"searxng": ["query 1", "query 2"], "langsearch": ["query 3"]}
        result = _parse_new_queries(data)
        assert result["searxng"] == ["query 1", "query 2"]
        assert result["langsearch"] == ["query 3"]

    def test_non_dict_input_returns_empty_dict(self):
        from agents.commander import _parse_new_queries
        assert _parse_new_queries(None) == {}
        assert _parse_new_queries("string") == {}
        assert _parse_new_queries([1, 2]) == {}

    def test_empty_strings_in_query_list_are_filtered(self):
        from agents.commander import _parse_new_queries
        data = {"searxng": ["q1", "", "q2", None]}
        result = _parse_new_queries(data)
        # Leere Strings und None werden gefiltert (if q)
        assert "" not in result["searxng"]
        assert "q1" in result["searxng"]
        assert "q2" in result["searxng"]

    def test_non_list_engine_value_is_ignored(self):
        from agents.commander import _parse_new_queries
        data = {"searxng": "not a list", "langsearch": ["ok query"]}
        result = _parse_new_queries(data)
        assert "searxng" not in result
        assert result["langsearch"] == ["ok query"]


# ── _merge_evidence_packs ─────────────────────────────────────────────────────


class TestMergeEvidencePacks:
    def _make_pack(self, claim_id: str, urls: list[str], queries: list[str]):
        from models.evidence_models import (
            EvidenceItem,
            EvidencePack,
            EvidenceSource,
        )
        items = [
            EvidenceItem(
                source=EvidenceSource(
                    url=url,
                    title=f"Title for {url}",
                    domain=url.split("/")[2],
                    domain_tier=2,
                    is_primary_source=False,
                ),
                excerpt="Excerpt",
                relevance_score=0.5,
                extraction_confidence=0.7,
                supports_claim=True,
            )
            for url in urls
        ]
        return EvidencePack(
            claim_id=claim_id,
            claim_text="Test claim",
            queries_used=queries,
            web_results=items,
            retrieval_notes=["note"],
        )

    def test_deduplicates_web_results_by_url(self):
        from agents.commander import _merge_evidence_packs
        pack_a = self._make_pack("C1", ["http://a.de", "http://b.de"], ["q1"])
        pack_b = self._make_pack("C1", ["http://b.de", "http://c.de"], ["q2"])
        merged = _merge_evidence_packs(pack_a, pack_b)
        urls = [item.source.url for item in merged.web_results]
        assert len(urls) == len(set(urls))  # keine Duplikate
        assert "http://a.de" in urls
        assert "http://b.de" in urls
        assert "http://c.de" in urls

    def test_merges_queries_without_duplicates(self):
        from agents.commander import _merge_evidence_packs
        pack_a = self._make_pack("C1", ["http://a.de"], ["query1", "query2"])
        pack_b = self._make_pack("C1", ["http://b.de"], ["query2", "query3"])
        merged = _merge_evidence_packs(pack_a, pack_b)
        assert merged.queries_used.count("query2") == 1
        assert "query1" in merged.queries_used
        assert "query3" in merged.queries_used


# ── CommanderAgent.execute_async ──────────────────────────────────────────────


class TestCommanderAgentAsync:
    async def test_execute_async_returns_commander_result(
        self, minimal_config, mock_llm_client, mock_search_client, sample_evidence_pack,
        sample_factual_claim,
    ):
        from agents.commander import CommanderAgent
        from models.commander_models import CommanderResult

        agent = CommanderAgent(minimal_config, mock_llm_client, mock_search_client)

        # Prompt 1: initiale Queries; Prompt 2: alle sufficient
        mock_llm_client.complete_json.side_effect = [
            {sample_factual_claim.id: {"queries": ["Kriminalität Deutschland Statistik"]}},
            {sample_factual_claim.id: {"sufficient": True, "reasoning": "ok", "new_queries": {}}},
        ]
        evidence_builder = AsyncMock()
        evidence_builder.execute_with_queries_async.return_value = sample_evidence_pack

        result = await agent.execute_async({
            "claims": [sample_factual_claim],
            "article_text": "Artikel-Text für den Test",
            "evidence_builder": evidence_builder,
        })

        assert isinstance(result, CommanderResult)
        assert result.total_prompts_used >= 1
        assert sample_factual_claim.id in result.claim_difficulties

    async def test_round_logs_created_correctly(
        self, minimal_config, mock_llm_client, mock_search_client, sample_evidence_pack,
        sample_factual_claim,
    ):
        from agents.commander import CommanderAgent
        from models.commander_models import CommanderRoundLog

        agent = CommanderAgent(minimal_config, mock_llm_client, mock_search_client)

        mock_llm_client.complete_json.side_effect = [
            {sample_factual_claim.id: {"queries": ["q1"]}},
            {sample_factual_claim.id: {"sufficient": True, "reasoning": "ok", "new_queries": {}}},
        ]
        evidence_builder = AsyncMock()
        evidence_builder.execute_with_queries_async.return_value = sample_evidence_pack

        result = await agent.execute_async({
            "claims": [sample_factual_claim],
            "article_text": "Text",
            "evidence_builder": evidence_builder,
        })

        assert len(result.round_logs) >= 1
        first_log = result.round_logs[0]
        assert isinstance(first_log, CommanderRoundLog)
        assert first_log.prompt_type == "initial"
        assert first_log.claims_evaluated == 1

    async def test_claim_difficulty_computed_for_all_claims(
        self, minimal_config, mock_llm_client, mock_search_client, sample_evidence_pack,
        sample_factual_claim, sample_statistical_claim,
    ):
        from agents.commander import CommanderAgent

        agent = CommanderAgent(minimal_config, mock_llm_client, mock_search_client)

        mock_llm_client.complete_json.side_effect = [
            {
                sample_factual_claim.id: {"queries": ["q1"]},
                sample_statistical_claim.id: {"queries": ["q2"]},
            },
            {
                sample_factual_claim.id: {"sufficient": True, "reasoning": "", "new_queries": {}},
                sample_statistical_claim.id: {"sufficient": True, "reasoning": "", "new_queries": {}},
            },
        ]
        evidence_builder = AsyncMock()
        evidence_builder.execute_with_queries_async.return_value = sample_evidence_pack

        result = await agent.execute_async({
            "claims": [sample_factual_claim, sample_statistical_claim],
            "article_text": "Text",
            "evidence_builder": evidence_builder,
        })

        assert sample_factual_claim.id in result.claim_difficulties
        assert sample_statistical_claim.id in result.claim_difficulties
        for diff in result.claim_difficulties.values():
            assert 0.0 <= diff <= 1.0
