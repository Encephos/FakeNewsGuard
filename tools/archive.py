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

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

from config import ArchiveConfig


def _input_hash(text: str = "", url: str = "") -> str:
    """Stabiler Lookup-Key: URL hat Vorrang vor Text."""
    raw = (url.strip().lower() if url else text.strip().lower())
    return hashlib.sha256(raw.encode()).hexdigest()


class AnalysisArchive:
    """Persistentes Archiv vergangener Faktencheck-Analysen."""

    _placeholder = "?"

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
                    techniques_count  INTEGER DEFAULT 0,
                    input_hash        TEXT
                )
                """
            )
            # Migration: input_hash zu bestehenden Datenbanken hinzufügen
            try:
                conn.execute("ALTER TABLE analysis_archive ADD COLUMN input_hash TEXT")
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

            # Indices für schnelle Sortierung und Filterung
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_archive_hash
                ON analysis_archive (input_hash)
                """
            )
            # Composite-Index: Rating-Filter + Sortierung (häufigste Query)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_archive_rating_created
                ON analysis_archive (overall_rating, created_at DESC)
                """
            )

            # FTS5 Virtual Table für performante Volltextsuche
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                    title, summary, source_url, input_text,
                    content=analysis_archive,
                    content_rowid=rowid
                )
                """
            )

            # archive_shares – Token-basierte öffentliche Share-Links
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_shares (
                    id          TEXT PRIMARY KEY,
                    archive_id  TEXT NOT NULL REFERENCES analysis_archive(id) ON DELETE CASCADE,
                    token       TEXT UNIQUE NOT NULL,
                    created_by  TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL,
                    view_count  INTEGER DEFAULT 0,
                    allow_embed INTEGER DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_token ON archive_shares(token)"
            )

            # FTS-Index initial befüllen (idempotent: nur wenn leer)
            fts_count = conn.execute("SELECT COUNT(*) FROM archive_fts").fetchone()[0]
            if fts_count == 0:
                main_count = conn.execute("SELECT COUNT(*) FROM analysis_archive").fetchone()[0]
                if main_count > 0:
                    conn.execute(
                        """
                        INSERT INTO archive_fts(rowid, title, summary, source_url, input_text)
                        SELECT rowid, COALESCE(title, ''), COALESCE(summary, ''),
                               COALESCE(source_url, ''), COALESCE(input_text, '')
                        FROM analysis_archive
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

    def find_duplicate(self, text: str = "", url: str = "") -> dict[str, Any] | None:
        """Suche nach einem bereits analysierten identischen Input.

        Gibt den vollständigen Archiv-Eintrag zurück (inkl. ``result``),
        oder None wenn kein Treffer.  URL hat Vorrang vor Text.
        """
        if not self.config.enabled or (not text and not url):
            return None

        key = _input_hash(text=text, url=url)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, result_json,
                       title, claims_count, techniques_count
                FROM analysis_archive
                WHERE input_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (key,),
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

        lookup_hash = _input_hash(text=input_text, url=source_url or "")

        input_short = input_text[:500]  # Gekürzt für Übersicht
        summary_text = result.get("summary", "")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_archive
                    (id, created_at, input_text, source_url, platform,
                     overall_rating, confidence, summary, result_json,
                     title, claims_count, techniques_count, input_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    archive_id,
                    time.time(),
                    input_short,
                    source_url,
                    platform,
                    result.get("overall_rating_key") or result.get("overall_rating", "?"),
                    result.get("confidence", 0),
                    summary_text,
                    json.dumps(result, ensure_ascii=False),
                    title,
                    claims_count,
                    techniques_count,
                    lookup_hash,
                ),
            )
            # FTS-Index synchron aktualisieren
            rowid = conn.execute(
                "SELECT rowid FROM analysis_archive WHERE id = ?", (archive_id,)
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO archive_fts(rowid, title, summary, source_url, input_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rowid, title or "", summary_text, source_url or "", input_short),
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
            # FTS-Eintrag vor dem Löschen entfernen
            row = conn.execute(
                "SELECT rowid FROM analysis_archive WHERE id = ?", (archive_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT INTO archive_fts(archive_fts, rowid, title, summary, source_url, input_text) "
                    "SELECT 'delete', rowid, COALESCE(title,''), COALESCE(summary,''), "
                    "COALESCE(source_url,''), COALESCE(input_text,'') "
                    "FROM analysis_archive WHERE id = ?",
                    (archive_id,),
                )
            cursor = conn.execute(
                "DELETE FROM analysis_archive WHERE id = ?", (archive_id,)
            )
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        """Lösche alle Archiv-Einträge. Gibt Anzahl gelöschter zurück."""
        if not self.config.enabled:
            return 0

        with self._connect() as conn:
            # FTS-Index komplett leeren
            conn.execute("DELETE FROM archive_fts")
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
            where_clauses.append("a.overall_rating = ?")
            params.append(rating_filter)

        # FTS5-basierte Volltextsuche (O(log n) statt O(n) LIKE-Scan)
        fts_join = ""
        if search:
            fts_join = "JOIN archive_fts ON a.rowid = archive_fts.rowid"
            where_clauses.append("archive_fts MATCH ?")
            # FTS5-Syntax: Begriffe mit OR verknüpfen für Teilwort-Suche
            fts_query = " OR ".join(f'"{term}"' for term in search.split() if term)
            params.append(fts_query)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        with self._connect() as conn:
            # Total count
            total = conn.execute(
                f"SELECT COUNT(*) FROM analysis_archive a {fts_join} {where_sql}", params
            ).fetchone()[0]

            # Paginated results (ohne das große result_json)
            rows = conn.execute(
                f"""
                SELECT a.id, a.created_at, a.input_text, a.source_url, a.platform,
                       a.overall_rating, a.confidence, a.summary, a.title,
                       a.claims_count, a.techniques_count
                FROM analysis_archive a
                {fts_join}
                {where_sql}
                ORDER BY a.created_at DESC
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

    def count_analyses(self) -> dict[str, int]:
        """Zähle Analysen: gesamt und letzte 30 Tage."""
        if not self.config.enabled:
            return {"total": 0, "last_30_days": 0}
        import time
        cutoff = time.time() - 30 * 86400
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive WHERE created_at > ?",
                (cutoff,),
            ).fetchone()[0]
        return {"total": total, "last_30_days": recent}

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

    def analytics_data(self, days: int = 30) -> dict[str, Any]:
        """Aggregated analytics for the admin dashboard."""
        KNOWN_RATINGS = ["TRUE", "MOSTLY_TRUE", "MISLEADING", "MOSTLY_FALSE", "FALSE", "UNVERIFIABLE"]
        empty_histogram = [{"bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "count": 0} for i in range(10)]

        if not self.config.enabled:
            return {
                "verdict_distribution": {k: 0 for k in KNOWN_RATINGS},
                "confidence_histogram": empty_histogram,
                "top_domains": [],
                "analyses_per_day": [],
                "period_days": days,
            }

        import time
        from datetime import date, timedelta
        from urllib.parse import urlparse

        cutoff = time.time() - days * 86400

        with self._connect() as conn:
            # 1. Verdict distribution
            verdict_distribution: dict[str, int] = {k: 0 for k in KNOWN_RATINGS}
            for row in conn.execute(
                "SELECT overall_rating, COUNT(*) as cnt FROM analysis_archive "
                "WHERE created_at > ? GROUP BY overall_rating",
                (cutoff,),
            ):
                verdict_distribution[row["overall_rating"]] = row["cnt"]

            # 2. Confidence histogram (confidence is INTEGER 0-100)
            buckets = [0] * 10
            for row in conn.execute(
                "SELECT confidence FROM analysis_archive WHERE created_at > ?",
                (cutoff,),
            ):
                idx = min(row["confidence"] // 10, 9)
                buckets[idx] += 1
            confidence_histogram = [
                {"bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "count": buckets[i]}
                for i in range(10)
            ]

            # 3. Top domains (extract from source_url in Python)
            domain_counts: dict[str, int] = {}
            for row in conn.execute(
                "SELECT source_url FROM analysis_archive "
                "WHERE created_at > ? AND source_url IS NOT NULL",
                (cutoff,),
            ):
                netloc = urlparse(row["source_url"]).netloc
                domain = netloc[4:] if netloc.startswith("www.") else netloc
                if domain:
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
            top_domains = [
                {"domain": d, "count": c, "avg_tier": 0}
                for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])[:15]
            ]

            # 4. Analyses per day
            day_map: dict[str, int] = {}
            for row in conn.execute(
                "SELECT date(created_at, 'unixepoch') as day, COUNT(*) as cnt "
                "FROM analysis_archive WHERE created_at > ? GROUP BY day ORDER BY day",
                (cutoff,),
            ):
                day_map[row["day"]] = row["cnt"]

        # Fill missing days with 0 so the chart has no gaps
        start_date = date.fromtimestamp(cutoff)
        end_date = date.today()
        analyses_per_day: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            analyses_per_day.append({"date": current.isoformat(), "count": day_map.get(current.isoformat(), 0)})
            current += timedelta(days=1)

        return {
            "verdict_distribution": verdict_distribution,
            "confidence_histogram": confidence_histogram,
            "top_domains": top_domains,
            "analyses_per_day": analyses_per_day,
            "period_days": days,
        }

    # ── Share-Links ──────────────────────────────────────────────

    def create_share(
        self,
        archive_id: str,
        created_by: str,
        expires_days: int | None = None,
        allow_embed: bool = False,
    ) -> dict[str, Any]:
        """Erstelle einen öffentlichen Share-Link für einen Archiv-Eintrag."""
        share_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(16)
        now = time.time()
        expires_at = (now + expires_days * 86400) if expires_days else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO archive_shares
                    (id, archive_id, token, created_by, created_at, expires_at, allow_embed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (share_id, archive_id, token, created_by, now, expires_at,
                 1 if allow_embed else 0),
            )

        return {
            "id": share_id,
            "archive_id": archive_id,
            "token": token,
            "created_by": created_by,
            "created_at": now,
            "expires_at": expires_at,
            "view_count": 0,
            "allow_embed": allow_embed,
        }

    def get_share_by_token(self, token: str) -> dict[str, Any] | None:
        """Hole Share-Eintrag per Token; prüft Ablauf und inkrementiert view_count."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM archive_shares WHERE token = ?", (token,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] is not None and row["expires_at"] < time.time():
                return None
            conn.execute(
                "UPDATE archive_shares SET view_count = view_count + 1 WHERE token = ?",
                (token,),
            )
            updated = conn.execute(
                "SELECT * FROM archive_shares WHERE token = ?", (token,)
            ).fetchone()
        return dict(updated)

    def delete_share(self, token: str, user_id: str) -> bool:
        """Lösche einen Share-Link. Nur der Ersteller darf löschen."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT created_by FROM archive_shares WHERE token = ?", (token,)
            ).fetchone()
            if row is None:
                return False
            if row["created_by"] != user_id:
                return False
            conn.execute("DELETE FROM archive_shares WHERE token = ?", (token,))
        return True

    def list_shares_for_archive(self, archive_id: str) -> list[dict[str, Any]]:
        """Liste alle Share-Links für einen Archiv-Eintrag."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archive_shares WHERE archive_id = ? ORDER BY created_at DESC",
                (archive_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Maintenance ──────────────────────────────────────────────

    def _enforce_max_entries(self) -> None:
        """Lösche älteste Einträge wenn max_entries überschritten."""
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]

            if count > self.config.max_entries:
                excess = count - self.config.max_entries
                # FTS-Einträge der zu löschenden Zeilen entfernen
                conn.execute(
                    """
                    INSERT INTO archive_fts(archive_fts, rowid, title, summary, source_url, input_text)
                    SELECT 'delete', rowid, COALESCE(title,''), COALESCE(summary,''),
                           COALESCE(source_url,''), COALESCE(input_text,'')
                    FROM analysis_archive
                    WHERE id IN (
                        SELECT id FROM analysis_archive
                        ORDER BY created_at ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
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
