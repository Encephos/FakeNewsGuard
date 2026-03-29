"""Tests für den Confidence-Calibration-Tracker."""

from __future__ import annotations

import os
import tempfile

import pytest

from tools.calibration_tracker import CalibrationTracker


@pytest.fixture
def tracker(tmp_path):
    """Erstelle einen Tracker mit temporärer DB."""
    db_path = str(tmp_path / "test_calibration.db")
    t = CalibrationTracker(db_path=db_path)
    yield t
    t.close()


class TestCalibrationTracker:
    def test_record_and_stats(self, tracker):
        """Predictions werden gespeichert und in Stats gezählt."""
        tracker.record_prediction("C1", 0.8, "TRUE")
        tracker.record_prediction("C2", 0.6, "MISLEADING")
        stats = tracker.stats()
        assert stats["total"] == 2
        assert stats["labeled"] == 0
        assert stats["unlabeled"] == 2

    def test_ground_truth_updates_latest(self, tracker):
        """Ground-Truth wird auf den neuesten Eintrag des Claims gesetzt."""
        tracker.record_prediction("C1", 0.8, "TRUE")
        tracker.record_prediction("C1", 0.9, "TRUE")  # neuerer Eintrag
        updated = tracker.record_ground_truth("C1", True)
        assert updated == 1
        stats = tracker.stats()
        assert stats["labeled"] == 1

    def test_ground_truth_missing_claim(self, tracker):
        """Ground-Truth für unbekannten Claim gibt 0 zurück."""
        updated = tracker.record_ground_truth("UNKNOWN", True)
        assert updated == 0

    def test_brier_score_perfect(self, tracker):
        """Perfekte Kalibrierung → Brier Score ≈ 0."""
        # Confidence 0.9, korrekt → (0.9 - 1)^2 = 0.01
        tracker.record_prediction("C1", 0.9, "TRUE")
        tracker.record_ground_truth("C1", True)
        # Confidence 0.1, falsch → (0.1 - 0)^2 = 0.01
        tracker.record_prediction("C2", 0.1, "FALSE")
        tracker.record_ground_truth("C2", False)

        report = tracker.compute_report()
        assert report.total_predictions == 2
        assert report.brier_score == pytest.approx(0.01, abs=0.001)

    def test_brier_score_worst(self, tracker):
        """Komplett falsche Kalibrierung → Brier Score = 1.0."""
        tracker.record_prediction("C1", 1.0, "TRUE")
        tracker.record_ground_truth("C1", False)

        report = tracker.compute_report()
        assert report.brier_score == pytest.approx(1.0, abs=0.001)

    def test_empty_report(self, tracker):
        """Leerer Tracker → Brier Score 0, keine Buckets."""
        report = tracker.compute_report()
        assert report.total_predictions == 0
        assert report.brier_score == 0.0
        assert report.buckets == []

    def test_reliability_buckets(self, tracker):
        """Buckets gruppieren Predictions nach Confidence-Intervall."""
        # 5 Predictions in Bucket 0.8-0.9, davon 3 korrekt
        for i in range(5):
            tracker.record_prediction(f"C{i}", 0.85, "TRUE")
            tracker.record_ground_truth(f"C{i}", i < 3)

        report = tracker.compute_report(n_buckets=10)
        assert len(report.buckets) == 1
        bucket = report.buckets[0]
        assert bucket.bin_start == pytest.approx(0.8)
        assert bucket.bin_end == pytest.approx(0.9)
        assert bucket.count == 5
        assert bucket.observed_rate == pytest.approx(0.6, abs=0.01)

    def test_unlabeled_excluded_from_report(self, tracker):
        """Predictions ohne Ground-Truth fließen nicht in den Brier Score ein."""
        tracker.record_prediction("C1", 0.9, "TRUE")  # kein Ground Truth
        tracker.record_prediction("C2", 0.8, "TRUE")
        tracker.record_ground_truth("C2", True)

        report = tracker.compute_report()
        assert report.total_predictions == 1  # nur C2

    def test_analysis_id_stored(self, tracker):
        """analysis_id wird korrekt gespeichert."""
        pid = tracker.record_prediction("C1", 0.7, "MISLEADING", analysis_id="abc123")
        assert pid > 0
