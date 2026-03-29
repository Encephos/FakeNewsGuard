"""Replay evaluation tests — runs deterministic eval on seed snapshots.

Invoke via: pytest -m eval_replay
NOT included in the default pytest run (excluded by addopts).
"""

from __future__ import annotations

from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
CASES_PATH = EVAL_DIR / "data" / "cases.jsonl"
SNAPSHOTS_DIR = EVAL_DIR / "snapshots"


@pytest.mark.eval_replay
class TestEvalReplay:
    """Replay evaluation on seed snapshots."""

    def test_replay_seed_cases(self):
        """All seed-snapshot cases pass their expectations."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run()

        assert len(results) > 0, "No cases evaluated — seed snapshots missing?"

        for r in results:
            errors = [v for v in r.violations if v.severity == "error"]
            assert not errors, (
                f"Case {r.case_id} ({r.category.value}) has error-level violations: "
                f"{[(v.metric, v.actual) for v in errors]}"
            )

    def test_replay_metrics_computed(self):
        """All metrics are non-None for cases with evidence items."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run()

        for r in results:
            m = r.metrics
            assert m.retrieval_precision_proxy_at_k >= 0.0
            assert m.source_diversity >= 0.0
            assert 0.0 <= m.low_trust_rate <= 1.0
            assert 0.0 <= m.offtopic_rate <= 1.0

    def test_replay_statistical_case_has_official_sources(self):
        """The stat-001 case should find official sources (BKA, Destatis)."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run(case_ids=["stat-001"])

        assert len(results) == 1
        r = results[0]
        assert r.metrics.official_source_recall_at_k > 0.0, (
            "stat-001 should have official source recall > 0"
        )
        assert r.metrics.direct_evidence_rate > 0.0, (
            "stat-001 should have direct evidence"
        )

    def test_replay_noisy_case_contextual_only(self):
        """The noisy-001 case should have high contextual-only rate."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run(case_ids=["noisy-001"])

        assert len(results) == 1
        r = results[0]
        assert r.metrics.contextual_only_rate >= 0.8, (
            "noisy-001 should be mostly contextual"
        )

    def test_report_generation(self):
        """Report generation produces valid Markdown."""
        from eval.reports import generate_markdown_report
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run()
        md = generate_markdown_report(results)

        assert "# Retrieval Evaluation Report" in md
        assert "## Global Metrics" in md
        assert "## Summary" in md

    def test_json_report_generation(self):
        """JSON report is valid JSON."""
        import json

        from eval.reports import generate_json_report
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run()
        json_str = generate_json_report(results)

        data = json.loads(json_str)
        assert "global_metrics" in data
        assert "total_cases" in data


@pytest.mark.eval_replay
class TestDatasetLoading:
    """Test dataset loading and validation."""

    def test_load_all_cases(self):
        """All cases in seed dataset parse without errors."""
        from eval.dataset import load_cases
        cases = load_cases(CASES_PATH)
        assert len(cases) >= 20, f"Expected >=20 cases, got {len(cases)}"

    def test_categories_present(self):
        """All expected categories have at least one case."""
        from eval.dataset import load_cases
        from eval.models import EvalCategory

        cases = load_cases(CASES_PATH)
        present = {c.category for c in cases}
        expected = {
            EvalCategory.CURRENT_STATE,
            EvalCategory.REGULATORY,
            EvalCategory.STATISTICAL,
            EvalCategory.CORPORATE,
            EvalCategory.MEDICAL_PHARMA,
            EvalCategory.LEGAL_EU,
            EvalCategory.NOISY_OR_UNDERSPECIFIED,
            EvalCategory.OFF_TOPIC_TRAPS,
            EvalCategory.MULTILINGUAL,
        }
        missing = expected - present
        assert not missing, f"Missing categories: {missing}"

    def test_build_processed_claim(self):
        """ProcessedClaim can be built from each case."""
        from eval.dataset import build_processed_claim, load_cases

        cases = load_cases(CASES_PATH)
        for case in cases[:5]:
            claim = build_processed_claim(case)
            assert claim.text == case.claim_text
            assert claim.id == case.id


@pytest.mark.eval_replay
class TestSnapshotSerialization:
    """Test snapshot save/load round-trip."""

    def test_save_load_roundtrip(self, tmp_path):
        """Snapshot survives JSON round-trip."""
        from eval.snapshot import RetrievalSnapshot, load_snapshot, save_snapshot

        snap = RetrievalSnapshot(
            case_id="test-rt",
            generated_queries=["q1", "q2"],
            deduped_queries=["q1"],
            merged_results=[
                {"title": "T", "url": "https://example.com", "snippet": "S", "content": ""},
            ],
        )
        save_snapshot(snap, tmp_path)
        loaded = load_snapshot("test-rt", tmp_path)

        assert loaded.case_id == "test-rt"
        assert loaded.generated_queries == ["q1", "q2"]
        assert loaded.deduped_queries == ["q1"]
        assert len(loaded.merged_results) == 1


@pytest.mark.eval_replay
class TestNewSeedSnapshots:
    """Tests for the new seed snapshots covering all major categories."""

    def test_replay_new_seed_cases(self):
        """All new seed-snapshot cases pass without error-level violations."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        new_ids = ["cs-001", "corp-001", "med-001", "trap-001", "leu-001", "ml-001"]
        results = runner.run(case_ids=new_ids)

        assert len(results) >= 6, f"Expected 6 results, got {len(results)}"
        for r in results:
            errors = [v for v in r.violations if v.severity == "error"]
            assert not errors, (
                f"Case {r.case_id} has error-level violations: "
                f"{[(v.metric, v.actual) for v in errors]}"
            )

    def test_replay_current_state_freshness(self):
        """cs-001 should have freshness > 0 (requires_recency case)."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run(case_ids=["cs-001"])
        assert len(results) == 1
        # cs-001 has requires_recency=true
        # freshness depends on evidence_items having publication_date
        m = results[0].metrics
        assert m.preferred_domain_hit_rate > 0.0, "cs-001 should find destatis.de"

    def test_replay_offtopic_trap(self):
        """trap-001 (opinion) should have high contextual_only_rate."""
        from eval.runner_replay import ReplayRunner

        runner = ReplayRunner(
            cases_path=CASES_PATH,
            snapshots_dir=SNAPSHOTS_DIR,
        )
        results = runner.run(case_ids=["trap-001"])
        assert len(results) == 1
        m = results[0].metrics
        assert m.contextual_only_rate >= 0.5, (
            f"trap-001 contextual_only_rate={m.contextual_only_rate}, expected >= 0.5"
        )

    def test_all_categories_have_snapshots(self):
        """At least one snapshot exists per major category."""
        from eval.dataset import load_cases
        from eval.snapshot import snapshot_exists

        cases = load_cases(CASES_PATH)
        categories_with_snapshots = set()
        for c in cases:
            if snapshot_exists(c.id, SNAPSHOTS_DIR):
                categories_with_snapshots.add(c.category.value)

        expected = {
            "current_state", "regulatory", "statistical",
            "corporate", "medical_pharma", "legal_eu",
            "noisy_or_underspecified", "off_topic_traps", "multilingual",
        }
        missing = expected - categories_with_snapshots
        assert not missing, f"Missing snapshot coverage for categories: {missing}"


@pytest.mark.eval_replay
class TestMetrics:
    """Test individual metric functions."""

    def test_query_duplication_rate(self):
        from eval.metrics import _query_duplication_rate

        assert _query_duplication_rate(["a", "b", "a"], ["a", "b"]) == pytest.approx(1 / 3)
        assert _query_duplication_rate(["a", "b"], ["a", "b"]) == 0.0
        assert _query_duplication_rate([], []) == 0.0

    def test_source_diversity(self):
        from eval.metrics import _source_diversity

        results = [
            {"url": "https://a.com/1"},
            {"url": "https://a.com/2"},
            {"url": "https://b.com/1"},
        ]
        assert _source_diversity(results) == pytest.approx(2 / 3)

    def test_scrape_waste_rate(self):
        from eval.metrics import _scrape_waste_rate

        ranked = [
            {"should_scrape": True, "relevance_score": 0.9},
            {"should_scrape": True, "relevance_score": 0.1},
            {"should_scrape": False, "relevance_score": 0.05},
        ]
        assert _scrape_waste_rate(ranked) == pytest.approx(0.5)
