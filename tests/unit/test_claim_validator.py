"""Tests für die ClaimValidator-Stufe (Stufe 4.5 der Pipeline).

Testet:
    - Harte Filter für Meta-Claims
    - Harte Filter für Recherche-Claims / Suchdimensionen
    - Weiche Qualitätssignale
    - Durchlass gültiger Claims
    - Qualitätsscoring
    - Abstrakte Qualitätssignale (missing_artifact_evidence, underspecified_actor,
      extraordinary_claim, elevated_burden_of_proof)
"""

from __future__ import annotations

import pytest

from agents.claim_processor import ClaimValidator
from config import ClaimQualitySignalConfig
from models.schemas import AmbiguityLevel, ClaimFrame, ClaimType, ProcessedClaim


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


# ── Hilfsfunktionen für Signal-Tests ──────────────────────────────────────────

def _make_claim_with_frame(
    text: str,
    claim_type: ClaimType = ClaimType.FACTUAL,
    *,
    subject: str = "",
    institution: str = "",
    time_reference: str = "",
    numbers: list[str] | None = None,
    sanction: str = "",
    enforcement: str = "",
) -> ProcessedClaim:
    """ProcessedClaim mit befülltem ClaimFrame für Signal-Tests."""
    frame = ClaimFrame(
        raw_text=text,
        subject=subject,
        institution=institution,
        time_reference=time_reference,
        numbers=numbers or [],
        sanction=sanction,
        enforcement=enforcement,
    )
    return ProcessedClaim(
        id="C_sig",
        text=text,
        type=claim_type,
        context="",
        is_checkworthy=True,
        frame=frame,
    )


# ── Signal-Tests ───────────────────────────────────────────────────────────────

class TestMissingArtifactEvidence:
    """Signal: missing_artifact_evidence.

    Feuert wenn alle Frame-Anker (Akteur, Institution, Zeit, Zahlen) fehlen.
    Keine Prüfung auf Artefakttyp-Wörter – rein strukturell.
    """

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_fires_when_all_anchors_empty(self):
        """Leerer Frame → Signal wird erkannt."""
        claim = _make_claim_with_frame(
            "Es wurde ein Beschluss gefasst.",
            subject="", institution="", time_reference="", numbers=[],
        )
        result = self.validator.validate([claim])[0]
        assert "missing_artifact_evidence" in result.quality_signals

    def test_does_not_fire_when_subject_present(self):
        """Subject vorhanden → Signal nicht ausgelöst."""
        claim = _make_claim_with_frame(
            "Der Stadtrat hat einen Beschluss gefasst.",
            subject="Stadtrat",
        )
        result = self.validator.validate([claim])[0]
        assert "missing_artifact_evidence" not in result.quality_signals

    def test_does_not_fire_when_institution_present(self):
        """Institution vorhanden → Signal nicht ausgelöst."""
        claim = _make_claim_with_frame(
            "Die WHO hat eine Studie veröffentlicht.",
            institution="Weltgesundheitsorganisation",
        )
        result = self.validator.validate([claim])[0]
        assert "missing_artifact_evidence" not in result.quality_signals

    def test_does_not_fire_when_numbers_present(self):
        """Zahlen vorhanden → Signal nicht ausgelöst."""
        claim = _make_claim_with_frame(
            "Es wurden 500 Fälle gemeldet.",
            numbers=["500"],
        )
        result = self.validator.validate([claim])[0]
        assert "missing_artifact_evidence" not in result.quality_signals

    def test_does_not_fire_without_frame(self):
        """Kein Frame → Signal nicht auslösbar."""
        claim = _make_claim("Eine Entscheidung wurde getroffen.")
        result = self.validator.validate([claim])[0]
        assert "missing_artifact_evidence" not in result.quality_signals

    def test_lowers_quality_score(self):
        """Signal senkt den claim_quality_score."""
        anchored = _make_claim_with_frame(
            "Die Bundesregierung hat 2023 entschieden.",
            subject="Bundesregierung", time_reference="2023",
        )
        unanchored = _make_claim_with_frame(
            "Es wurde eine Entscheidung getroffen.",
        )
        r_anchored = self.validator.validate([anchored])[0]
        r_unanchored = self.validator.validate([unanchored])[0]
        assert r_unanchored.claim_quality_score < r_anchored.claim_quality_score


class TestUnderspecifiedActor:
    """Signal: underspecified_actor.

    Feuert wenn weder subject noch institution die konfigurierte Mindestlänge
    überschreiten. Keine Prüfung auf bestimmte Wörter.
    """

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_fires_when_both_actor_fields_too_short(self):
        """Beide Felder kürzer als min_actor_length → Signal."""
        claim = _make_claim_with_frame(
            "Sie haben die Preise erhöht.",
            subject="sie",  # < 6 chars
            institution="",
        )
        result = self.validator.validate([claim])[0]
        assert "underspecified_actor" in result.quality_signals

    def test_does_not_fire_when_subject_long_enough(self):
        """Langes subject → kein Signal."""
        claim = _make_claim_with_frame(
            "Die Bundesregierung hat die Preise erhöht.",
            subject="Bundesregierung",
        )
        result = self.validator.validate([claim])[0]
        assert "underspecified_actor" not in result.quality_signals

    def test_does_not_fire_when_institution_long_enough(self):
        """Lange institution → kein Signal."""
        claim = _make_claim_with_frame(
            "Behörden haben die Preise erhöht.",
            subject="",
            institution="Bundesnetzagentur",
        )
        result = self.validator.validate([claim])[0]
        assert "underspecified_actor" not in result.quality_signals

    def test_configurable_min_actor_length(self):
        """min_actor_length ist konfigurierbar."""
        # Mit sehr kleiner Schwelle feuert das Signal nicht mehr
        cfg = ClaimQualitySignalConfig(min_actor_length=2)
        validator = ClaimValidator(cfg)
        claim = _make_claim_with_frame(
            "Sie haben gehandelt.",
            subject="sie",  # 3 chars ≥ 2 → kein Signal
        )
        result = validator.validate([claim])[0]
        assert "underspecified_actor" not in result.quality_signals

    def test_does_not_fire_without_frame(self):
        """Kein Frame → Signal nicht auslösbar."""
        claim = _make_claim("Behörden haben reagiert.")
        result = self.validator.validate([claim])[0]
        assert "underspecified_actor" not in result.quality_signals


class TestExtraordinaryClaim:
    """Signal: extraordinary_claim.

    Feuert bei Absolutheitssprache (konfigurierbar) oder Extremprozentwerten
    (>= Schwellwert). Keine Themenbindung.
    """

    def setup_method(self):
        self.validator = ClaimValidator()

    @pytest.mark.parametrize("text", [
        "Alle Impfungen führen zu schweren Nebenwirkungen.",
        "Niemand aus dieser Gruppe hat überlebt.",
        "Das System versagt niemals.",
        "Vollständig alle Befragten stimmten zu.",
        "Jeder Bürger ist davon betroffen.",
    ])
    def test_fires_on_absolute_quantifiers(self, text: str):
        """Absolutheitsquantoren lösen das Signal aus."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])[0]
        assert "extraordinary_claim" in result.quality_signals

    @pytest.mark.parametrize("text", [
        "Die Kriminalität stieg um 95%.",
        "100% der Fälle wurden nachgewiesen.",
        "Die Kosten stiegen um 99,5%.",
    ])
    def test_fires_on_extreme_percentage(self, text: str):
        """Extremprozentwerte (>= 90%) lösen das Signal aus."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])[0]
        assert "extraordinary_claim" in result.quality_signals

    @pytest.mark.parametrize("text", [
        "Die Kriminalität stieg um 15%.",
        "Ein Teil der Bevölkerung ist betroffen.",
        "Die meisten Studien zeigen Verbesserungen.",
    ])
    def test_does_not_fire_on_moderate_claims(self, text: str):
        """Moderate Claims ohne Absolutheitssprache → kein Signal."""
        claim = _make_claim(text)
        result = self.validator.validate([claim])[0]
        assert "extraordinary_claim" not in result.quality_signals

    def test_configurable_percentage_threshold(self):
        """Prozentwert-Schwellwert ist konfigurierbar."""
        cfg = ClaimQualitySignalConfig(extraordinary_percentage_threshold=50.0)
        validator = ClaimValidator(cfg)
        claim = _make_claim("Die Kosten stiegen um 60%.")
        result = validator.validate([claim])[0]
        assert "extraordinary_claim" in result.quality_signals

    def test_configurable_absolute_pattern(self):
        """Absolut-Muster ist konfigurierbar."""
        cfg = ClaimQualitySignalConfig(
            extraordinary_absolute_pattern=r"\b(immer)\b"
        )
        validator = ClaimValidator(cfg)
        # "alle" ist nicht im Pattern → kein Signal
        claim_alle = _make_claim("Alle Bürger sind betroffen.")
        result = validator.validate([claim_alle])[0]
        assert "extraordinary_claim" not in result.quality_signals
        # "immer" ist im Pattern → Signal
        claim_immer = _make_claim("Das passiert immer wieder.")
        result2 = validator.validate([claim_immer])[0]
        assert "extraordinary_claim" in result2.quality_signals


class TestElevatedBurdenOfProof:
    """Signal: elevated_burden_of_proof.

    Feuert bei CAUSAL-Claims oder wenn Sanktions-/Durchsetzungskontext
    im Frame vorliegt. Keine Themenbindung.
    """

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_fires_on_causal_claim_type(self):
        """CAUSAL-Claim löst das Signal aus."""
        claim = _make_claim(
            "Die neue Regelung führt zu steigenden Mieten.",
            claim_type=ClaimType.CAUSAL,
        )
        result = self.validator.validate([claim])[0]
        assert "elevated_burden_of_proof" in result.quality_signals

    def test_fires_when_sanction_in_frame(self):
        """frame.sanction nicht leer → Signal."""
        claim = _make_claim_with_frame(
            "Verstöße werden mit 500 Euro bestraft.",
            sanction="500 Euro Bußgeld",
        )
        result = self.validator.validate([claim])[0]
        assert "elevated_burden_of_proof" in result.quality_signals

    def test_fires_when_enforcement_in_frame(self):
        """frame.enforcement nicht leer → Signal."""
        claim = _make_claim_with_frame(
            "Die Einhaltung wird per Kameraüberwachung kontrolliert.",
            enforcement="Kameraüberwachung",
        )
        result = self.validator.validate([claim])[0]
        assert "elevated_burden_of_proof" in result.quality_signals

    def test_does_not_fire_on_factual_without_frame_context(self):
        """Reiner FACTUAL-Claim ohne Frame-Kontext → kein Signal."""
        claim = _make_claim_with_frame(
            "Deutschland hat 83 Millionen Einwohner.",
            subject="Deutschland",
            numbers=["83 Millionen"],
        )
        result = self.validator.validate([claim])[0]
        assert "elevated_burden_of_proof" not in result.quality_signals

    def test_does_not_fire_without_frame_and_factual(self):
        """FACTUAL ohne Frame → kein Signal."""
        claim = _make_claim("Die Erde ist 4,5 Milliarden Jahre alt.")
        result = self.validator.validate([claim])[0]
        assert "elevated_burden_of_proof" not in result.quality_signals


class TestSignalCombination:
    """Kombinationsverhalten: mehrere Signale senken Score stärker
    und können requires_more_context auslösen."""

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_multiple_signals_accumulate_penalties(self):
        """Zwei Signale → stärkerer Score-Abzug als eines allein."""
        # Nur extraordinary_claim
        claim_one = _make_claim("Alle Bürger sind betroffen.")
        # extraordinary_claim + elevated_burden (CAUSAL)
        claim_two = _make_claim(
            "Alle Bürger erkranken durch diese Maßnahme.",
            claim_type=ClaimType.CAUSAL,
        )
        r_one = self.validator.validate([claim_one])[0]
        r_two = self.validator.validate([claim_two])[0]
        assert r_two.claim_quality_score < r_one.claim_quality_score

    def test_requires_more_context_set_at_threshold(self):
        """Ab requires_context_signal_threshold aktiven Signalen → requires_more_context=True."""
        cfg = ClaimQualitySignalConfig(requires_context_signal_threshold=2)
        validator = ClaimValidator(cfg)

        # Claim der extraordinary_claim + elevated_burden auslöst (CAUSAL + Absolut)
        claim = _make_claim(
            "Alle Bürger erkranken durch diese Maßnahme.",
            claim_type=ClaimType.CAUSAL,
        )
        result = validator.validate([claim])[0]
        assert len(result.quality_signals) >= 2
        assert result.requires_more_context is True

    def test_requires_more_context_not_set_below_threshold(self):
        """Unter der Schwelle → requires_more_context bleibt False."""
        cfg = ClaimQualitySignalConfig(requires_context_signal_threshold=3)
        validator = ClaimValidator(cfg)

        # Nur ein Signal: extraordinary_claim
        claim = _make_claim("Alle Bürger sind betroffen.")
        result = validator.validate([claim])[0]
        assert len(result.quality_signals) < 3
        assert result.requires_more_context is False

    def test_quality_signals_field_populated(self):
        """quality_signals-Feld wird im Ergebnis-Claim gesetzt."""
        claim = _make_claim("Alle Impfungen sind gefährlich.", claim_type=ClaimType.CAUSAL)
        result = self.validator.validate([claim])[0]
        assert isinstance(result.quality_signals, list)
        assert "extraordinary_claim" in result.quality_signals
        assert "elevated_burden_of_proof" in result.quality_signals

    def test_valid_claim_has_empty_signals(self):
        """Vollständig spezifizierter, moderater Claim hat keine Signale."""
        claim = _make_claim_with_frame(
            "Die Bundesregierung hat 2023 das Klimapaket verabschiedet.",
            subject="Bundesregierung",
            institution="Bundesregierung",
            time_reference="2023",
            numbers=[],
        )
        result = self.validator.validate([claim])[0]
        assert result.quality_signals == []


# ── Opinion-Reklassifizierung ────────────────────────────────────────────────


class TestOpinionReclassification:
    """Post-Processing: Evaluative/subjektive Claims werden als OPINION reklassifiziert."""

    def setup_method(self):
        self.validator = ClaimValidator()

    @pytest.mark.parametrize("text", [
        "Steinmeier ist ein Spalter.",
        "Der Kanzler ist ein Lügner und Betrüger.",
        "Diese Politikerin ist eine Versagerin.",
        "Er ist ein Heuchler und Manipulator.",
    ])
    def test_character_judgments_reclassified(self, text: str):
        """Charakterurteile ('X ist ein Y') werden als OPINION erkannt."""
        claim = _make_claim(text, ClaimType.FACTUAL)
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.OPINION
        assert result.is_checkworthy is False

    @pytest.mark.parametrize("text", [
        "Die Politik ist schlecht und verantwortungslos.",
        "Der Vorschlag ist furchtbar und inkompetent.",
    ])
    def test_evaluative_adjectives_reclassified(self, text: str):
        """Wertende Adjektive werden als OPINION erkannt."""
        claim = _make_claim(text, ClaimType.FACTUAL)
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.OPINION

    def test_memory_judgment_reclassified(self):
        """'wird als X in Erinnerung bleiben' ist OPINION."""
        claim = _make_claim(
            "Steinmeier wird als Präsident der Spaltung in Erinnerung bleiben.",
            ClaimType.FACTUAL,
        )
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.OPINION

    def test_explicit_opinion_marker(self):
        """'Ich finde / meiner Meinung nach' ist OPINION."""
        claim = _make_claim("Ich finde, das ist eine Katastrophe.", ClaimType.FACTUAL)
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.OPINION

    @pytest.mark.parametrize("text", [
        "Steinmeier hat den Iran-Konflikt kritisiert.",
        "Die Kriminalität in Deutschland stieg 2023 um 5,5%.",
        "Deutschland hat 83 Millionen Einwohner.",
        "Die AfD hat bei der Europawahl 2024 über 15% erreicht.",
    ])
    def test_factual_claims_not_reclassified(self, text: str):
        """Echte Faktenbehauptungen bleiben FACTUAL."""
        claim = _make_claim(text, ClaimType.FACTUAL)
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.FACTUAL

    def test_already_opinion_stays_opinion(self):
        """Claims die schon OPINION sind, bleiben OPINION."""
        claim = _make_claim("Er ist ein Versager.", ClaimType.OPINION)
        result = self.validator.validate([claim])[0]
        assert result.type == ClaimType.OPINION
