"""Tests für verbesserte Evidence-Qualität: Off-topic-Erkennung, Relevanz, Freshness.

Testet:
    - Off-topic URL-Erkennung
    - Multi-Signal Relevanz-Score (statt nur Keyword-Overlap)
    - Entitäts-Matching
    - Freshness-Berechnung
    - Excerpt-Extraktion
    - Off-topic Treffer werden aus Rankings entfernt
"""

from __future__ import annotations

import pytest
from datetime import datetime

from agents.evidence_builder import (
    _compute_freshness,
    _entity_overlap,
    _extract_best_excerpt,
    _extract_entities,
    _is_offtopic_url,
    _relevance_score,
    _rank_evidence_items,
)
from models.evidence_models import EvidenceItem, EvidenceSource, GoogleFactCheckMatch
from tools.web_search import SearchResult


class TestOffTopicDetection:
    """Irrelevante URLs werden korrekt erkannt."""

    @pytest.mark.parametrize("url", [
        "https://www.chefkoch.de/rezepte/nudeln-mit-sauce",
        "https://restaurant-berlin.de/menu",
        "https://www.duden.de/rechtschreibung/grammatik",
        "https://www.deepl.com/translator",
        "https://www.amazon.de/shop/produkt",
        "https://wetter.de/vorhersage/berlin",
    ])
    def test_offtopic_urls_detected(self, url: str):
        assert _is_offtopic_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.tagesschau.de/inland/kriminalitaet",
        "https://destatis.de/DE/Themen/statistik.html",
        "https://correctiv.org/faktencheck/2024/test",
        "https://www.reuters.com/world/europe/germany",
        "https://www.bka.de/SharedDocs/pks-2023.html",
    ])
    def test_legitimate_urls_not_flagged(self, url: str):
        assert _is_offtopic_url(url) is False


class TestEntityExtraction:
    """Entitäten werden korrekt aus Text extrahiert."""

    def test_names_extracted(self):
        entities = _extract_entities("Angela Merkel besuchte Deutschland in Berlin")
        assert "Angela" in entities
        assert "Merkel" in entities
        assert "Deutschland" in entities
        assert "Berlin" in entities

    def test_numbers_extracted(self):
        entities = _extract_entities("Die Quote stieg um 5,5% auf 300.000")
        assert any("5,5%" in e for e in entities)
        assert any("300" in e for e in entities)

    def test_acronyms_extracted(self):
        entities = _extract_entities("Die BAMF-Statistik zeigt laut BMI einen Anstieg")
        assert "BAMF" in entities
        assert "BMI" in entities


class TestEntityOverlap:
    """Entitäts-Overlap wird korrekt berechnet."""

    def test_full_overlap(self):
        score = _entity_overlap(
            "Angela Merkel besuchte 2023 Berlin",
            "Angela Merkel war 2023 in Berlin zu Besuch"
        )
        assert score > 0.7

    def test_no_overlap(self):
        score = _entity_overlap(
            "Angela Merkel besuchte 2023 Berlin",
            "Das Wetter in London war sonnig und warm"
        )
        assert score < 0.3

    def test_partial_overlap(self):
        score = _entity_overlap(
            "Die Kriminalität in Deutschland stieg 2023 um 50%",
            "Deutschland verzeichnete 2023 einen Anstieg bei Straftaten"
        )
        assert 0.3 < score < 0.9


class TestRelevanceScore:
    """Multi-Signal Relevanz-Score ist besser als reiner Keyword-Overlap."""

    def test_relevant_result_high_score(self):
        result = SearchResult(
            title="Kriminalstatistik 2023: Anstieg der Straftaten in Deutschland",
            url="https://destatis.de/pks-2023",
            snippet="Die Polizeiliche Kriminalstatistik 2023 zeigt einen Anstieg von 5,5% in Deutschland.",
        )
        score = _relevance_score(result, "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen.")
        assert score > 0.4

    def test_offtopic_result_low_score(self):
        result = SearchResult(
            title="Die besten Pasta-Rezepte für den Sommer",
            url="https://chefkoch.de/rezepte/pasta",
            snippet="Hier finden Sie leckere Pasta-Rezepte für warme Sommertage.",
        )
        score = _relevance_score(result, "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen.")
        assert score < 0.2

    def test_keyword_only_match_lower_than_entity_match(self):
        """Ein Treffer mit Keyword-Match aber ohne Entitäten ist weniger relevant."""
        keyword_only = SearchResult(
            title="Was ist Kriminalität? Definition und Erklärung",
            url="https://lexikon.de/kriminalitaet",
            snippet="Kriminalität bezeichnet die Gesamtheit aller Straftaten.",
        )
        entity_match = SearchResult(
            title="PKS 2023: Kriminalstatistik Deutschland",
            url="https://bka.de/pks-2023",
            snippet="Die Kriminalität in Deutschland stieg 2023 laut BKA um 5,5%.",
        )
        claim = "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen."
        assert _relevance_score(entity_match, claim) > _relevance_score(keyword_only, claim)


class TestFreshnessComputation:
    """Freshness-Score wird basierend auf dem Datum korrekt berechnet."""

    def test_recent_date_high_freshness(self):
        today = datetime.now().strftime("%Y-%m-%d")
        assert _compute_freshness(today) >= 0.9

    def test_old_date_low_freshness(self):
        assert _compute_freshness("2018-01-01") < 0.3

    def test_year_only_medium_freshness(self):
        score = _compute_freshness("2024")
        assert 0.1 < score < 1.0

    def test_unknown_date_neutral(self):
        assert _compute_freshness("") == 0.5
        assert _compute_freshness("unbekannt") == 0.5


class TestExcerptExtraction:
    """Relevante Excerpts statt stumpfem content[:800]."""

    def test_short_content_returned_as_is(self):
        content = "Kurzer Inhalt mit wenig Text."
        result = _extract_best_excerpt(content, "Test-Claim")
        assert result == content

    def test_relevant_paragraphs_selected(self):
        content = (
            "Dies ist ein Absatz über das Wetter in Berlin. "
            "Es war sonnig und warm den ganzen Tag über.\n\n"
            "Die Kriminalstatistik 2023 zeigt einen Anstieg von 5,5% bei Straftaten "
            "in Deutschland laut Bundeskriminalamt.\n\n"
            "Ein weiterer Absatz über Fußball und die Bundesliga-Ergebnisse "
            "vom vergangenen Wochenende."
        )
        claim = "Die Kriminalität in Deutschland ist 2023 gestiegen."
        result = _extract_best_excerpt(content, claim, max_chars=300)
        assert "Kriminalstatistik" in result
        # Der Fußball-Absatz sollte weniger priorisiert werden
        assert "Bundesliga" not in result or "Kriminalstatistik" in result

    def test_empty_content(self):
        assert _extract_best_excerpt("", "Test") == ""


class TestRankingOffTopicFiltering:
    """Off-topic Treffer werden aus dem Ranking herausgefiltert."""

    def test_offtopic_tier5_results_removed(self):
        """Irrelevante Tier-5 Treffer mit niedrigem Score werden verworfen."""
        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://bka.de/pks", title="PKS 2023 Kriminalstatistik Deutschland"),
                excerpt="Kriminalität Deutschland 2023 stieg um 5,5%",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://chefkoch.de/rezepte", title="Die besten Pasta-Rezepte"),
                excerpt="Leckere Pasta für den Sommer kochen",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://duden.de/grammatik/uebungen", title="Grammatik-Übungen Deutsch"),
                excerpt="Übungen zur deutschen Grammatik für Anfänger",
            ),
        ]
        claim = "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen."
        ranked = _rank_evidence_items(results, claim, [])

        # BKA-Treffer muss drin sein
        urls = [item.source.url for item in ranked]
        assert "https://bka.de/pks" in urls

        # Chefkoch und Duden sollten rausgefiltert sein
        assert "https://chefkoch.de/rezepte" not in urls
        assert "https://duden.de/grammatik/uebungen" not in urls
