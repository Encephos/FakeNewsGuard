"""Tests für die adaptive Suchstrategie in agents/fact_checker.py."""

from __future__ import annotations

import pytest

from agents.fact_checker import _adaptive_max_results, _build_search_queries
from models.schemas import Claim, ClaimType


# ── _build_search_queries adaptive ───────────────────────────────


def test_factual_short_claim_minimal_queries():
    """Kurze FACTUAL Claims: nur Direktsuche."""
    claim = Claim(id="C1", text="Berlin ist Hauptstadt.", type=ClaimType.FACTUAL)
    queries = _build_search_queries(claim)
    assert len(queries) == 1  # Nur Direktsuche (text < 60 Zeichen)


def test_factual_long_claim_adds_faktencheck():
    """Längere FACTUAL Claims: Direktsuche + Faktencheck."""
    claim = Claim(
        id="C1",
        text="Die Kriminalität in Deutschland ist laut Polizeistatistik seit 2015 um 50% gestiegen.",
        type=ClaimType.FACTUAL,
    )
    queries = _build_search_queries(claim)
    assert len(queries) == 2
    assert "faktencheck" in queries[1].lower()


def test_statistical_claim_aggressive_search():
    """STATISTICAL Claims: 3+ Queries mit Datenquellen."""
    claim = Claim(
        id="C1",
        text="40% der Einbrüche werden von Ausländern begangen.",
        type=ClaimType.STATISTICAL,
    )
    queries = _build_search_queries(claim)
    assert len(queries) >= 3
    combined = " ".join(queries).lower()
    assert "faktencheck" in combined
    assert "statistik" in combined
    assert "destatis" in combined


def test_statistical_claim_with_context():
    """STATISTICAL Claims mit Originaltext: Kontext-Query wird ergänzt."""
    claim = Claim(
        id="C1",
        text="40% der Einbrüche werden von Ausländern begangen.",
        type=ClaimType.STATISTICAL,
    )
    context = (
        "In einer Rede zum Thema Innere Sicherheit in Nordrhein-Westfalen "
        "behauptete der Politiker, dass die Polizeiliche Kriminalstatistik "
        "einen dramatischen Anstieg zeige."
    )
    queries = _build_search_queries(claim, original_text=context)
    assert len(queries) >= 4  # Direktsuche + faktencheck + statistik + kontext


def test_causal_claim_queries():
    """CAUSAL Claims: Faktencheck + Ursache-Wirkung."""
    claim = Claim(
        id="C1",
        text="Zuwanderung führt zu steigender Kriminalität.",
        type=ClaimType.CAUSAL,
    )
    queries = _build_search_queries(claim)
    assert len(queries) >= 2
    combined = " ".join(queries).lower()
    assert "ursache" in combined or "wirkung" in combined or "zusammenhang" in combined


def test_contextual_claim_queries():
    """CONTEXTUAL Claims: Faktencheck + Kontext."""
    claim = Claim(
        id="C1",
        text="Deutschland hat die meisten Asylanträge in der EU.",
        type=ClaimType.CONTEXTUAL,
    )
    queries = _build_search_queries(claim)
    assert len(queries) >= 2
    assert "faktencheck" in queries[1].lower()


# ── _adaptive_max_results ────────────────────────────────────────


def test_statistical_gets_most_results():
    claim = Claim(id="C1", text="x", type=ClaimType.STATISTICAL)
    assert _adaptive_max_results(claim) == 10


def test_causal_gets_medium_results():
    claim = Claim(id="C1", text="x", type=ClaimType.CAUSAL)
    assert _adaptive_max_results(claim) == 8


def test_contextual_gets_medium_results():
    claim = Claim(id="C1", text="x", type=ClaimType.CONTEXTUAL)
    assert _adaptive_max_results(claim) == 8


def test_factual_gets_minimal_results():
    claim = Claim(id="C1", text="x", type=ClaimType.FACTUAL)
    assert _adaptive_max_results(claim) == 5
