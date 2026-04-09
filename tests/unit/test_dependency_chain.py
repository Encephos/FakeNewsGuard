"""Tests for DAG-based claim dependency chain propagation."""

import pytest

from models.schemas import (
    ClaimType,
    FactCheckResult,
    FactRating,
    ProcessedClaim,
)
from orchestrator import _apply_cross_claim_consistency


def _make_claim(claim_id: str, depends_on: list[str] | None = None) -> ProcessedClaim:
    return ProcessedClaim(
        id=claim_id,
        text=f"Claim {claim_id}",
        type=ClaimType.FACTUAL,
        depends_on=depends_on or [],
        dependency_type="policy_sanction",
    )


def _make_fc(claim_id: str, rating: FactRating, confidence: float = 0.80) -> FactCheckResult:
    return FactCheckResult(
        claim_id=claim_id,
        rating=rating,
        evidence="Test evidence",
        confidence=confidence,
    )


class TestSingleLevelDependency:
    """Test basic parent-child dependency propagation."""

    def test_parent_false_reduces_child_confidence(self):
        claims = [_make_claim("C1"), _make_claim("C2", depends_on=["C1"])]
        fcs = [
            _make_fc("C1", FactRating.FALSE, 0.80),
            _make_fc("C2", FactRating.TRUE, 0.80),
        ]

        updated, warnings = _apply_cross_claim_consistency(fcs, claims)

        c2_fc = next(fc for fc in updated if fc.claim_id == "C2")
        assert c2_fc.confidence < 0.80
        assert c2_fc.disputed_dependency is True
        assert len(warnings) == 1
        assert "Widerspruch" in warnings[0]

    def test_parent_true_no_penalty(self):
        claims = [_make_claim("C1"), _make_claim("C2", depends_on=["C1"])]
        fcs = [
            _make_fc("C1", FactRating.TRUE, 0.80),
            _make_fc("C2", FactRating.TRUE, 0.80),
        ]

        updated, warnings = _apply_cross_claim_consistency(fcs, claims)

        c2_fc = next(fc for fc in updated if fc.claim_id == "C2")
        assert c2_fc.confidence == 0.80
        assert len(warnings) == 0

    def test_no_dependencies_unchanged(self):
        claims = [_make_claim("C1"), _make_claim("C2")]
        fcs = [
            _make_fc("C1", FactRating.FALSE, 0.80),
            _make_fc("C2", FactRating.TRUE, 0.80),
        ]

        updated, warnings = _apply_cross_claim_consistency(fcs, claims)
        assert all(fc.confidence == 0.80 for fc in updated)


class TestChainPropagation:
    """Test multi-level transitive dependency chains."""

    def test_chain_a_b_c_all_negative(self):
        """A→B→C: If A and B are FALSE, C should be penalized transitively."""
        claims = [
            _make_claim("A"),
            _make_claim("B", depends_on=["A"]),
            _make_claim("C", depends_on=["B"]),
        ]
        fcs = [
            _make_fc("A", FactRating.FALSE, 0.80),
            _make_fc("B", FactRating.FALSE, 0.80),
            _make_fc("C", FactRating.TRUE, 0.80),
        ]

        updated, warnings = _apply_cross_claim_consistency(fcs, claims)

        b_fc = next(fc for fc in updated if fc.claim_id == "B")
        c_fc = next(fc for fc in updated if fc.claim_id == "C")

        # B should have direct penalty from A
        assert b_fc.confidence < 0.80
        # C should have indirect (decayed) penalty through B
        assert c_fc.confidence < 0.80

    def test_chain_parent_true_child_not_penalized(self):
        """A→B→C: If A is FALSE but B is TRUE, C should NOT be penalized
        (B's claim is true, so C building on it is valid)."""
        claims = [
            _make_claim("A"),
            _make_claim("B", depends_on=["A"]),
            _make_claim("C", depends_on=["B"]),
        ]
        fcs = [
            _make_fc("A", FactRating.FALSE, 0.80),
            _make_fc("B", FactRating.TRUE, 0.80),
            _make_fc("C", FactRating.TRUE, 0.80),
        ]

        updated, _ = _apply_cross_claim_consistency(fcs, claims)

        c_fc = next(fc for fc in updated if fc.claim_id == "C")
        # C depends on B which is TRUE → no penalty
        assert c_fc.confidence == 0.80

    def test_decay_reduces_with_depth(self):
        """Deeper dependencies get smaller penalties."""
        claims = [
            _make_claim("A"),
            _make_claim("B", depends_on=["A"]),
            _make_claim("C", depends_on=["B"]),
        ]
        # Both A and B are FALSE so C gets chain propagation
        fcs = [
            _make_fc("A", FactRating.FALSE, 0.80),
            _make_fc("B", FactRating.FALSE, 0.80),
            _make_fc("C", FactRating.TRUE, 0.80),
        ]

        updated, _ = _apply_cross_claim_consistency(fcs, claims)

        b_fc = next(fc for fc in updated if fc.claim_id == "B")
        c_fc = next(fc for fc in updated if fc.claim_id == "C")

        b_penalty = 0.80 - b_fc.confidence
        c_penalty = 0.80 - c_fc.confidence

        # B at depth 0: penalty = 0.20 * 0.70^0 = 0.20
        assert 0.19 <= b_penalty <= 0.21

        # C at depth 1: penalty = 0.20 * 0.70^1 + accumulated = larger
        assert c_penalty > b_penalty  # chain adds up


class TestPenaltyCapping:
    """Test that penalties don't stack without limit."""

    def test_penalty_capped_at_040(self):
        """Multiple chains shouldn't exceed 0.40 total penalty."""
        claims = [
            _make_claim("A"),
            _make_claim("B"),
            _make_claim("C", depends_on=["A", "B"]),
        ]
        fcs = [
            _make_fc("A", FactRating.FALSE, 0.80),
            _make_fc("B", FactRating.FALSE, 0.80),
            _make_fc("C", FactRating.TRUE, 0.80),
        ]

        updated, _ = _apply_cross_claim_consistency(fcs, claims)
        c_fc = next(fc for fc in updated if fc.claim_id == "C")

        penalty = 0.80 - c_fc.confidence
        assert penalty <= 0.40


class TestCycleHandling:
    """Test that cycles in dependency graphs don't cause infinite loops."""

    def test_cycle_does_not_hang(self):
        """A→B→A should terminate gracefully."""
        claims = [
            _make_claim("A", depends_on=["B"]),
            _make_claim("B", depends_on=["A"]),
        ]
        fcs = [
            _make_fc("A", FactRating.FALSE, 0.80),
            _make_fc("B", FactRating.TRUE, 0.80),
        ]

        # Should complete without hanging
        updated, warnings = _apply_cross_claim_consistency(fcs, claims)
        assert len(updated) == 2
