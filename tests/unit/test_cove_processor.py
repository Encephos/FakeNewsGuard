"""Tests für den CoVe-Prozessor (Chain-of-Verification)."""

from __future__ import annotations

import pytest


# ── Unit Tests: CoVeProcessor (Mock-basiert) ─────────────────────────────────

class TestCoVeProcessor:
    @pytest.fixture
    def mock_llm(self, mocker):
        mock = mocker.MagicMock()
        # Baseline
        mock.complete.side_effect = [
            '{"rating": "MISLEADING", "reasoning": "Test-Begründung", "confidence": 0.7, "main_evidence_urls": []}',
            # Questions
            '{"questions": [{"question_id": "Q1", "text": "Welche Quelle belegt die 50%?", "category": "source", "rationale": "Wichtig", "priority": 1}]}',
            # Answer
            '{"answer": "Keine Primärquelle gefunden", "confidence": 0.4, "supporting_evidence_urls": [], "supporting_excerpt": "", "contradicts_baseline": false, "answer_found_in_evidence": false}',
            # Reconciliation
            '{"final_rating": "MISLEADING", "final_confidence": 0.65, "rating_changed": false, "confidence_delta": -0.05, "contradictions_found": [], "unanswered_questions": ["Q1"]}',
        ]
        return mock

    def test_cove_produces_trace(self, mock_llm, sample_evidence_pack):
        from agents.cove_processor import CoVeProcessor
        from config import CoVeConfig
        from models.schemas import Claim, ClaimType

        config = CoVeConfig(enabled=True, max_verification_questions=1, max_additional_searches=0)
        processor = CoVeProcessor(mock_llm, config)

        claim = Claim(id="C1", text="Test Claim", type=ClaimType.STATISTICAL)
        trace = processor.process(claim, sample_evidence_pack)

        assert trace.claim_id == "C1"
        assert trace.baseline is not None
        assert trace.baseline.rating in ("TRUE", "MOSTLY_TRUE", "MISLEADING", "MOSTLY_FALSE", "FALSE", "UNVERIFIABLE")

    def test_cove_disabled_returns_empty_trace(self, sample_evidence_pack):
        from agents.cove_processor import CoVeProcessor
        from config import CoVeConfig
        from models.schemas import Claim, ClaimType
        from unittest.mock import MagicMock

        config = CoVeConfig(enabled=False)
        processor = CoVeProcessor(MagicMock(), config)

        claim = Claim(id="C1", text="Test", type=ClaimType.FACTUAL)
        trace = processor.process(claim, sample_evidence_pack)

        assert trace.claim_id == "C1"
        assert len(trace.verification_questions) == 0

    def test_cove_budget_limits_questions(self, mocker, sample_evidence_pack):
        """max_verification_questions wird eingehalten."""
        from agents.cove_processor import CoVeProcessor
        from config import CoVeConfig
        from models.schemas import Claim, ClaimType

        mock_llm = mocker.MagicMock()
        mock_llm.complete.side_effect = [
            # Baseline
            '{"rating": "UNVERIFIABLE", "reasoning": "Test", "confidence": 0.5, "main_evidence_urls": []}',
            # Questions – LLM gibt 5 zurück, aber Config limitiert auf 2
            '{"questions": [{"question_id": "Q1", "text": "Frage 1", "category": "number", "rationale": "R", "priority": 1}, {"question_id": "Q2", "text": "Frage 2", "category": "source", "rationale": "R", "priority": 1}, {"question_id": "Q3", "text": "Frage 3", "category": "context", "rationale": "R", "priority": 2}]}',
            # Answers (max 2)
            '{"answer": "A1", "confidence": 0.5, "supporting_evidence_urls": [], "supporting_excerpt": "", "contradicts_baseline": false, "answer_found_in_evidence": true}',
            '{"answer": "A2", "confidence": 0.5, "supporting_evidence_urls": [], "supporting_excerpt": "", "contradicts_baseline": false, "answer_found_in_evidence": true}',
            # Reconciliation
            '{"final_rating": "UNVERIFIABLE", "final_confidence": 0.5, "rating_changed": false, "confidence_delta": 0.0, "contradictions_found": [], "unanswered_questions": []}',
        ]
        config = CoVeConfig(enabled=True, max_verification_questions=2)
        processor = CoVeProcessor(mock_llm, config)

        claim = Claim(id="C1", text="Test", type=ClaimType.STATISTICAL)
        trace = processor.process(claim, sample_evidence_pack)

        assert len(trace.verification_questions) <= 2

    def test_baseline_rating_differs_from_final(self, mocker, sample_evidence_pack):
        """Finale Einschätzung kann von Baseline abweichen."""
        from agents.cove_processor import CoVeProcessor
        from config import CoVeConfig
        from models.schemas import Claim, ClaimType

        mock_llm = mocker.MagicMock()
        mock_llm.complete.side_effect = [
            # Baseline: MISLEADING
            '{"rating": "MISLEADING", "reasoning": "Ersteinschätzung", "confidence": 0.6, "main_evidence_urls": []}',
            # Questions
            '{"questions": [{"question_id": "Q1", "text": "Primärquelle?", "category": "source", "rationale": "wichtig", "priority": 1}]}',
            # Answer: widerspricht Baseline
            '{"answer": "Keine Quelle", "confidence": 0.3, "supporting_evidence_urls": [], "supporting_excerpt": "", "contradicts_baseline": true, "answer_found_in_evidence": false}',
            # Reconciliation: Rating ändert sich zu UNVERIFIABLE
            '{"final_rating": "UNVERIFIABLE", "final_confidence": 0.4, "rating_changed": true, "confidence_delta": -0.2, "contradictions_found": ["Keine Primärquelle"], "unanswered_questions": ["Q1"]}',
        ]
        config = CoVeConfig(enabled=True, max_verification_questions=1)
        processor = CoVeProcessor(mock_llm, config)

        claim = Claim(id="C1", text="Zahl stieg um 50%", type=ClaimType.STATISTICAL)
        trace = processor.process(claim, sample_evidence_pack)

        assert trace.final_rating_changed is True
        assert trace.confidence_delta < 0  # Konfidenz sank

    def test_contradictions_detected(self, mocker, sample_evidence_pack):
        """Widersprüche zwischen Baseline und Verifikation werden erkannt."""
        from agents.cove_processor import CoVeProcessor
        from config import CoVeConfig
        from models.schemas import Claim, ClaimType

        mock_llm = mocker.MagicMock()
        mock_llm.complete.side_effect = [
            '{"rating": "TRUE", "reasoning": "Sieht gut aus", "confidence": 0.8, "main_evidence_urls": []}',
            '{"questions": [{"question_id": "Q1", "text": "Welches Jahr?", "category": "timeframe", "rationale": "R", "priority": 1}]}',
            '{"answer": "Daten von 2019, nicht 2023", "confidence": 0.9, "supporting_evidence_urls": ["https://destatis.de"], "supporting_excerpt": "PKS 2019", "contradicts_baseline": true, "answer_found_in_evidence": true}',
            '{"final_rating": "MISLEADING", "final_confidence": 0.5, "rating_changed": true, "confidence_delta": -0.3, "contradictions_found": ["Falsches Jahr verwendet"], "unanswered_questions": []}',
        ]
        config = CoVeConfig(enabled=True, max_verification_questions=1)
        processor = CoVeProcessor(mock_llm, config)

        claim = Claim(id="C1", text="Daten belegen Anstieg", type=ClaimType.STATISTICAL)
        trace = processor.process(claim, sample_evidence_pack)

        assert len(trace.contradictions_found) > 0
        assert trace.has_significant_contradictions() is True


# ── Unit Tests: CoVeTrace Modell ──────────────────────────────────────────────

class TestCoVeTrace:
    def test_has_significant_contradictions_false(self):
        from models.verdict_models import BaselineAssessment, CoVeTrace, VerificationAnswer

        trace = CoVeTrace(
            claim_id="C1",
            baseline=BaselineAssessment(rating="TRUE", reasoning="Test", confidence=0.8),
            verification_answers=[
                VerificationAnswer(
                    question_id="Q1",
                    answer="Bestätigt",
                    confidence=0.9,
                    contradicts_baseline=False,
                )
            ],
        )
        assert trace.has_significant_contradictions() is False

    def test_has_significant_contradictions_true(self):
        from models.verdict_models import BaselineAssessment, CoVeTrace, VerificationAnswer

        trace = CoVeTrace(
            claim_id="C1",
            baseline=BaselineAssessment(rating="TRUE", reasoning="Test", confidence=0.8),
            verification_answers=[
                VerificationAnswer(
                    question_id="Q1",
                    answer="Widerspricht!",
                    confidence=0.9,
                    contradicts_baseline=True,
                )
            ],
        )
        assert trace.has_significant_contradictions() is True
