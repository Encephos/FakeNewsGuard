"""Tests für die generalisierte Hint-Generierung.

Prüft, dass ``_derive_source_hints`` und ``_infer_jurisdiction`` in
``agents/claim_processor.py`` korrekte Hints aus SourceRegistry und
``domain_tiers.yaml`` ableiten – ohne hardcodierte Stadt-/Institutions-Domains.
"""

from __future__ import annotations

import pytest

from models.schemas import ClaimFrame
from agents.claim_processor import _derive_source_hints, _infer_jurisdiction
from tools.data_loader import fact_checker_domains, government_domains
from tools.sources.registry import SourceRegistry


# ── _infer_jurisdiction ──────────────────────────────────────────────────────


class TestInferJurisdiction:
    """Jurisdiktions-Erkennung aus Location/Institution-Strings."""

    def test_de_from_location(self):
        assert _infer_jurisdiction("berlin", "") == "de"

    def test_de_from_institution(self):
        assert _infer_jurisdiction("", "bundesregierung") == "de"

    def test_eu_from_institution(self):
        assert _infer_jurisdiction("", "europäische kommission") == "eu"

    def test_uk_from_location(self):
        assert _infer_jurisdiction("london", "") == "uk"

    def test_us_from_institution(self):
        assert _infer_jurisdiction("", "fda") == "us"

    def test_global_fallback(self):
        assert _infer_jurisdiction("tokio", "regierung") == "global"


# ── _derive_source_hints: Institutions-Match ─────────────────────────────────


class TestDeriveSourceHintsInstitution:
    """Institutions-Match gegen government_domains() aus domain_tiers.yaml."""

    def test_institution_bundesregierung(self):
        frame = ClaimFrame(
            raw_text="Test",
            institution="Bundesregierung",
        )
        official, _ = _derive_source_hints(frame)
        assert any("bundesregierung.de" in h for h in official)

    def test_institution_bundestag(self):
        frame = ClaimFrame(
            raw_text="Test",
            institution="Bundestag",
        )
        official, _ = _derive_source_hints(frame)
        assert any("bundestag.de" in h for h in official)

    def test_institution_destatis(self):
        frame = ClaimFrame(
            raw_text="Statistik",
            institution="Destatis",
        )
        official, _ = _derive_source_hints(frame)
        assert any("destatis.de" in h for h in official)


# ── _derive_source_hints: Jurisdiktions-Fallback ────────────────────────────


class TestDeriveSourceHintsJurisdiction:
    """Jurisdiktions-basierte Hints aus SourceRegistry."""

    def test_jurisdiction_de_fallback(self):
        """Location=Hamburg, keine Institutions-Match → DE-Registry-Quellen."""
        frame = ClaimFrame(
            raw_text="Hamburg Wirtschaftsdaten",
            location="Hamburg",
        )
        official, _ = _derive_source_hints(frame)
        assert official, "Mindestens ein Hint für DE-Jurisdiktion erwartet"
        # Hints müssen aus Registry-Quellen mit Jurisdiktion DE stammen
        registry_domains = {
            d for src in SourceRegistry.by_jurisdiction_safe("de")
            for d in src.classifier_domains
        }
        for hint in official:
            domain = hint.replace("site:", "")
            assert domain in registry_domains, (
                f"Hint '{hint}' nicht in DE-Registry-Quellen"
            )

    def test_jurisdiction_eu(self):
        """Europäische Kommission → EU-Registry-Quellen."""
        frame = ClaimFrame(
            raw_text="EU-Statistik",
            institution="Europäische Kommission",
        )
        official, _ = _derive_source_hints(frame)
        assert official
        # Mindestens ein EU-Source-Hint
        eu_domains = {
            d for src in SourceRegistry.by_jurisdiction_safe("eu")
            for d in src.classifier_domains
        }
        assert any(
            hint.replace("site:", "") in eu_domains for hint in official
        ), f"Kein EU-Registry-Hint gefunden in {official}"

    def test_jurisdiction_us(self):
        """FDA-Institution → US-Registry-Quellen."""
        frame = ClaimFrame(
            raw_text="FDA drug recall",
            institution="FDA",
        )
        official, _ = _derive_source_hints(frame)
        assert official
        us_domains = {
            d for src in SourceRegistry.by_jurisdiction_safe("us")
            for d in src.classifier_domains
        }
        assert any(
            hint.replace("site:", "") in us_domains for hint in official
        ), f"Kein US-Registry-Hint gefunden in {official}"

    def test_jurisdiction_uk(self):
        """London-Location → UK-Registry-Quellen."""
        frame = ClaimFrame(
            raw_text="UK company data",
            location="London",
        )
        official, _ = _derive_source_hints(frame)
        assert official
        uk_domains = {
            d for src in SourceRegistry.by_jurisdiction_safe("uk")
            for d in src.classifier_domains
        }
        assert any(
            hint.replace("site:", "") in uk_domains for hint in official
        ), f"Kein UK-Registry-Hint gefunden in {official}"


# ── _derive_source_hints: Statistik-Fallback ────────────────────────────────


class TestDeriveSourceHintsStatistical:
    """Statistische Claims mit Zahlen → destatis.de als Fallback."""

    def test_statistical_fallback(self):
        frame = ClaimFrame(
            raw_text="Eine Statistik",
            numbers=["100", "42"],
        )
        official, _ = _derive_source_hints(frame)
        assert any("destatis.de" in h for h in official), (
            f"Statistik-Fallback erwartet destatis.de, got {official}"
        )


# ── _derive_source_hints: Keine Stadt-Domains ───────────────────────────────


class TestDeriveSourceHintsNoCityDomains:
    """Hardcodierte Stadt-Domains dürfen nicht mehr erzeugt werden."""

    _FORBIDDEN = {"hannover.de", "berlin.de", "bildungsserver.berlin-brandenburg.de"}

    @pytest.mark.parametrize("location,institution", [
        ("Hannover", "Stadtrat Hannover"),
        ("Berlin", "Senat Berlin"),
        ("Berlin", "Bildungsministerium"),
        ("München", ""),
        ("", ""),
    ])
    def test_no_city_domains(self, location: str, institution: str):
        kwargs: dict = {"raw_text": "Test"}
        if location:
            kwargs["location"] = location
        if institution:
            kwargs["institution"] = institution
        frame = ClaimFrame(**kwargs)
        official, _ = _derive_source_hints(frame)
        for hint in official:
            domain = hint.replace("site:", "")
            assert domain not in self._FORBIDDEN, (
                f"Verbotene Stadt-Domain '{domain}' in Hints für "
                f"location={location!r}, institution={institution!r}"
            )


# ── _derive_source_hints: Fact-Check-Hints ──────────────────────────────────


class TestDeriveSourceHintsFactCheck:
    """Fact-Check-Hints müssen aus domain_tiers.yaml tier4 stammen."""

    def test_fact_check_from_yaml(self):
        frame = ClaimFrame(raw_text="Beliebige Behauptung")
        _, fact_check = _derive_source_hints(frame)
        assert fact_check, "Fact-Check-Hints dürfen nicht leer sein"
        fc_set = set(fact_checker_domains())
        for hint in fact_check:
            domain = hint.replace("site:", "")
            assert domain in fc_set, f"'{domain}' nicht in tier4_fact_checkers"

    def test_fact_check_max_two(self):
        frame = ClaimFrame(raw_text="Test")
        _, fact_check = _derive_source_hints(frame)
        assert len(fact_check) <= 2

    def test_fact_check_deterministic(self):
        """Gleicher Input → gleiche Fact-Check-Hints (sortiert)."""
        frame = ClaimFrame(raw_text="Determinismus-Test")
        _, fc1 = _derive_source_hints(frame)
        _, fc2 = _derive_source_hints(frame)
        assert fc1 == fc2


# ── SourceRegistry.by_jurisdiction ───────────────────────────────────────────


class TestSourceRegistryJurisdiction:
    """Neue by_jurisdiction-Methoden auf SourceRegistry."""

    def test_by_jurisdiction_de(self):
        sources = SourceRegistry.by_jurisdiction("de")
        ids = {s.source_id for s in sources}
        assert "eurostat" in ids, "Eurostat muss in DE-Jurisdiktion enthalten sein"

    def test_by_jurisdiction_us(self):
        sources = SourceRegistry.by_jurisdiction("us")
        ids = {s.source_id for s in sources}
        assert "openfda" in ids
        assert "uspto" in ids

    def test_by_jurisdiction_eu(self):
        sources = SourceRegistry.by_jurisdiction("eu")
        ids = {s.source_id for s in sources}
        assert "eurostat" in ids
        assert "eur_lex" in ids

    def test_by_jurisdiction_uk(self):
        sources = SourceRegistry.by_jurisdiction("uk")
        ids = {s.source_id for s in sources}
        assert "companies_house" in ids

    def test_by_jurisdiction_safe_excludes_check_terms(self):
        """by_jurisdiction_safe schließt CHECK_TERMS-Quellen aus."""
        safe = SourceRegistry.by_jurisdiction_safe("global")
        safe_ids = {s.source_id for s in safe}
        # arxiv und pubmed sind CHECK_TERMS
        assert "arxiv" not in safe_ids, "arXiv (CHECK_TERMS) darf nicht in safe sein"
        assert "pubmed" not in safe_ids, "PubMed (CHECK_TERMS) darf nicht in safe sein"

    def test_by_jurisdiction_sorted_by_authority(self):
        sources = SourceRegistry.by_jurisdiction("us")
        weights = [s.authority_weight for s in sources]
        assert weights == sorted(weights, reverse=True)

    def test_all_sources_have_jurisdictions(self):
        """Alle 14 Registry-Quellen müssen mindestens eine Jurisdiktion haben."""
        for src in SourceRegistry.all():
            assert src.jurisdictions, (
                f"Source '{src.source_id}' hat keine Jurisdiktion"
            )
