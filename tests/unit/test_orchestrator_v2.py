"""Tests für den refaktorierten Orchestrator (v2) mit Top-N und neuer Pipeline."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── Helper: Mock-Orchestrator bauen ──────────────────────────────────────────

def _make_orchestrator(mocker, config):
    """Erstelle einen Orchestrator mit vollständig gemockten Agenten."""
    from orchestrator import Orchestrator
    from tools.claim_router import ClaimRouter

    with patch("orchestrator.ClaimExtractorAgent") as MockCE, \
         patch("orchestrator.FactCheckerAgent") as MockFC, \
         patch("orchestrator.NumberAuditorAgent") as MockNA, \
         patch("orchestrator.RhetoricAnalyzerAgent") as MockRA, \
         patch("orchestrator.SynthesizerAgent") as MockSyn, \
         patch("orchestrator.ImageAnalyzerAgent") as MockIA, \
         patch("orchestrator.LLMClient"), \
         patch("orchestrator.WebSearchClient"), \
         patch("orchestrator.ClaimCache"):

        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.config = config
        orchestrator._router = ClaimRouter()

        from models.schemas import (
            ClaimProcessingResult, ClaimType, FactCheckResult, FactRating,
            OverallRating, ProcessedClaim, RhetoricAnalysisResult, SynthesisResult,
        )

        # Default ProcessedClaim
        processed_claim = ProcessedClaim(
            id="C1",
            text="Test Behauptung",
            type=ClaimType.STATISTICAL,
            requires_agents=["fact_checker", "number_auditor"],
            priority_score=0.8,
            is_checkworthy=True,
        )

        # Mock claim_extractor
        mock_extractor = MagicMock()
        mock_extractor.run_safe.return_value = (
            ClaimProcessingResult(claims=[processed_claim], implicit_claims=[]),
            None,
        )
        orchestrator.claim_extractor = mock_extractor

        # Mock fact_checker
        mock_fc = MagicMock()
        mock_fc.run_safe.return_value = (
            FactCheckResult(
                claim_id="C1",
                rating=FactRating.MISLEADING,
                evidence="Test Evidenz",
                sources=["https://example.com"],
            ),
            None,
        )
        orchestrator.fact_checker = mock_fc

        # Mock number_auditor
        from models.schemas import ManipulationType, NumberAuditResult
        mock_na = MagicMock()
        mock_na.run_safe.return_value = (
            NumberAuditResult(
                claim_id="C1",
                calculation_check="Rechnung korrekt",
                correct_interpretation="Korrekte Einordnung",
                manipulation_type=ManipulationType.NONE,
            ),
            None,
        )
        orchestrator.number_auditor = mock_na

        # Mock rhetoric_analyzer
        mock_ra = MagicMock()
        mock_ra.run_safe.return_value = (
            RhetoricAnalysisResult(techniques=[], overall_framing="neutral"),
            None,
        )
        orchestrator.rhetoric_analyzer = mock_ra

        # Mock synthesizer
        mock_syn = MagicMock()
        mock_syn.run.return_value = SynthesisResult(
            overall_rating=OverallRating.MISLEADING,
            confidence=0.8,
            summary="Test Zusammenfassung",
        )
        orchestrator.synthesizer = mock_syn

        # Mock image_analyzer
        orchestrator.image_analyzer = MagicMock()

        return orchestrator

    return None


# ── Tests: Top-N Auswahl ──────────────────────────────────────────────────────

class TestTopNSelection:
    def test_top_n_0_keeps_all(self, minimal_config):
        """top_n=0 behält alle prüfbaren Claims."""
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        claims = [
            ProcessedClaim(id=f"C{i}", text=f"Claim {i}", type=ClaimType.STATISTICAL,
                           priority_score=float(i) / 10, is_checkworthy=True)
            for i in range(1, 6)
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)
        assert len(checkable) == 5

    def test_top_n_2_keeps_highest_priority(self):
        """top_n=2 behält die 2 Claims mit höchstem priority_score."""
        from config import AppConfig, CacheConfig, ClaimProcessingConfig, CoVeConfig, GoogleFactCheckConfig, LangSearchConfig, LLMConfig, RetryConfig, SearchConfig
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        config = AppConfig(
            llm=LLMConfig(provider="anthropic", api_key="test-key"),
            search=SearchConfig(provider="searxng", base_url="http://localhost:8888"),
            langsearch=LangSearchConfig(api_key="", enabled=False),
            google_fact_check=GoogleFactCheckConfig(api_key="", enabled=False),
            claim_processing=ClaimProcessingConfig(top_n=2),
            cove=CoVeConfig(enabled=False),
            retry=RetryConfig(max_attempts=1, base_delay_s=0.0),
            cache=CacheConfig(enabled=False),
            verbose=False,
        )

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config

        claims = [
            ProcessedClaim(id="C1", text="Niedrig", type=ClaimType.FACTUAL, priority_score=0.2, is_checkworthy=True),
            ProcessedClaim(id="C2", text="Hoch", type=ClaimType.STATISTICAL, priority_score=0.9, is_checkworthy=True),
            ProcessedClaim(id="C3", text="Mittel", type=ClaimType.CAUSAL, priority_score=0.5, is_checkworthy=True),
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)

        assert len(checkable) == 2
        ids = [c.id for c in checkable]
        assert "C2" in ids  # Höchste Priorität
        assert "C3" in ids  # Zweithöchste Priorität
        assert "C1" not in ids  # Niedrigste – ausgeschlossen

    def test_duplicate_canonical_hash_deduplicated(self, minimal_config):
        """Claims mit identischem canonical_hash werden dedupliziert."""
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        claims = [
            ProcessedClaim(id="C1", text="Variante A", type=ClaimType.FACTUAL,
                           canonical_hash="abc123", priority_score=0.8, is_checkworthy=True),
            ProcessedClaim(id="C2", text="Variante B", type=ClaimType.FACTUAL,
                           canonical_hash="abc123", priority_score=0.7, is_checkworthy=True),
            ProcessedClaim(id="C3", text="Anderer Claim", type=ClaimType.FACTUAL,
                           canonical_hash="def456", priority_score=0.6, is_checkworthy=True),
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)

        ids = [c.id for c in checkable]
        assert len(checkable) == 2
        assert "C1" in ids  # Erster mit diesem Hash bleibt
        assert "C2" not in ids  # Duplikat entfernt
        assert "C3" in ids  # Anderer Hash bleibt

    def test_empty_canonical_hash_not_deduplicated(self, minimal_config):
        """Claims ohne canonical_hash werden nicht als Duplikate behandelt."""
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        claims = [
            ProcessedClaim(id="C1", text="Claim A", type=ClaimType.FACTUAL,
                           canonical_hash="", priority_score=0.8, is_checkworthy=True),
            ProcessedClaim(id="C2", text="Claim B", type=ClaimType.FACTUAL,
                           canonical_hash="", priority_score=0.7, is_checkworthy=True),
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)

        assert len(checkable) == 2

    def test_opinions_always_excluded(self, minimal_config):
        """OPINION Claims werden immer ausgeschlossen, unabhängig von top_n."""
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        claims = [
            ProcessedClaim(id="C1", text="Meinung", type=ClaimType.OPINION, priority_score=1.0, is_checkworthy=True),
            ProcessedClaim(id="C2", text="Fakt", type=ClaimType.FACTUAL, priority_score=0.5, is_checkworthy=True),
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)

        ids = [c.id for c in checkable]
        assert "C1" not in ids
        assert "C2" in ids

    def test_not_checkworthy_excluded(self, minimal_config):
        """Claims mit is_checkworthy=False werden ausgeschlossen."""
        from orchestrator import Orchestrator
        from models.schemas import ClaimType, ClaimProcessingResult, ProcessedClaim

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        claims = [
            ProcessedClaim(id="C1", text="Trivial", type=ClaimType.FACTUAL, priority_score=0.1,
                           is_checkworthy=False),
            ProcessedClaim(id="C2", text="Relevant", type=ClaimType.STATISTICAL, priority_score=0.8,
                           is_checkworthy=True),
        ]
        result = ClaimProcessingResult(claims=claims)
        checkable = orch._select_top_claims(result)

        ids = [c.id for c in checkable]
        assert "C1" not in ids
        assert "C2" in ids


# ── Tests: Orchestrator Workflow ──────────────────────────────────────────────

class TestOrchestratorWorkflow:
    def _make_orch(self, minimal_config, claim, extractor_result, fc_result=None,
                   fc_error=None, na_result=None, rhetoric_result=None):
        """Helper: Orchestrator ohne echte LLM/Search-Clients erstellen."""
        from orchestrator import Orchestrator
        from models.schemas import (
            ClaimType, FactRating, FactCheckResult, ManipulationType,
            NumberAuditResult, OverallRating, RhetoricAnalysisResult, SynthesisResult,
        )

        from tools.claim_router import ClaimRouter

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config
        orch._on_step = None  # kein Step-Callback
        orch._router = ClaimRouter()
        orch.commander = None  # Commander deaktiviert in Tests

        orch.claim_extractor = MagicMock()
        orch.claim_extractor.run_safe.return_value = extractor_result

        orch.fact_checker = MagicMock()
        orch.fact_checker.run_safe.return_value = (
            fc_result or FactCheckResult(claim_id=claim.id if claim else "C1",
                                          rating=FactRating.TRUE, evidence="ok"),
            fc_error,
        )

        orch.number_auditor = MagicMock()
        orch.number_auditor.run_safe.return_value = (
            na_result or NumberAuditResult(
                claim_id=claim.id if claim else "C1",
                calculation_check="ok",
                correct_interpretation="ok",
                manipulation_type=ManipulationType.NONE,
            ),
            None,
        )

        rhet = rhetoric_result or RhetoricAnalysisResult(techniques=[], overall_framing="")
        orch.rhetoric_analyzer = MagicMock()
        orch.rhetoric_analyzer.run_safe.return_value = (rhet, None)

        orch.synthesizer = MagicMock()
        orch.synthesizer.run.return_value = SynthesisResult(
            overall_rating=OverallRating.MIXED,
            confidence=0.6,
            summary="Test",
        )
        return orch

    def test_analyze_returns_synthesis_result(self, mocker, minimal_config):
        """analyze() gibt ein SynthesisResult zurück."""
        from models.schemas import (
            ClaimProcessingResult, ClaimType, FactCheckResult, FactRating,
            OverallRating, ProcessedClaim, SynthesisResult,
        )

        claim = ProcessedClaim(
            id="C1", text="Test", type=ClaimType.STATISTICAL,
            priority_score=0.8, is_checkworthy=True,
            requires_agents=["fact_checker"],
        )
        orch = self._make_orch(
            minimal_config, claim,
            extractor_result=(ClaimProcessingResult(claims=[claim]), None),
            fc_result=FactCheckResult(claim_id="C1", rating=FactRating.TRUE, evidence="ok"),
        )
        orch.synthesizer.run.return_value = SynthesisResult(
            overall_rating=OverallRating.RELIABLE, confidence=0.9, summary="ok"
        )

        result = orch.analyze("Test Behauptung über irgendwas.")
        assert isinstance(result, SynthesisResult)

    def test_claim_extraction_failure_returns_graceful(self, minimal_config):
        """Wenn Claim-Extraktion fehlschlägt, wird ein Fallback-Ergebnis zurückgegeben."""
        from models.schemas import OverallRating

        orch = self._make_orch(
            minimal_config, None,
            extractor_result=(None, "LLM timeout"),
        )
        result = orch.analyze("Test Text")
        assert result.overall_rating == OverallRating.MIXED
        assert len(result.analysis_errors) > 0

    def test_empty_claims_returns_reliable(self, minimal_config):
        """Wenn keine Claims gefunden werden, wird RELIABLE zurückgegeben."""
        from models.schemas import ClaimProcessingResult, OverallRating

        orch = self._make_orch(
            minimal_config, None,
            extractor_result=(ClaimProcessingResult(claims=[]), None),
        )
        result = orch.analyze("Test Text")
        assert result.overall_rating == OverallRating.RELIABLE

    def test_fact_check_failure_graceful(self, minimal_config):
        """Fact-Check-Fehler führen nicht zum Totalabbruch."""
        from models.schemas import (
            ClaimProcessingResult, ClaimType, OverallRating, ProcessedClaim,
        )

        claim = ProcessedClaim(
            id="C1", text="Test", type=ClaimType.FACTUAL,
            priority_score=0.5, is_checkworthy=True,
        )
        orch = self._make_orch(
            minimal_config, claim,
            extractor_result=(ClaimProcessingResult(claims=[claim]), None),
            fc_result=None,
            fc_error="API Rate Limit",
        )

        result = orch.analyze("Test")
        assert result is not None
        assert "API Rate Limit" in result.analysis_errors

    def test_input_validation_empty_raises(self, minimal_config):
        """Leerer Input wirft InputValidationError."""
        from orchestrator import InputValidationError, Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config

        with pytest.raises(InputValidationError):
            orch._validate_input("")

    def test_input_truncated_to_max_chars(self, minimal_config):
        """Langer Input wird auf max_input_chars gekürzt."""
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = minimal_config  # max_input_chars=10000

        long_text = "x" * 20_000
        result = orch._validate_input(long_text)
        assert len(result) <= minimal_config.max_input_chars

    def test_image_analyzer_called_when_image_urls_provided(self, minimal_config):
        """analyze() ruft image_analyzer.run_safe() auf wenn image_urls übergeben werden."""
        from models.schemas import (
            ClaimProcessingResult, ClaimType, ImageAnalysisResult,
            OverallRating, ProcessedClaim,
        )

        claim = ProcessedClaim(
            id="C1", text="Test", type=ClaimType.FACTUAL,
            priority_score=0.8, is_checkworthy=True,
        )
        orch = self._make_orch(
            minimal_config, claim,
            extractor_result=(ClaimProcessingResult(claims=[claim]), None),
        )
        img_result = ImageAnalysisResult(items=[])
        orch.image_analyzer = MagicMock()
        orch.image_analyzer.run_safe.return_value = (img_result, None)

        orch.analyze("Test Text", image_urls=["https://example.com/img.jpg"])

        orch.image_analyzer.run_safe.assert_called_once()
        call_input = orch.image_analyzer.run_safe.call_args[0][0]
        assert call_input["image_urls"] == ["https://example.com/img.jpg"]
        assert "post_text" in call_input

        synth_call_input = orch.synthesizer.run.call_args[0][0]
        assert "image_analysis" in synth_call_input
        assert "image_analysis_result" in synth_call_input

    def test_image_analyzer_not_called_without_image_urls(self, minimal_config):
        """analyze() ohne image_urls ruft image_analyzer.run_safe() NICHT auf."""
        from models.schemas import ClaimProcessingResult, ClaimType, ProcessedClaim

        claim = ProcessedClaim(
            id="C1", text="Test", type=ClaimType.FACTUAL,
            priority_score=0.8, is_checkworthy=True,
        )
        orch = self._make_orch(
            minimal_config, claim,
            extractor_result=(ClaimProcessingResult(claims=[claim]), None),
        )
        orch.image_analyzer = MagicMock()

        orch.analyze("Test Text")

        orch.image_analyzer.run_safe.assert_not_called()
        synth_call_input = orch.synthesizer.run.call_args[0][0]
        assert "image_analysis" not in synth_call_input
