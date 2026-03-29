"""Confidence-Calibration-Tracker – Brier Scores und Reliability-Diagramme.

Speichert Vorhersagen (claim_id, confidence, rating) und Ground-Truth-Labels.
Berechnet Brier Scores pro Bucket (Reliability Diagram) und gesamt.

Storage: SQLite (gleicher Ansatz wie ClaimCache – WAL-Modus, thread-safe).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CalibrationBucket:
    """Ein Bucket im Reliability-Diagramm."""

    bin_start: float
    bin_end: float
    predicted_mean: float  # Durchschnittliche vorhergesagte Confidence
    observed_rate: float  # Anteil korrekter Vorhersagen
    count: int  # Anzahl Vorhersagen in diesem Bucket


@dataclass
class CalibrationReport:
    """Kalibrierungsbericht mit Brier Score und Reliability-Diagramm."""

    brier_score: float  # 0 = perfekt, 1 = schlecht
    total_predictions: int
    correct_predictions: int
    accuracy: float
    buckets: list[CalibrationBucket] = field(default_factory=list)


class CalibrationTracker:
    """SQLite-basierter Tracker für Confidence-Kalibrierung.

    Schema:
        predictions: claim_id, confidence, rating, is_correct (nullable), timestamp
    """

    def __init__(self, db_path: str = "data/calibration.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    analysis_id TEXT DEFAULT '',
                    confidence REAL NOT NULL,
                    rating TEXT NOT NULL,
                    is_correct INTEGER,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_predictions_analysis
                ON predictions(analysis_id)
            """)
            conn.commit()

    def record_prediction(
        self,
        claim_id: str,
        confidence: float,
        rating: str,
        analysis_id: str = "",
    ) -> int:
        """Speichere eine Vorhersage. Gibt die Prediction-ID zurück."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO predictions (claim_id, analysis_id, confidence, rating) "
                "VALUES (?, ?, ?, ?)",
                (claim_id, analysis_id, confidence, rating),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def record_ground_truth(self, claim_id: str, is_correct: bool) -> int:
        """Setze Ground-Truth für den neuesten Eintrag eines Claims.

        Returns:
            Anzahl aktualisierter Zeilen.
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE predictions SET is_correct = ? "
                "WHERE id = ("
                "  SELECT id FROM predictions "
                "  WHERE claim_id = ? AND is_correct IS NULL "
                "  ORDER BY created_at DESC LIMIT 1"
                ")",
                (int(is_correct), claim_id),
            )
            conn.commit()
            return cursor.rowcount

    def compute_report(self, n_buckets: int = 10) -> CalibrationReport:
        """Berechne Brier Score und Reliability-Diagramm.

        Nur Vorhersagen mit Ground-Truth (is_correct IS NOT NULL) fließen ein.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT confidence, is_correct FROM predictions "
                "WHERE is_correct IS NOT NULL "
                "ORDER BY confidence",
            ).fetchall()

        if not rows:
            return CalibrationReport(
                brier_score=0.0,
                total_predictions=0,
                correct_predictions=0,
                accuracy=0.0,
            )

        # Brier Score: mean((confidence - is_correct)^2)
        total = len(rows)
        correct = sum(1 for _, ic in rows if ic == 1)
        brier = sum((conf - ic) ** 2 for conf, ic in rows) / total

        # Reliability Diagram: Buckets
        bucket_size = 1.0 / n_buckets
        buckets: list[CalibrationBucket] = []
        for b in range(n_buckets):
            lo = b * bucket_size
            hi = lo + bucket_size
            in_bucket = [(conf, ic) for conf, ic in rows if lo <= conf < hi]
            if not in_bucket:
                continue
            pred_mean = sum(c for c, _ in in_bucket) / len(in_bucket)
            obs_rate = sum(ic for _, ic in in_bucket) / len(in_bucket)
            buckets.append(CalibrationBucket(
                bin_start=lo,
                bin_end=hi,
                predicted_mean=round(pred_mean, 3),
                observed_rate=round(obs_rate, 3),
                count=len(in_bucket),
            ))

        return CalibrationReport(
            brier_score=round(brier, 4),
            total_predictions=total,
            correct_predictions=correct,
            accuracy=round(correct / total, 4) if total else 0.0,
            buckets=buckets,
        )

    def stats(self) -> dict:
        """Zusammenfassung: Gesamtzahl, davon mit/ohne Ground Truth."""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            labeled = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE is_correct IS NOT NULL"
            ).fetchone()[0]
        return {"total": total, "labeled": labeled, "unlabeled": total - labeled}

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
