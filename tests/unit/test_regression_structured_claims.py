"""Regressionstests für strukturierte Claim-Repräsentation.

Testet die neuen Komponenten:
    - ClaimFrame / ClaimSearchProfile (Struktur, Felder)
    - ClaimFrameExtractor._build_search_profile (frame → Queries)
    - ClaimDecomposer._has_context_integrity (Mini-Claim-Filter)
    - _build_search_queries_from_profile (profile → Queries)
    - _calibrate_confidence mit claim_quality_score
    - EvidenceQualitySignals.off_topic_rate

Konkrete Regressionserwartungen aus dem Ticket:
    1. Aus dem Hannover/15-Minuten-Stadt-Claim dürfen keine isolierten
       Mini-Claims wie "Die Höhe des Bußgeldes beträgt 250 Euro" entstehen.
    2. Aus dem Bildungsplan-Claim dürfen keine Meta-Claims wie
       "Es gibt Informationen darüber, wann die Verweigerung stattfindet" entstehen.
    3. Queries müssen den Kontext erhalten und dürfen nicht auf generische
       Einzelbegriffe kollabieren.
    4. Off-topic-Treffer müssen abgewertet werden.
    5. Confidence darf bei schwacher oder verschmutzter Evidenz nicht
       künstlich hoch bleiben.
"""

from __future__ import annotations

import pytest

from agents.claim_processor import (
    ClaimDecomposer,
    ClaimValidator,
    _build_search_profile,
)
from agents.fact_checker import (
    _build_search_queries,
    _build_search_queries_from_profile,
)
from agents.evidence_builder import (
    _compute_quality_signals,
    _relevance_score,
)
from agents.verdict_agent import _calibrate_confidence
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    GoogleFactCheckMatch,
    SourceConsensus,
)
from models.schemas import (
    ClaimFrame,
    ClaimSearchProfile,
    ClaimType,
    ProcessedClaim,
)
from tools.web_search import SearchResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_claim(text: str, claim_id: str = "C1", claim_type: ClaimType = ClaimType.FACTUAL) -> ProcessedClaim:
    return ProcessedClaim(id=claim_id, text=text, type=claim_type)


def _make_claim_with_frame(
    text: str,
    frame: ClaimFrame,
    claim_id: str = "C1",
    claim_type: ClaimType = ClaimType.FACTUAL,
    claim_quality: float = 1.0,
) -> ProcessedClaim:
    profile = _build_search_profile(frame)
    return ProcessedClaim(
        id=claim_id,
        text=text,
        type=claim_type,
        frame=frame,
        search_profile=profile,
        claim_quality_score=claim_quality,
    )


def _make_evidence_pack(
    items: list[EvidenceItem],
    quality: EvidenceQualitySignals | None = None,
) -> EvidencePack:
    return EvidencePack(
        claim_id="C1",
        claim_text="Test-Claim",
        web_results=items,
        evidence_quality=quality or EvidenceQualitySignals(),
    )


def _make_item(url: str, relevance: float, tier: int = 5) -> EvidenceItem:
    return EvidenceItem(
        source=EvidenceSource(url=url, title="Test", domain="test.de", domain_tier=tier),
        excerpt="Test-Auszug",
        relevance_score=relevance,
    )


# ── 1. ClaimFrame / ClaimSearchProfile ───────────────────────────────────────


class TestClaimFrame:
    def test_frame_fields_present(self):
        frame = ClaimFrame(
            raw_text="Der Stadtrat von Hannover plant 100 Fahrten Bußgeld 250 Euro.",
            institution="Stadtrat Hannover",
            location="Hannover",
            numbers=["100", "250"],
            sanction="Bußgeld 250 Euro",
            enforcement="Kameraüberwachung",
            policy_context="15-Minuten-Stadt",
        )
        assert frame.institution == "Stadtrat Hannover"
        assert frame.location == "Hannover"
        assert "100" in frame.numbers
        assert "250" in frame.numbers
        assert frame.sanction == "Bußgeld 250 Euro"
        assert frame.policy_context == "15-Minuten-Stadt"

    def test_frame_default_empty_fields(self):
        frame = ClaimFrame(raw_text="Irgendeine Behauptung.")
        assert frame.institution == ""
        assert frame.location == ""
        assert frame.numbers == []
        assert frame.sanction == ""


class TestBuildSearchProfile:
    """_build_search_profile muss aus dem Frame ein sinnvolles Suchprofil bauen."""

    def test_hannover_profile_contains_core_entities(self):
        """Das Profil für den Hannover-Frame muss Institution und Ort enthalten."""
        frame = ClaimFrame(
            raw_text="Hannover 15-Minuten-Stadt 100 Autofahrten 250 Euro Bußgeld",
            institution="Stadtrat Hannover",
            location="Hannover",
            numbers=["100", "250"],
            sanction="Bußgeld 250 Euro",
            enforcement="Kameraüberwachung",
            policy_context="15-Minuten-Stadt",
        )
        profile = _build_search_profile(frame)
        assert "Stadtrat Hannover" in profile.core_entities or "Stadtrat Hannover" in profile.institutions
        assert "Hannover" in profile.locations
        assert "15-Minuten-Stadt" in profile.policy_terms
        assert "100" in profile.number_terms or "250" in profile.number_terms
        assert profile.sanction_terms  # mindestens Bußgeld oder Kamera

    def test_profile_official_source_hint_hannover(self):
        """Hannover-Frame muss site:hannover.de als Hint erzeugen."""
        frame = ClaimFrame(
            raw_text="Hannover Stadtrat 15-Minuten-Stadt",
            institution="Stadtrat Hannover",
            location="Hannover",
            policy_context="15-Minuten-Stadt",
        )
        profile = _build_search_profile(frame)
        assert any("hannover.de" in h for h in profile.official_source_hints)

    def test_profile_fact_check_hints_always_present(self):
        """Fact-Check-Hints (correctiv.org, dpa-factchecking.com) müssen immer gesetzt sein."""
        frame = ClaimFrame(raw_text="Irgendeine Behauptung", institution="Testbehörde")
        profile = _build_search_profile(frame)
        assert any("correctiv.org" in h for h in profile.fact_check_hints)


# ── 2. Decomposer Context-Integrity-Filter ────────────────────────────────────


class TestClaimDecomposerIntegrity:
    """Mini-Claims ohne Kontext-Anker müssen gefiltert werden."""

    def test_mini_claim_no_context_rejected(self):
        """'Die Höhe des Bußgeldes beträgt 250 Euro.' → kein Kontext-Anker → verwerfen."""
        frame = ClaimFrame(
            raw_text="Der Stadtrat von Hannover will im Rahmen der 15-Minuten-Stadt Autofahrten begrenzen.",
            institution="Stadtrat Hannover",
            location="Hannover",
            policy_context="15-Minuten-Stadt",
        )
        original = _make_claim_with_frame(
            "Der Stadtrat von Hannover will im Rahmen der 15-Minuten-Stadt Autofahrten auf 100 begrenzen.",
            frame=frame,
        )
        mini_claim_text = "Die Höhe des Bußgeldes beträgt 250 Euro."
        result = ClaimDecomposer._has_context_integrity(mini_claim_text, original)
        assert result is False, "Mini-Claim ohne Kontext-Anker darf nicht als integer gelten"

    def test_context_rich_claim_accepted(self):
        """Vollständiger Claim mit Institution und Kontext → als integer akzeptieren."""
        frame = ClaimFrame(
            raw_text="Stadtrat Hannover 15-Minuten-Stadt 100 Autofahrten",
            institution="Stadtrat Hannover",
            location="Hannover",
            policy_context="15-Minuten-Stadt",
        )
        original = _make_claim_with_frame(
            "Der Stadtrat von Hannover plant im Rahmen der 15-Minuten-Stadt die Autofahrten zu begrenzen.",
            frame=frame,
        )
        rich_claim = (
            "Verstöße gegen die Fahrtenbeschränkung des Stadtrats Hannover sollen "
            "automatisch per Kameraüberwachung mit 250 Euro Bußgeld geahndet werden."
        )
        result = ClaimDecomposer._has_context_integrity(rich_claim, original)
        assert result is True, "Kontext-reicher Claim muss als integer gelten"

    def test_too_short_claim_rejected(self):
        """Zu kurze Claims (< 40 Zeichen) sind nie integer."""
        original = _make_claim("Ein kurzer Text zur Orientierung.")
        assert ClaimDecomposer._has_context_integrity("Kurz.", original) is False
        assert ClaimDecomposer._has_context_integrity("Zu kurz.", original) is False

    def test_claim_without_entity_rejected(self):
        """Claims ohne Eigennamen und ohne Zahlen dürfen nicht als integer gelten."""
        original = _make_claim("Ein konkreter Claim mit Kontext und Details zur Prüfung.")
        no_entity_text = "die kosten wurden erhöht und es gibt konsequenzen für verstöße."
        result = ClaimDecomposer._has_context_integrity(no_entity_text, original)
        assert result is False


# ── 3. ClaimValidator: Meta-Claims aus Bildungsplan-Szenario ──────────────────


class TestRegressionEducationScenario:
    """Regression: Meta-Claims aus dem Bildungsplan-Szenario müssen gefiltert werden."""

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_meta_claim_verweigerung_rejected(self):
        """'Es gibt Informationen darüber, wann die Verweigerung stattfindet.'
        muss als Meta-Claim erkannt und gefiltert werden."""
        claim = ProcessedClaim(
            id="C1",
            text="Es gibt Informationen darüber, wann die Verweigerung stattfindet.",
            type=ClaimType.FACTUAL,
        )
        results = self.validator.validate([claim])
        assert results[0].is_valid_claim is False

    def test_meta_claim_gender_rollenspiele_rejected(self):
        """'Es gibt Informationen darüber, wie Gender-Transition-Rollenspiele durchgeführt werden.'
        muss als Meta-Claim gefiltert werden."""
        claim = ProcessedClaim(
            id="C2",
            text="Es gibt Informationen darüber, wie Gender-Transition-Rollenspiele durchgeführt werden.",
            type=ClaimType.FACTUAL,
        )
        results = self.validator.validate([claim])
        assert results[0].is_valid_claim is False

    def test_real_education_claim_accepted(self):
        """Der echte Bildungsplan-Claim mit konkreter Aussage muss gültig sein."""
        claim = ProcessedClaim(
            id="C3",
            text="Der Rahmenlehrplan für die 2. Klasse sieht laut Text Gender-Transition-Rollenspiele vor.",
            type=ClaimType.FACTUAL,
        )
        results = self.validator.validate([claim])
        assert results[0].is_valid_claim is True

    def test_sanction_education_claim_accepted(self):
        """Sanktionsbehauptung mit Kontext muss gültig sein."""
        claim = ProcessedClaim(
            id="C4",
            text="Bei Verweigerung solcher Unterrichtsinhalte drohen Eltern laut Text Bußgelder.",
            type=ClaimType.FACTUAL,
        )
        results = self.validator.validate([claim])
        assert results[0].is_valid_claim is True


# ── 4. Profile-basierte Query-Generierung ─────────────────────────────────────


class TestProfileBasedQueries:
    """Queries müssen den Kontext erhalten und nicht auf Einzelbegriffe kollabieren."""

    def test_hannover_queries_contain_context(self):
        """Queries für Hannover/15-Minuten-Stadt müssen Kontext-Anker enthalten."""
        frame = ClaimFrame(
            raw_text="Hannover 15-Minuten-Stadt 100 Autofahrten",
            institution="Stadtrat Hannover",
            location="Hannover",
            numbers=["100", "250"],
            sanction="Bußgeld 250 Euro",
            policy_context="15-Minuten-Stadt",
        )
        claim = _make_claim_with_frame(
            "Der Stadtrat von Hannover will im Rahmen der 15-Minuten-Stadt die Zahl der "
            "jährlichen Autofahrten pro Bürger auf 100 begrenzen.",
            frame=frame,
            claim_type=ClaimType.FACTUAL,
        )
        queries = _build_search_queries_from_profile(claim)

        assert queries, "Es müssen Queries erzeugt werden"

        # Keine Query darf nur aus einem generischen Einzelbegriff bestehen
        for q in queries:
            words = q.split()
            assert len(words) >= 2, f"Query zu kurz/generisch: '{q}'"

        # Mindestens eine Query muss Kontext-Anker enthalten
        all_text = " ".join(queries).lower()
        has_context = (
            "hannover" in all_text
            or "stadtrat" in all_text
            or "15-minuten" in all_text
        )
        assert has_context, f"Keine Query enthält Kontext-Anker. Queries: {queries}"

    def test_fallback_to_fulltext_without_profile(self):
        """Ohne SearchProfile: Fallback auf reguläre Query-Logik."""
        claim = _make_claim("Der Stadtrat Hannover plant Fahrtenbeschränkung.")
        # _build_search_queries_from_profile mit leerem profile
        queries = _build_search_queries_from_profile(claim)
        # Kein Profil → leere Liste erwartet
        assert queries == []

    def test_queries_via_build_search_queries_use_profile(self):
        """_build_search_queries soll bei vorhandenem Profile das Profile nutzen."""
        frame = ClaimFrame(
            raw_text="Stadtrat Hannover 15-Minuten-Stadt",
            institution="Stadtrat Hannover",
            location="Hannover",
            policy_context="15-Minuten-Stadt",
            numbers=["100"],
        )
        claim = _make_claim_with_frame(
            "Der Stadtrat von Hannover plant Fahrtenbeschränkung.",
            frame=frame,
        )
        queries = _build_search_queries(claim)
        # Wenn Profil vorhanden: soll keine trivialen 1-Wort-Queries erzeugen
        for q in queries:
            assert len(q.split()) >= 2, f"Trivialer Query aus Profil: '{q}'"


# ── 5. Confidence-Kalibrierung ────────────────────────────────────────────────


class TestConfidenceCalibrationWithClaimQuality:
    """Confidence muss bei schlechter Claim-Qualität gedeckelt/bestraft werden."""

    def _base_pack(self, off_topic_rate: float = 0.0) -> EvidencePack:
        items = [_make_item(f"https://example.de/{i}", relevance=0.7, tier=3) for i in range(3)]
        quality = EvidenceQualitySignals(
            has_primary_sources=True,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.AGREEING,
            overall_quality=0.6,
            off_topic_rate=off_topic_rate,
        )
        return _make_evidence_pack(items, quality)

    def test_good_claim_quality_no_extra_penalty(self):
        """Bei hoher Claim-Qualität (1.0) kein Penalty für Claim-Qualität."""
        pack = self._base_pack()
        conf, reasons = _calibrate_confidence(0.85, pack, None, claim_quality_score=1.0)
        # Kein Penalty-Grund für Claim-Qualität in reasons
        assert not any("Claim-Qualität" in r for r in reasons)

    def test_low_claim_quality_applies_penalty(self):
        """Niedrige Claim-Qualität (< 0.70) muss Penalty auslösen."""
        pack = self._base_pack()
        conf, reasons = _calibrate_confidence(0.85, pack, None, claim_quality_score=0.50)
        assert any("Claim-Qualität" in r for r in reasons), \
            f"Kein Claim-Qualitäts-Penalty in reasons: {reasons}"

    def test_very_low_claim_quality_ceiling_applies(self):
        """Sehr niedrige Claim-Qualität (< 0.50) muss Ceiling auslösen."""
        pack = self._base_pack()
        conf, reasons = _calibrate_confidence(0.90, pack, None, claim_quality_score=0.40)
        # Ceiling 0.72 muss greifen
        assert conf <= 0.72, f"Confidence {conf} überschreitet Ceiling bei schlechter Claim-Qualität"

    def test_high_offtopic_rate_caps_confidence(self):
        """Off-topic-Rate > 50% muss Confidence auf max 0.75 begrenzen."""
        pack = self._base_pack(off_topic_rate=0.8)
        conf, reasons = _calibrate_confidence(0.90, pack, None)
        assert conf <= 0.75, f"Confidence {conf} zu hoch bei 80% Off-topic-Rate"
        assert any("Off-topic" in r or "off-topic" in r.lower() for r in reasons), \
            f"Kein Off-topic-Ceiling-Grund in reasons: {reasons}"

    def test_weak_evidence_caps_confidence(self):
        """Schwache Evidenzqualität (< 0.30) muss Confidence auf max 0.70 begrenzen."""
        items = [_make_item(f"https://example.de/{i}", relevance=0.1) for i in range(3)]
        quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            overall_quality=0.15,
            off_topic_rate=0.8,
        )
        pack = _make_evidence_pack(items, quality)
        conf, _ = _calibrate_confidence(0.95, pack, None, claim_quality_score=1.0)
        assert conf <= 0.70, f"Confidence {conf} zu hoch bei sehr schwacher Evidenz"

    def test_no_artificial_inflation_with_contaminated_evidence(self):
        """Verschmutzte Evidenz + schwache Claim-Qualität darf keine hohe Confidence erzeugen."""
        items = [_make_item(f"https://example.de/{i}", relevance=0.05) for i in range(5)]
        quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            overall_quality=0.10,
            off_topic_rate=1.0,
        )
        pack = _make_evidence_pack(items, quality)
        conf, reasons = _calibrate_confidence(0.95, pack, None, claim_quality_score=0.40)
        # Bei so schlechter Lage darf Confidence max ~0.55 sein
        assert conf <= 0.60, \
            f"Confidence {conf} viel zu hoch bei verschmutzter Evidenz und schlechter Claim-Qualität. Reasons: {reasons}"


# ── 6. EvidenceQualitySignals.off_topic_rate ──────────────────────────────────


class TestEvidenceQualityOffTopicRate:
    """off_topic_rate und avg_top5_relevance müssen korrekt berechnet werden."""

    def test_off_topic_rate_computed(self):
        """_compute_quality_signals muss off_topic_rate korrekt setzen."""
        # 3 von 5 Items haben relevance < 0.2 → Rate = 0.6
        items = [
            _make_item("https://irrelevant1.de", relevance=0.05),
            _make_item("https://irrelevant2.de", relevance=0.10),
            _make_item("https://irrelevant3.de", relevance=0.15),
            _make_item("https://relevant1.de", relevance=0.80, tier=3),
            _make_item("https://relevant2.de", relevance=0.75, tier=2),
        ]
        quality = _compute_quality_signals(items, [])
        assert quality.off_topic_rate == pytest.approx(0.6, abs=0.01)

    def test_zero_off_topic_rate_for_clean_results(self):
        """Alle relevanten Items → off_topic_rate = 0.0."""
        items = [_make_item(f"https://relevant{i}.de", relevance=0.7, tier=3) for i in range(5)]
        quality = _compute_quality_signals(items, [])
        assert quality.off_topic_rate == pytest.approx(0.0, abs=0.01)

    def test_avg_top5_relevance_computed(self):
        """avg_top5_relevance muss korrekt berechnet werden."""
        items = [
            _make_item("https://a.de", relevance=0.8),
            _make_item("https://b.de", relevance=0.6),
            _make_item("https://c.de", relevance=0.4),
            _make_item("https://d.de", relevance=0.2),
            _make_item("https://e.de", relevance=0.0),
        ]
        quality = _compute_quality_signals(items, [])
        expected_avg = (0.8 + 0.6 + 0.4 + 0.2 + 0.0) / 5
        assert quality.avg_top5_relevance == pytest.approx(expected_avg, abs=0.01)

    def test_off_topic_reduces_overall_quality(self):
        """Off-topic-Treffer müssen overall_quality reduzieren."""
        good_items = [_make_item(f"https://g{i}.de", relevance=0.8, tier=2) for i in range(5)]
        bad_items = [_make_item(f"https://b{i}.de", relevance=0.05) for i in range(5)]

        quality_good = _compute_quality_signals(good_items, [])
        quality_bad = _compute_quality_signals(bad_items, [])
        assert quality_good.overall_quality > quality_bad.overall_quality


# ── 7. Hannover-Regression: Kein isolierter Mini-Claim ───────────────────────


class TestHannoverRegression:
    """Regression: Der Hannover/15-Minuten-Stadt-Claim darf nicht in Mini-Claims
    zerfallen, die Kontext verlieren."""

    def test_bussgeldhöhe_mini_claim_is_invalid(self):
        """'Die Höhe des Bußgeldes beträgt 250 Euro.' ist kein valider Claim."""
        validator = ClaimValidator()
        claim = ProcessedClaim(
            id="C1",
            text="Die Höhe des Bußgeldes beträgt 250 Euro.",
            type=ClaimType.STATISTICAL,
        )
        results = validator.validate([claim])
        # Dieser Claim ist entweder ungültig (validator erkennt fehlende Entität)
        # oder hat niedrige quality_score (< 0.7)
        c = results[0]
        insufficient = not c.is_valid_claim or c.claim_quality_score < 0.7
        assert insufficient, (
            f"Mini-Claim ohne Kontext sollte ungültig oder schwach sein. "
            f"is_valid={c.is_valid_claim}, quality={c.claim_quality_score}"
        )

    def test_full_hannover_claim_is_valid(self):
        """Der vollständige Hannover-Claim mit Kontext muss valide und berkannt sein."""
        validator = ClaimValidator()
        claim = ProcessedClaim(
            id="C2",
            text=(
                "Der Stadtrat von Hannover will im Rahmen der 15-Minuten-Stadt die Zahl "
                "der jährlichen Autofahrten pro Bürger auf 100 begrenzen und Verstöße "
                "per Kameraüberwachung mit 250 Euro Bußgeld ahnden."
            ),
            type=ClaimType.STATISTICAL,
        )
        results = validator.validate([claim])
        assert results[0].is_valid_claim is True
        assert results[0].claim_quality_score >= 0.7

    def test_hannover_queries_not_single_word(self):
        """Queries für den Hannover-Claim dürfen keine trivialen Einzelwörter sein."""
        frame = ClaimFrame(
            raw_text="Hannover 15-Minuten-Stadt 100 Autofahrten 250 Bußgeld",
            institution="Stadtrat Hannover",
            location="Hannover",
            numbers=["100", "250"],
            sanction="Bußgeld 250 Euro",
            enforcement="Kameraüberwachung",
            policy_context="15-Minuten-Stadt",
        )
        claim = _make_claim_with_frame(
            "Der Stadtrat Hannover plant die 15-Minuten-Stadt.",
            frame=frame,
            claim_type=ClaimType.FACTUAL,
        )
        queries = _build_search_queries_from_profile(claim)
        bad_queries = [q for q in queries if len(q.split()) < 2]
        assert not bad_queries, f"Triviale Queries gefunden: {bad_queries}"
