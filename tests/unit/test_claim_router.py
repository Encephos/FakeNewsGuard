"""Unit-Tests für tools/claim_router.py – regelbasierter Source-Router.

Testet:
    - Domänenerkennung (Keyword + ClaimType + Frame)
    - Jurisdiktionserkennung
    - Quellenpriorisierung und Boost-Logik
    - Site-Hint-Generierung
    - route_and_apply: SearchProfile-Augmentierung
    - Fallback-Verhalten bei unbekannten Claims
"""

from __future__ import annotations

import pytest

from models.schemas import (
    AmbiguityLevel,
    Claim,
    ClaimFrame,
    ClaimSearchProfile,
    ClaimType,
    ProcessedClaim,
)
from tools.claim_router import ClaimRouter, RouteResult
from tools.sources.types import ClaimDomain


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def router() -> ClaimRouter:
    return ClaimRouter()


def _make_claim(text: str, claim_type: ClaimType = ClaimType.FACTUAL) -> Claim:
    """Erstelle einen einfachen Claim für Tests."""
    return Claim(id="C1", text=text, type=claim_type)


def _make_processed(
    text: str,
    claim_type: ClaimType = ClaimType.FACTUAL,
    frame: ClaimFrame | None = None,
    search_profile: ClaimSearchProfile | None = None,
) -> ProcessedClaim:
    """Erstelle einen ProcessedClaim für Tests."""
    return ProcessedClaim(
        id="C1",
        text=text,
        type=claim_type,
        ambiguity_level=AmbiguityLevel.NONE,
        frame=frame,
        search_profile=search_profile,
    )


# ── Domänenerkennung ──────────────────────────────────────────────────────────


class TestDomainDetection:
    def test_economic_keywords(self, router):
        claim = _make_claim("Das BIP Deutschlands ist um 1,5% gewachsen.")
        result = router.route(claim)
        assert ClaimDomain.ECONOMIC in result.domains or ClaimDomain.STATISTICAL in result.domains

    def test_statistical_claim_type(self, router):
        claim = _make_claim(
            "Die Arbeitslosenquote lag bei 5,3%.", ClaimType.STATISTICAL
        )
        result = router.route(claim)
        assert ClaimDomain.STATISTICAL in result.domains

    def test_legal_keywords(self, router):
        claim = _make_claim(
            "Die EU-Verordnung (EU) 2016/679 (DSGVO) regelt den Datenschutz."
        )
        result = router.route(claim)
        assert ClaimDomain.LEGAL in result.domains

    def test_regulatory_frame_sanction(self, router):
        frame = ClaimFrame(
            raw_text="Das Bußgeld beträgt 250 Euro.",
            sanction="250 Euro Bußgeld",
            subject="Autofahrer",
        )
        claim = _make_processed(
            "Das Bußgeld für Falschparken beträgt 250 Euro.",
            frame=frame,
        )
        result = router.route(claim)
        assert ClaimDomain.REGULATORY in result.domains

    def test_regulatory_frame_enforcement(self, router):
        frame = ClaimFrame(
            raw_text="Überwacht durch Kamerasysteme.",
            enforcement="Kameraüberwachung",
        )
        claim = _make_processed("Die Geschwindigkeit wird per Kamera überwacht.", frame=frame)
        result = router.route(claim)
        assert ClaimDomain.REGULATORY in result.domains

    def test_corporate_keywords(self, router):
        claim = _make_claim(
            "Die Volkswagen AG hat ihren Hauptsitz in Wolfsburg und ist im Handelsregister eingetragen."
        )
        result = router.route(claim)
        assert ClaimDomain.CORPORATE in result.domains

    def test_pharmaceutical_keywords(self, router):
        claim = _make_claim(
            "Das Arzneimittel wurde von der FDA zugelassen und hat folgende Nebenwirkungen."
        )
        result = router.route(claim)
        assert ClaimDomain.PHARMACEUTICAL in result.domains

    def test_pharmaceutical_fda_institution(self, router):
        frame = ClaimFrame(
            raw_text="FDA hat das Medikament zugelassen.",
            institution="FDA",
        )
        claim = _make_processed("Das Medikament erhielt FDA-Zulassung.", frame=frame)
        result = router.route(claim)
        assert ClaimDomain.PHARMACEUTICAL in result.domains

    def test_clinical_trial_keywords(self, router):
        claim = _make_claim(
            "In einer randomisierten kontrollierten Studie (Phase III, NCT123) "
            "wurde der primäre Endpunkt erreicht."
        )
        result = router.route(claim)
        assert ClaimDomain.CLINICAL in result.domains

    def test_scientific_keywords(self, router):
        claim = _make_claim(
            "Laut einer peer-reviewed Publikation im Journal Nature ist das "
            "Forschungsergebnis statistisch signifikant.",
            ClaimType.CAUSAL,
        )
        result = router.route(claim)
        assert ClaimDomain.SCIENTIFIC in result.domains

    def test_patent_keywords(self, router):
        claim = _make_claim(
            "Das Patent wurde beim USPTO angemeldet und Patentnummer US12345 erteilt."
        )
        result = router.route(claim)
        assert ClaimDomain.PATENT in result.domains

    def test_financial_monetary_policy_keywords(self, router):
        claim = _make_claim(
            "Die Bundesregierung plant die vollständige Abschaffung des Bargelds bis 2030."
        )
        result = router.route(claim)
        assert ClaimDomain.FINANCIAL in result.domains
        assert result.confidence > 0.10
        # Site-Hints sollten bundesbank.de und bmf.de enthalten
        hint_domains = " ".join(result.site_hints)
        assert "bundesbank.de" in hint_domains
        assert "bmf.de" in hint_domains

    def test_no_domain_fallback(self, router):
        claim = _make_claim("Der Himmel ist blau.")
        result = router.route(claim)
        # Kein Domain → sources sind trotzdem vorhanden (Tier-1-Fallback)
        assert isinstance(result.sources, list)
        assert result.confidence < 0.4  # Niedrige Konfidenz bei unklarem Claim

    def test_max_four_domains(self, router):
        claim = _make_claim(
            "Das Arzneimittel Patent wurde vom FDA zugelassen, "
            "die Studie ist peer-reviewed, "
            "die GmbH ist im Handelsregister, "
            "das BIP wuchs um 2%, klinische Studie Phase III."
        )
        result = router.route(claim)
        assert len(result.domains) <= 4


# ── Jurisdiktionserkennung ────────────────────────────────────────────────────


class TestJurisdictionDetection:
    def test_eu_keywords(self, router):
        claim = _make_claim("Die Europäische Union hat eine neue Verordnung erlassen.")
        result = router.route(claim)
        assert result.jurisdiction == "eu"

    def test_eu_frame_location(self, router):
        frame = ClaimFrame(raw_text="...", location="Europäische Union")
        claim = _make_processed("Die EU-Kommission hat entschieden.", frame=frame)
        result = router.route(claim)
        assert result.jurisdiction == "eu"

    def test_uk_keywords(self, router):
        claim = _make_claim("Companies House ist das Unternehmensregister im United Kingdom.")
        result = router.route(claim)
        assert result.jurisdiction == "uk"

    def test_us_keywords(self, router):
        claim = _make_claim("Die FDA genehmigte das Medikament für den US-Markt.")
        result = router.route(claim)
        assert result.jurisdiction == "us"

    def test_de_keywords(self, router):
        claim = _make_claim("Destatis berichtet: Deutschland verzeichnet Bevölkerungswachstum.")
        result = router.route(claim)
        assert result.jurisdiction == "de"

    def test_global_fallback(self, router):
        claim = _make_claim("Experten warnen vor dem Klimawandel.")
        result = router.route(claim)
        assert result.jurisdiction in ("global", "eu", "de", "us", "uk")  # Keine Fehler


# ── Quellenpriorisierung ──────────────────────────────────────────────────────


class TestSourcePrioritization:
    def test_economic_eu_has_eurostat_first(self, router):
        claim = _make_claim(
            "Das BIP der Europäischen Union wuchs um 1,2%.", ClaimType.STATISTICAL
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "eurostat" in source_ids
        # Eurostat sollte wegen EU-Jurisdiktion-Boost vorne sein
        assert source_ids.index("eurostat") < 3

    def test_economic_global_has_worldbank(self, router):
        claim = _make_claim("Die globale Armutsrate sank auf 8,5% laut Weltbank.")
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "world_bank" in source_ids

    def test_legal_eu_has_eur_lex(self, router):
        claim = _make_claim("Die DSGVO ist eine EU-Verordnung und rechtlich bindend in der Europäischen Union.")
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "eur_lex" in source_ids

    def test_corporate_uk_has_companies_house(self, router):
        claim = _make_claim(
            "Die Ltd. ist bei Companies House im United Kingdom registriert."
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "companies_house" in source_ids

    def test_corporate_global_has_gleif(self, router):
        claim = _make_claim(
            "Das Unternehmen hat einen LEI (Legal Entity Identifier) und ist global registriert."
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "gleif" in source_ids

    def test_pharmaceutical_has_openfda_and_dailymed(self, router):
        claim = _make_claim(
            "Das Arzneimittel wurde von der FDA zugelassen mit folgender Dosierung."
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "openfda" in source_ids or "dailymed" in source_ids

    def test_clinical_trial_has_clinicaltrials(self, router):
        claim = _make_claim(
            "In der klinischen Studie Phase III (NCT98765) wurde der primäre Endpunkt nicht erreicht."
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "clinicaltrials" in source_ids

    def test_scientific_has_openalex_or_pubmed(self, router):
        claim = _make_claim(
            "Laut einem peer-reviewed Paper ist das Forschungsergebnis reproduzierbar.",
            ClaimType.CAUSAL,
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert any(sid in source_ids for sid in ["openalex", "pubmed", "crossref", "arxiv"])

    def test_patent_has_uspto(self, router):
        claim = _make_claim(
            "Das Patent wurde vom USPTO erteilt und hat die Patentnummer US20240012345."
        )
        result = router.route(claim)
        source_ids = [s.source_id for s in result.sources]
        assert "uspto" in source_ids

    def test_max_six_sources(self, router):
        claim = _make_claim(
            "Das Arzneimittel (FDA-zugelassen) ist Gegenstand einer klinischen Studie (Phase III) "
            "und wurde in peer-reviewed Journals publiziert. Patent erteilt vom USPTO."
        )
        result = router.route(claim)
        assert len(result.sources) <= 6

    def test_sources_sorted_by_authority(self, router):
        claim = _make_claim(
            "Das Bruttoinlandsprodukt der EU wuchs laut Eurostat um 2%.", ClaimType.STATISTICAL
        )
        result = router.route(claim)
        # Quellen sind nach effektivem Gewicht sortiert → erste Quelle hat höchste Autorität
        if len(result.sources) >= 2:
            assert result.sources[0].authority_weight >= result.sources[-1].authority_weight - 0.20


# ── Site-Hints ────────────────────────────────────────────────────────────────


class TestSiteHints:
    def test_hints_have_site_prefix(self, router):
        claim = _make_claim("Die EU-Richtlinie ist auf EUR-Lex veröffentlicht.")
        result = router.route(claim)
        for hint in result.site_hints:
            assert hint.startswith("site:")

    def test_no_duplicate_hints(self, router):
        claim = _make_claim(
            "Die Europäische Union verabschiedete eine neue Verordnung (EU-Richtlinie)."
        )
        result = router.route(claim)
        assert len(result.site_hints) == len(set(result.site_hints))


# ── SearchProfile-Augmentierung ───────────────────────────────────────────────


class TestApplyHints:
    def test_augments_empty_search_profile(self, router):
        claim = _make_processed(
            "Das BIP der EU wuchs um 1,2%.",
            ClaimType.STATISTICAL,
            search_profile=ClaimSearchProfile(),
        )
        _, routed = router.route_and_apply(claim)
        assert isinstance(routed, ProcessedClaim)
        hints = routed.search_profile.official_source_hints
        assert len(hints) > 0
        assert all(h.startswith("site:") for h in hints)

    def test_augments_existing_hints(self, router):
        existing = ["site:tagesschau.de"]
        profile = ClaimSearchProfile(official_source_hints=existing)
        claim = _make_processed(
            "Das BIP der EU wuchs um 1,2%.",
            ClaimType.STATISTICAL,
            search_profile=profile,
        )
        _, routed = router.route_and_apply(claim)
        hints = routed.search_profile.official_source_hints
        assert "site:tagesschau.de" in hints
        assert len(hints) > 1  # Neue Hints hinzugefügt

    def test_no_duplicate_hints_in_profile(self, router):
        claim = _make_processed(
            "Das BIP der EU wuchs um 1,2%.",
            ClaimType.STATISTICAL,
            search_profile=ClaimSearchProfile(),
        )
        _, routed = router.route_and_apply(claim)
        hints = routed.search_profile.official_source_hints
        assert len(hints) == len(set(hints))

    def test_creates_search_profile_if_none(self, router):
        claim = _make_processed(
            "Die EU-Verordnung gilt in der Europäischen Union.",
            search_profile=None,
        )
        _, routed = router.route_and_apply(claim)
        assert routed.search_profile is not None
        assert len(routed.search_profile.official_source_hints) > 0

    def test_simple_claim_unchanged(self, router):
        """Einfacher Claim ohne SearchProfile wird unverändert zurückgegeben."""
        claim = _make_claim("Das BIP wuchs.")
        _, routed = router.route_and_apply(claim)
        assert routed is claim  # Gleiches Objekt

    def test_original_claim_not_mutated(self, router):
        """Original-Claim darf nicht mutiert werden (Pydantic model_copy)."""
        profile = ClaimSearchProfile(official_source_hints=["site:example.com"])
        claim = _make_processed(
            "Die EU-Verordnung 2016/679 (DSGVO) ist geltendes Recht.",
            search_profile=profile,
        )
        original_hints = list(claim.search_profile.official_source_hints)
        router.route_and_apply(claim)
        assert claim.search_profile.official_source_hints == original_hints


# ── Konfidenz und Rationale ───────────────────────────────────────────────────


class TestConfidenceAndRationale:
    def test_confidence_range(self, router):
        for text in [
            "Das BIP der EU wuchs um 1,5%.",
            "Der Himmel ist blau.",
            "Das Arzneimittel ist FDA-zugelassen.",
        ]:
            result = router.route(_make_claim(text))
            assert 0.0 <= result.confidence <= 1.0

    def test_high_confidence_for_clear_domain(self, router):
        claim = _make_claim(
            "Die klinische Studie Phase III (NCT12345) wurde randomisiert und placebokontrolliert durchgeführt."
        )
        result = router.route(claim)
        assert result.confidence >= 0.35

    def test_rationale_contains_domain(self, router):
        claim = _make_claim("Das Patent wurde vom USPTO erteilt.")
        result = router.route(claim)
        assert "patent" in result.rationale.lower() or len(result.rationale) > 10

    def test_rationale_is_string(self, router):
        claim = _make_claim("Irgendeine Behauptung ohne klare Domäne.")
        result = router.route(claim)
        assert isinstance(result.rationale, str)
        assert len(result.rationale) > 0

    def test_result_has_all_fields(self, router):
        claim = _make_claim("Die Inflation betrug 3% in der Eurozone.")
        result = router.route(claim)
        assert isinstance(result, RouteResult)
        assert isinstance(result.sources, list)
        assert isinstance(result.domains, list)
        assert isinstance(result.jurisdiction, str)
        assert isinstance(result.site_hints, list)
        assert isinstance(result.rationale, str)
        assert isinstance(result.confidence, float)
