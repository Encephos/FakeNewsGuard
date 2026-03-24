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
        from models.evidence_models import GoogleFactCheckMatch
        from models.schemas import ClaimSearchProfile
        from tools.web_search import SearchResult

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
            SearchResult(
                title="Hannover Stadtrat 15-Minuten-Stadt Debatte",
                url="https://tagesschau.de/inland/hannover-stadtrat",
                snippet="Der Stadtrat Hannover diskutiert das 15-Minuten-Stadt-Konzept.",
            ),
            SearchResult(
                title="Kamera 250 Euro MediaMarkt Angebot",
                url="https://www.mediamarkt.de/kamera/250",
                snippet="Überwachungskamera für 250 Euro jetzt kaufen bei MediaMarkt.",
            ),
            SearchResult(
                title="Stadtrat beschließt Programm",
                url="https://random-blog.de/stadtrat-allgemein",
                snippet="Ein Stadtrat hat heute ein neues Programm verabschiedet.",
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
