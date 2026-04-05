"""Tests for verdict accuracy metrics and error categorization."""

from __future__ import annotations

import pytest

from eval.metrics import (
    _verdict_distance,
    _topic_relevance_avg,
    build_confusion_matrix,
    compute_verdict_metrics,
    compute_verdict_accuracy_report,
)
from eval.models import CaseMetrics, CaseResult, EvalCategory, VerdictAccuracyReport
from eval.error_analysis import (
    ErrorCategory,
    analyze_errors,
    detect_claim_orphaned,
    detect_cross_claim_inconsistent,
    detect_evidence_off_topic,
    detect_verdict_overcautious,
    detect_verdict_overconfident,
)


# ── _verdict_distance ───────────────────────────────────────────────────────


class TestVerdictDistance:
    def test_exact_match(self):
        assert _verdict_distance("TRUE", "TRUE") == 0

    def test_one_step(self):
        assert _verdict_distance("TRUE", "MOSTLY_TRUE") == 1

    def test_two_steps(self):
        assert _verdict_distance("TRUE", "MISLEADING") == 2

    def test_full_scale(self):
        assert _verdict_distance("TRUE", "FALSE") == 4

    def test_symmetric(self):
        assert _verdict_distance("FALSE", "TRUE") == 4

    def test_unverifiable_exact(self):
        assert _verdict_distance("UNVERIFIABLE", "UNVERIFIABLE") == 0

    def test_unverifiable_vs_other(self):
        assert _verdict_distance("UNVERIFIABLE", "TRUE") == 99

    def test_case_insensitive(self):
        assert _verdict_distance("true", "mostly_true") == 1

    def test_unknown_rating(self):
        assert _verdict_distance("INVALID", "TRUE") == 99


# ── _topic_relevance_avg ────────────────────────────────────────────────────


class TestTopicRelevanceAvg:
    def test_empty(self):
        assert _topic_relevance_avg([]) is None

    def test_default_scores(self):
        items = [{"title": "a"}, {"title": "b"}]
        assert _topic_relevance_avg(items, k=2) == 1.0

    def test_explicit_scores(self):
        items = [
            {"topic_relevance_score": 0.8},
            {"topic_relevance_score": 0.2},
        ]
        assert _topic_relevance_avg(items, k=2) == pytest.approx(0.5)

    def test_respects_k(self):
        items = [
            {"topic_relevance_score": 1.0},
            {"topic_relevance_score": 0.0},
            {"topic_relevance_score": 0.0},
        ]
        assert _topic_relevance_avg(items, k=1) == 1.0


# ── compute_verdict_metrics ─────────────────────────────────────────────────


class TestComputeVerdictMetrics:
    def test_exact_match(self):
        m = compute_verdict_metrics("TRUE", "TRUE", [])
        assert m["verdict_accuracy"] == 1.0
        assert m["verdict_within_one_step"] is True
        assert m["verdict_distance"] == 0

    def test_wrong_verdict(self):
        m = compute_verdict_metrics("FALSE", "TRUE", [])
        assert m["verdict_accuracy"] == 0.0
        assert m["verdict_within_one_step"] is False
        assert m["verdict_distance"] == 4

    def test_within_one_step(self):
        m = compute_verdict_metrics("MOSTLY_TRUE", "TRUE", [{"topic_relevance_score": 0.6}])
        assert m["verdict_within_one_step"] is True
        assert m["topic_relevance_avg"] == pytest.approx(0.6)


# ── build_confusion_matrix ──────────────────────────────────────────────────


class TestConfusionMatrix:
    def test_basic(self):
        pairs = [("TRUE", "TRUE"), ("FALSE", "TRUE"), ("FALSE", "FALSE")]
        matrix = build_confusion_matrix(pairs)
        assert matrix["TRUE"]["TRUE"] == 1
        assert matrix["TRUE"]["FALSE"] == 1
        assert matrix["FALSE"]["FALSE"] == 1
        assert matrix["FALSE"]["TRUE"] == 0

    def test_empty(self):
        matrix = build_confusion_matrix([])
        assert matrix["TRUE"]["TRUE"] == 0


# ── compute_verdict_accuracy_report ─────────────────────────────────────────


class TestVerdictAccuracyReport:
    @staticmethod
    def _make_result(case_id, accuracy, within_one, distance, topic_rel=None):
        return CaseResult(
            case_id=case_id,
            category=EvalCategory.REGULATORY,
            metrics=CaseMetrics(
                verdict_accuracy=accuracy,
                verdict_within_one_step=within_one,
                verdict_distance=distance,
                topic_relevance_avg=topic_rel,
            ),
        )

    def test_all_correct(self):
        results = [
            self._make_result("c1", 1.0, True, 0),
            self._make_result("c2", 1.0, True, 0),
        ]
        report = compute_verdict_accuracy_report(results)
        assert report.exact_match_rate == 1.0
        assert report.within_one_step_rate == 1.0
        assert report.avg_verdict_distance == 0.0

    def test_mixed(self):
        results = [
            self._make_result("c1", 1.0, True, 0, 0.9),
            self._make_result("c2", 0.0, True, 1, 0.5),
            self._make_result("c3", 0.0, False, 3, 0.1),
        ]
        report = compute_verdict_accuracy_report(results)
        assert report.exact_match_count == 1
        assert report.exact_match_rate == pytest.approx(1 / 3)
        assert report.within_one_step_count == 2
        assert report.avg_verdict_distance == pytest.approx(4 / 3)
        assert report.avg_topic_relevance == pytest.approx(0.5)

    def test_no_verdicts(self):
        results = [
            CaseResult(
                case_id="c1",
                category=EvalCategory.REGULATORY,
                metrics=CaseMetrics(),
            )
        ]
        report = compute_verdict_accuracy_report(results)
        assert report.cases_with_expected_verdict == 0
        assert report.exact_match_rate == 0.0


# ── Error Analysis ──────────────────────────────────────────────────────────


class TestDetectEvidenceOffTopic:
    def test_low_relevance(self):
        items = [{"topic_relevance_score": 0.1}] * 5
        err = detect_evidence_off_topic("c1", items)
        assert err is not None
        assert err.category == ErrorCategory.EVIDENCE_OFF_TOPIC
        assert err.severity == "error"

    def test_borderline(self):
        items = [{"topic_relevance_score": 0.2}] * 5
        err = detect_evidence_off_topic("c1", items)
        assert err is not None
        assert err.severity == "warning"

    def test_good_relevance(self):
        items = [{"topic_relevance_score": 0.8}] * 5
        assert detect_evidence_off_topic("c1", items) is None

    def test_empty(self):
        assert detect_evidence_off_topic("c1", []) is None


class TestDetectClaimOrphaned:
    def test_orphaned(self):
        err = detect_claim_orphaned(
            "c1", "cl1", "some unrelated text",
            topic_keywords=["Hannover", "Verkehr"],
            topic_entities=["Stadtrat"],
        )
        assert err is not None
        assert err.category == ErrorCategory.CLAIM_ORPHANED

    def test_connected(self):
        err = detect_claim_orphaned(
            "c1", "cl1", "Hannover plant neue Verkehrsregeln",
            topic_keywords=["Hannover", "Verkehr"],
            topic_entities=["Stadtrat"],
        )
        assert err is None

    def test_no_topic(self):
        err = detect_claim_orphaned("c1", "cl1", "anything", [], [])
        assert err is None


class TestDetectVerdictOvercautious:
    def test_overcautious(self):
        err = detect_verdict_overcautious("c1", "UNVERIFIABLE", "TRUE", 0.3, 5)
        assert err is not None
        assert err.category == ErrorCategory.VERDICT_OVERCAUTIOUS

    def test_correct_unverifiable(self):
        assert detect_verdict_overcautious("c1", "UNVERIFIABLE", "UNVERIFIABLE", 0.3, 5) is None

    def test_not_unverifiable(self):
        assert detect_verdict_overcautious("c1", "TRUE", "TRUE", 0.9, 5) is None

    def test_thin_evidence(self):
        assert detect_verdict_overcautious("c1", "UNVERIFIABLE", "TRUE", 0.3, 1) is None


class TestDetectVerdictOverconfident:
    def test_overconfident(self):
        err = detect_verdict_overconfident("c1", "TRUE", "FALSE", 0.85, 4)
        assert err is not None
        assert err.category == ErrorCategory.VERDICT_OVERCONFIDENT
        assert err.severity == "error"

    def test_close_verdict(self):
        assert detect_verdict_overconfident("c1", "TRUE", "MOSTLY_TRUE", 0.9, 1) is None

    def test_low_confidence(self):
        assert detect_verdict_overconfident("c1", "TRUE", "FALSE", 0.3, 4) is None


class TestDetectCrossClaimInconsistent:
    def test_inconsistent(self):
        err = detect_cross_claim_inconsistent(
            "c1", "cl_a", "FALSE", "cl_b", "TRUE", "policy_sanction"
        )
        assert err is not None
        assert err.category == ErrorCategory.CROSS_CLAIM_INCONSISTENT

    def test_consistent(self):
        err = detect_cross_claim_inconsistent(
            "c1", "cl_a", "TRUE", "cl_b", "TRUE", "policy_sanction"
        )
        assert err is None

    def test_both_negative(self):
        err = detect_cross_claim_inconsistent(
            "c1", "cl_a", "FALSE", "cl_b", "FALSE", "policy_sanction"
        )
        assert err is None


class TestAnalyzeErrors:
    def test_aggregation(self):
        entries = [
            detect_evidence_off_topic("c1", [{"topic_relevance_score": 0.1}] * 5),
            detect_verdict_overconfident("c2", "TRUE", "FALSE", 0.9, 4),
        ]
        entries = [e for e in entries if e is not None]
        report = analyze_errors(entries)
        assert report.total_errors == 2
        assert report.by_category["EVIDENCE_OFF_TOPIC"] == 1
        assert report.by_category["VERDICT_OVERCONFIDENT"] == 1
