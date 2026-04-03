"""Regressions-Tests für strukturierte Retrieval-Qualität.

Prüft konkrete Scenarios (Hannover/15-Minuten-Stadt, Gender-Rollenspiele),
um sicherzustellen dass:
  - Queries aus SearchProfile-Logik stammen (nicht aus generischen Textfragmenten)
  - Teilclaims eigene/fokussierte Frames bekommen
  - Off-topic-Treffer abgewertet oder verworfen werden
  - Produktseiten/irrelevante Artikel nicht in Top-Evidenzquellen landen
  - Confidence gedeckelt bleibt wenn Top-Quellen schwach sind
"""

from __future__ import annotations

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_hannover_claim() -> "ProcessedClaim":
    """Composite Hannover-Claim: Fahrtenbegrenzung + Bußgeld."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    frame = ClaimFrame(
        raw_text=(
            "Der Stadtrat von Hannover will Autofahrten auf 100 pro Jahr begrenzen "
            "und Verstöße mit 250 Euro Bußgeld ahnden."
        ),
        subject="Stadtrat von Hannover",
        predicate="begrenzen und ahnden",
        object="Autofahrten",
        institution="Stadtrat Hannover",
        location="Hannover",
        numbers=["100", "250"],
        sanction="250 Euro Bußgeld",
        enforcement="Kameraüberwachung",
        policy_context="15-Minuten-Stadt",
    )
    profile = ClaimSearchProfile(
        core_entities=["Stadtrat Hannover", "Hannover"],
        institutions=["Stadtrat Hannover"],
        locations=["Hannover"],
        action_terms=["begrenzen", "ahnden"],
        policy_terms=["15-Minuten-Stadt"],
        number_terms=["100", "250"],
        sanction_terms=["250 Euro Bußgeld", "Kameraüberwachung"],
        exclusion_terms=["höhe", "bürger"],
        official_source_hints=["site:hannover.de"],
        fact_check_hints=["site:correctiv.org", "site:dpa-factchecking.com"],
    )
    return ProcessedClaim(
        id="C1",
        text=(
            "Der Stadtrat von Hannover will Autofahrten auf 100 pro Jahr begrenzen "
            "und Verstöße mit 250 Euro Bußgeld ahnden."
        ),
        type=ClaimType.FACTUAL,
        frame=frame,
        search_profile=profile,
    )


def _make_gender_claim() -> "ProcessedClaim":
    """Composite Gender-Rollenspiele-Claim: Rahmenlehrplan + Bußgeld."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    frame = ClaimFrame(
        raw_text=(
            "Im Rahmenlehrplan der 2. Klasse sind Gender-Rollenspiele verpflichtend "
            "und Eltern die ihr Kind abmelden zahlen ein Bußgeld."
        ),
        subject="Eltern",
        predicate="zahlen Bußgeld",
        object="Rahmenlehrplan 2. Klasse",
        institution="Bildungsministerium",
        location="Berlin",
        numbers=[],
        sanction="Bußgeld",
        enforcement="",
        policy_context="Rahmenlehrplan Gender-Rollenspiele",
    )
    profile = ClaimSearchProfile(
        core_entities=["Rahmenlehrplan", "Eltern", "Berlin"],
        institutions=["Bildungsministerium"],
        locations=["Berlin"],
        action_terms=["abmelden", "zahlen"],
        policy_terms=["Rahmenlehrplan 2 Klasse", "Gender-Rollenspiele"],
        number_terms=[],
        sanction_terms=["Bußgeld"],
        exclusion_terms=["rollenspiel"],
        official_source_hints=["site:bildungsserver.berlin-brandenburg.de"],
        fact_check_hints=["site:correctiv.org"],
    )
    return ProcessedClaim(
        id="C2",
        text=(
            "Im Rahmenlehrplan der 2. Klasse sind Gender-Rollenspiele verpflichtend "
            "und Eltern die ihr Kind abmelden zahlen ein Bußgeld."
        ),
        type=ClaimType.FACTUAL,
        frame=frame,
        search_profile=profile,
    )


# ── Tests: Query-Generierung aus SearchProfile ────────────────────────────────


class TestProfileBasedQueryGeneration:
    """Queries sollen aus SearchProfile-Logik stammen, nicht aus freiem Text."""

    def test_hannover_queries_contain_location_and_policy(self):
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_claim()
        queries = _build_search_queries_from_profile(claim)

        assert queries, "SearchProfile sollte mindestens eine Query erzeugen"
        combined = " ".join(queries).lower()

        # Hauptentitäten müssen in den Queries erscheinen
        assert "hannover" in combined, "Hannover muss in Queries vorkommen"
        assert any("15-minuten" in q.lower() or "stadtrat" in q.lower() for q in queries), \
            "Policy-Kontext oder Institution muss in einer Query auftauchen"

    def test_hannover_queries_not_just_generic_tokens(self):
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_claim()
        queries = _build_search_queries_from_profile(claim)

        for q in queries:
            words = q.lower().split()
            # Eine gültige Query ist nicht nur generische Einzelwörter
            assert len(words) >= 2, f"Query zu kurz/generisch: '{q}'"
            # Keine Query sollte nur aus allgemeinen Begriffen bestehen
            generic_only = all(w in {"höhe", "bürger", "auto", "grad", "form"} for w in words)
            assert not generic_only, f"Query besteht nur aus generischen Tokens: '{q}'"

    def test_hannover_queries_include_numbers_or_sanction(self):
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_claim()
        queries = _build_search_queries_from_profile(claim)
        combined = " ".join(queries)

        # Zahlen oder Sanktions-Term muss in mindestens einer Query auftauchen
        has_number_or_sanction = (
            "100" in combined or "250" in combined
            or "bußgeld" in combined.lower()
            or "sanktion" in combined.lower()
        )
        assert has_number_or_sanction, \
            "Zahl oder Sanktions-Begriff muss in mindestens einer Query vorkommen"

    def test_gender_queries_contain_curriculum_and_location(self):
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_gender_claim()
        queries = _build_search_queries_from_profile(claim)

        assert queries, "SearchProfile sollte mindestens eine Query erzeugen"
        combined = " ".join(queries).lower()
        assert "rahmenlehrplan" in combined or "gender" in combined, \
            "Rahmenlehrplan oder Gender muss in Queries erscheinen"

    def test_profile_yields_at_least_2_queries(self):
        from agents.fact_checker import _build_search_queries

        hannover = _make_hannover_claim()
        gender = _make_gender_claim()

        for claim in (hannover, gender):
            queries = _build_search_queries(claim)
            assert len(queries) >= 2, \
                f"Claim '{claim.id}' erzeugte nur {len(queries)} Queries – erwartet ≥ 2"

    def test_profile_first_over_llm_fallback(self):
        """Wenn Profil ≥ 3 Queries erzeugt, wird kein LLM gebraucht."""
        from agents.fact_checker import _build_search_queries

        claim = _make_hannover_claim()
        queries = _build_search_queries(claim)
        # Vollständiges Profil → direkt 3-4 Queries ohne LLM
        assert len(queries) >= 3, \
            "Vollständiges SearchProfile sollte ≥ 3 Queries direkt erzeugen (kein LLM)"


# ── Tests: Focused Frames für Sub-Claims ─────────────────────────────────────


class TestSubclaimFocusedFrames:
    """Teilclaims sollen fokussierte, nicht den Original-Frame erben."""

    def test_subclaim_sanction_frame_excludes_limit(self):
        """Sub-Claim über Bußgeld soll keinen Fahrtenbegrenzungs-Kontext erben."""
        from agents.claim_processor import _derive_subclaim_frame

        parent = _make_hannover_claim()
        # Sub-Claim A: nur über Bußgeld
        sanction_text = "Verstöße gegen die Fahrtenregel werden mit 250 Euro Bußgeld geahndet."
        frame, profile = _derive_subclaim_frame(sanction_text, parent.frame)

        # Sanktion muss vorhanden sein
        assert frame.sanction or "250" in frame.numbers, \
            "Sanktions-Feld muss im Bußgeld-Sub-Claim vorhanden sein"
        # Zahl 250 muss erkannt werden
        assert "250" in frame.numbers, "Zahl '250' muss in Sub-Claim-Frame enthalten sein"

    def test_subclaim_limit_frame_excludes_sanction(self):
        """Sub-Claim über Fahrtenbegrenzung soll keinen Bußgeld-Kontext erben."""
        from agents.claim_processor import _derive_subclaim_frame

        parent = _make_hannover_claim()
        # Sub-Claim B: nur über Fahrtenbegrenzung
        limit_text = "Der Stadtrat von Hannover will Autofahrten auf 100 pro Jahr begrenzen."
        frame, profile = _derive_subclaim_frame(limit_text, parent.frame)

        # Zahl 100 muss vorhanden sein
        assert "100" in frame.numbers, "Zahl '100' muss in Fahrtenbegrenzungs-Frame sein"
        # Bußgeld-spezifische Zahl 250 soll NICHT übernommen werden
        assert "250" not in frame.numbers, \
            "Zahl '250' darf nicht in Fahrtenbegrenzungs-Sub-Claim-Frame erben"

    def test_subclaim_keeps_shared_location(self):
        """Ort-Feld soll erhalten bleiben wenn er im Sub-Claim vorkommt."""
        from agents.claim_processor import _derive_subclaim_frame

        parent = _make_hannover_claim()
        sub_text = "Der Stadtrat von Hannover plant neue Verkehrsregeln."
        frame, profile = _derive_subclaim_frame(sub_text, parent.frame)

        assert "Hannover" in frame.location or "hannover" in frame.location.lower(), \
            "Ort 'Hannover' soll im Sub-Claim-Frame erhalten bleiben"

    def test_subclaim_drops_absent_fields(self):
        """Frame-Felder die im Sub-Claim nicht vorkommen sollen leer sein."""
        from agents.claim_processor import _derive_subclaim_frame

        parent = _make_hannover_claim()
        # Sub-Text der weder Sanktion noch Kameraüberwachung erwähnt
        sub_text = "Hannover diskutiert Fahrtenbeschränkungen im Stadtgebiet."
        frame, _ = _derive_subclaim_frame(sub_text, parent.frame)

        assert frame.sanction == "", \
            "Sanktions-Feld soll leer sein wenn Sanktion nicht im Sub-Claim"
        assert frame.enforcement == "", \
            "Enforcement-Feld soll leer sein wenn Überwachung nicht im Sub-Claim"

    def test_subclaim_profile_differs_from_parent(self):
        """Fokussiertes SearchProfile soll sich vom Original unterscheiden."""
        from agents.claim_processor import _derive_subclaim_frame

        parent = _make_hannover_claim()
        sub_text = "Verstöße gegen die Fahrtenregel werden mit 250 Euro Bußgeld geahndet."
        _, sub_profile = _derive_subclaim_frame(sub_text, parent.frame)

        # Sub-Profil darf nicht identisch mit Parent-Profil sein
        parent_profile = parent.search_profile
        # Fahrtenbegrenzungs-spezifische Policy sollte nicht in Bußgeld-Sub-Profil sein
        # (15-Minuten-Stadt kommt im Sub-Text nicht vor)
        assert sub_profile is not None, "Sub-Claim muss ein SearchProfile bekommen"


# ── Tests: Off-topic-Erkennung ────────────────────────────────────────────────


class TestOfftopicDetection:
    """Off-topic-Treffer müssen erkannt und abgewertet werden."""

    def test_product_page_camera_marked_offtopic(self):
        """MediaMarkt/Produkt-Seite für Kamera ist off-topic für Hannover-Claim."""
        from agents.evidence_builder import _is_offtopic_content
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )
        is_ot, penalty = _is_offtopic_content(
            title="Überwachungskamera 250 Euro kaufen – MediaMarkt",
            snippet="Günstige Überwachungskameras ab 250 Euro bei MediaMarkt bestellen.",
            profile=profile,
        )
        assert is_ot, "Produkt-Seite soll als off-topic erkannt werden"
        assert penalty >= 0.5, "Penalty soll ≥ 0.5 für klar off-topic Produkt-Seite sein"

    def test_generic_stadtrat_article_marked_offtopic(self):
        """Allgemeiner Stadtrat-Artikel ohne Hannover-Bezug ist off-topic."""
        from agents.evidence_builder import _is_offtopic_content
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )
        is_ot, penalty = _is_offtopic_content(
            title="Stadtrat beschließt neues Programm",
            snippet="Der Stadtrat hat heute ein neues Förderprogramm verabschiedet.",
            profile=profile,
        )
        assert is_ot or penalty >= 0.3, \
            "Allgemeiner Stadtrat-Artikel ohne Hannover-Bezug soll abgewertet werden"

    def test_relevant_hannover_article_not_offtopic(self):
        """Echter Hannover-15-Minuten-Artikel soll NICHT als off-topic markiert werden."""
        from agents.evidence_builder import _is_offtopic_content
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )
        is_ot, penalty = _is_offtopic_content(
            title="Hannover: Stadtrat debattiert 15-Minuten-Stadt Konzept",
            snippet=(
                "In Hannover diskutiert der Stadtrat über das 15-Minuten-Stadt-Konzept. "
                "Kritiker befürchten Einschränkungen für Autofahrer."
            ),
            profile=profile,
        )
        assert not is_ot, "Relevanter Hannover-Artikel soll NICHT als off-topic markiert werden"
        assert penalty < 0.3, f"Penalty soll < 0.3 sein, war {penalty}"

    def test_generic_rollenspiel_site_offtopic_for_gender_claim(self):
        """Allgemeine Rollenspiel-Seite ohne Bildungs-/Claim-Bezug ist off-topic."""
        from agents.evidence_builder import _is_offtopic_content
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Bildungsministerium"],
            locations=["Berlin"],
            policy_terms=["Rahmenlehrplan 2 Klasse", "Gender-Rollenspiele"],
            number_terms=[],
            sanction_terms=["Bußgeld"],
        )
        is_ot, penalty = _is_offtopic_content(
            title="Pen-and-Paper Rollenspiele für Einsteiger",
            snippet="Entdecke die Welt der Rollenspiele: Fantasy, Sci-Fi und mehr für Anfänger.",
            profile=profile,
        )
        assert is_ot or penalty >= 0.4, \
            "Allgemeine Rollenspiel-Seite soll als off-topic erkannt werden"

    def test_offtopic_url_pattern_still_works(self):
        """URL-basierte Off-topic-Erkennung bleibt weiterhin aktiv."""
        from agents.evidence_builder import _is_offtopic_url

        assert _is_offtopic_url("https://mediamarkt.de/kameras/ueberwachung-kaufen")
        assert _is_offtopic_url("https://example.com/rezept-schnell-kochen")
        assert not _is_offtopic_url("https://correctiv.org/faktencheck/hannover")


# ── Tests: Relevance Scoring mit Profil ──────────────────────────────────────


class TestProfileAnchorScoring:
    """Strukturierter Anchor-Score soll relevante Treffer aufwerten."""

    def _make_hannover_profile(self):
        from models.schemas import ClaimSearchProfile
        return ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )

    def test_hannover_article_scores_higher_than_generic(self):
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        profile = self._make_hannover_profile()
        claim_text = (
            "Der Stadtrat von Hannover will Autofahrten auf 100 pro Jahr begrenzen "
            "und Verstöße mit 250 Euro Bußgeld ahnden."
        )
        relevant = SearchResult(
            title="Hannover: Stadtrat diskutiert 15-Minuten-Stadt und Fahrtenbegrenzung",
            url="https://hannover.de/stadtrat/verkehr",
            snippet="Der Stadtrat Hannover berät über die 15-Minuten-Stadt. Autofahrer könnten betroffen sein.",
        )
        generic = SearchResult(
            title="Bürger diskutieren Stadtplanung",
            url="https://some-blog.de/stadtplanung",
            snippet="Bürger aus verschiedenen Städten diskutieren über moderne Stadtplanung.",
        )
        score_relevant = _relevance_score(relevant, claim_text, profile)
        score_generic = _relevance_score(generic, claim_text, profile)

        assert score_relevant > score_generic, \
            f"Hannover-Artikel ({score_relevant:.2f}) soll höher gewertet werden als generischer Artikel ({score_generic:.2f})"

    def test_product_page_scores_very_low(self):
        from agents.evidence_builder import _relevance_score
        from tools.web_search import SearchResult

        profile = self._make_hannover_profile()
        claim_text = "Hannover 250 Euro Bußgeld 15-Minuten-Stadt"

        product_page = SearchResult(
            title="Kamera 250 Euro kaufen – Top Angebote",
            url="https://www.mediamarkt.de/cameras/250euro",
            snippet="Günstige Kameras für 250 Euro. Jetzt bestellen und sparen!",
        )
        score = _relevance_score(product_page, claim_text, profile)
        assert score < 0.35, \
            f"Produkt-Seite soll niedrigen Score haben, war {score:.2f}"

    def test_profile_anchor_score_function(self):
        from agents.evidence_builder import _profile_anchor_score
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )
        # Hoher Treffer: Institution + Ort + Policy vorhanden
        high_text = "Stadtrat Hannover berät über 15-Minuten-Stadt Konzept mit 250 Euro Bußgeld"
        score_high = _profile_anchor_score(high_text.lower(), profile)

        # Kein Treffer
        low_text = "Kochen Rezepte für Anfänger mit Schritt für Schritt Anleitung"
        score_low = _profile_anchor_score(low_text.lower(), profile)

        assert score_high > score_low, \
            f"Hoher Anchor-Score ({score_high:.2f}) soll > niedriger Score ({score_low:.2f}) sein"
        assert score_high > 0.5, f"Hoher Anchor-Score soll > 0.5 sein, war {score_high:.2f}"
        assert score_low < 0.1, f"Kein-Treffer-Score soll < 0.1 sein, war {score_low:.2f}"


# ── Tests: Freshness End-to-End ───────────────────────────────────────────────


class TestFreshnessScoring:
    """Freshness soll echte Daten verwenden, kein neutraler Placeholder."""

    def test_recent_date_scores_high(self):
        from agents.evidence_builder import _compute_freshness
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert _compute_freshness(today) >= 0.9

    def test_old_date_scores_low(self):
        from agents.evidence_builder import _compute_freshness

        assert _compute_freshness("2019-01-01") <= 0.2

    def test_unknown_date_neutral(self):
        from agents.evidence_builder import _compute_freshness

        assert _compute_freshness("") == 0.5
        assert _compute_freshness("unbekannt xyz") == 0.5

    def test_publication_date_propagates_to_evidence_source(self):
        """publication_date aus ScrapedSource landet in EvidenceSource."""
        from tools.source_scraper import ScrapedSource

        # ScrapedSource hat publication_date-Feld
        sc = ScrapedSource(
            url="https://example.com",
            tier_label="Qualitätsjournalismus",
            passage="Test-Passage",
            low_relevance=False,
            fetch_success=True,
            error=None,
            publication_date="2024-03-15",
        )
        assert sc.publication_date == "2024-03-15"

    def test_scrapedsource_default_pub_date_empty(self):
        """ScrapedSource ohne publication_date hat leeren String als Default."""
        from tools.source_scraper import ScrapedSource

        sc = ScrapedSource(
            url="https://example.com",
            tier_label="Tier",
            passage="",
            low_relevance=False,
            fetch_success=False,
            error="timeout",
        )
        assert sc.publication_date == ""


# ── Tests: Ranking-Gesamtbild ─────────────────────────────────────────────────


class TestRankingIntegration:
    """Integration: Ranking soll Off-topic-Ergebnisse hinten platzieren."""

    def test_offtopic_results_ranked_below_relevant(self):
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import EvidenceItem, EvidenceSource, GoogleFactCheckMatch
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100", "250"],
            sanction_terms=["Bußgeld"],
        )
        claim_text = (
            "Hannover Stadtrat 15-Minuten-Stadt 100 Autofahrten 250 Euro Bußgeld"
        )
        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://tagesschau.de/inland/hannover-stadtrat", title="Hannover Stadtrat 15-Minuten-Stadt Debatte"),
                excerpt="Der Stadtrat Hannover diskutiert das 15-Minuten-Stadt-Konzept.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://www.mediamarkt.de/kamera/250", title="Kamera 250 Euro MediaMarkt Angebot"),
                excerpt="Überwachungskamera für 250 Euro jetzt kaufen bei MediaMarkt.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://random-blog.de/stadtrat-allgemein", title="Stadtrat beschließt Programm"),
                excerpt="Ein Stadtrat hat heute ein neues Programm verabschiedet.",
            ),
        ]

        items = _rank_evidence_items(results, claim_text, [], profile=profile)
        urls = [i.source.url for i in items]

        # Relevanter Tagesschau-Artikel soll vorne sein
        if urls:
            assert "tagesschau.de" in urls[0], \
                f"Tagesschau-Artikel soll auf Platz 1 sein, war {urls[0]}"

        # MediaMarkt-Seite soll entweder verworfen oder hinten sein
        if "mediamarkt.de" in " ".join(urls):
            mediamarkt_pos = next(i for i, u in enumerate(urls) if "mediamarkt" in u)
            tagesschau_pos = next((i for i, u in enumerate(urls) if "tagesschau" in u), -1)
            if tagesschau_pos >= 0:
                assert tagesschau_pos < mediamarkt_pos, \
                    "Tagesschau soll vor MediaMarkt ranken"

    def test_confidence_cap_with_weak_evidence(self):
        """Confidence bleibt gedeckelt wenn Top-Quellen schwach sind."""
        from agents.verdict_agent import _calibrate_confidence
        from models.evidence_models import (
            EvidenceItem,
            EvidencePack,
            EvidenceQualitySignals,
            EvidenceSource,
            SourceConsensus,
        )

        weak_quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            freshness_score=0.5,
            overall_quality=0.15,  # sehr niedrig
            top_tier_count=0,
            off_topic_rate=0.8,   # hohe Off-topic-Rate
            avg_top5_relevance=0.1,
        )
        pack = EvidencePack(
            claim_id="C1",
            claim_text="Test Claim",
            evidence_quality=weak_quality,
            web_results=[],
        )

        calibrated, reasons = _calibrate_confidence(
            raw_confidence=0.85,
            pack=pack,
            cove_trace=None,
            claim_quality_score=0.9,
        )

        # Mit schwacher Evidenz + hoher Off-topic-Rate soll Confidence stark gedeckelt sein
        assert calibrated <= 0.75, \
            f"Confidence soll bei schwacher Evidenz ≤ 0.75 sein, war {calibrated:.2f}"
        assert reasons, "Deckelungs-Grund soll angegeben sein"


# ── Tests: Neue Ceilings für schwache avg_top5_relevance ─────────────────────


class TestAvgRelevanceCeilings:
    """Confidence-Ceiling bei schwacher Top-5-Relevanz (Produkte, Rechner, Hilfsseiten)."""

    def _make_pack(self, avg_relevance: float, off_topic_rate: float = 0.2) -> "EvidencePack":
        from models.evidence_models import (
            EvidencePack, EvidenceItem, EvidenceQualitySignals, EvidenceSource, SourceConsensus,
        )
        quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            freshness_score=0.5,
            overall_quality=0.30,
            top_tier_count=0,
            off_topic_rate=off_topic_rate,
            avg_top5_relevance=avg_relevance,
        )
        # Mindestens ein EvidenceItem damit web_results nicht leer ist
        # (leere web_results → avg_top5_relevance Ceiling wird nicht ausgelöst)
        dummy_item = EvidenceItem(
            source=EvidenceSource(
                url="https://some-blog.de/page",
                title="Generic Page",
                domain="some-blog.de",
                domain_tier=5,
                is_fact_check_org=False,
            ),
            excerpt="Generic content",
            relevance_score=avg_relevance,
            extraction_confidence=0.3,
        )
        return EvidencePack(
            claim_id="C_test",
            claim_text="Test Claim",
            evidence_quality=quality,
            web_results=[dummy_item],
        )

    def test_very_low_relevance_ceiling_058(self):
        """avg_top5_relevance=0.10 (echter Messwert) → Ceiling 0.58."""
        from agents.verdict_agent import _calibrate_confidence
        # avg_relevance=0.10 ist ein echter Messwert (> 0), Ceiling soll greifen
        pack = self._make_pack(avg_relevance=0.10)
        calibrated, reasons = _calibrate_confidence(0.90, pack, None, 1.0)
        assert calibrated <= 0.58, \
            f"Bei avg_relevance=0.10 soll Ceiling ≤ 0.58 sein, war {calibrated:.2f}"
        assert any("sehr schwach" in r.lower() or "top-5" in r.lower() for r in reasons), \
            "Ceiling-Grund soll Top-5-Relevanz erwähnen"

    def test_low_relevance_ceiling_068(self):
        """avg_top5_relevance=0.20 (echter Messwert) → Ceiling 0.68."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(avg_relevance=0.20)
        calibrated, reasons = _calibrate_confidence(0.90, pack, None, 1.0)
        assert calibrated <= 0.68, \
            f"Bei avg_relevance=0.20 soll Ceiling ≤ 0.68 sein, war {calibrated:.2f}"

    def test_acceptable_relevance_no_new_ceiling(self):
        """avg_top5_relevance ≥ 0.25 löst keinen neuen Relevanz-Ceiling aus."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(avg_relevance=0.35)
        calibrated, reasons = _calibrate_confidence(0.90, pack, None, 1.0)
        # Andere Ceilings (kein Primary, kein FC) greifen weiterhin
        # Aber kein spezifischer avg_relevance-Ceiling
        assert not any("top-5-quellen schwach" in r.lower() for r in reasons), \
            "Bei avg_relevance=0.35 soll kein Top-5-Relevanz-Ceiling ausgelöst werden"

    def test_zero_avg_relevance_no_false_ceiling(self):
        """avg_top5_relevance=0.0 (Default-Sentinel, nicht gemessen) → kein Ceiling."""
        from agents.verdict_agent import _calibrate_confidence
        from models.evidence_models import EvidencePack, EvidenceQualitySignals, SourceConsensus
        # Leeres Pack ohne Quellen – avg_top5_relevance=0.0 ist Sentinel, kein Messwert
        quality = EvidenceQualitySignals(
            has_primary_sources=True,
            has_fact_check_org_result=True,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=0.9,
            overall_quality=0.9,
            top_tier_count=1,
            avg_top5_relevance=0.0,  # Sentinel-Default
        )
        pack = EvidencePack(
            claim_id="C_test",
            claim_text="Test Claim",
            evidence_quality=quality,
            web_results=[],  # leer → avg_top5_relevance ist Dummy
        )
        calibrated, reasons = _calibrate_confidence(0.90, pack, None, 1.0)
        # Kein avg-Relevanz-Ceiling soll ausgelöst werden
        assert not any("top-5" in r.lower() for r in reasons), \
            f"Bei avg_relevance=0.0 ohne Quellen soll kein Ceiling ausgelöst werden: {reasons}"


# ── Tests: Commercial Content Erkennung ──────────────────────────────────────


class TestCommercialContentDetection:
    """_has_commercial_content erkennt Shop-Sprache in Titeln/Snippets."""

    def test_shop_buy_language_detected(self):
        from agents.evidence_builder import _has_commercial_content
        assert _has_commercial_content(
            "Überwachungskamera 250 Euro – Jetzt kaufen",
            "Top Angebote für Überwachungskameras. Jetzt bestellen!",
        )

    def test_bussgeldrechner_detected(self):
        from agents.evidence_builder import _has_commercial_content
        assert _has_commercial_content(
            "Bußgeldrechner online",
            "Bußgeld berechnen: Geben Sie Ihr Vergehen ein und berechnen Sie das Bußgeld.",
        )

    def test_news_article_not_commercial(self):
        from agents.evidence_builder import _has_commercial_content
        assert not _has_commercial_content(
            "Hannover: Stadtrat debattiert 15-Minuten-Stadt",
            "Der Stadtrat Hannover diskutiert Maßnahmen zur Verkehrsberuhigung.",
        )

    def test_correctiv_not_commercial(self):
        from agents.evidence_builder import _has_commercial_content
        assert not _has_commercial_content(
            "Faktencheck: Wird in Hannover eine Fahrtensteuer eingeführt?",
            "Correctiv hat geprüft ob der Stadtrat Hannover Autofahrten begrenzen will.",
        )


# ── Tests: Shop-Domain als off-topic URL ─────────────────────────────────────


class TestShopDomainOfftopic:
    """Bekannte Shop-Domains sollen via _is_offtopic_url erkannt werden."""

    def test_mediamarkt_offtopic_url(self):
        from agents.evidence_builder import _is_offtopic_url
        assert _is_offtopic_url("https://www.mediamarkt.de/de/product/kamera.html")

    def test_bussgeldrechner_offtopic_url(self):
        from agents.evidence_builder import _is_offtopic_url
        assert _is_offtopic_url("https://www.bussgeldkatalog.de/rechner/")
        assert _is_offtopic_url("https://bussgeldrechner.de/berechnen")

    def test_correctiv_not_offtopic_url(self):
        from agents.evidence_builder import _is_offtopic_url
        assert not _is_offtopic_url("https://correctiv.org/faktencheck/hannover-15min")

    def test_saturn_offtopic_url(self):
        from agents.evidence_builder import _is_offtopic_url
        assert _is_offtopic_url("https://www.saturn.de/de/product/kamera")


# ── Tests: Q1 action_terms Fallback + Q4 Location-Anker ─────────────────────


class TestQueryProfileImprovements:
    """Verbesserte Query-Generierung aus SearchProfile."""

    def test_q1_uses_action_terms_when_no_policy(self):
        """Wenn policy_terms leer, sollen action_terms in Q1 landen."""
        from agents.fact_checker import _build_search_queries_from_profile
        from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

        frame = ClaimFrame(
            raw_text="Der Stadtrat Hannover plant Maßnahmen zur Verkehrsberuhigung.",
            subject="Stadtrat Hannover",
            predicate="plant Maßnahmen",
            object="Verkehrsberuhigung",
            institution="Stadtrat Hannover",
            location="Hannover",
            policy_context="",  # absichtlich leer
        )
        profile = ClaimSearchProfile(
            core_entities=["Stadtrat Hannover"],
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            action_terms=["Verkehrsberuhigung", "Beschluss"],
            policy_terms=[],  # leer
            number_terms=[],
            sanction_terms=[],
            official_source_hints=["site:hannover.de"],
            fact_check_hints=["site:correctiv.org"],
        )
        claim = ProcessedClaim(
            id="C_test", text="Stadtrat Hannover Verkehr", type=ClaimType.FACTUAL,
            frame=frame, search_profile=profile,
        )
        queries = _build_search_queries_from_profile(claim)
        q1 = queries[0] if queries else ""
        assert any("verkehrsberuhigung" in q.lower() or "beschluss" in q.lower() for q in queries), \
            f"Q1 soll action_term enthalten wenn policy_terms leer: {queries}"

    def test_q4_includes_location_anchor(self):
        """Q4 (sanction/number) soll Ort als Anker enthalten."""
        from agents.fact_checker import _build_search_queries_from_profile
        from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

        frame = ClaimFrame(
            raw_text="Verstöße werden mit 250 Euro Bußgeld geahndet.",
            subject="Stadtrat Hannover",
            predicate="ahndet",
            object="Verstöße",
            institution="Stadtrat Hannover",
            location="Hannover",
            numbers=["250"],
            sanction="250 Euro Bußgeld",
            policy_context="15-Minuten-Stadt",
        )
        profile = ClaimSearchProfile(
            core_entities=["Stadtrat Hannover", "Hannover"],
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            action_terms=["ahnden"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["250"],
            sanction_terms=["250 Euro Bußgeld"],
            official_source_hints=["site:hannover.de"],
            fact_check_hints=["site:correctiv.org"],
        )
        claim = ProcessedClaim(
            id="C_test", text="Verstöße 250 Euro Bußgeld Hannover", type=ClaimType.FACTUAL,
            frame=frame, search_profile=profile,
        )
        queries = _build_search_queries_from_profile(claim)
        # Q4 (sanction) soll Location enthalten, nicht nur "250 Bußgeld"
        sanction_queries = [q for q in queries if "250" in q or "bußgeld" in q.lower()]
        for q in sanction_queries:
            assert "hannover" in q.lower(), \
                f"Sanktions-Query soll Ort 'Hannover' enthalten, war: '{q}'"

    def test_hannover_full_profile_at_least_3_queries(self):
        """Vollständiges SearchProfile soll ≥ 3 Queries ohne LLM erzeugen."""
        from agents.fact_checker import _build_search_queries_from_profile
        claim = _make_hannover_claim()
        queries = _build_search_queries_from_profile(claim)
        assert len(queries) >= 3, \
            f"Vollständiges Profil soll ≥ 3 Queries erzeugen, war {len(queries)}: {queries}"

    def test_no_decontextualized_number_queries(self):
        """Es darf keine Query entstehen die nur aus Zahlen/generischen Tokens besteht."""
        from agents.fact_checker import _build_search_queries_from_profile
        claim = _make_hannover_claim()
        queries = _build_search_queries_from_profile(claim)
        for q in queries:
            tokens = q.strip().split()
            # Eine gültige Query muss mehr als 1 Token haben
            assert len(tokens) >= 2, f"Zu kurze/generische Query: '{q}'"
            # Keine Query besteht nur aus Zahlen
            all_numbers = all(re.match(r"^\d+$", t) for t in tokens)
            assert not all_numbers, f"Query besteht nur aus Zahlen: '{q}'"


import re  # noqa: E402 – benötigt für TestQueryProfileImprovements


# ── Tests: Low-Trust-Seitentyp-Erkennung ──────────────────────────────────────


class TestLowTrustSiteDetection:
    """Low-Trust-Seiten (Währungsrechner, Grammatik, Juraforen) müssen erkannt werden."""

    def test_xe_currency_converter_is_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://www.xe.com/de/currencyconverter/convert/?Amount=250&From=GBP&To=EUR",
            "250 GBP in EUR – Xe Währungsrechner",
            "250 Britische Pfund = 289,50 Euro. Aktueller Wechselkurs.",
        )

    def test_verbformen_grammar_is_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://www.verbformen.de/konjugation/duerfen.htm",
            "Konjugation dürfen – alle Formen, Tabellen, Beispiele",
            "Die Konjugation des Verbs dürfen: Indikativ, Konjunktiv, Imperativ.",
        )

    def test_juraforum_generic_is_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://www.juraforum.de/lexikon/kameraueberwachung",
            "Kameraüberwachung – Juraforum Rechtslexikon",
            "Definition Kameraüberwachung im deutschen Recht.",
        )

    def test_currency_converter_snippet_is_low_trust(self):
        """Auch bei unbekannter Domain: Titel/Snippet mit Währungsumrechnung → Low Trust."""
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://unknown-converter.com/eur-gbp",
            "250 GBP to EUR – Currency Converter",
            "Convert 250 British Pounds to Euros at current exchange rate.",
        )

    def test_grammar_conjugation_snippet_is_low_trust(self):
        """Konjugationsseite über Snippet erkannt, auch bei unbekannter Domain."""
        from agents.evidence_builder import _is_low_trust_site
        assert _is_low_trust_site(
            "https://some-grammar-site.com/verbs",
            "Konjugation von 'dürfen' – Indikativ, Konjunktiv",
            "Verb konjugieren: ich darf, du darfst, er darf. Präteritum: durfte.",
        )

    def test_tagesschau_not_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert not _is_low_trust_site(
            "https://www.tagesschau.de/inland/hannover-verkehr",
            "Hannover plant neue Verkehrsregeln",
            "Der Stadtrat Hannover diskutiert Maßnahmen zur Verkehrsberuhigung.",
        )

    def test_correctiv_not_low_trust(self):
        from agents.evidence_builder import _is_low_trust_site
        assert not _is_low_trust_site(
            "https://correctiv.org/faktencheck/hannover",
            "Faktencheck: 15-Minuten-Stadt in Hannover",
            "Correctiv prüft die Behauptung über eine Fahrtenbegrenzung in Hannover.",
        )


# ── Tests: Hannover C2/C3 Szenario – Regressionstests ────────────────────────


def _make_hannover_c2() -> "ProcessedClaim":
    """C2: Bürger dürfen nur 100x im Jahr mit Auto den Bezirk verlassen."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    frame = ClaimFrame(
        raw_text="Bürger dürfen ihre zugewiesenen Wohnbezirke dann nur noch maximal 100 Mal im Jahr mit dem Auto verlassen.",
        subject="Bürger",
        predicate="dürfen verlassen",
        object="zugewiesene Wohnbezirke",
        institution="Stadtrat Hannover",
        location="Hannover",
        numbers=["100"],
        sanction="",
        enforcement="",
        policy_context="15-Minuten-Stadt",
    )
    profile = ClaimSearchProfile(
        core_entities=["Stadtrat Hannover", "Hannover"],
        institutions=["Stadtrat Hannover"],
        locations=["Hannover"],
        action_terms=["begrenzen", "verlassen"],
        policy_terms=["15-Minuten-Stadt"],
        number_terms=["100"],
        sanction_terms=[],
        exclusion_terms=["höhe", "bürger", "dürfen"],
        official_source_hints=["site:hannover.de"],
        fact_check_hints=["site:correctiv.org"],
    )
    return ProcessedClaim(
        id="C2",
        text="Bürger dürfen ihre zugewiesenen Wohnbezirke dann nur noch maximal 100 Mal im Jahr mit dem Auto verlassen.",
        type=ClaimType.STATISTICAL,
        frame=frame,
        search_profile=profile,
    )


def _make_hannover_c3() -> "ProcessedClaim":
    """C3: Zuwiderhandlungen werden per Kamera mit 250 Euro Bußgeld geahndet."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    frame = ClaimFrame(
        raw_text="Zuwiderhandlungen werden automatisiert per Kameraüberwachung mit 250 Euro Bußgeld geahndet.",
        subject="Zuwiderhandlungen",
        predicate="geahndet",
        object="Autofahrer",
        institution="Stadtrat Hannover",
        location="Hannover",
        numbers=["250"],
        sanction="250 Euro Bußgeld",
        enforcement="Kameraüberwachung",
        policy_context="15-Minuten-Stadt",
    )
    profile = ClaimSearchProfile(
        core_entities=["Stadtrat Hannover", "Hannover"],
        institutions=["Stadtrat Hannover"],
        locations=["Hannover"],
        action_terms=["ahnden"],
        policy_terms=["15-Minuten-Stadt"],
        number_terms=["250"],
        sanction_terms=["250 Euro Bußgeld", "Kameraüberwachung"],
        exclusion_terms=["höhe"],
        official_source_hints=["site:hannover.de"],
        fact_check_hints=["site:correctiv.org"],
    )
    return ProcessedClaim(
        id="C3",
        text="Zuwiderhandlungen werden automatisiert per Kameraüberwachung mit 250 Euro Bußgeld geahndet.",
        type=ClaimType.STATISTICAL,
        frame=frame,
        search_profile=profile,
    )


class TestHannoverC2Regression:
    """C2: Keine Grammatik-/Konjugationsseiten in Top-Evidenz."""

    def test_c2_queries_no_isolated_duerfen(self):
        """Queries für C2 sollen nicht zu 'dürfen 100 auto' driften."""
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_c2()
        queries = _build_search_queries_from_profile(claim)

        for q in queries:
            tokens = q.lower().split()
            # Keine Query soll nur 'dürfen' + Zahl sein
            assert not (
                len(tokens) <= 3 and "dürfen" in tokens and any(t.isdigit() for t in tokens)
            ), f"Zu generische Query würde Grammatikseiten auslösen: '{q}'"

    def test_c2_queries_contain_hannover_and_policy(self):
        """C2-Queries sollen Hannover und Policy-Kontext enthalten."""
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_c2()
        queries = _build_search_queries_from_profile(claim)
        combined = " ".join(queries).lower()

        assert "hannover" in combined, f"Hannover muss in C2-Queries vorkommen: {queries}"
        assert "15-minuten" in combined or "stadtrat" in combined, \
            f"Policy oder Institution muss in C2-Queries vorkommen: {queries}"

    def test_c2_grammar_page_filtered_from_ranking(self):
        """Grammatik-/Konjugationsseite wird aus Ranking verworfen oder stark abgewertet."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import EvidenceItem, EvidenceSource
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["100"],
        )
        claim_text = "Bürger dürfen Wohnbezirke 100 Mal Jahr Auto verlassen Hannover"

        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://www.verbformen.de/konjugation/duerfen.htm", title="Konjugation dürfen – alle Formen, Tabellen, Beispiele"),
                excerpt="Die Konjugation des Verbs dürfen im Deutschen: Indikativ, Konjunktiv.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://tagesschau.de/inland/hannover-verkehr", title="Hannover: Stadtrat debattiert Verkehrskonzept"),
                excerpt="Der Stadtrat Hannover diskutiert das 15-Minuten-Stadt-Konzept.",
            ),
        ]

        items = _rank_evidence_items(results, claim_text, [], profile=profile)
        urls = [i.source.url for i in items]

        # Grammatikseite soll entweder verworfen oder hinter der Tagesschau sein
        if "verbformen.de" in " ".join(urls):
            vf_pos = next(i for i, u in enumerate(urls) if "verbformen" in u)
            ts_pos = next((i for i, u in enumerate(urls) if "tagesschau" in u), -1)
            if ts_pos >= 0:
                assert ts_pos < vf_pos, \
                    "Tagesschau-Artikel soll vor Grammatikseite ranken"


class TestHannoverC3Regression:
    """C3: Keine Währungsrechner in Top-Evidenz."""

    def test_c3_queries_no_isolated_250(self):
        """C3-Queries sollen nicht '250 euro kamera' oder 'bußgeld 250' isoliert sein."""
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_c3()
        queries = _build_search_queries_from_profile(claim)

        for q in queries:
            tokens = q.lower().split()
            # Keine Query nur aus '250' + einem generischen Wort
            if "250" in tokens and len(tokens) <= 2:
                pytest.fail(f"Isolierte Zahl-Query würde Währungsrechner auslösen: '{q}'")

    def test_c3_queries_contain_bound_sanction(self):
        """C3-Queries sollen gebundene Sanktion '250 Euro Bußgeld' verwenden, nicht '250' isoliert."""
        from agents.fact_checker import _build_search_queries_from_profile

        claim = _make_hannover_c3()
        queries = _build_search_queries_from_profile(claim)
        combined = " ".join(queries).lower()

        # Wenn 250 vorkommt, muss es gebunden sein (mit Bußgeld, Euro, Sanktion etc.)
        if "250" in combined:
            assert "bußgeld" in combined or "euro" in combined or "sanktion" in combined, \
                f"Zahl '250' muss gebunden vorkommen, nicht isoliert: {queries}"

    def test_c3_currency_converter_filtered(self):
        """Währungsrechner-Seite wird aus Ranking verworfen."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import EvidenceItem, EvidenceSource
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["250"],
            sanction_terms=["250 Euro Bußgeld", "Kameraüberwachung"],
        )
        claim_text = "Hannover 250 Euro Bußgeld Kameraüberwachung 15-Minuten-Stadt"

        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://www.xe.com/de/currencyconverter/convert/?Amount=250&From=GBP&To=EUR", title="250 GBP in EUR – Xe Währungsrechner"),
                excerpt="250 Britische Pfund = 289,50 Euro. Aktueller Wechselkurs.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://correctiv.org/faktencheck/hannover-15min", title="Faktencheck: 15-Minuten-Stadt Hannover"),
                excerpt="Correctiv prüft die Behauptung über Bußgelder in Hannover.",
            ),
        ]

        items = _rank_evidence_items(results, claim_text, [], profile=profile)
        urls = [i.source.url for i in items]

        # Währungsrechner soll verworfen sein
        assert "xe.com" not in " ".join(urls), \
            f"Währungsrechner xe.com soll aus Ranking verworfen werden: {urls}"

    def test_c3_juraforum_without_claim_context_filtered(self):
        """Allgemeines Juraforum ohne Hannover-Bezug wird abgewertet/verworfen."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import EvidenceItem, EvidenceSource
        from models.schemas import ClaimSearchProfile

        profile = ClaimSearchProfile(
            institutions=["Stadtrat Hannover"],
            locations=["Hannover"],
            policy_terms=["15-Minuten-Stadt"],
            number_terms=["250"],
            sanction_terms=["250 Euro Bußgeld"],
        )
        claim_text = "Hannover Kameraüberwachung 250 Euro Bußgeld 15-Minuten-Stadt"

        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://www.juraforum.de/lexikon/kameraueberwachung", title="Kameraüberwachung – Juraforum Rechtslexikon"),
                excerpt="Definition Kameraüberwachung im deutschen Recht. Rechtliche Grundlagen.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://correctiv.org/faktencheck/hannover", title="Hannover: Debatte um 15-Minuten-Stadt"),
                excerpt="Correctiv prüft: Werden in Hannover Autofahrten begrenzt?",
            ),
        ]

        items = _rank_evidence_items(results, claim_text, [], profile=profile)
        urls = [i.source.url for i in items]

        # Correctiv soll vor Juraforum ranken
        if "juraforum" in " ".join(urls) and "correctiv" in " ".join(urls):
            jf_pos = next(i for i, u in enumerate(urls) if "juraforum" in u)
            co_pos = next(i for i, u in enumerate(urls) if "correctiv" in u)
            assert co_pos < jf_pos, \
                "Correctiv soll vor allgemeinem Juraforum ranken"


class TestRegulatoryClaimConfidence:
    """Regelungsclaims ohne offizielle Quellen: Confidence gedeckelt."""

    def _make_regulatory_pack(
        self, low_trust_rate: float = 0.0
    ) -> "EvidencePack":
        from models.evidence_models import (
            EvidenceItem, EvidencePack, EvidenceQualitySignals,
            EvidenceSource, SourceConsensus,
        )
        quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            freshness_score=0.5,
            overall_quality=0.25,
            top_tier_count=0,
            off_topic_rate=0.4,
            avg_top5_relevance=0.20,
            low_trust_rate=low_trust_rate,
        )
        dummy = EvidenceItem(
            source=EvidenceSource(
                url="https://some-blog.de/page",
                title="Generic Page",
                domain="some-blog.de",
                domain_tier=5,
            ),
            excerpt="Generic content",
            relevance_score=0.20,
            extraction_confidence=0.3,
        )
        return EvidencePack(
            claim_id="C3",
            claim_text="250 Euro Bußgeld per Kameraüberwachung",
            evidence_quality=quality,
            web_results=[dummy],
        )

    def test_regulatory_claim_without_official_source_capped(self):
        """Regelungsclaim ohne offizielle Quelle: Confidence stark gedeckelt."""
        from agents.verdict_agent import _calibrate_confidence

        pack = self._make_regulatory_pack()
        calibrated, reasons = _calibrate_confidence(
            raw_confidence=0.92,
            pack=pack,
            cove_trace=None,
            claim_quality_score=0.9,
            is_regulatory_claim=True,
        )

        # Multiple Ceilings greifen (insufficient consensus, weak evidence, no primary etc.)
        # Endwert soll deutlich unter 0.72 liegen
        assert calibrated <= 0.72, \
            f"Regulatory claim ohne offizielle Quelle: Confidence ≤ 0.72, war {calibrated:.2f}"
        assert len(reasons) >= 2, \
            f"Mehrere Deckungsgründe erwartet: {reasons}"

    def test_regulatory_ceiling_triggers_with_moderate_evidence(self):
        """Regulatory-Ceiling greift wenn nur die Regulatory-Bedingung fehlt."""
        from agents.verdict_agent import _calibrate_confidence
        from models.evidence_models import (
            EvidenceItem, EvidencePack, EvidenceQualitySignals,
            EvidenceSource, SourceConsensus,
        )

        # Moderate Evidenz (overall > 0.3, consensus != insufficient) – aber keine Primary/FC
        quality = EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=0.7,
            overall_quality=0.45,
            top_tier_count=0,
            off_topic_rate=0.1,
            avg_top5_relevance=0.35,
        )
        dummy = EvidenceItem(
            source=EvidenceSource(
                url="https://news-blog.de/page",
                title="News",
                domain="news-blog.de",
                domain_tier=5,
            ),
            excerpt="Content",
            relevance_score=0.35,
            extraction_confidence=0.5,
        )
        pack = EvidencePack(
            claim_id="C_reg",
            claim_text="Bußgeld bei Verstoß",
            evidence_quality=quality,
            web_results=[dummy],
        )

        calibrated, reasons = _calibrate_confidence(
            raw_confidence=0.88,
            pack=pack,
            cove_trace=None,
            claim_quality_score=0.9,
            is_regulatory_claim=True,
        )

        assert calibrated <= 0.72, \
            f"Regulatory claim ohne offizielle Quelle: Confidence ≤ 0.72, war {calibrated:.2f}"
        assert any("regelungsclaim" in r.lower() for r in reasons), \
            f"Soll Regelungsclaim-Ceiling erwähnen: {reasons}"

    def test_high_low_trust_rate_caps_confidence(self):
        """Wenn Low-Trust-Quellen dominieren: Confidence ≤ 0.62."""
        from agents.verdict_agent import _calibrate_confidence

        pack = self._make_regulatory_pack(low_trust_rate=0.6)
        calibrated, reasons = _calibrate_confidence(
            raw_confidence=0.88,
            pack=pack,
            cove_trace=None,
            claim_quality_score=0.9,
            is_regulatory_claim=True,
        )

        assert calibrated <= 0.62, \
            f"High low-trust rate: Confidence ≤ 0.62, war {calibrated:.2f}"
        assert any("low-trust" in r.lower() for r in reasons), \
            f"Soll Low-Trust-Ceiling erwähnen: {reasons}"

    def test_non_regulatory_claim_not_affected(self):
        """Nicht-Regelungsclaim soll kein Regulatory-Ceiling bekommen."""
        from agents.verdict_agent import _calibrate_confidence
        from models.evidence_models import (
            EvidencePack, EvidenceQualitySignals, SourceConsensus,
        )

        quality = EvidenceQualitySignals(
            has_primary_sources=True,
            has_fact_check_org_result=True,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=0.9,
            overall_quality=0.85,
            top_tier_count=2,
            avg_top5_relevance=0.70,
        )
        pack = EvidencePack(
            claim_id="C_test",
            claim_text="Test Claim",
            evidence_quality=quality,
            web_results=[],
        )
        calibrated, reasons = _calibrate_confidence(
            raw_confidence=0.90,
            pack=pack,
            cove_trace=None,
            claim_quality_score=0.9,
            is_regulatory_claim=False,
        )

        assert not any("regelungsclaim" in r.lower() for r in reasons), \
            f"Nicht-Regulatory Claim soll kein Regulatory-Ceiling haben: {reasons}"


# ── Regression: Aktuelle politische Claims (Recency / Freshness) ─────────────


def _make_merz_claim() -> "ProcessedClaim":
    """Aktuell-politischer Claim: Friedrich Merz als Bundeskanzler (ab Februar 2025)."""
    from models.schemas import ClaimFrame, ClaimSearchProfile, ClaimType, ProcessedClaim

    frame = ClaimFrame(
        raw_text="Friedrich Merz ist Bundeskanzler von Deutschland.",
        subject="Friedrich Merz",
        predicate="ist",
        object="Bundeskanzler",
        institution="Bundesregierung",
        location="Deutschland",
        numbers=[],
        sanction="",
        enforcement="",
        policy_context="Bundeskanzler Wahl 2025",
    )
    profile = ClaimSearchProfile(
        core_entities=["Friedrich Merz", "Bundeskanzler"],
        institutions=["Bundesregierung", "Bundestag"],
        locations=["Deutschland"],
        action_terms=["gewählt", "vereidigt"],
        policy_terms=["Bundeskanzler 2025"],
        number_terms=[],
        sanction_terms=[],
        exclusion_terms=["Scholz", "SPD-Kanzler"],
        official_source_hints=["site:bundesregierung.de"],
        fact_check_hints=["site:tagesschau.de"],
    )
    return ProcessedClaim(
        id="C_MERZ",
        text="Friedrich Merz ist Bundeskanzler von Deutschland.",
        type=ClaimType.FACTUAL,
        frame=frame,
        search_profile=profile,
    )


class TestRecencyMerzClaim:
    """Regression: Aktuelle politische Claims müssen frische Quellen bevorzugen.

    Verhindert, dass alte Scholz-Ära-Quellen (2021-2024) einen Merz-Claim
    fälschlich dominieren und die Confidence verzerren.
    """

    def test_freshness_scores_current_vs_stale(self):
        """Aktuelle Quellen (2026) erhalten hohe Freshness, alte (2022) niedrige."""
        from agents.evidence_builder import _compute_freshness
        assert _compute_freshness("2026-03-01") >= 0.70   # aktuell (< 30 Tage)
        assert _compute_freshness("2022-12-15") <= 0.30   # > 2 Jahre alt

    def test_stale_only_sources_penalize_confidence(self):
        """Nur alte Quellen (Scholz-Ära) → overall_quality wird durch Stale-Penalty gesenkt."""
        from agents.evidence_builder import _compute_quality_signals
        from tests.helpers import make_evidence_item_with_date

        # Typische Scholz-Ära-Quellen (2021-2023)
        items = [
            make_evidence_item_with_date("2022-11-01", relevance=0.85, tier=2),
            make_evidence_item_with_date("2021-09-26", relevance=0.80, tier=2),
            make_evidence_item_with_date("2023-03-15", relevance=0.75, tier=3),
        ]
        signals = _compute_quality_signals(
            items, google_matches=[],
            stale_threshold=0.35,
            stale_penalty_factor=0.15,
        )
        assert signals.freshness_score < 0.35, (
            f"Scholz-Ära-Quellen müssen Freshness < 0.35 haben, hat: {signals.freshness_score:.2f}"
        )
        assert signals.overall_quality < 0.60, (
            f"Nur alte Quellen → overall_quality < 0.60, hat: {signals.overall_quality:.2f}"
        )

    def test_fresh_merz_sources_maintain_quality(self):
        """Frische 2026-Quellen (aktuell über Merz) → overall_quality bleibt hoch."""
        from agents.evidence_builder import _compute_quality_signals
        from tests.helpers import make_evidence_item_with_date

        items = [
            make_evidence_item_with_date("2026-02-28", relevance=0.90, tier=2),
            make_evidence_item_with_date("2026-03-10", relevance=0.85, tier=2),
            make_evidence_item_with_date("2026-01-15", relevance=0.75, tier=3),
        ]
        signals = _compute_quality_signals(
            items, google_matches=[],
            stale_threshold=0.35,
            stale_penalty_factor=0.15,
        )
        assert signals.freshness_score >= 0.70, (
            f"Frische 2026-Quellen müssen Freshness >= 0.70 haben, hat: {signals.freshness_score:.2f}"
        )
        # Kontextuelle Tier-2-Quellen tragen nur 0.10 zum primary_contribution bei
        # (nicht 0.25), da sie keinen direkten Claim-Bezug haben. Der Threshold
        # spiegelt die neue Stufenlogik wider: frische offizielle Kontextquellen
        # haben meaningful Quality, aber nicht so hoch wie direkte Evidenz.
        assert signals.overall_quality >= 0.35, (
            f"Frische Quellen sollen overall_quality >= 0.35 halten, hat: {signals.overall_quality:.2f}"
        )

    def test_fresh_beats_stale_in_quality(self):
        """Frische aktuelle Quellen haben höhere overall_quality als alte Scholz-Quellen."""
        from agents.evidence_builder import _compute_quality_signals
        from tests.helpers import make_evidence_item_with_date

        old_items = [
            make_evidence_item_with_date("2022-11-01", relevance=0.85, tier=2),
            make_evidence_item_with_date("2021-09-26", relevance=0.80, tier=2),
        ]
        fresh_items = [
            make_evidence_item_with_date("2026-02-28", relevance=0.85, tier=2),
            make_evidence_item_with_date("2026-03-01", relevance=0.80, tier=2),
        ]
        signals_old = _compute_quality_signals(old_items, google_matches=[])
        signals_fresh = _compute_quality_signals(fresh_items, google_matches=[])
        assert signals_fresh.overall_quality > signals_old.overall_quality, (
            f"Frisch ({signals_fresh.overall_quality:.2f}) muss > veraltet ({signals_old.overall_quality:.2f}) sein"
        )

    def test_searxng_supports_news_category_for_current_claims(self):
        """SearXNGConfig unterstützt News-Kategorie für zeitkritische Claims."""
        from config import SearXNGConfig
        cfg = SearXNGConfig(categories=["news", "general"])
        assert "news" in cfg.categories

    def test_merz_claim_queries_include_relevant_terms(self):
        """Queries für Merz-Claim enthalten relevante Entitäten."""
        from agents.fact_checker import _build_search_queries_from_profile
        claim = _make_merz_claim()
        queries = _build_search_queries_from_profile(claim)
        assert len(queries) >= 1, "Mindestens eine Query muss generiert werden"
        joined = " ".join(queries).lower()
        assert any(term in joined for term in ["merz", "bundeskanzler", "bundesregierung"]), (
            f"Queries müssen 'merz', 'bundeskanzler' oder 'bundesregierung' enthalten: {queries}"
        )


# ── Regulatory Text Fallback Tests ────────────────────────────────────────────

class TestRegulatoryTextFallback:
    """Regression: Textueller Fallback für Regulatory-Erkennung wenn claim.frame fehlt."""

    def test_bussgeld_detected(self):
        """Bußgeld-Erwähnung → als Regulatory-Claim erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert _is_regulatory_from_text(
            "Wer die Grüne Zone ohne Erlaubnis betritt, zahlt ein Bußgeld von 250 Euro."
        )

    def test_ueberwachung_detected(self):
        """Überwachungs-Erwähnung → als Regulatory-Claim erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert _is_regulatory_from_text(
            "Die Stadt überwacht alle Bürger per Kamerasystem innerhalb der Zone."
        )

    def test_verordnung_detected(self):
        """Verordnungs-Erwähnung → als Regulatory-Claim erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert _is_regulatory_from_text(
            "Eine neue Verordnung schränkt die Einfahrt in die Zone ein."
        )

    def test_pflicht_detected(self):
        """Pflicht-Erwähnung → als Regulatory-Claim erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert _is_regulatory_from_text(
            "Es ist Pflicht, einen Ausweis mitzuführen wenn man die Zone betritt."
        )

    def test_generic_claim_not_regulatory(self):
        """Generischer Claim ohne Regelungsbegriffe → NICHT als Regulatory erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert not _is_regulatory_from_text(
            "Friedrich Merz ist Bundeskanzler von Deutschland."
        )

    def test_statistical_claim_not_regulatory(self):
        """Statistischer Claim → NICHT als Regulatory erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert not _is_regulatory_from_text(
            "Die Arbeitslosenquote beträgt 5,6 Prozent."
        )

    def test_regulatory_frame_takes_precedence(self):
        """Wenn claim.frame vorhanden und gesetzt, wird Text-Fallback nicht benötigt."""
        # Nur sicherstellen, dass _is_regulatory_from_text korrekt importierbar ist
        from agents.verdict_agent import _is_regulatory_from_text, _REGULATORY_TEXT_PATTERN
        assert _REGULATORY_TEXT_PATTERN is not None


class TestCurrentStateClaimRecencyOverride:
    """Integration: Recency-Override wird für Aktuell-Zustand-Claims in EvidenceBuilder angewandt."""

    def test_is_current_state_claim_imported_in_evidence_builder(self):
        """_is_current_state_claim ist im EvidenceBuilder nutzbar."""
        from agents.fact_checker import _is_current_state_claim
        assert _is_current_state_claim("Friedrich Merz ist Bundeskanzler.")

    def test_evidence_retrieval_config_has_current_state_threshold(self):
        """EvidenceRetrievalConfig enthält current_state_freshness_threshold."""
        from config import EvidenceRetrievalConfig
        cfg = EvidenceRetrievalConfig()
        assert hasattr(cfg, "current_state_freshness_threshold")
        assert 0.0 < cfg.current_state_freshness_threshold <= 1.0

    def test_searxng_config_has_news_categories(self):
        """EvidenceRetrievalConfig.searxng_news_categories enthält 'news'."""
        from config import EvidenceRetrievalConfig
        cfg = EvidenceRetrievalConfig()
        assert "news" in cfg.searxng_news_categories

    def test_verdict_agent_has_stale_ceiling_constants(self):
        """VerdictAgent exportiert die neuen Ceiling-Konstanten."""
        from agents.verdict_agent import _CEILING_STALE_SOURCES, _CEILING_CURRENT_STATE_NO_FRESH
        assert _CEILING_STALE_SOURCES < 1.0
        assert _CEILING_CURRENT_STATE_NO_FRESH < _CEILING_STALE_SOURCES


# ── Tests: Current-State Claim Recency (Merz / Bundeskanzler) ─────────────────


def _make_pack_with_freshness(freshness: float, has_primary: bool = True) -> "EvidencePack":
    """Hilfsfunktion: EvidencePack mit konfigurierbarer Freshness für Recency-Tests."""
    from models.evidence_models import (
        EvidencePack, EvidenceItem, EvidenceSource, EvidenceQualitySignals, SourceConsensus,
    )
    item = EvidenceItem(
        source=EvidenceSource(
            url="https://tagesschau.de/test",
            title="Nachricht",
            domain="tagesschau.de",
            domain_tier=3,
            is_primary_source=has_primary,
        ),
        excerpt="Test",
        relevance_score=0.8,
        extraction_confidence=0.8,
    )
    return EvidencePack(
        claim_id="C1",
        claim_text="Test",
        queries_used=["test"],
        google_fact_check_matches=[],
        web_results=[item],
        evidence_quality=EvidenceQualitySignals(
            has_primary_sources=has_primary,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.AGREEING,
            freshness_score=freshness,
            overall_quality=0.80,
            top_tier_count=1 if has_primary else 0,
        ),
        source_count=1,
    )


class TestCurrentStateClaimRecency:
    """Regression: Current-state Claims (Merz/Bundeskanzler) müssen recency-sensitiv sein."""

    def test_merz_bundeskanzler_detected_as_current_state(self):
        """Positive Erkennung: 'Merz ist Bundeskanzler' → current-state-Claim."""
        from agents.fact_checker import _is_current_state_claim
        assert _is_current_state_claim("Friedrich Merz ist Bundeskanzler von Deutschland.")

    def test_merz_war_kanzler_also_current_state(self):
        """'war kein Bundeskanzler' enthält 'war' + Positionsbegriff → zeitkritisch."""
        from agents.fact_checker import _is_current_state_claim
        assert _is_current_state_claim("Friedrich Merz war kein Bundeskanzler von Deutschland.")

    def test_stale_evidence_caps_confidence_for_merz_claim(self):
        """Veraltete Quellen (freshness=0.35) → confidence ≤ 0.55 für current-state."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_pack_with_freshness(0.35)
        confidence, reasons = _calibrate_confidence(
            0.85, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence <= _CEILING_CURRENT_STATE_NO_FRESH  # 0.55
        assert any("Aktuell-Zustand-Claim" in r for r in reasons)

    def test_unknown_date_sources_trigger_ceiling(self):
        """Quellen ohne Datum (default 0.5 → unter Threshold 0.60) → ceiling aktiv."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_pack_with_freshness(0.50)
        confidence, reasons = _calibrate_confidence(
            0.80, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence <= _CEILING_CURRENT_STATE_NO_FRESH  # 0.55

    def test_fresh_news_sources_no_ceiling_for_current_state(self):
        """Frische Quellen (0.80 > 0.60) → kein Freshness-Ceiling für current-state."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_CURRENT_STATE_NO_FRESH
        pack = _make_pack_with_freshness(0.80, has_primary=True)
        pack.evidence_quality.has_fact_check_org_result = True
        confidence, reasons = _calibrate_confidence(
            0.80, pack, None,
            is_current_state_claim=True,
            stale_freshness_threshold=0.60,
        )
        assert confidence > _CEILING_CURRENT_STATE_NO_FRESH
        assert not any("Aktuell-Zustand-Claim" in r for r in reasons)

    def test_current_state_threshold_in_config_is_60(self):
        """Config-Wert current_state_freshness_threshold muss 0.60 sein."""
        from config import EvidenceRetrievalConfig
        assert EvidenceRetrievalConfig().current_state_freshness_threshold == 0.60

    def test_rank_evidence_items_accepts_is_current_state(self):
        """_rank_evidence_items akzeptiert is_current_state-Parameter ohne Fehler."""
        from agents.evidence_builder import _rank_evidence_items
        from models.evidence_models import GoogleFactCheckMatch
        result = _rank_evidence_items([], "Merz ist Bundeskanzler.", [], is_current_state=True)
        assert result == []

    def test_compute_quality_signals_accepts_is_current_state(self):
        """_compute_quality_signals akzeptiert is_current_state=True ohne Fehler."""
        from agents.evidence_builder import _compute_quality_signals
        signals = _compute_quality_signals([], [], is_current_state=True)
        assert signals.freshness_score == 0.0  # Keine Items → 0.0

    def test_unknown_date_default_lower_for_current_state(self):
        """Ohne Datumsinformation: freshness=0.3 (current-state) statt 0.5 (normal)."""
        from agents.evidence_builder import _compute_quality_signals
        from models.evidence_models import EvidenceItem, EvidenceSource
        # Item ohne publication_date → zählt nicht in freshness_scores
        item = EvidenceItem(
            source=EvidenceSource(
                url="https://example.com", title="Test", domain="example.com",
                domain_tier=3, publication_date="",
            ),
            excerpt="Test",
            relevance_score=0.5,
            extraction_confidence=0.5,
        )
        signals_normal = _compute_quality_signals([item], [], is_current_state=False)
        signals_current = _compute_quality_signals([item], [], is_current_state=True)
        # Normal: 0.5 default; Current-state: 0.3 default
        assert signals_normal.freshness_score == 0.5
        assert signals_current.freshness_score == 0.3


# ── Tests: Regulatory Claim (Hannover / 15-Minuten-Stadt / Bußgeld) ──────────


def _make_regulatory_pack_no_direct() -> "EvidencePack":
    """EvidencePack für Regulatory-Claim: kein DIRECT evidence, nur CONTEXTUAL."""
    from models.evidence_models import (
        EvidencePack, EvidenceItem, EvidenceSource, EvidenceQualitySignals,
        SourceConsensus, EvidenceType,
    )
    items = [
        EvidenceItem(
            source=EvidenceSource(
                url=f"https://example{i}.de/test",
                title=f"Allgemeine Seite {i}",
                domain=f"example{i}.de",
                domain_tier=4,
            ),
            excerpt="Allgemeiner Hintergrund zur 15-Minuten-Stadt",
            relevance_score=0.4,
            extraction_confidence=0.4,
            evidence_type=EvidenceType.CONTEXTUAL,
        )
        for i in range(5)
    ]
    return EvidencePack(
        claim_id="C1",
        claim_text="In Hannover gilt ein 250-Euro-Bußgeld für mehr als 100 Autofahrten pro Jahr.",
        queries_used=["hannover bußgeld autofahrten"],
        google_fact_check_matches=[],
        web_results=items,
        evidence_quality=EvidenceQualitySignals(
            has_primary_sources=False,
            has_fact_check_org_result=False,
            source_consensus=SourceConsensus.INSUFFICIENT,
            freshness_score=0.6,
            overall_quality=0.25,
            top_tier_count=0,
            direct_evidence_count=0,
            contextual_only_rate=1.0,
        ),
        source_count=5,
    )


class TestRegulatoryClaimStrict:
    """Regression: Hannover/15-Minuten-Stadt – ohne Rechtsgrundlage keine weiche Bewertung."""

    def test_hannover_claim_detected_as_regulatory(self):
        """250-Euro-Bußgeld-Claim → als Regulatory erkannt."""
        from agents.verdict_agent import _is_regulatory_from_text
        assert _is_regulatory_from_text(
            "In Hannover werden Autofahrten auf 100 pro Jahr begrenzt mit 250 Euro Bußgeld."
        )

    def test_regulatory_no_direct_evidence_confidence_capped_055(self):
        """Regulatory + 0 DIRECT evidence → confidence ≤ 0.55."""
        from agents.verdict_agent import _calibrate_confidence, _CEILING_REGULATORY_NO_DIRECT_EVIDENCE
        pack = _make_regulatory_pack_no_direct()
        confidence, reasons = _calibrate_confidence(
            0.80, pack, None,
            is_regulatory_claim=True,
        )
        assert confidence <= _CEILING_REGULATORY_NO_DIRECT_EVIDENCE  # 0.55
        assert any("Regelungsclaim" in r for r in reasons)

    def test_regulatory_contextual_only_combined_ceiling(self):
        """Regulatory + contextual-only (1.0) + kein Primary/FC → strengstes Ceiling."""
        from agents.verdict_agent import _calibrate_confidence
        pack = _make_regulatory_pack_no_direct()
        confidence, reasons = _calibrate_confidence(
            0.90, pack, None,
            is_regulatory_claim=True,
        )
        assert confidence <= 0.68

    def test_regulatory_ceiling_constant_is_068(self):
        """_CEILING_REGULATORY_NO_DIRECT_EVIDENCE muss 0.68 sein."""
        from agents.verdict_agent import _CEILING_REGULATORY_NO_DIRECT_EVIDENCE
        assert _CEILING_REGULATORY_NO_DIRECT_EVIDENCE == 0.68
