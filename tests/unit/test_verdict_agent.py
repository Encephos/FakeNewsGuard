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


# ── Tests: Satire-Erkennung ───────────────────────────────────────────────────


class TestVerdictAgentSatireDetection:
    def test_satire_source_skips_llm_call(
        self, mocker, minimal_config, sample_processed_claim
    ):
        """EvidencePack mit der-postillon.com → is_satire=True, kein LLM-Call."""
        from agents.verdict_agent import VerdictAgent
        from models.evidence_models import EvidencePack, EvidenceSource

        mock_llm = mocker.MagicMock()
        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_llm, mock_search)

        satire_pack = EvidencePack(
            claim_id="C1",
            claim_text="Test-Satire-Claim",
            selected_sources=[
                EvidenceSource(
                    url="https://der-postillon.com/2024/artikel",
                    domain="der-postillon.com",
                    domain_tier=5,
                )
            ],
        )

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": satire_pack,
        })

        from models.schemas import FactRating
        assert result.is_satire is True
        assert result.rating == FactRating.UNVERIFIABLE
        assert result.confidence == pytest.approx(0.85)
        mock_llm.complete_structured.assert_not_called()

    def test_non_satire_source_calls_llm(
        self, mocker, minimal_config, sample_processed_claim, sample_evidence_pack
    ):
        """Normales EvidencePack → kein Satire-Flag, LLM wird aufgerufen."""
        from agents.verdict_agent import VerdictAgent

        mock_llm = mocker.MagicMock()
        mock_llm.complete_structured.return_value = {
            "claim_id": "C1",
            "rating": "FALSE",
            "evidence": "Test",
            "correction": "",
            "missing_context": "",
            "sources": [],
        }
        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_llm, mock_search)

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": sample_evidence_pack,
        })

        assert result.is_satire is False
        mock_llm.complete_structured.assert_called_once()

    def test_satire_correction_text_is_set(
        self, mocker, minimal_config, sample_processed_claim
    ):
        """Satire-Erkennung setzt eine erklärende correction."""
        from agents.verdict_agent import VerdictAgent
        from models.evidence_models import EvidencePack, EvidenceSource

        mock_llm = mocker.MagicMock()
        mock_search = mocker.MagicMock()
        agent = VerdictAgent(minimal_config, mock_llm, mock_search)

        satire_pack = EvidencePack(
            claim_id="C1",
            claim_text="Satire-Test",
            selected_sources=[
                EvidenceSource(
                    url="https://babylonbee.com/news/test",
                    domain="babylonbee.com",
                    domain_tier=5,
                )
            ],
        )

        result = agent.execute({
            "claim": sample_processed_claim,
            "evidence_pack": satire_pack,
        })

        assert result.is_satire is True
        assert "Satire" in result.correction


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


# ── Unit Tests: _check_verdict_grounding() ──────────────────────────────────

class TestCheckVerdictGrounding:
    @pytest.fixture
    def agent(self, minimal_config, mocker):
        mock_llm = mocker.MagicMock()
        mock_search = mocker.MagicMock()
        from agents.verdict_agent import VerdictAgent
        return VerdictAgent(minimal_config, mock_llm, mock_search)

    def _make_pack(self, excerpts: list[str], domains: list[str] | None = None):
        from models.evidence_models import (
            EvidenceItem,
            EvidencePack,
            EvidenceSource,
        )
        web_results = [
            EvidenceItem(
                source=EvidenceSource(
                    url=f"https://{d}/test",
                    title=f"Source {i}",
                    domain=d,
                    domain_tier=2,
                ),
                excerpt=exc,
                relevance_score=0.8,
            )
            for i, (exc, d) in enumerate(
                zip(excerpts, (domains or ["example.com"] * len(excerpts)))
            )
        ]
        return EvidencePack(
            claim_id="TEST",
            claim_text="test claim",
            web_results=web_results,
        )

    def test_high_overlap_returns_high_score(self, agent):
        """Verdict-Text der fast wörtlich Excerpts zitiert → Score > 0.8."""
        excerpt = (
            "Die Polizeiliche Kriminalstatistik zeigt einen Anstieg von 5,5 Prozent "
            "bei registrierten Straftaten im Jahr 2023 laut Bundesinnenministerium."
        )
        # Verdict repeats core words from excerpt
        verdict = (
            "Die Polizeiliche Kriminalstatistik zeigt einen Anstieg von 5,5 Prozent "
            "bei registrierten Straftaten im Jahr 2023 laut Bundesinnenministerium. "
            "Das Bundesinnenministerium bestätigt den Anstieg von 5,5 Prozent in der "
            "Kriminalstatistik 2023 für registrierte Straftaten in Deutschland."
        )
        pack = self._make_pack([excerpt])
        score = agent._check_verdict_grounding(verdict, pack)
        assert score > 0.8

    def test_hallucinated_reasoning_returns_low_score(self, agent):
        """Verdict-Text ohne Überlappung mit Excerpts → Score < 0.3."""
        excerpt = "Die Inflationsrate lag im März 2024 bei 2,2 Prozent laut Statistikamt."
        verdict = (
            "Quantenmechanische Phänomene beeinflussen die Quantenchromodynamik "
            "in supraleitenden Materialien bei Temperaturen nahe dem absoluten Nullpunkt. "
            "Subatomare Teilchen folgen der Schrödinger-Gleichung in verschränkten Systemen."
        )
        pack = self._make_pack([excerpt])
        score = agent._check_verdict_grounding(verdict, pack)
        assert score < 0.3

    def test_number_bonus_applies(self, agent):
        """Satz mit Zahl die auch im Excerpt vorkommt → als grounded gewertet."""
        excerpt = "Im Jahr 2023 wurden 42 Prozent mehr Fälle registriert als im Vorjahr."
        # Sentence shares the number 42 but uses completely different vocabulary
        verdict = (
            "Laut verfügbaren Quellen wurden deutlich mehr Vorfälle gemeldet. "
            "Die Steigerungsrate betrug 42 Prozent gegenüber dem Vergleichszeitraum."
        )
        pack = self._make_pack([excerpt])
        score = agent._check_verdict_grounding(verdict, pack)
        # At least the sentence with 42% should be grounded via number bonus
        assert score > 0.0

    def test_empty_reasoning_returns_minus_one(self, agent):
        """Leerer verdict_reasoning → -1.0."""
        pack = self._make_pack(["Beliebiger Excerpt mit Text über Statistiken."])
        assert agent._check_verdict_grounding("", pack) == -1.0
        assert agent._check_verdict_grounding("   ", pack) == -1.0
