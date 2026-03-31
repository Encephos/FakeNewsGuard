"""Lokale Faktencheck-Datenbank auf Basis von DataCommons ClaimReview-Daten.

Bietet einen Offline-Fallback wenn die Google Fact Check Tools API keine
Treffer liefert. Die Datenbank wird per Bulk-Import aus dem DataCommons
Fact Check Dataset (CC-BY 4.0) befüllt.

Architektur:
    - SQLite mit FTS5 für Volltextsuche auf Claim-Texten.
    - Optional: Embedding-basierte semantische Suche (sentence-transformers).
    - Wird als Fallback in FactCheckDatabaseClient integriert.

Datenquelle:
    https://datacommons.org/factcheck/download
    Lizenz: CC-BY 4.0 – kommerziell nutzbar mit Attribution.

Datenbankschema:
    claim_reviews(
        id INTEGER PRIMARY KEY,
        claim_text TEXT NOT NULL,
        rating TEXT NOT NULL,
        publisher TEXT NOT NULL,
        url TEXT NOT NULL,
        review_date TEXT,
        language TEXT DEFAULT 'de'
    )
    claim_reviews_fts (FTS5 virtual table on claim_text)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from tools.factcheck_databases import ExternalFactCheck

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/factcheck_local.db"


class LocalFactCheckDatabase:
    """SQLite-basierte lokale Faktencheck-Datenbank.

    Verwendung::

        db = LocalFactCheckDatabase()
        count = db.import_datacommons("data/factchecks.json")
        results = db.search("Impfung verursacht Autismus")
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Lazy-initialisierte SQLite-Verbindung mit WAL-Modus."""
        if self._conn is None:
            # Verzeichnis erstellen falls nötig
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """Erstelle Tabellen und FTS5-Index falls nicht vorhanden."""
        conn = self._conn
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_text TEXT NOT NULL,
                rating TEXT NOT NULL,
                publisher TEXT NOT NULL,
                url TEXT NOT NULL,
                review_date TEXT DEFAULT '',
                language TEXT DEFAULT 'de'
            )
        """)
        # FTS5-Index für Volltextsuche
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS claim_reviews_fts
            USING fts5(claim_text, content='claim_reviews', content_rowid='id')
        """)
        # Trigger für FTS-Synchronisation
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS claim_reviews_ai AFTER INSERT ON claim_reviews BEGIN
                INSERT INTO claim_reviews_fts(rowid, claim_text) VALUES (new.id, new.claim_text);
            END
        """)
        conn.commit()

    @property
    def is_populated(self) -> bool:
        """True wenn die Datenbank Einträge enthält."""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()
            return row[0] > 0
        except Exception:
            return False

    def count(self) -> int:
        """Anzahl der Einträge in der Datenbank."""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()
            return row[0]
        except Exception:
            return 0

    def import_datacommons(self, json_path: str) -> int:
        """Importiere ClaimReview-Daten aus einer DataCommons JSON-Datei.

        Erwartet ein JSON-Array oder ein Objekt mit "dataFeedElement"-Array,
        wobei jedes Element ClaimReview-Felder enthält.

        Args:
            json_path: Pfad zur JSON-Datei mit ClaimReview-Daten.

        Returns:
            Anzahl der importierten Einträge.
        """
        path = Path(json_path)
        if not path.exists():
            logger.error("DataCommons-Datei nicht gefunden: %s", json_path)
            return 0

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        # Verschiedene DataCommons-Formate unterstützen
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get("dataFeedElement", data.get("claims", []))
        else:
            logger.error("Unbekanntes Format in %s", json_path)
            return 0

        conn = self._get_conn()
        imported = 0

        for entry in entries:
            try:
                claim_text = _extract_claim_text(entry)
                rating = _extract_rating(entry)
                publisher = _extract_publisher(entry)
                url = _extract_url(entry)

                if not claim_text or not rating:
                    continue

                conn.execute(
                    "INSERT INTO claim_reviews (claim_text, rating, publisher, url, review_date, language) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        claim_text,
                        rating,
                        publisher,
                        url,
                        _extract_date(entry),
                        _extract_language(entry),
                    ),
                )
                imported += 1
            except Exception as exc:
                logger.debug("Import-Fehler für Eintrag: %s", exc)

        conn.commit()

        # FTS-Index rebuilden nach Bulk-Import
        try:
            conn.execute("INSERT INTO claim_reviews_fts(claim_reviews_fts) VALUES('rebuild')")
            conn.commit()
        except Exception:
            pass

        logger.info("DataCommons-Import abgeschlossen: %d Einträge", imported)
        return imported

    def search(self, query: str, max_results: int = 5) -> list[ExternalFactCheck]:
        """Durchsuche die lokale Datenbank per FTS5-Volltextsuche.

        Args:
            query: Suchtext (Claim oder Schlüsselwörter).
            max_results: Maximale Anzahl Ergebnisse.

        Returns:
            Liste von ExternalFactCheck-Objekten.
        """
        if not query.strip():
            return []

        conn = self._get_conn()

        # FTS5 MATCH-Query (Wörter mit implizitem AND)
        fts_query = " ".join(
            word for word in query.split() if len(word) > 2
        )
        if not fts_query:
            return []

        try:
            rows = conn.execute(
                """
                SELECT cr.claim_text, cr.rating, cr.publisher, cr.url,
                       cr.language, rank
                FROM claim_reviews_fts fts
                JOIN claim_reviews cr ON cr.id = fts.rowid
                WHERE claim_reviews_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, max_results),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.debug("FTS5-Suche fehlgeschlagen: %s", exc)
            return []

        return [
            ExternalFactCheck(
                claim_reviewed=row[0],
                rating=row[1],
                publisher=row[2],
                url=row[3],
                source_api="datacommons_local",
                language=row[4] or "",
            )
            for row in rows
        ]

    def close(self) -> None:
        """Schließe die Datenbankverbindung."""
        if self._conn:
            self._conn.close()
            self._conn = None


# ── Extraktions-Hilfsfunktionen für DataCommons-Format ───────────────────────


def _extract_claim_text(entry: dict) -> str:
    """Extrahiere den Claim-Text aus einem ClaimReview-Eintrag."""
    # DataCommons-Format
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            if item.get("@type") == "ClaimReview":
                claim_reviewed = item.get("claimReviewed", "")
                if claim_reviewed:
                    return claim_reviewed

    # Flat-Format
    return entry.get("claimReviewed", entry.get("claim_text", entry.get("text", "")))


def _extract_rating(entry: dict) -> str:
    """Extrahiere das Rating."""
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            review_rating = item.get("reviewRating", {})
            if review_rating:
                return review_rating.get("alternateName", review_rating.get("ratingValue", ""))

    review_rating = entry.get("reviewRating", {})
    if isinstance(review_rating, dict):
        return review_rating.get("alternateName", review_rating.get("ratingValue", ""))
    return entry.get("rating", entry.get("textualRating", ""))


def _extract_publisher(entry: dict) -> str:
    """Extrahiere den Publisher-Namen."""
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            author = item.get("author", {})
            if isinstance(author, dict):
                return author.get("name", "")

    author = entry.get("author", entry.get("publisher", {}))
    if isinstance(author, dict):
        return author.get("name", "")
    if isinstance(author, str):
        return author
    return ""


def _extract_url(entry: dict) -> str:
    """Extrahiere die Review-URL."""
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            url = item.get("url", "")
            if url:
                return url

    return entry.get("url", "")


def _extract_date(entry: dict) -> str:
    """Extrahiere das Review-Datum."""
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            return item.get("datePublished", "")

    return entry.get("datePublished", entry.get("review_date", ""))


def _extract_language(entry: dict) -> str:
    """Extrahiere die Sprache."""
    if "item" in entry and isinstance(entry["item"], list):
        for item in entry["item"]:
            return item.get("inLanguage", "")

    return entry.get("inLanguage", entry.get("language", entry.get("languageCode", "")))
