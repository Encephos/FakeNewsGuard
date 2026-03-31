"""Tests für die lokale Faktencheck-Datenbank (DataCommons)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tools.factcheck_local import LocalFactCheckDatabase


# ── Fixtures ─────────────────────────────────────────────────────────────────


SAMPLE_FLAT_DATA = [
    {
        "claimReviewed": "Impfungen verursachen Autismus",
        "reviewRating": {"alternateName": "Falsch", "ratingValue": "1"},
        "author": {"name": "Correctiv"},
        "url": "https://correctiv.org/faktencheck/impfung",
        "datePublished": "2024-03-15",
        "inLanguage": "de",
    },
    {
        "claimReviewed": "5G-Strahlung verbreitet COVID-19",
        "reviewRating": {"alternateName": "Falsch"},
        "author": {"name": "dpa Faktencheck"},
        "url": "https://dpa-factchecking.com/5g-covid",
        "datePublished": "2024-01-20",
        "inLanguage": "de",
    },
    {
        "claimReviewed": "Die Erde ist flach",
        "reviewRating": {"alternateName": "Falsch"},
        "author": {"name": "Snopes"},
        "url": "https://snopes.com/flat-earth",
        "inLanguage": "en",
    },
]

SAMPLE_DATACOMMONS_FORMAT = {
    "dataFeedElement": [
        {
            "item": [
                {
                    "@type": "ClaimReview",
                    "claimReviewed": "Bill Gates implantiert Mikrochips",
                    "reviewRating": {"alternateName": "Falsch"},
                    "author": {"name": "AFP Faktencheck"},
                    "url": "https://afp.com/faktencheck/mikrochip",
                    "datePublished": "2023-06-01",
                    "inLanguage": "de",
                }
            ]
        }
    ]
}


@pytest.fixture
def db(tmp_path):
    """Erstelle eine temporäre lokale Datenbank."""
    db_path = str(tmp_path / "test_factcheck.db")
    database = LocalFactCheckDatabase(db_path=db_path)
    yield database
    database.close()


@pytest.fixture
def flat_json_file(tmp_path):
    """Erstelle eine temporäre JSON-Datei mit Flat-Format."""
    path = tmp_path / "flat_data.json"
    path.write_text(json.dumps(SAMPLE_FLAT_DATA), encoding="utf-8")
    return str(path)


@pytest.fixture
def datacommons_json_file(tmp_path):
    """Erstelle eine temporäre JSON-Datei mit DataCommons-Format."""
    path = tmp_path / "dc_data.json"
    path.write_text(json.dumps(SAMPLE_DATACOMMONS_FORMAT), encoding="utf-8")
    return str(path)


# ── Schema & Grundfunktionen ────────────────────────────────────────────────


class TestBasicOperations:
    def test_empty_database(self, db):
        assert db.is_populated is False
        assert db.count() == 0

    def test_search_empty_db(self, db):
        results = db.search("anything")
        assert results == []


# ── Import ───────────────────────────────────────────────────────────────────


class TestImport:
    def test_import_flat_format(self, db, flat_json_file):
        count = db.import_datacommons(flat_json_file)
        assert count == 3
        assert db.is_populated is True
        assert db.count() == 3

    def test_import_datacommons_format(self, db, datacommons_json_file):
        count = db.import_datacommons(datacommons_json_file)
        assert count == 1
        assert db.count() == 1

    def test_import_nonexistent_file(self, db):
        count = db.import_datacommons("/nonexistent/path.json")
        assert count == 0

    def test_import_preserves_fields(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("Impfungen Autismus")

        assert len(results) >= 1
        result = results[0]
        assert "Impfungen" in result.claim_reviewed or "Autismus" in result.claim_reviewed
        assert result.rating == "Falsch"
        assert result.publisher == "Correctiv"
        assert result.source_api == "datacommons_local"


# ── Suche ────────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_finds_matching_claim(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("Impfungen verursachen Autismus")

        assert len(results) >= 1
        assert any("Autismus" in r.claim_reviewed for r in results)

    def test_search_5g_covid(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("5G COVID Strahlung")

        assert len(results) >= 1
        assert any("5G" in r.claim_reviewed for r in results)

    def test_search_respects_max_results(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("Falsch", max_results=1)
        assert len(results) <= 1

    def test_search_empty_query(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("")
        assert results == []

    def test_search_no_match(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        results = db.search("Quantencomputer Verschlüsselung")
        assert results == []

    def test_search_short_words_filtered(self, db, flat_json_file):
        db.import_datacommons(flat_json_file)
        # Wörter mit ≤2 Zeichen werden gefiltert
        results = db.search("ab cd ef")
        assert results == []


# ── DataCommons-Format ───────────────────────────────────────────────────────


class TestDataCommonsFormat:
    def test_parses_nested_claim_review(self, db, datacommons_json_file):
        db.import_datacommons(datacommons_json_file)
        results = db.search("Mikrochips Bill Gates")

        assert len(results) >= 1
        result = results[0]
        assert "Mikrochip" in result.claim_reviewed
        assert result.publisher == "AFP Faktencheck"
