"""Tests für die korrekte Behandlung von Regelungs-/Sanktionsclaims.

Abgedeckte Szenarien:
    1. Regelungsclaim mit Zahlen wird nicht fälschlicherweise als rein statistischer
       Claim behandelt → NumberAuditor wird nicht ausgeführt.
    2. Query-Generierung für Beschluss-/Sitzungsclaims enthält verfahrensnahe Queries
       (Beschluss, Protokoll, Drucksache).
    3. Allgemeine Themen-Seiten (DSGVO-Artikel, generische Überwachungsseiten,
       Bußgeldrechner) werden bei konkreten kommunalen Sanktionsclaims als
       off-topic/weak eingestuft.
    4. Konkrete Sanktionsclaims ohne direkte Evidenz enden nicht als stark
       begründetes MISLEADING → stattdessen UNVERIFIABLE.
    5. Confidence sinkt bei kontextnaher, aber nicht direkter Evidenz unter 0.50.

Designprinzip: Alle Tests sind generisch – keine Hardcodings für einzelne Städte,
    Begriffe oder Testfälle. Die Testparameter leiten sich aus Frame-Feldern ab.
"""

from __future__ import annotations

import pytest

from agents.fact_checker import _build_search_queries_from_profile
from agents.evidence_builder import _is_offtopic_content
from agents.verdict_agent import (
    VerdictRatingCalibrationConfig,
    _calibrate_confidence,
    _calibrate_rating,
    _CEILING_REGULATORY_NOISY_CONTEXTUAL,
)
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    EvidenceType,
    SourceConsensus,
    SourceDirection,
)
from models.schemas import (
    ClaimFrame,
    ClaimSearchProfile,
    ClaimType,
    FactRating,
    ProcessedClaim,
)
from orchestrator import _should_run_number_auditor


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────


def _make_regulatory_processed_claim(
    sanction: str = "Bußgeld 250 Euro",
    enforcement: str = "Kameraüberwachung",
    policy_context: str = "Parkraumkonzept",
    institution: str = "Stadtrat Musterstadt",
    location: str = "Musterstadt",
    numbers: list[str] | None = None,
    claim_type: ClaimType = ClaimType.STATISTICAL,
) -> ProcessedClaim:
    """Erstellt einen ProcessedClaim mit regulatorischem Frame.

    Keine echten Städte oder Behörden – vollständig generische Werte.
    """
    frame = ClaimFrame(
        raw_text="Testclaim mit Sanktion",
        institution=institution,
        location=location,
        sanction=sanction,
        enforcement=enforcement,
        policy_context=policy_context,
        numbers=numbers or ["250"],
    )
    profile = ClaimSearchProfile(
        institutions=[institution],
        locations=[location],
        policy_terms=[policy_context],
        sanction_terms=[sanction],
        number_terms=numbers or ["250"],
        action_terms=["beschlossen", "eingeführt"],
        official_source_hints=[f"site:{location.lower()}.de"],
        fact_check_hints=["site:correctiv.org"],
    )
    return ProcessedClaim(
        id="C1",
        text=(
            f"Der {institution} hat im Rahmen des {policy_context} beschlossen, "
            f"Verstöße per {enforcement} mit {sanction} zu ahnden."
        ),
        type=claim_type,
        requires_agents=[],
        frame=frame,
        search_profile=profile,
    )


def _make_evidence_item(
    url: str = "https://example.com",
    tier: int = 5,
    direction: SourceDirection = SourceDirection.NEUTRAL,
    ev_type: EvidenceType = EvidenceType.CONTEXTUAL,
    relevance: float = 0.5,
    scope: float = 0.3,
) -> EvidenceItem:
    return EvidenceItem(
        source=EvidenceSource(
            url=url,
            title="Testartikel",
            domain="example.com",
            domain_tier=tier,
        ),
        excerpt="Testauszug",
        relevance_score=relevance,
        source_direction=direction,
        evidence_type=ev_type,
        claim_scope_score=scope,
    )


def _make_evidence_pack(
    items: list[EvidenceItem] | None = None,
    consensus: SourceConsensus = SourceConsensus.INSUFFICIENT,
    has_direct_refutation: bool = False,
    has_fc_direct: bool = False,
    has_fc_any: bool = False,
    direct_count: int = 0,
    contextual_only_rate: float = 1.0,
    has_primary: bool = False,
    off_topic_rate: float = 0.6,
    avg_top5_relevance: float = 0.2,
    low_trust_rate: float = 0.0,
    overall_quality: float = 0.3,
) -> EvidencePack:
    return EvidencePack(
        claim_id="C1",
        claim_text="Testclaim",
        web_results=items or [],
        google_fact_check_matches=[],
        evidence_quality=EvidenceQualitySignals(
            source_consensus=consensus,
            has_direct_refutation=has_direct_refutation,
            direct_refutation_count=1 if has_direct_refutation else 0,
            has_fact_check_direct_match=has_fc_direct,
            has_fact_check_any=has_fc_any,
            has_primary_source_any=has_primary,
            has_primary_direct_evidence=False,
            direct_evidence_count=direct_count,
            contextual_only_rate=contextual_only_rate,
            overall_quality=overall_quality,
            freshness_score=0.8,
            off_topic_rate=off_topic_rate,
            avg_top5_relevance=avg_top5_relevance,
            low_trust_rate=low_trust_rate,
            top_tier_count=0,
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: NumberAuditor nicht für Regelungsclaims
# ══════════════════════════════════════════════════════════════════════════════


class TestNumberAuditorSkipForRegulatoryClaims:
    """Regelungsclaims mit Zahlen sollen nicht in den NumberAuditor laufen."""

    def test_statistical_claim_with_sanction_frame_skips_number_auditor(self):
        """STATISTICAL-Claim mit Sanktions-Frame → _should_run_number_auditor=False."""
        claim = _make_regulatory_processed_claim(
            sanction="Bußgeld 500 Euro",
            claim_type=ClaimType.STATISTICAL,
        )
        assert not _should_run_number_auditor(claim), (
            "Claim mit Sanktions-Frame und Typ STATISTICAL soll NumberAuditor überspringen"
        )

    def test_statistical_claim_with_enforcement_frame_skips_number_auditor(self):
        """STATISTICAL-Claim mit Enforcement-Frame → _should_run_number_auditor=False."""
        claim = _make_regulatory_processed_claim(
            enforcement="Geschwindigkeitskontrolle",
            sanction="",
            claim_type=ClaimType.STATISTICAL,
        )
        assert not _should_run_number_auditor(claim)

    def test_statistical_claim_with_policy_and_institution_skips_number_auditor(self):
        """STATISTICAL mit policy_context + institution → _should_run_number_auditor=False."""
        claim = _make_regulatory_processed_claim(
            sanction="",
            enforcement="",
            policy_context="Fahrtenbegrenzungsplan",
            institution="Stadtrat Testort",
            claim_type=ClaimType.STATISTICAL,
        )
        assert not _should_run_number_auditor(claim)

    def test_pure_statistical_claim_without_regulatory_frame_runs_number_auditor(self):
        """Echter statistischer Claim (ohne regulatorischen Frame) → NumberAuditor läuft."""
        claim = ProcessedClaim(
            id="C2",
            text="Die Arbeitslosigkeit stieg um 15 Prozent.",
            type=ClaimType.STATISTICAL,
            requires_agents=[],
        )
        assert _should_run_number_auditor(claim), (
            "Echter statistischer Claim ohne Regulatory-Frame soll NumberAuditor ausführen"
        )

    def test_explicit_number_auditor_request_always_runs(self):
        """Explizit angefordert → NumberAuditor läuft auch bei Regulatory-Frame."""
        claim = _make_regulatory_processed_claim(claim_type=ClaimType.STATISTICAL)
        claim.requires_agents = ["number_auditor"]
        assert _should_run_number_auditor(claim), (
            "Explizit angefordert: NumberAuditor muss ausgeführt werden"
        )

    def test_factual_claim_without_regulatory_frame_does_not_run_number_auditor(self):
        """FACTUAL-Claim → NumberAuditor läuft nicht (kein STATISTICAL)."""
        claim = ProcessedClaim(
            id="C3",
            text="Die Partei hat die Wahl gewonnen.",
            type=ClaimType.FACTUAL,
            requires_agents=[],
        )
        assert not _should_run_number_auditor(claim)


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Verfahrensnahe Query-Generierung
# ══════════════════════════════════════════════════════════════════════════════


class TestProceduralQueryGeneration:
    """Query-Generierung für Beschluss-/Sitzungsclaims enthält verfahrensnahe Queries."""

    def test_regulatory_claim_generates_beschluss_query(self):
        """Claims mit Sanktion/Enforcement erzeugen Query mit 'Beschluss'."""
        claim = _make_regulatory_processed_claim(
            institution="Stadtrat Teststadt",
            location="Teststadt",
            sanction="Bußgeld 300 Euro",
            enforcement="Überwachungskamera",
        )
        queries = _build_search_queries_from_profile(claim)
        has_beschluss = any("Beschluss" in q for q in queries)
        assert has_beschluss, (
            f"Keine 'Beschluss'-Query erzeugt. Queries: {queries}"
        )

    def test_regulatory_claim_generates_procedural_document_query(self):
        """Claims mit Policy+Institution erzeugen Query mit 'Ratsprotokoll' oder 'Drucksache'."""
        claim = _make_regulatory_processed_claim(
            institution="Gemeinderat Beispielgemeinde",
            location="Beispielgemeinde",
            policy_context="Mobilitätskonzept 2025",
        )
        queries = _build_search_queries_from_profile(claim)
        has_procedural = any(
            ("Ratsprotokoll" in q or "Drucksache" in q or "Protokoll" in q)
            for q in queries
        )
        assert has_procedural, (
            f"Keine verfahrensnahe Dokument-Query erzeugt. Queries: {queries}"
        )

    def test_procedural_queries_use_only_frame_anchors(self):
        """Verfahrensnahe Queries enthalten ausschließlich Frame-Felder – keine freien Begriffe."""
        claim = _make_regulatory_processed_claim(
            institution="Kreisrat Testkreis",
            location="Testkreis",
            policy_context="Verkehrsberuhigung",
        )
        queries = _build_search_queries_from_profile(claim)
        # Jede Query muss mindestens einen Frame-abgeleiteten Anker enthalten
        for q in queries:
            q_lower = q.lower()
            has_anchor = (
                "testkreis" in q_lower
                or "kreisrat" in q_lower
                or "verkehrsberuhigung" in q_lower
                or "beschluss" in q_lower
                or "drucksache" in q_lower
                or "ratsprotokoll" in q_lower
                or "faktencheck" in q_lower
                or "falschmeldung" in q_lower
            )
            assert has_anchor, (
                f"Query '{q}' enthält keinen Frame-Anker – mögliche Halluzination"
            )

    def test_non_regulatory_claim_does_not_get_procedural_queries(self):
        """Nicht-regulatorischer Claim erzeugt keine Beschluss/Drucksache-Queries."""
        claim = _make_regulatory_processed_claim(
            sanction="",
            enforcement="",
            policy_context="",
            institution="",
        )
        # Profil ohne Sanktion/Enforcement/Policy+Institution
        claim.frame.sanction = ""
        claim.frame.enforcement = ""
        claim.frame.policy_context = ""
        claim.frame.institution = ""
        queries = _build_search_queries_from_profile(claim)
        # Darf keine Beschluss-Queries enthalten
        has_beschluss = any("Beschluss" in q for q in queries)
        has_drucksache = any("Drucksache" in q for q in queries)
        assert not has_beschluss and not has_drucksache, (
            "Nicht-regulatorischer Claim soll keine Verfahrensdokument-Queries erzeugen"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: Regulatory Offtopic-Filter
# ══════════════════════════════════════════════════════════════════════════════


class TestRegulatoryOfftopicFilter:
    """Allgemeine Themen-Seiten werden bei konkreten Regelungsclaims als off-topic eingestuft."""

    def _make_full_regulatory_profile(
        self,
        institution: str = "Stadtrat Testort",
        location: str = "Testort",
        policy: str = "Parkraumkonzept",
        sanction: str = "Bußgeld",
    ) -> ClaimSearchProfile:
        return ClaimSearchProfile(
            institutions=[institution],
            locations=[location],
            policy_terms=[policy],
            sanction_terms=[sanction],
        )

    def test_generic_page_with_only_sanction_match_is_offtopic(self):
        """Seite trifft nur Sanktionsbegriff, aber nicht Institution/Ort/Policy → off-topic."""
        profile = self._make_full_regulatory_profile()
        # Snippet: nur "Bußgeld" ohne Institution/Ort/Policy
        is_ot, penalty = _is_offtopic_content(
            title="Allgemeine Bußgeldinformationen",
            snippet="Informationen zu Bußgeldern in Deutschland.",
            profile=profile,
        )
        assert is_ot, "Seite mit nur Sanktionsbegriff (kein Ort/Institution/Policy) muss off-topic sein"
        assert penalty >= 0.70, f"Penalty {penalty} zu niedrig für fehlende Kernanker"

    def test_generic_page_with_only_number_match_is_offtopic(self):
        """Seite trifft nur Zahl, aber nicht Ort/Institution/Policy → off-topic."""
        profile = self._make_full_regulatory_profile()
        profile.number_terms = ["300"]
        is_ot, penalty = _is_offtopic_content(
            title="Bußgeldkatalog 2024",
            snippet="Bußgelder bis 300 Euro für verschiedene Verstöße.",
            profile=profile,
        )
        assert is_ot, "Seite mit nur Zahlenmatch ohne Ort/Institution/Policy muss off-topic sein"
        assert penalty >= 0.70

    def test_page_with_two_key_anchors_is_not_offtopic(self):
        """Seite trifft Institution + Ort → nicht off-topic für Regelungsclaim."""
        profile = self._make_full_regulatory_profile(
            institution="Stadtrat Musterstadt",
            location="Musterstadt",
        )
        is_ot, penalty = _is_offtopic_content(
            title="Stadtrat Musterstadt beschließt Maßnahmen",
            snippet="Der Stadtrat Musterstadt hat neue Regelungen verabschiedet.",
            profile=profile,
        )
        assert not is_ot, "Seite mit Institution+Ort-Match soll nicht als off-topic gelten"

    def test_page_with_institution_plus_policy_is_not_offtopic(self):
        """Seite trifft Institution + Policy → nicht off-topic."""
        profile = self._make_full_regulatory_profile(
            institution="Gemeinderat Testgemeinde",
            policy="Parkraumkonzept",
        )
        is_ot, penalty = _is_offtopic_content(
            title="Gemeinderat Testgemeinde verabschiedet Parkraumkonzept",
            snippet="Das Parkraumkonzept wurde einstimmig beschlossen.",
            profile=profile,
        )
        assert not is_ot

    def test_single_anchor_hit_on_fully_regulatory_profile_is_offtopic(self):
        """Bei vollständigem Regulatory-Profil (Sanktion+Policy+Institution):
        nur 1 Kernanker-Treffer → off-topic.

        Institution und Ort müssen orthogonal sein (keine Wort-Überlappung),
        damit _inst_match nicht durch den Ortsnamen fälschlich anschlägt.
        """
        profile = self._make_full_regulatory_profile(
            institution="Gemeinderat Beispielstadt",  # kein Wort aus Snippet
            location="Alphadorf",                     # eindeutiger Ortsname
            policy="Verkehrskonzept",
            sanction="Bußgeld",
        )
        # Nur Ort trifft (1 von 3 Kernanker: Institution und Policy fehlen)
        is_ot, penalty = _is_offtopic_content(
            title="Alphadorf Sehenswürdigkeiten",
            snippet="Besuchen Sie Alphadorf. Viele Sehenswürdigkeiten warten.",
            profile=profile,
        )
        assert is_ot, "Nur 1 Kernanker (Ort) ohne Institution/Policy → off-topic"
        assert penalty >= 0.70


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: Rating – MISLEADING → UNVERIFIABLE bei konkretem Sanktionsclaim
# ══════════════════════════════════════════════════════════════════════════════


class TestRegulatoryMisleadingToUnverifiable:
    """Konkrete Sanktionsclaims ohne direkte Evidenz landen nicht als MISLEADING."""

    def test_misleading_without_evidence_becomes_unverifiable_for_regulatory(self):
        """MISLEADING + kein Widerlegungssignal + 0 DIRECT + is_regulatory → UNVERIFIABLE."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, reasons = _calibrate_rating(
            FactRating.MISLEADING, pack, is_regulatory_claim=True
        )
        assert rating == FactRating.UNVERIFIABLE, (
            "Regelungsclaim: MISLEADING ohne Widerlegungssignal + 0 DIRECT muss UNVERIFIABLE werden"
        )
        assert any("UNVERIFIABLE" in r for r in reasons)

    def test_misleading_with_refutation_signal_stays_misleading_for_regulatory(self):
        """MISLEADING + Widerlegungssignal → bleibt MISLEADING (auch bei regulatory)."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.MIXED,  # MIXED = Widerlegungssignal
            has_direct_refutation=False,
            has_fc_any=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, reasons = _calibrate_rating(
            FactRating.MISLEADING, pack, is_regulatory_claim=True
        )
        assert rating == FactRating.MISLEADING, (
            "Mit Widerlegungssignal (MIXED) soll MISLEADING bleiben"
        )

    def test_misleading_without_evidence_stays_misleading_for_non_regulatory(self):
        """Für nicht-regulatorische Claims: MISLEADING bleibt MISLEADING (Downgrade gilt nicht)."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
            direct_count=0,
            contextual_only_rate=1.0,
        )
        rating, reasons = _calibrate_rating(
            FactRating.MISLEADING, pack, is_regulatory_claim=False
        )
        assert rating == FactRating.MISLEADING, (
            "Nicht-regulatorischer Claim: MISLEADING soll nicht pauschal zu UNVERIFIABLE"
        )

    def test_misleading_with_direct_evidence_stays_misleading_for_regulatory(self):
        """MISLEADING + direkte Evidenz vorhanden → bleibt MISLEADING."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
            direct_count=1,  # Direkte Evidenz vorhanden
            contextual_only_rate=0.5,
        )
        rating, reasons = _calibrate_rating(
            FactRating.MISLEADING, pack, is_regulatory_claim=True
        )
        assert rating == FactRating.MISLEADING, (
            "Mit direkter Evidenz soll MISLEADING auch bei regulatory bleiben"
        )

    def test_false_calibration_unaffected_by_regulatory_flag(self):
        """FALSE ohne aktive Widerlegung → UNVERIFIABLE (bestehende Logik unverändert)."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_direct=False,
        )
        rating, reasons = _calibrate_rating(
            FactRating.FALSE, pack, is_regulatory_claim=True
        )
        assert rating == FactRating.UNVERIFIABLE


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: Confidence sinkt bei verrauschter Evidenz
# ══════════════════════════════════════════════════════════════════════════════


class TestRegulatoryConfidenceCeiling:
    """Confidence sinkt unter 0.50 wenn nur kontextnahe, nicht direkte Evidenz vorliegt."""

    def test_regulatory_noisy_evidence_confidence_capped_below_50(self):
        """Regelungsclaim + nur Kontextquellen + keine Primärquelle → Confidence ≤ 0.45."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_direct_refutation=False,
            has_fc_any=False,
            has_primary=False,
            direct_count=0,
            contextual_only_rate=0.8,  # Überwiegend kontextuelle Evidenz
            off_topic_rate=0.6,
            avg_top5_relevance=0.2,
            overall_quality=0.25,
        )
        confidence, reasons = _calibrate_confidence(
            raw_confidence=0.70,
            pack=pack,
            cove_trace=None,
            is_regulatory_claim=True,
        )
        assert confidence <= _CEILING_REGULATORY_NOISY_CONTEXTUAL, (
            f"Confidence {confidence:.2f} überschreitet Ceiling "
            f"{_CEILING_REGULATORY_NOISY_CONTEXTUAL} für verrauschte Regulatory-Evidenz"
        )
        has_ceiling_reason = any(
            "Kontext-Evidenz" in r or "Regelungsclaim" in r for r in reasons
        )
        assert has_ceiling_reason, f"Kein Ceiling-Grund im Reasoning. Reasons: {reasons}"

    def test_regulatory_with_primary_source_not_affected_by_noisy_ceiling(self):
        """Regelungsclaim mit Primärquelle: verrauschtes Ceiling greift nicht."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.AGREEING,
            has_direct_refutation=False,
            has_fc_any=False,
            has_primary=True,  # Primärquelle vorhanden
            direct_count=1,
            contextual_only_rate=0.8,
            off_topic_rate=0.2,
            avg_top5_relevance=0.6,
            overall_quality=0.7,
        )
        confidence, reasons = _calibrate_confidence(
            raw_confidence=0.72,
            pack=pack,
            cove_trace=None,
            is_regulatory_claim=True,
        )
        # Mit Primärquelle: verrauschtes Ceiling greift nicht
        # (andere Ceilings können noch greifen, aber nicht _CEILING_REGULATORY_NOISY_CONTEXTUAL)
        noisy_ceiling_applied = any(
            "überwiegend Kontext-Evidenz" in r and "keine Primärquelle" in r
            for r in reasons
        )
        assert not noisy_ceiling_applied, (
            "Das verrauschte Confidence-Ceiling soll bei vorhandener Primärquelle nicht greifen"
        )

    def test_non_regulatory_claim_not_capped_by_regulatory_noisy_ceiling(self):
        """Nicht-regulatorischer Claim mit kontextueller Evidenz: verrauschtes Ceiling greift nicht."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_primary=False,
            direct_count=0,
            contextual_only_rate=0.9,
            off_topic_rate=0.3,
            avg_top5_relevance=0.35,
            overall_quality=0.4,
        )
        confidence, reasons = _calibrate_confidence(
            raw_confidence=0.65,
            pack=pack,
            cove_trace=None,
            is_regulatory_claim=False,  # Kein Regulatory-Claim
        )
        noisy_ceiling_applied = any(
            "überwiegend Kontext-Evidenz" in r and "keine Primärquelle" in r
            for r in reasons
        )
        assert not noisy_ceiling_applied, (
            "Das verrauschte Confidence-Ceiling soll nur für Regulatory-Claims gelten"
        )

    def test_regulatory_with_fact_check_not_affected_by_noisy_ceiling(self):
        """Regelungsclaim mit Faktenchecker: verrauschtes Ceiling greift nicht."""
        pack = _make_evidence_pack(
            consensus=SourceConsensus.INSUFFICIENT,
            has_fc_any=True,  # Faktenchecker-Ergebnis vorhanden
            has_primary=False,
            direct_count=0,
            contextual_only_rate=0.8,
        )
        confidence, reasons = _calibrate_confidence(
            raw_confidence=0.65,
            pack=pack,
            cove_trace=None,
            is_regulatory_claim=True,
        )
        noisy_ceiling_applied = any(
            "überwiegend Kontext-Evidenz" in r and "keine Primärquelle" in r
            for r in reasons
        )
        assert not noisy_ceiling_applied, (
            "Faktenchecker-Ergebnis verhindert das verrauschte Ceiling"
        )
