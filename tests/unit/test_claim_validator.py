"""Tests für die ClaimValidator-Stufe (Stufe 4.5 der Pipeline).

Testet:
    - Harte Filter für Meta-Claims
    - Harte Filter für Recherche-Claims / Suchdimensionen
    - Weiche Qualitätssignale
    - Durchlass gültiger Claims
    - Qualitätsscoring
"""

from __future__ import annotations

import pytest

from agents.claim_processor import ClaimValidator
from models.schemas import AmbiguityLevel, ClaimType, ProcessedClaim


def _make_claim(text: str, claim_type: ClaimType = ClaimType.FACTUAL) -> ProcessedClaim:
    """Hilfsfunktion: minimaler ProcessedClaim."""
    return ProcessedClaim(
        id="C_test",
        text=text,
        type=claim_type,
        context="",
        is_checkworthy=True,
    )


class TestClaimValidatorHardFilters:
    """Harte Filter: Diese Claims müssen als ungültig markiert werden."""

    def setup_method(self):
        self.validator = ClaimValidator()

    @pytest.mark.parametrize("text", [
        "Es gibt Informationen darüber, wann das Gesetz verabschiedet wurde.",
        "Es gibt Informationen darüber, wie viele Flüchtlinge 2023 kamen.",
        "Es gibt Hinweise, wie das System funktioniert.",
        "Es wird behauptet, dass die Regierung versagt hat.",
        "Es gibt Berichte, dass die Zahlen manipuliert wurden.",
        "Es gibt Quellen, die das bestätigen.",
        "Es ist bekannt, dass Impfungen wirken.",
        "Man kann herausfinden, wann das passiert ist.",
        "Es lässt sich recherchieren, ob das stimmt.",
        "Es gibt Daten darüber, wie hoch die Quote ist.",
        "Es existieren Studien zu diesem Thema.",
        "Informationen über die Migrationszahlen 2024.",
    ])
    def test_meta_claims_rejected(self, text: str):
        """Meta-/Recherche-Claims werden als ungültig markiert."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])
        assert len(result) == 1
        assert result[0].is_valid_claim is False
        assert result[0].invalid_reason != ""
        assert result[0].claim_quality_score < 0.3

    @pytest.mark.parametrize("text", [
        "Wie viele Flüchtlinge kamen 2023 nach Deutschland?",
        "Wann wurde das Gesetz verabschiedet?",
        "Wo fand der Vorfall statt?",
        "Warum ist die Quote gestiegen?",
        "Ob das stimmt, ist unklar.",
    ])
    def test_search_dimensions_rejected(self, text: str):
        """Suchdimensionen (Fragen statt Behauptungen) werden als ungültig markiert."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])
        assert len(result) == 1
        assert result[0].is_valid_claim is False

    def test_too_short_claim_rejected(self):
        """Zu kurze Claims werden als ungültig markiert."""
        claim = _make_claim("Stimmt so.")
        result = self.validator.validate([claim])
        assert result[0].is_valid_claim is False
        assert "zu kurz" in result[0].invalid_reason.lower()


class TestClaimValidatorValidClaims:
    """Diese Claims müssen als gültig durchgelassen werden."""

    def setup_method(self):
        self.validator = ClaimValidator()

    @pytest.mark.parametrize("text", [
        "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen.",
        "40% der Einbrüche werden von Ausländern begangen.",
        "Deutschland hat 2023 über 300.000 Asylbewerber aufgenommen.",
        "Die CO2-Emissionen sind seit 1990 um 40% gesunken.",
        "Die Arbeitslosenquote in Spanien liegt bei 12%.",
        "Die AfD hat bei der Europawahl 2024 über 15% erreicht.",
        "Impfungen verursachen Autismus.",
        "Die Erde ist eine Scheibe.",
    ])
    def test_valid_claims_accepted(self, text: str):
        """Echte, falsifizierbare Behauptungen werden als gültig markiert."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])
        assert result[0].is_valid_claim is True
        assert result[0].claim_quality_score > 0.5

    def test_empty_list_returns_empty(self):
        """Leere Eingabe → leere Ausgabe."""
        assert self.validator.validate([]) == []


class TestClaimValidatorWeakSignals:
    """Weiche Signale: Diese Claims haben reduzierte Qualitätsscores."""

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_vague_claim_lower_quality(self):
        """Vage Claims haben einen niedrigeren Qualitätsscore."""
        vague = _make_claim("Einige Leute sagen, dass es Probleme gibt.")
        specific = _make_claim("Die Kriminalität in Deutschland stieg 2023 um 5,5%.")
        r_vague = self.validator.validate([vague])[0]
        r_specific = self.validator.validate([specific])[0]
        assert r_vague.claim_quality_score < r_specific.claim_quality_score

    def test_mixed_batch_filtering(self):
        """Bei gemischten Batches werden nur ungültige markiert, nicht entfernt."""
        claims = [
            _make_claim("Es gibt Informationen darüber, wann das passiert ist."),
            _make_claim("Die Kriminalität in Deutschland stieg 2023 um 5,5%."),
            _make_claim("Wie viele Leute waren betroffen?"),
            _make_claim("Deutschland hat 83 Millionen Einwohner."),
        ]
        results = self.validator.validate(claims)
        assert len(results) == 4  # Alle bleiben erhalten

        valid = [r for r in results if r.is_valid_claim]
        invalid = [r for r in results if not r.is_valid_claim]
        assert len(valid) == 2
        assert len(invalid) == 2


class TestClaimValidatorNoFalsePositives:
    """Sicherstellen, dass gültige Claims nicht fälschlich abgelehnt werden."""

    def setup_method(self):
        self.validator = ClaimValidator()

    @pytest.mark.parametrize("text", [
        "Stimmt es, dass die Mehrwertsteuer 2024 erhöht wurde?",
        "Ist es wahr, dass Windräder Infraschall abgeben?",
        "Trifft es zu, dass die Mieten in Berlin um 30% gestiegen sind?",
    ])
    def test_rhetorical_fact_questions_accepted(self, text: str):
        """Rhetorische Faktenfragen ('Stimmt es, dass ...') werden durchgelassen."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])
        assert result[0].is_valid_claim is True
