"""Tests für orchestrator.py."""

from __future__ import annotations

import pytest

from models.schemas import (
    Claim,
    ClaimExtractionResult,
    ClaimType,
    FactCheckResult,
    FactRating,
    OverallRating,
    RhetoricAnalysisResult,
    SynthesisResult,
)


# ── Hilfsfunktionen ───────────────────────────────────────────────


def _make_extraction(claims: list[Claim]) -> ClaimExtractionResult:
    return ClaimExtractionResult(claims=claims)


def _make_fact_check(claim_id: str = "C1", rating: FactRating = FactRating.TRUE) -> FactCheckResult:
    return FactCheckResult(
        claim_id=claim_id,
        rating=rating,
        evidence="Test",
        sources=["https://example.com"],
    )


def _make_synthesis() -> SynthesisResult:
    return SynthesisResult(
        overall_rating=OverallRating.RELIABLE,
        confidence=0.9,
        summary="Alles stimmt.",
        sources=[],
    )


# ── Orchestrator.analyze ──────────────────────────────────────────


def test_analyze_no_claims_returns_reliable(minimal_config, mocker):
    """Wenn keine Claims extrahiert werden, soll RELIABLE zurückgegeben werden."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    # Mock alle Agenten
    orch.claim_extractor = mocker.MagicMock()
    orch.fact_checker = mocker.MagicMock()
    orch.number_auditor = mocker.MagicMock()
    orch.rhetoric_analyzer = mocker.MagicMock()
    orch.synthesizer = mocker.MagicMock()

    orch.claim_extractor.run_safe.return_value = (_make_extraction([]), None)

    result = orch.analyze("Leerer Text ohne Claims.")
    assert result.overall_rating == OverallRating.RELIABLE
    assert result.confidence == 0.3


def test_analyze_skips_opinion_claims(minimal_config, mocker):
    """OPINION Claims sollen weder Fact-Check noch Number-Audit auslösen."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    opinion = Claim(id="C1", text="Das ist schlecht.", type=ClaimType.OPINION)

    orch.claim_extractor = mocker.MagicMock()
    orch.claim_extractor.run_safe.return_value = (_make_extraction([opinion]), None)

    orch.fact_checker = mocker.MagicMock()
    orch.number_auditor = mocker.MagicMock()
    orch.rhetoric_analyzer = mocker.MagicMock()
    orch.rhetoric_analyzer.run_safe.return_value = (RhetoricAnalysisResult(), None)
    orch.synthesizer = mocker.MagicMock()
    orch.synthesizer.run.return_value = _make_synthesis()

    orch.analyze("Meinungstext.")

    orch.fact_checker.run_safe.assert_not_called()
    orch.number_auditor.run_safe.assert_not_called()


def test_analyze_graceful_degradation_on_fact_check_failure(minimal_config, mocker):
    """Wenn Fact-Checker fehlschlägt, soll die Analyse trotzdem weiterlaufen."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    factual = Claim(id="C1", text="Faktenbehauptung", type=ClaimType.FACTUAL)
    orch.claim_extractor = mocker.MagicMock()
    orch.claim_extractor.run_safe.return_value = (_make_extraction([factual]), None)

    orch.fact_checker = mocker.MagicMock()
    orch.fact_checker.run_safe.return_value = (None, "FactChecker: ConnectionError: timeout")

    orch.number_auditor = mocker.MagicMock()
    orch.rhetoric_analyzer = mocker.MagicMock()
    orch.rhetoric_analyzer.run_safe.return_value = (RhetoricAnalysisResult(), None)

    synthesis = _make_synthesis()
    synthesis.analysis_errors = []
    orch.synthesizer = mocker.MagicMock()
    orch.synthesizer.run.return_value = synthesis

    result = orch.analyze("Text mit einem Claim.")

    assert len(result.analysis_errors) > 0
    assert "FactChecker" in result.analysis_errors[0]


def test_analyze_collects_sources_from_fact_checks(minimal_config, mocker):
    """Quellen aus Fact-Checks sollen im Ergebnis erscheinen."""
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = minimal_config

    factual = Claim(id="C1", text="Behauptung", type=ClaimType.FACTUAL)
    fc = _make_fact_check("C1")

    orch.claim_extractor = mocker.MagicMock()
    orch.claim_extractor.run_safe.return_value = (_make_extraction([factual]), None)
    orch.fact_checker = mocker.MagicMock()
    orch.fact_checker.run_safe.return_value = (fc, None)
    orch.number_auditor = mocker.MagicMock()
    orch.rhetoric_analyzer = mocker.MagicMock()
    orch.rhetoric_analyzer.run_safe.return_value = (RhetoricAnalysisResult(), None)

    synthesis = SynthesisResult(
        overall_rating=OverallRating.RELIABLE,
        confidence=0.8,
        summary="OK",
        sources=["https://synthesis.com"],
        claims_analysis=[fc],
    )
    orch.synthesizer = mocker.MagicMock()
    orch.synthesizer.run.return_value = synthesis

    result = orch.analyze("Behauptungstext.")
    assert "https://example.com" in result.sources or "https://synthesis.com" in result.sources
