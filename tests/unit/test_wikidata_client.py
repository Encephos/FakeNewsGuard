"""Tests für den Wikidata SPARQL Adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.sources.clients.wikidata import (
    WikidataClient,
    _escape_sparql,
    _extract_qid,
    _extract_search_entities,
    _format_wikidata_value,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


SPARQL_ENTITY_RESULT = {
    "results": {
        "bindings": [
            {"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q567"}}
        ]
    }
}

SPARQL_PROPERTIES_RESULT = {
    "results": {
        "bindings": [
            {
                "val_P39": {"type": "uri", "value": "http://www.wikidata.org/entity/Q4970706"},
                "val_P569": {"type": "literal", "value": "1954-07-17T00:00:00Z"},
                "val_P27": {"type": "uri", "value": "http://www.wikidata.org/entity/Q183"},
            }
        ]
    }
}

SPARQL_LABEL_RESULT = {
    "results": {
        "bindings": [
            {"label": {"type": "literal", "value": "Angela Merkel"}}
        ]
    }
}


@pytest.fixture
def client():
    """WikidataClient mit gemocktem HTTP-Client."""
    c = WikidataClient()
    c._http = MagicMock()
    return c


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────


class TestEscapeSparql:
    def test_escapes_quotes(self):
        assert _escape_sparql('He said "hello"') == 'He said \\"hello\\"'

    def test_escapes_backslash(self):
        assert _escape_sparql("path\\to") == "path\\\\to"

    def test_strips_whitespace(self):
        assert _escape_sparql("  test  ") == "test"

    def test_replaces_newlines(self):
        assert _escape_sparql("line1\nline2") == "line1 line2"


class TestExtractQid:
    def test_extracts_from_uri(self):
        assert _extract_qid(SPARQL_ENTITY_RESULT) == "Q567"

    def test_empty_bindings(self):
        assert _extract_qid({"results": {"bindings": []}}) is None

    def test_empty_dict(self):
        assert _extract_qid({}) is None

    def test_non_entity_uri(self):
        result = {
            "results": {
                "bindings": [
                    {"item": {"type": "literal", "value": "just text"}}
                ]
            }
        }
        assert _extract_qid(result) is None


class TestFormatWikidataValue:
    def test_entity_uri(self):
        assert _format_wikidata_value(
            "http://www.wikidata.org/entity/Q183", "uri"
        ) == "Q183"

    def test_date_value(self):
        assert _format_wikidata_value(
            "1954-07-17T00:00:00Z", "literal"
        ) == "1954-07-17"

    def test_plain_text(self):
        assert _format_wikidata_value("Berlin", "literal") == "Berlin"

    def test_empty_value(self):
        assert _format_wikidata_value("", "literal") == ""

    def test_coordinates(self):
        val = "Point(13.383333333 52.516666666)"
        assert _format_wikidata_value(val, "literal") == val


# ── Entity-Resolution ────────────────────────────────────────────────────────


class TestResolveEntity:
    def test_resolve_person(self, client):
        client._http.get.return_value = SPARQL_ENTITY_RESULT

        qid = client._resolve_entity("Angela Merkel", "person")

        assert qid == "Q567"
        # Sollte SPARQL-Query mit Label-Match aufrufen
        call_args = client._http.get.call_args
        assert "Angela Merkel" in call_args[1]["params"]["query"] if "params" in call_args[1] else "Angela Merkel" in str(call_args)

    def test_resolve_not_found_tries_fuzzy(self, client):
        # Erster Aufruf: kein Treffer, zweiter: Fuzzy-Treffer
        client._http.get.side_effect = [
            {"results": {"bindings": []}},
            SPARQL_ENTITY_RESULT,
        ]
        qid = client._resolve_entity("Unbekannt", "person")
        assert qid == "Q567"
        assert client._http.get.call_count == 2

    def test_resolve_complete_failure(self, client):
        client._http.get.return_value = {"results": {"bindings": []}}
        qid = client._resolve_entity("???", "person")
        assert qid is None


# ── Property-Abfragen ────────────────────────────────────────────────────────


class TestQueryProperties:
    def test_extracts_facts(self, client):
        client._http.get.return_value = SPARQL_PROPERTIES_RESULT

        facts = client._query_properties("Q567", "Angela Merkel", "person")

        assert len(facts) >= 1
        # Mindestens ein ENTITY_PROPERTY-Fakt
        assert any(f.fact_type.value == "entity_property" for f in facts)

    def test_empty_result(self, client):
        client._http.get.return_value = {"results": {"bindings": []}}
        facts = client._query_properties("Q999999", "Nobody", "person")
        assert facts == []

    def test_sparql_error(self, client):
        client._http.get.return_value = {}
        facts = client._query_properties("Q567", "Test", "person")
        assert facts == []


# ── search() ─────────────────────────────────────────────────────────────────


class TestSearch:
    @patch("tools.sources.clients.wikidata._extract_search_entities")
    def test_search_with_entities(self, mock_extract, client):
        mock_extract.return_value = [("Angela Merkel", "person")]
        # Resolve → Properties → Label
        client._http.get.side_effect = [
            SPARQL_ENTITY_RESULT,       # resolve_entity (exact)
            SPARQL_PROPERTIES_RESULT,   # query_properties
        ]

        items = client.search("Angela Merkel Bundeskanzlerin")

        assert len(items) == 1
        assert items[0].record_id == "Q567"
        assert items[0].claim_relevance == 0.65

    @patch("tools.sources.clients.wikidata._extract_search_entities")
    def test_search_no_entities(self, mock_extract, client):
        mock_extract.return_value = []
        items = client.search("Keine Entitäten hier")
        assert items == []

    @patch("tools.sources.clients.wikidata._extract_search_entities")
    def test_search_entity_not_found(self, mock_extract, client):
        mock_extract.return_value = [("Unbekannt", "person")]
        client._http.get.return_value = {"results": {"bindings": []}}

        items = client.search("Unbekannt Test")
        assert items == []


# ── fetch_details() ──────────────────────────────────────────────────────────


class TestFetchDetails:
    def test_valid_qid(self, client):
        client._http.get.side_effect = [
            SPARQL_LABEL_RESULT,        # _get_label
            SPARQL_PROPERTIES_RESULT,   # _query_properties_by_dict
        ]

        item = client.fetch_details("Q567")

        assert item is not None
        assert item.record_id == "Q567"
        assert item.title == "Angela Merkel"
        assert item.claim_relevance == 0.85

    def test_invalid_qid(self, client):
        assert client.fetch_details("not-a-qid") is None
        assert client.fetch_details("P123") is None

    def test_label_not_found(self, client):
        client._http.get.return_value = {"results": {"bindings": []}}
        assert client.fetch_details("Q999999999") is None


# ── NER-Integration ──────────────────────────────────────────────────────────


class TestExtractSearchEntities:
    @patch("tools.ner_extractor.extract_entities")
    def test_with_ner(self, mock_ner):
        mock_entities = MagicMock()
        mock_entities.persons = ["Angela Merkel"]
        mock_entities.organizations = ["CDU"]
        mock_entities.locations = ["Berlin"]
        mock_ner.return_value = mock_entities

        result = _extract_search_entities("Angela Merkel CDU Berlin")

        assert ("Angela Merkel", "person") in result
        assert ("CDU", "organization") in result
        assert ("Berlin", "location") in result

    @patch("tools.ner_extractor.extract_entities", side_effect=Exception("spaCy not available"))
    def test_fallback_without_ner(self, mock_ner):
        """Falls NER nicht verfügbar → Titel-Case-Fallback."""
        result = _extract_search_entities("Angela Merkel regiert Deutschland")
        # Sollte zumindest einige Wörter mit Großbuchstaben finden
        names = [name for name, _ in result]
        assert "Angela" in names or "Merkel" in names
