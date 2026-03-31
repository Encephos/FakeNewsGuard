"""Tests für die adaptive Suchstrategie in agents/fact_checker.py."""

from __future__ import annotations

import pytest

from agents.fact_checker import _adaptive_max_results, _build_search_queries
from models.schemas import Claim, ClaimType, ProcessedClaim


# ── _build_search_queries adaptive ───────────────────────────────


def test_factual_short_claim_minimal_queries():
    """Kurze FACTUAL Claims: Direktsuche + Faktencheck."""
    claim = Claim(id="C1", text="Berlin ist Hauptstadt.", type=ClaimType.FACTUAL)
    queries = _build_search_queries(claim)
    assert len(queries) == 2  # Direktsuche + Faktencheck
    assert "faktencheck" in queries[1].lower()


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


# ── Context disambiguation ──────────────────────────────────────


def test_context_query_prioritizes_proper_nouns():
    """Eigennamen aus dem Quelltext werden priorisiert (Spalter-Szenario)."""
    claim = Claim(
        id="C1",
        text="Sie sind ein Spalter.",
        type=ClaimType.FACTUAL,
    )
    context = (
        "Waldi Hartmann kritisiert Bundespräsident Frank-Walter Steinmeier "
        "in seiner Kolumne bei NIUS. Er bezeichnet ihn als Präsident der "
        "deutschen Spaltung und wirft ihm mangelnde Neutralität vor."
    )
    queries = _build_search_queries(claim, original_text=context)
    # Context-Query muss Eigennamen enthalten (Steinmeier, Hartmann, etc.)
    context_queries = [q for q in queries if q != claim.text and "faktencheck" not in q.lower()]
    assert len(context_queries) >= 1
    combined_context = " ".join(context_queries).lower()
    assert "steinmeier" in combined_context or "hartmann" in combined_context


def test_context_query_added_for_all_claim_types():
    """Kontext-Query wird für alle Claim-Typen ergänzt, nicht nur STATISTICAL."""
    for ct in [ClaimType.FACTUAL, ClaimType.CAUSAL, ClaimType.CONTEXTUAL]:
        claim = Claim(id="C1", text="Das stimmt nicht.", type=ct)
        context = "Bundeskanzler Scholz sagte in einer Pressekonferenz zum Thema Migration"
        queries = _build_search_queries(claim, original_text=context)
        context_queries = [q for q in queries if "Scholz" in q or "Migration" in q or "Bundeskanzler" in q]
        assert len(context_queries) >= 1, f"No context query for {ct.value}"


# ── Pronoun resolution via canonical_text ────────────────────────


def test_canonical_text_used_for_search_queries():
    """canonical_text (mit aufgelösten Pronomen) wird statt claim.text für Suche genutzt."""
    claim = ProcessedClaim(
        id="C1",
        text="Sie sind ein Spalter.",
        type=ClaimType.FACTUAL,
        canonical_text="Frank-Walter Steinmeier ist ein Spalter.",
        is_checkworthy=True,
    )
    queries = _build_search_queries(claim)
    # Die erste Query (Direktsuche) muss den aufgelösten Text verwenden
    assert queries[0] == "Frank-Walter Steinmeier ist ein Spalter."
    assert "Sie sind ein Spalter" not in queries[0]


def test_raw_text_used_when_no_canonical():
    """Ohne canonical_text wird claim.text verwendet (Fallback)."""
    claim = ProcessedClaim(
        id="C1",
        text="Berlin ist Hauptstadt.",
        type=ClaimType.FACTUAL,
        canonical_text="",
        is_checkworthy=True,
    )
    queries = _build_search_queries(claim)
    assert queries[0] == "Berlin ist Hauptstadt."


def test_plain_claim_without_canonical_text():
    """Einfache Claim-Objekte (ohne canonical_text) nutzen claim.text."""
    claim = Claim(id="C1", text="Test Claim.", type=ClaimType.FACTUAL)
    queries = _build_search_queries(claim)
    assert queries[0] == "Test Claim."
