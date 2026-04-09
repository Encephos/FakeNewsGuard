"""Tests for the outcome logger."""

import json
import sqlite3
from pathlib import Path

import pytest

from tools.outcome_logger import _get_db, log_outcome


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary database path."""
    return tmp_path / "test_outcomes.db"


class TestOutcomeLogger:
    def test_log_outcome_creates_record(self, tmp_db):
        log_outcome(
            claim_id="C1",
            claim_text="Test claim",
            claim_type="FACTUAL",
            verdict_rating="TRUE",
            verdict_confidence=0.85,
            evidence_quality=0.72,
            consensus="agreeing",
            consensus_score=0.9,
            db_path=tmp_db,
        )

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM outcomes").fetchall()
        conn.close()

        assert len(rows) == 1
        row = dict(rows[0])
        assert row["claim_id"] == "C1"
        assert row["verdict_rating"] == "TRUE"
        assert row["verdict_confidence"] == 0.85
        assert row["evidence_quality"] == 0.72
        assert row["consensus_score"] == 0.9

    def test_log_multiple_outcomes(self, tmp_db):
        for i in range(5):
            log_outcome(
                claim_id=f"C{i}",
                claim_text=f"Claim {i}",
                verdict_rating="TRUE",
                verdict_confidence=0.80,
                db_path=tmp_db,
            )

        conn = sqlite3.connect(str(tmp_db))
        count = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        conn.close()
        assert count == 5

    def test_log_with_json_fields(self, tmp_db):
        log_outcome(
            claim_id="C1",
            claim_text="Test",
            route_domains=["statistical", "regulatory"],
            route_sources=["eurostat", "destatis"],
            queries_used=["query 1", "query 2"],
            calibration_reasons=["Ceiling 0.88", "Penalty -0.10"],
            db_path=tmp_db,
        )

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM outcomes").fetchone())
        conn.close()

        assert json.loads(row["route_domains"]) == ["statistical", "regulatory"]
        assert json.loads(row["queries_used"]) == ["query 1", "query 2"]
        assert json.loads(row["calibration_reasons"]) == ["Ceiling 0.88", "Penalty -0.10"]

    def test_log_truncates_long_claim_text(self, tmp_db):
        long_text = "A" * 1000
        log_outcome(
            claim_id="C1",
            claim_text=long_text,
            db_path=tmp_db,
        )

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM outcomes").fetchone())
        conn.close()

        assert len(row["claim_text"]) == 500

    def test_log_does_not_raise_on_failure(self, tmp_path):
        """Logger should never raise — best-effort only."""
        bad_path = tmp_path / "readonly" / "db.sqlite"
        # Don't create the directory — write will fail
        log_outcome(
            claim_id="C1",
            claim_text="Test",
            db_path=bad_path,
        )
        # Should not raise
