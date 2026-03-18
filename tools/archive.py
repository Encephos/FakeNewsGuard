"""SQLite-basiertes Archiv für abgeschlossene Faktencheck-Analysen.

Speichert vollständige Analyse-Ergebnisse persistent. Verwendet dasselbe
Pattern wie der bestehende ClaimCache (SQLite + WAL-Modus).

Schema:
    analysis_archive (
        id, created_at, input_text, source_url, platform,
        overall_rating, confidence, summary, result_json,
        title, claims_count, techniques_count
    )
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any

from config import ArchiveConfig


class AnalysisArchive:
    """Persistentes Archiv vergangener Faktencheck-Analysen."""

    def __init__(self, config: ArchiveConfig) -> None:
        self.config = config
        self._db_path = config.db_path
        if config.enabled:
            self._init_db()

    # ── Database Setup ───────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_archive (
                    id                TEXT PRIMARY KEY,
                    created_at        REAL NOT NULL,
                    input_text        TEXT NOT NULL DEFAULT '',
                    source_url        TEXT,
                    platform          TEXT,
                    overall_rating    TEXT NOT NULL,
                    confidence        INTEGER NOT NULL,
                    summary           TEXT NOT NULL DEFAULT '',
                    result_json       TEXT NOT NULL,
                    title             TEXT,
                    claims_count      INTEGER DEFAULT 0,
                    techniques_count  INTEGER DEFAULT 0
                )
                """
            )
            # Index für schnelle Sortierung und Filterung
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_archive_created
                ON analysis_archive (created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_archive_rating
                ON analysis_archive (overall_rating)
                """
            )

    @contextmanager
    def _connect(self):
        # Verzeichnis anlegen falls nötig
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Write Operations ─────────────────────────────────────────

    def save(
        self,
        result: dict[str, Any],
        input_text: str = "",
        source_url: str | None = None,
        platform: str | None = None,
        title: str | None = None,
    ) -> str:
        """Speichere ein Analyse-Ergebnis im Archiv.

        Args:
            result: Das vollständige AnalysisResult-Dict (Frontend-Format).
            input_text: Der ursprüngliche Eingabetext (wird auf 500 Zeichen gekürzt).
            source_url: Die analysierte URL (falls vorhanden).
            platform: Die erkannte Plattform (twitter, article, etc.).
            title: Titel des Inhalts oder erste Zeichen des Texts.

        Returns:
            Die generierte Archiv-ID.
        """
        if not self.config.enabled:
            return ""

        import time

        archive_id = str(uuid.uuid4())

        # Titel ableiten falls nicht gesetzt
        if not title:
            title = (result.get("summary", "") or input_text)[:120]

        # Metadaten aus dem Result extrahieren
        claims_count = len(result.get("claims", []))
        techniques_count = len(result.get("rhetoric", []))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_archive
                    (id, created_at, input_text, source_url, platform,
                     overall_rating, confidence, summary, result_json,
                     title, claims_count, techniques_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    archive_id,
                    time.time(),
                    input_text[:500],  # Gekürzt für Übersicht
                    source_url,
                    platform,
                    result.get("overall_rating", "?"),
                    result.get("confidence", 0),
                    result.get("summary", ""),
                    json.dumps(result, ensure_ascii=False),
                    title,
                    claims_count,
                    techniques_count,
                ),
            )

        # Automatisches Aufräumen (max. Einträge begrenzen)
        if self.config.max_entries > 0:
            self._enforce_max_entries()

        return archive_id

    def delete(self, archive_id: str) -> bool:
        """Lösche einen Archiv-Eintrag. Gibt True zurück wenn gefunden."""
        if not self.config.enabled:
            return False

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM analysis_archive WHERE id = ?", (archive_id,)
            )
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        """Lösche alle Archiv-Einträge. Gibt Anzahl gelöschter zurück."""
        if not self.config.enabled:
            return 0

        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM analysis_archive")
            return cursor.rowcount

    # ── Read Operations ──────────────────────────────────────────

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        rating_filter: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Liste Archiv-Einträge auf (neueste zuerst).

        Args:
            limit: Max. Anzahl Einträge (1-100).
            offset: Überspringe die ersten N Einträge (Pagination).
            rating_filter: Optional – nur Einträge mit dieser Bewertung.
            search: Optional – Volltextsuche in Titel, Zusammenfassung, URL.

        Returns:
            { items: [...], total: int, limit: int, offset: int }
        """
        if not self.config.enabled:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}

        limit = max(1, min(limit, 100))

        where_clauses: list[str] = []
        params: list[Any] = []

        if rating_filter:
            where_clauses.append("overall_rating = ?")
            params.append(rating_filter)

        if search:
            where_clauses.append(
                "(title LIKE ? OR summary LIKE ? OR source_url LIKE ? OR input_text LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        with self._connect() as conn:
            # Total count
            total = conn.execute(
                f"SELECT COUNT(*) FROM analysis_archive {where_sql}", params
            ).fetchone()[0]

            # Paginated results (ohne das große result_json)
            rows = conn.execute(
                f"""
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, title,
                       claims_count, techniques_count
                FROM analysis_archive
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()

        items = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "input_text": row["input_text"],
                "source_url": row["source_url"],
                "platform": row["platform"],
                "overall_rating": row["overall_rating"],
                "confidence": row["confidence"],
                "summary": row["summary"],
                "title": row["title"],
                "claims_count": row["claims_count"],
                "techniques_count": row["techniques_count"],
            }
            for row in rows
        ]

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, archive_id: str) -> dict[str, Any] | None:
        """Hole einen vollständigen Archiv-Eintrag mit result_json.

        Returns:
            Das vollständige Dict inkl. `result` (geparst), oder None.
        """
        if not self.config.enabled:
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, result_json,
                       title, claims_count, techniques_count
                FROM analysis_archive
                WHERE id = ?
                """,
                (archive_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "input_text": row["input_text"],
            "source_url": row["source_url"],
            "platform": row["platform"],
            "overall_rating": row["overall_rating"],
            "confidence": row["confidence"],
            "summary": row["summary"],
            "result": json.loads(row["result_json"]),
            "title": row["title"],
            "claims_count": row["claims_count"],
            "techniques_count": row["techniques_count"],
        }

    def stats(self) -> dict[str, Any]:
        """Statistiken über das Archiv."""
        if not self.config.enabled:
            return {"enabled": False}

        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]

            rating_counts = {}
            for row in conn.execute(
                "SELECT overall_rating, COUNT(*) as cnt FROM analysis_archive GROUP BY overall_rating"
            ):
                rating_counts[row["overall_rating"]] = row["cnt"]

            avg_confidence = conn.execute(
                "SELECT AVG(confidence) FROM analysis_archive"
            ).fetchone()[0]

        return {
            "enabled": True,
            "total_entries": total,
            "rating_distribution": rating_counts,
            "average_confidence": round(avg_confidence or 0, 1),
            "max_entries": self.config.max_entries,
        }

    # ── Maintenance ──────────────────────────────────────────────

    def _enforce_max_entries(self) -> None:
        """Lösche älteste Einträge wenn max_entries überschritten."""
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]

            if count > self.config.max_entries:
                excess = count - self.config.max_entries
                conn.execute(
                    """
                    DELETE FROM analysis_archive
                    WHERE id IN (
                        SELECT id FROM analysis_archive
                        ORDER BY created_at ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
