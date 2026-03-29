"""Tests für die harte Durchsetzung der Source-Commercial-Policy.

Stellt sicher, dass:
    - Nur kommerziell sichere Quellen (ALLOWED) im Standardpfad landen
    - CHECK_TERMS-Quellen (arXiv, PubMed) ausgefiltert werden
    - Display-Policy (METADATA_ONLY, EXCERPT, FULL) technisch durchgesetzt wird
    - Storage-Policy (CACHE, SESSION_ONLY, NO_STORAGE) beachtet wird
    - fulltext_allowed=False den Excerpt begrenzt
"""

from __future__ import annotations

import pytest

from models.schemas import (
    Claim,
    ClaimType,
)
from models.source_evidence import OfficialEvidenceItem
from tools.claim_router import ClaimRouter
from tools.sources.registry import SourceRegistry
from tools.sources.types import (
    AllowedDisplay,
    AllowedStorage,
    AuthMode,
    ClaimDomain,
    CommercialUsePolicy,
    SourceConfig,
)


# ── Helper ───────────────────────────────────────────────────────────────────

def _make_claim(text: str, claim_type: ClaimType = ClaimType.FACTUAL) -> Claim:
    return Claim(id="C1", text=text, type=claim_type)


def _make_source_config(
    *,
    source_id: str = "test_source",
    commercial: CommercialUsePolicy = CommercialUsePolicy.ALLOWED,
    storage: AllowedStorage = AllowedStorage.CACHE,
    display: AllowedDisplay = AllowedDisplay.FULL,
    fulltext: bool = True,
    domains: tuple[ClaimDomain, ...] = (ClaimDomain.SCIENTIFIC,),
    weight: float = 0.85,
) -> SourceConfig:
    return SourceConfig(
        source_id=source_id,
        display_name="Test Source",
        source_class="tools.sources.clients.test.TestClient",
        base_url="https://test.example.com",
        auth_mode=AuthMode.NONE,
        supports_search=True,
        supports_detail_fetch=True,
        allowed_storage=storage,
        allowed_display=display,
        fulltext_allowed=fulltext,
        commercial_reuse_ok=commercial,
        citation_required=False,
        claim_domains=domains,
        authority_weight=weight,
    )


# ── SourceConfig Policy-Helper Tests ────────────────────────────────────────


class TestSourceConfigPolicyHelpers:
    """Tests für is_runtime_allowed(), can_cache(), max_excerpt_length()."""

    def test_is_runtime_allowed_allowed(self):
        cfg = _make_source_config(commercial=CommercialUsePolicy.ALLOWED)
        assert cfg.is_runtime_allowed() is True

    def test_is_runtime_allowed_check_terms(self):
        cfg = _make_source_config(commercial=CommercialUsePolicy.CHECK_TERMS)
        assert cfg.is_runtime_allowed() is False

    def test_is_runtime_allowed_restricted(self):
        cfg = _make_source_config(commercial=CommercialUsePolicy.RESTRICTED)
        assert cfg.is_runtime_allowed() is False

    def test_is_runtime_allowed_unknown(self):
        cfg = _make_source_config(commercial=CommercialUsePolicy.UNKNOWN)
        assert cfg.is_runtime_allowed() is False

    def test_can_cache_true(self):
        cfg = _make_source_config(storage=AllowedStorage.CACHE)
        assert cfg.can_cache() is True

    def test_can_cache_session_only(self):
        cfg = _make_source_config(storage=AllowedStorage.SESSION_ONLY)
        assert cfg.can_cache() is False

    def test_can_cache_no_storage(self):
        cfg = _make_source_config(storage=AllowedStorage.NO_STORAGE)
        assert cfg.can_cache() is False

    def test_max_excerpt_length_full_with_fulltext(self):
        cfg = _make_source_config(display=AllowedDisplay.FULL, fulltext=True)
        assert cfg.max_excerpt_length() == 800

    def test_max_excerpt_length_excerpt(self):
        cfg = _make_source_config(display=AllowedDisplay.EXCERPT, fulltext=True)
        assert cfg.max_excerpt_length() == 400

    def test_max_excerpt_length_metadata_only(self):
        cfg = _make_source_config(display=AllowedDisplay.METADATA_ONLY)
        assert cfg.max_excerpt_length() == 0

    def test_max_excerpt_length_full_but_no_fulltext(self):
        """FULL display aber fulltext_allowed=False -> konservativ 400."""
        cfg = _make_source_config(display=AllowedDisplay.FULL, fulltext=False)
        assert cfg.max_excerpt_length() == 400


# ── Registry by_domain_safe Tests ────────────────────────────────────────────


class TestRegistryByDomainSafe:
    """Tests für SourceRegistry.by_domain_safe()."""

    def test_excludes_check_terms(self):
        """by_domain_safe() schließt CHECK_TERMS-Quellen aus."""
        safe_sources = SourceRegistry.by_domain_safe(ClaimDomain.SCIENTIFIC)
        safe_ids = {s.source_id for s in safe_sources}

        # arXiv und PubMed sind CHECK_TERMS -> nicht enthalten
        assert "arxiv" not in safe_ids
        assert "pubmed" not in safe_ids

    def test_includes_allowed_sources(self):
        """by_domain_safe() enthält ALLOWED-Quellen."""
        safe_sources = SourceRegistry.by_domain_safe(ClaimDomain.SCIENTIFIC)
        safe_ids = {s.source_id for s in safe_sources}

        # OpenAlex und Crossref sind ALLOWED + SCIENTIFIC
        assert "openalex" in safe_ids or "crossref" in safe_ids

    def test_all_returned_are_allowed(self):
        """Alle von by_domain_safe() zurückgegebenen Quellen sind ALLOWED."""
        for domain in ClaimDomain:
            safe_sources = SourceRegistry.by_domain_safe(domain)
            for src in safe_sources:
                assert src.commercial_reuse_ok == CommercialUsePolicy.ALLOWED, (
                    f"{src.source_id} ist {src.commercial_reuse_ok.value}, nicht ALLOWED"
                )

    def test_sorted_by_authority_weight(self):
        """Ergebnis ist nach authority_weight absteigend sortiert."""
        safe_sources = SourceRegistry.by_domain_safe(ClaimDomain.SCIENTIFIC)
        if len(safe_sources) > 1:
            weights = [s.authority_weight for s in safe_sources]
            assert weights == sorted(weights, reverse=True)


# ── ClaimRouter Policy-Durchsetzung Tests ────────────────────────────────────


class TestClaimRouterPolicyEnforcement:
    """Tests, dass ClaimRouter nur kommerziell sichere Quellen routet."""

    @pytest.fixture
    def router(self):
        return ClaimRouter()

    def test_scientific_routing_excludes_check_terms(self, router):
        """Wissenschaftliche Claims routen nicht zu arXiv/PubMed."""
        claim = _make_claim(
            "Laut einer Studie in Nature ist der Effekt signifikant.",
            ClaimType.CAUSAL,
        )
        result = router.route(claim)
        source_ids = {s.source_id for s in result.sources}
        assert "arxiv" not in source_ids
        assert "pubmed" not in source_ids

    def test_all_routed_sources_are_commercially_safe(self, router):
        """Alle gerouteten Quellen müssen is_runtime_allowed() == True sein."""
        claims = [
            _make_claim("Die EU hat 2024 ein BIP-Wachstum von 1.2% verzeichnet.", ClaimType.STATISTICAL),
            _make_claim("Das Medikament wurde von der FDA zugelassen.", ClaimType.FACTUAL),
            _make_claim("Der Effekt wurde in einer Studie nachgewiesen.", ClaimType.CAUSAL),
        ]
        for claim in claims:
            result = router.route(claim)
            for src in result.sources:
                assert src.is_runtime_allowed(), (
                    f"Quelle {src.source_id} ({src.commercial_reuse_ok.value}) "
                    f"im Routing für: {claim.canonical_text[:50]}"
                )

    def test_fallback_excludes_check_terms(self, router):
        """Auch Fallback-Pfad (keine erkannte Domain) liefert nur sichere Quellen."""
        claim = _make_claim("Irgendein unklarer Satz ohne Domänenbezug.")
        result = router.route(claim)
        for src in result.sources:
            assert src.is_runtime_allowed(), (
                f"Fallback-Quelle {src.source_id} ist nicht runtime-allowed"
            )


# ── Display-Policy Durchsetzung in to_evidence_item() ───────────────────────


class TestDisplayPolicyEnforcement:
    """Tests für Display-Policy in OfficialEvidenceItem.to_evidence_item()."""

    def _make_evidence_item(
        self,
        display_policy: AllowedDisplay = AllowedDisplay.FULL,
        abstract: str = "A" * 1000,
    ) -> OfficialEvidenceItem:
        return OfficialEvidenceItem(
            source_id="test_source",
            source_class="tools.sources.clients.test.TestClient",
            record_id="test-123",
            title="Test Evidence",
            url="https://test.example.com/123",
            abstract=abstract,
            authority_score=0.85,
            display_policy=display_policy,
            storage_policy=AllowedStorage.CACHE,
            license_status=CommercialUsePolicy.ALLOWED,
            domains=[ClaimDomain.SCIENTIFIC],
        )

    def test_metadata_only_produces_empty_excerpt(self):
        """METADATA_ONLY -> leerer Excerpt."""
        item = self._make_evidence_item(display_policy=AllowedDisplay.METADATA_ONLY)
        evidence = item.to_evidence_item()
        assert evidence.excerpt == ""

    def test_excerpt_policy_caps_at_400(self):
        """EXCERPT -> max 400 Zeichen."""
        item = self._make_evidence_item(
            display_policy=AllowedDisplay.EXCERPT,
            abstract="B" * 1000,
        )
        evidence = item.to_evidence_item()
        assert len(evidence.excerpt) <= 400

    def test_full_policy_allows_800(self):
        """FULL -> bis zu 800 Zeichen."""
        item = self._make_evidence_item(
            display_policy=AllowedDisplay.FULL,
            abstract="C" * 1000,
        )
        evidence = item.to_evidence_item()
        assert len(evidence.excerpt) <= 800
        assert len(evidence.excerpt) > 400  # Nutzt vollen Spielraum


# ── Registry-Integrität: CHECK_TERMS Quellen identifizieren ─────────────────


class TestCheckTermsSourcesIdentified:
    """Stellt sicher, dass wir wissen welche Quellen CHECK_TERMS sind."""

    def test_exactly_two_check_terms_sources(self):
        """Genau arXiv und PubMed sind CHECK_TERMS."""
        check_terms = [
            s for s in SourceRegistry.all()
            if s.commercial_reuse_ok == CommercialUsePolicy.CHECK_TERMS
        ]
        check_ids = {s.source_id for s in check_terms}
        assert check_ids == {"arxiv", "pubmed"}

    def test_commercial_safe_excludes_check_terms(self):
        """SourceRegistry.commercial_safe() enthält keine CHECK_TERMS."""
        safe_ids = {s.source_id for s in SourceRegistry.commercial_safe()}
        assert "arxiv" not in safe_ids
        assert "pubmed" not in safe_ids

    def test_commercial_safe_count(self):
        """commercial_safe() hat 12 Quellen (14 total - 2 CHECK_TERMS)."""
        assert len(SourceRegistry.commercial_safe()) == 12
