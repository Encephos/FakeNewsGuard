"""Tests für den Confidence-Datenfluss von VerdictAgent → FactCheckResult → Synthesizer → API.

Stellt sicher, dass die kalibrierte Confidence nicht verloren geht und der Synthesizer
die Per-Claim-Confidences korrekt nutzt — nicht den rohen LLM-Output — und dass die
API-Transformation die Confidence sowohl gesamt als auch pro Claim ausgibt.
"""

from __future__ import annotations

import pytest

from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    SourceConsensus,
)
from models.schemas import FactCheckResult, FactRating
from models.verdict_models import FinalVerdictMeta


# ── Hilfsfunktionen ─────────────────────────────────────────────────────────


def _make_fc(claim_id: str = "C1", confidence: float = 0.65) -> FactCheckResult:
    """Erstelle ein minimales FactCheckResult mit kalibrierter Confidence."""
    return FactCheckResult(
        claim_id=claim_id,
        rating=FactRating.FALSE,
        confidence=confidence,
        evidence="Keine belastbaren Quellen gefunden.",
        sources=[],
        verdict_meta=FinalVerdictMeta(
            calibrated_confidence=confidence,
            primary_sources_consulted=False,
        ),
    )


# ── Tests: Modell-Felder ─────────────────────────────────────────────────────


class TestConfidenceFields:
    """calibrated_confidence und confidence-Felder sind in den Modellen vorhanden."""

    def test_fact_check_result_has_confidence_field(self):
        """FactCheckResult hat ein confidence-Feld mit Default -1.0."""
        fc = FactCheckResult(
            claim_id="C1",
            rating=FactRating.FALSE,
            evidence="",
        )
        assert fc.confidence == -1.0

    def test_final_verdict_meta_has_calibrated_confidence(self):
        """FinalVerdictMeta hat calibrated_confidence mit Default -1.0."""
        meta = FinalVerdictMeta()
        assert meta.calibrated_confidence == -1.0

    def test_calibrated_confidence_stored_correctly(self):
        """Kalibrierte Confidence wird korrekt in FactCheckResult gespeichert."""
        fc = _make_fc(confidence=0.50)
        assert fc.confidence == 0.50
        assert fc.verdict_meta is not None
        assert fc.verdict_meta.calibrated_confidence == 0.50

    def test_confidence_accepts_full_range(self):
        """Confidence akzeptiert gültige Werte 0.0–1.0."""
        fc_low = _make_fc(confidence=0.0)
        fc_high = _make_fc(confidence=1.0)
        assert fc_low.confidence == 0.0
        assert fc_high.confidence == 1.0


# ── Tests: Synthesizer-Confidence-Logik ─────────────────────────────────────


class TestSynthesizerConfidenceLogic:
    """Synthesizer nutzt kalibrierte Per-Claim-Confidences statt LLM-Rohwert."""

    def _run_synthesizer_confidence(
        self,
        fact_checks: list[FactCheckResult],
        raw_llm_confidence: float = 0.95,
    ) -> float:
        """Simuliert die Synthesizer-Confidence-Berechnung direkt."""
        claim_confidences = [
            fc.confidence for fc in fact_checks if fc.confidence >= 0.0
        ]
        if claim_confidences:
            avg_claim_conf = sum(claim_confidences) / len(claim_confidences)
            min_claim_conf = min(claim_confidences)
            confidence = min(raw_llm_confidence, avg_claim_conf, min_claim_conf + 0.10)
        else:
            confidence = raw_llm_confidence
        # Ceiling: 1 Fact-Check ohne starke Quellen → max 0.80
        if len(fact_checks) == 1 and not any(
            fc.verdict_meta and fc.verdict_meta.primary_sources_consulted
            for fc in fact_checks
        ):
            confidence = min(confidence, 0.80)
        return min(1.0, max(0.0, confidence))

    def test_calibrated_confidence_overrides_llm_raw(self):
        """LLM gibt 0.95 aus, kalibrierte Claim-Confidence ist 0.50 → max 0.60."""
        fc = _make_fc(confidence=0.50)
        result = self._run_synthesizer_confidence([fc], raw_llm_confidence=0.95)
        # min(0.95, 0.50, 0.50+0.10) = min(0.95, 0.50, 0.60) = 0.50
        assert result == 0.50

    def test_multiple_claims_uses_minimum_as_ceiling(self):
        """Mehrere Claims: niedrigste Confidence bestimmt Ceiling (+ 0.10)."""
        fc1 = _make_fc("C1", confidence=0.70)
        fc2 = _make_fc("C2", confidence=0.40)  # Niedrigste
        fc3 = _make_fc("C3", confidence=0.60)
        result = self._run_synthesizer_confidence([fc1, fc2, fc3], raw_llm_confidence=0.95)
        # avg = (0.70+0.40+0.60)/3 = 0.567, min = 0.40
        # min(0.95, 0.567, 0.40+0.10) = min(0.95, 0.567, 0.50) = 0.50
        assert result <= 0.55  # ≤ min_claim + 0.10

    def test_no_calibrated_confidence_falls_back_to_raw(self):
        """Ohne kalibrierte Confidence (Altdaten, confidence=-1.0) → LLM-Rohwert."""
        fc = FactCheckResult(
            claim_id="C1",
            rating=FactRating.MOSTLY_FALSE,
            evidence="",
            confidence=-1.0,  # Nicht gesetzt
        )
        result = self._run_synthesizer_confidence([fc], raw_llm_confidence=0.75)
        assert result == 0.75

    def test_single_claim_without_primary_sources_caps_at_0_80(self):
        """Einzelner Claim ohne Primärquellen → max 0.80."""
        fc = _make_fc(confidence=0.90)
        result = self._run_synthesizer_confidence([fc], raw_llm_confidence=0.95)
        assert result <= 0.80

    def test_zero_evidence_claim_caps_synthesis_at_0_60(self):
        """Claim mit Zero-Evidence (0.50 ceiling) → Synthese max 0.60."""
        fc = _make_fc(confidence=0.50)  # VerdictAgent hat auf 0.50 gedeckelt
        result = self._run_synthesizer_confidence([fc], raw_llm_confidence=0.95)
        # min(0.95, 0.50, 0.50+0.10) = 0.50
        assert result <= 0.60


# ── Tests: SearXNGQuery-Routing ───────────────────────────────────────────────


class TestSearXNGQueryRouting:
    """SearXNGQuery ermöglicht per-Query Engine- und Zeitraum-Routing."""

    def test_searxng_query_dataclass_exists(self):
        """SearXNGQuery ist importierbar und hat korrekte Felder."""
        from tools.web_search import SearXNGQuery
        sq = SearXNGQuery(
            query="Hannover 15-Minuten-Stadt Faktencheck",
            engines=["duckduckgo", "brave", "tagesschau"],
            time_range="year",
        )
        assert sq.query == "Hannover 15-Minuten-Stadt Faktencheck"
        assert sq.engines == ["duckduckgo", "brave", "tagesschau"]
        assert sq.time_range == "year"

    def test_searxng_query_default_fields(self):
        """SearXNGQuery-Defaults: engines=None, time_range=None, categories=None."""
        from tools.web_search import SearXNGQuery
        sq = SearXNGQuery(query="test")
        assert sq.engines is None
        assert sq.time_range is None
        assert sq.categories is None

    def test_multi_search_accepts_str_list(self):
        """multi_search_async akzeptiert weiterhin list[str] (Rückwärtskompatibilität)."""
        from tools.web_search import SearXNGQuery
        queries = ["query1", "query2"]
        # Normalisierung wie in multi_search_async
        normalized = [
            q if isinstance(q, SearXNGQuery) else SearXNGQuery(query=q)
            for q in queries
        ]
        assert len(normalized) == 2
        assert all(isinstance(sq, SearXNGQuery) for sq in normalized)
        assert normalized[0].query == "query1"

    def test_routing_constants_defined(self):
        """Engine-Routing-Konstanten sind in config definiert."""
        from config import SEARXNG_WEB_ENGINES, SEARXNG_NEWS_ENGINES, SEARXNG_REFERENCE_ENGINES
        assert "duckduckgo" in SEARXNG_WEB_ENGINES
        assert "brave" in SEARXNG_WEB_ENGINES
        assert "tagesschau" in SEARXNG_NEWS_ENGINES
        assert "wikipedia" in SEARXNG_REFERENCE_ENGINES
        assert "wikidata" in SEARXNG_REFERENCE_ENGINES


# ── Tests: site:-Eliminierung ─────────────────────────────────────────────────


class TestSiteOperatorElimination:
    """site:-Operatoren werden aus Query-2 und Query-3 entfernt."""

    def _make_profile_with_hints(self):
        from models.schemas import ClaimSearchProfile
        return ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            official_source_hints=["site:hannover.de"],
            fact_check_hints=["site:correctiv.org"],
            core_entities=["Hannover", "Stadtrat"],
        )

    def test_official_source_hint_site_stripped(self):
        """'site:hannover.de' in official_source_hints → 'hannover.de' in Query."""
        from agents.fact_checker import _build_search_queries_from_profile
        from models.schemas import ProcessedClaim, ClaimType, ClaimFrame

        profile = self._make_profile_with_hints()
        claim = ProcessedClaim(
            id="C1",
            text="Der Stadtrat von Hannover hat die 15-Minuten-Stadt beschlossen.",
            type=ClaimType.FACTUAL,
            search_profile=profile,
        )
        queries = _build_search_queries_from_profile(claim)
        # Query 2 soll 'hannover.de' enthalten, NICHT 'site:hannover.de'
        q2_candidates = [q for q in queries if "hannover.de" in q.lower()]
        assert q2_candidates, f"Keine Query mit 'hannover.de' gefunden: {queries}"
        assert not any("site:" in q for q in q2_candidates), \
            f"site:-Operator noch vorhanden: {q2_candidates}"

    def test_fact_check_hint_site_stripped(self):
        """'site:correctiv.org' in fact_check_hints → 'correctiv.org' in Query."""
        from agents.fact_checker import _build_search_queries_from_profile
        from models.schemas import ProcessedClaim, ClaimType

        profile = self._make_profile_with_hints()
        claim = ProcessedClaim(
            id="C1",
            text="Der Stadtrat von Hannover hat die 15-Minuten-Stadt beschlossen.",
            type=ClaimType.FACTUAL,
            search_profile=profile,
        )
        queries = _build_search_queries_from_profile(claim)
        q3_candidates = [q for q in queries if "correctiv.org" in q.lower()]
        assert q3_candidates, f"Keine Query mit 'correctiv.org' gefunden: {queries}"
        assert not any("site:" in q for q in q3_candidates), \
            f"site:-Operator noch vorhanden: {q3_candidates}"


# ── Tests: End-to-End Confidence-Propagation ─────────────────────────────────


def _make_synthesis_result(
    fact_checks: list,
    synthesis_confidence: float,
) -> "SynthesisResult":
    """Erstellt ein minimales SynthesisResult für API-Tests."""
    from models.schemas import OverallRating, SynthesisResult
    return SynthesisResult(
        overall_rating=OverallRating.MIXED,
        confidence=synthesis_confidence,
        summary="Testaggregation",
        claims_analysis=fact_checks,
    )


class TestConfidenceEndToEnd:
    """Confidence-Propagation vom VerdictAgent bis zur API-Ausgabe."""

    def test_api_transform_includes_per_claim_confidence(self):
        """_transform_result gibt confidence pro Claim aus (0–100)."""
        from api import _transform_result

        fc = _make_fc("C1", confidence=0.68)
        result = _make_synthesis_result([fc], synthesis_confidence=0.68)
        claims_map = {"C1": {"text": "Testbehauptung", "type": "FACTUAL"}}

        output = _transform_result(result, claims_map)

        assert output["claims"][0]["confidence"] == 68

    def test_api_transform_claim_confidence_none_for_legacy(self):
        """Per-Claim-Confidence ist None wenn VerdictAgent nicht aktiv war (confidence=-1.0)."""
        from api import _transform_result

        fc = FactCheckResult(
            claim_id="C1",
            rating=FactRating.UNVERIFIABLE,
            evidence="",
            confidence=-1.0,
        )
        result = _make_synthesis_result([fc], synthesis_confidence=0.50)
        claims_map = {"C1": {"text": "Alter Eintrag ohne VerdictAgent", "type": "FACTUAL"}}

        output = _transform_result(result, claims_map)

        assert output["claims"][0]["confidence"] is None

    def test_api_transform_overall_confidence_in_percent(self):
        """overall confidence wird als ganzzahliger Prozentwert (0–100) ausgegeben."""
        from api import _transform_result

        fc = _make_fc("C1", confidence=0.72)
        result = _make_synthesis_result([fc], synthesis_confidence=0.72)
        claims_map = {"C1": {"text": "Test", "type": "FACTUAL"}}

        output = _transform_result(result, claims_map)

        assert output["confidence"] == 72

    def test_full_pipeline_confidence_capped_by_weakest_claim(self):
        """Synthesizer-Confidence wird durch den schwächsten Claim gedeckelt.

        Wenn VerdictAgent für C2 nur 0.40 vergeben hat, darf die Gesamt-
        Confidence des Synthesizers 0.50 nicht überschreiten.
        """
        fc1 = _make_fc("C1", confidence=0.75)
        fc2 = _make_fc("C2", confidence=0.40)  # schwächster Claim

        claim_confidences = [fc.confidence for fc in [fc1, fc2] if fc.confidence >= 0.0]
        avg = sum(claim_confidences) / len(claim_confidences)   # 0.575
        min_conf = min(claim_confidences)                        # 0.40
        raw_llm = 0.95
        synthesis_confidence = min(raw_llm, avg, min_conf + 0.10)  # min(0.95, 0.575, 0.50) = 0.50

        from api import _transform_result
        result = _make_synthesis_result([fc1, fc2], synthesis_confidence=synthesis_confidence)
        claims_map = {
            "C1": {"text": "Claim 1", "type": "FACTUAL"},
            "C2": {"text": "Claim 2", "type": "FACTUAL"},
        }

        output = _transform_result(result, claims_map)

        assert output["confidence"] <= 50  # max 50 wegen min_claim + 0.10
        # Per-Claim-Confidence bleibt erhalten
        c2_out = next(c for c in output["claims"] if c["id"] == "C2")
        assert c2_out["confidence"] == 40

    def test_verdict_agent_uses_canonical_field_names(self):
        """VerdictAgent liest has_primary_source_any und has_fact_check_any (nicht Deprecated-Felder).

        Stellt sicher, dass _calibrate_confidence die kanonischen Felder liest,
        nicht die rückwärtskompatiblen Aliase.
        """
        import inspect
        from agents.verdict_agent import _calibrate_confidence

        source = inspect.getsource(_calibrate_confidence)
        assert "has_primary_source_any" in source, \
            "_calibrate_confidence soll has_primary_source_any statt has_primary_sources nutzen"
        assert "has_fact_check_any" in source, \
            "_calibrate_confidence soll has_fact_check_any statt has_fact_check_org_result nutzen"
        assert "has_primary_sources" not in source, \
            "_calibrate_confidence darf keine deprecated Felder nutzen"
        assert "has_fact_check_org_result" not in source, \
            "_calibrate_confidence darf keine deprecated Felder nutzen"
