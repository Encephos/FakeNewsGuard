"""Tests für den VerdictAgent."""

from __future__ import annotations

import pytest


class TestVerdictAgent:
    @pytest.fixture
    def mock_verdict_llm(self, mocker):
        mock = mocker.MagicMock()
        mock.complete_structured.return_value = {
            "claim_id": "C1",
            "rating": "MISLEADING",
            "evidence": "Quellen belegen einen Anstieg von 5,5%, nicht 50%.",
            "correction": "Die Zahl ist deutlich übertrieben.",
            "missing_context": "Vergleichsjahr fehlt.",
            "sources": ["https://destatis.de/test"],
        }
        mock.complete.return_value = mock.complete_structured.return_value
        return mock

    def test_verdict_produces_fact_check_result(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent

        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
            "cove_trace": None,
            "number_audit": None,
        })

        assert result is not None
        assert result.claim_id == "C1"
        assert result.rating is not None

    def test_verdict_attaches_evidence_pack(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent

        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
            "cove_trace": None,
            "number_audit": None,
        })

        assert result.evidence_pack is not None
        assert result.evidence_pack.claim_id == "C1"

    def test_verdict_with_cove_trace(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent
        from models.verdict_models import BaselineAssessment, CoVeTrace, VerificationAnswer, VerificationQuestion, VerificationCategory

        cove_trace = CoVeTrace(
            claim_id="C1",
            baseline=BaselineAssessment(rating="MISLEADING", reasoning="Test", confidence=0.6),
            verification_questions=[
                VerificationQuestion(question_id="Q1", text="Welche Quelle?", category=VerificationCategory.SOURCE)
            ],
            verification_answers=[
                VerificationAnswer(question_id="Q1", answer="Keine Primärquelle", confidence=0.3, contradicts_baseline=True)
            ],
            contradictions_found=["Keine Primärquelle"],
            confidence_delta=-0.2,
            final_rating_changed=False,
        )

        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
            "cove_trace": cove_trace,
            "number_audit": None,
        })

        assert result.cove_trace is not None
        assert result.verdict_meta is not None
        # Widersprüche sollten Unsicherheitssignale erzeugen
        assert len(result.verdict_meta.uncertainty_signals) > 0

    def test_confidence_reduced_by_contradictions(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent
        from models.verdict_models import BaselineAssessment, CoVeTrace, VerificationAnswer, VerificationQuestion, VerificationCategory

        cove_trace = CoVeTrace(
            claim_id="C1",
            baseline=BaselineAssessment(rating="TRUE", reasoning="Scheint richtig", confidence=0.9),
            verification_answers=[
                VerificationAnswer(
                    question_id="Q1", answer="Falsches Datum", confidence=0.8,
                    contradicts_baseline=True
                )
            ],
            contradictions_found=["Datum stimmt nicht"],
            confidence_delta=-0.3,
        )

        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
            "cove_trace": cove_trace,
        })

        assert result.verdict_meta is not None
        assert result.verdict_meta.confidence_reduction_reason != ""

    def test_gfc_matches_mark_fact_check_org(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent

        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        # sample_evidence_pack hat GFC Matches (aus conftest)
        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
        })

        assert result.verdict_meta is not None
        assert result.verdict_meta.verdict_based_on_fact_check_org is True

    def test_sources_deduped(
        self, mock_verdict_llm, minimal_config, mocker, sample_processed_claim, sample_evidence_pack
    ):
        from agents.verdict_agent import VerdictAgent

        # LLM gibt dieselbe URL zurück, die auch im EvidencePack ist
        mock_verdict_llm.complete_structured.return_value = {
            "claim_id": "C1",
            "rating": "FALSE",
            "evidence": "Test",
            "correction": "",
            "missing_context": "",
            "sources": ["https://destatis.de/test"],  # Gleiche URL wie im Pack
        }
        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_verdict_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
        })

        # URLs sollten dedupliziert sein
        assert len(result.sources) == len(set(result.sources))


# ── Integration Test: VerdictAgent + CoVeTrace Flow ──────────────────────────

class TestVerdictAgentCoVeIntegration:
    def test_full_flow_without_api(
        self, mocker, minimal_config, sample_processed_claim, sample_evidence_pack
    ):
        """Kompletter Verdict-Flow mit Mock-LLM."""
        from agents.verdict_agent import VerdictAgent

        mock_llm = mocker.MagicMock()
        mock_llm.complete_structured.return_value = {
            "claim_id": "C1",
            "rating": "FALSE",
            "evidence": "PKS 2023 zeigt nur +5,5%, nicht +50%.",
            "correction": "50% ist stark übertrieben.",
            "missing_context": "",
            "sources": ["https://destatis.de/pks2023"],
        }
        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
        })

        from models.schemas import FactRating
        assert isinstance(result.rating, FactRating)
        assert result.evidence != ""
        assert result.claim_id == "C1"
