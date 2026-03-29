"""Tests für agents/fact_checker.py.

Testet primär die shared Query-Building-Utilities (via agents/query_builder.py)
und den Legacy-Fallback-Pfad. Die v2-Pipeline (EvidenceBuilder → CoVe →
VerdictAgent) wird in test_evidence_builder.py, test_cove_processor.py und
test_verdict_agent.py getestet.
"""

from __future__ import annotations

import pytest

from agents.fact_checker import (
    FactCheckerAgent,
    _ARTIFACT_TERMS,
    _build_enriched_context,
    _build_fallback_queries,
    _build_queries_for_underspecified_claim,
    _build_search_queries,
    _categories_for_claim,
    _evaluate_scrape_quality,
    _is_current_state_claim,
)
from models.schemas import Claim, ClaimType, FactRating
from tools.scrape_ranker import RankedSource
from tools.source_classifier import SourceTier
from tools.source_scraper import ScrapedSource
from tools.web_search import SearchResult


# ── _build_search_queries ─────────────────────────────────────────


def test_build_queries_includes_direct_search(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    assert sample_factual_claim.text in queries


def test_build_queries_adds_factcheck_terms(sample_factual_claim):
    queries = _build_search_queries(sample_factual_claim)
    # Adaptive: FACTUAL mit >60 Zeichen bekommt faktencheck, kürzere nicht
    assert len(queries) >= 1
    if len(sample_factual_claim.text) > 60:
        combined = " ".join(queries)
        assert "faktencheck" in combined.lower()


def test_build_queries_statistical_has_multiple(sample_statistical_claim):
    queries = _build_search_queries(sample_statistical_claim)
    assert len(queries) >= 3  # Direktsuche + faktencheck + statistik + destatis


# ── FactCheckerAgent ──────────────────────────────────────────────


def test_fact_checker_returns_result(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim):
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)
    result = agent.execute(sample_factual_claim)

    assert result.claim_id == sample_factual_claim.id
    assert result.rating == FactRating.MISLEADING
    assert result.evidence == "Test-Evidenz"


def test_fact_checker_fallback_on_invalid_rating(minimal_config, mocker, mock_search_client, sample_factual_claim):
    mock_llm = mocker.MagicMock()
    mock_llm.complete_json.return_value = {
        "claim_id": "C1",
        "rating": "INVALID_RATING",
        "evidence": "some",
        "sources": [],
    }
    mock_llm.complete_structured.return_value = mock_llm.complete_json.return_value

    agent = FactCheckerAgent(minimal_config, mock_llm, mock_search_client)
    result = agent.execute(sample_factual_claim)
    assert result.rating == FactRating.UNVERIFIABLE


def test_fact_checker_uses_cache(minimal_config, mock_llm_client, mock_search_client, sample_factual_claim, cache_config):
    from tools.cache import ClaimCache

    cache = ClaimCache(cache_config)
    agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client, cache)

    # Erster Aufruf – schreibt in Cache
    result1 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1

    # Zweiter Aufruf – sollte Cache-Treffer sein, kein LLM-Call
    result2 = agent.execute(sample_factual_claim)
    assert mock_llm_client.complete_structured.call_count == 1  # Kein neuer Call
    assert result1.claim_id == result2.claim_id
    assert result1.rating == result2.rating


# ── _categories_for_claim ────────────────────────────────────────


def test_categories_statistical():
    claim = Claim(id="C1", text="x", type=ClaimType.STATISTICAL)
    assert _categories_for_claim(claim) == "general,science,news"


def test_categories_causal():
    claim = Claim(id="C1", text="x", type=ClaimType.CAUSAL)
    assert _categories_for_claim(claim) == "general,science"


def test_categories_factual():
    claim = Claim(id="C1", text="x", type=ClaimType.FACTUAL)
    assert _categories_for_claim(claim) == "general,news"


def test_categories_contextual():
    claim = Claim(id="C1", text="x", type=ClaimType.CONTEXTUAL)
    assert _categories_for_claim(claim) == "general,news"


def test_categories_opinion_fallback():
    claim = Claim(id="C1", text="x", type=ClaimType.OPINION)
    assert _categories_for_claim(claim) == "general"


# ── _build_enriched_context ──────────────────────────────────────


def _make_ranked(
    url: str, tier: SourceTier, should_scrape: bool, skip_reason: str | None = None,
    title: str = "Titel", snippet: str = "Snippet",
) -> RankedSource:
    return RankedSource(
        result=SearchResult(title=title, url=url, snippet=snippet),
        tier=tier, relevance_score=0.5, should_scrape=should_scrape,
        skip_reason=skip_reason,
    )


def test_enriched_context_with_fulltext():
    ranked = [_make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True)]
    scraped = [ScrapedSource(
        url="https://correctiv.org/c", tier_label="Faktencheck-Organisation",
        passage="Laut unserer Recherche...", low_relevance=False,
        fetch_success=True, error=None,
    )]
    ctx = _build_enriched_context(ranked, scraped)
    assert "Volltext-Auszug" in ctx
    assert "Laut unserer Recherche" in ctx
    assert "[Faktencheck-Organisation]" in ctx


def test_enriched_context_with_failed_scrape():
    ranked = [_make_ranked("https://tagesschau.de/t", SourceTier.QUALITY_JOURNALISM, True)]
    scraped = [ScrapedSource(
        url="https://tagesschau.de/t", tier_label="Qualitätsjournalismus",
        passage="", low_relevance=False,
        fetch_success=False, error="Timeout",
    )]
    ctx = _build_enriched_context(ranked, scraped)
    assert "Snippet" in ctx
    assert "[Kein Volltext: Timeout]" in ctx


def test_enriched_context_with_skipped_source():
    ranked = [_make_ranked(
        "https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall",
        snippet="Paywall-Snippet",
    )]
    ctx = _build_enriched_context(ranked, [])
    assert "Paywall-Snippet" in ctx
    assert "[Kein Volltext: Paywall]" in ctx


def test_enriched_context_skip_reason_labels():
    test_cases = [
        ("paywall", "Paywall"),
        ("low_tier", "Niedriger Quellen-Tier"),
        ("irrelevant", "Kein thematischer Bezug (Snippet-Analyse)"),
        ("limit_reached", "Scrape-Limit erreicht"),
    ]
    for reason, expected_label in test_cases:
        ranked = [_make_ranked(
            f"https://example.com/{reason}", SourceTier.MEDIA, False, skip_reason=reason,
        )]
        ctx = _build_enriched_context(ranked, [])
        assert expected_label in ctx, f"Expected '{expected_label}' for skip_reason='{reason}'"


def test_enriched_context_empty():
    ctx = _build_enriched_context([], [])
    assert ctx == "Keine Suchergebnisse gefunden."


def test_enriched_context_mixed_sources():
    """Mix of scraped, failed, and skipped sources."""
    ranked = [
        _make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True),
        _make_ranked("https://tagesschau.de/t", SourceTier.QUALITY_JOURNALISM, True),
        _make_ranked("https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall"),
    ]
    scraped = [
        ScrapedSource(
            url="https://correctiv.org/c", tier_label="Faktencheck",
            passage="Volltext hier", low_relevance=False,
            fetch_success=True, error=None,
        ),
        ScrapedSource(
            url="https://tagesschau.de/t", tier_label="Qualitätsjournalismus",
            passage="", low_relevance=False,
            fetch_success=False, error="403 Forbidden",
        ),
    ]
    ctx = _build_enriched_context(ranked, scraped)
    blocks = ctx.split("\n---\n")
    assert len(blocks) == 3
    assert "Volltext-Auszug" in blocks[0]
    assert "403 Forbidden" in blocks[1]
    assert "Paywall" in blocks[2]


def test_enriched_context_scrape_first_ordering():
    """Sources with should_scrape=True should appear first."""
    ranked = [
        _make_ranked("https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall"),
        _make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True),
    ]
    scraped = [ScrapedSource(
        url="https://correctiv.org/c", tier_label="Faktencheck",
        passage="Inhalt", low_relevance=False,
        fetch_success=True, error=None,
    )]
    ctx = _build_enriched_context(ranked, scraped)
    blocks = ctx.split("\n---\n")
    assert "Volltext-Auszug" in blocks[0]  # Scraped source first
    assert "Paywall" in blocks[1]


# ── _evaluate_scrape_quality ─────────────────────────────────────


def test_evaluate_no_scrapable_sources():
    ranked = [_make_ranked("https://bild.de/a", SourceTier.MEDIA, False, skip_reason="paywall")]
    needs, reason = _evaluate_scrape_quality(ranked, [])
    assert needs is True
    assert reason == "no_scrapable_sources"


def test_evaluate_all_scrapes_failed():
    ranked = [_make_ranked("https://tagesschau.de/a", SourceTier.QUALITY_JOURNALISM, True)]
    scraped = [ScrapedSource(
        url="https://tagesschau.de/a", tier_label="QJ",
        passage="", low_relevance=False,
        fetch_success=False, error="Timeout",
    )]
    needs, reason = _evaluate_scrape_quality(ranked, scraped)
    assert needs is True
    assert reason == "all_scrapes_failed"


def test_evaluate_all_low_relevance():
    ranked = [_make_ranked("https://tagesschau.de/a", SourceTier.QUALITY_JOURNALISM, True)]
    scraped = [ScrapedSource(
        url="https://tagesschau.de/a", tier_label="QJ",
        passage="irrelevant", low_relevance=True,
        fetch_success=True, error=None,
    )]
    needs, reason = _evaluate_scrape_quality(ranked, scraped)
    assert needs is True
    assert reason == "all_low_relevance"


def test_evaluate_good_quality_no_retry():
    ranked = [_make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True)]
    scraped = [ScrapedSource(
        url="https://correctiv.org/c", tier_label="FC",
        passage="Guter Inhalt", low_relevance=False,
        fetch_success=True, error=None,
    )]
    needs, reason = _evaluate_scrape_quality(ranked, scraped)
    assert needs is False
    assert reason == ""


def test_evaluate_mixed_success_no_retry():
    """One success + one failure → no retry (not ALL failed)."""
    ranked = [
        _make_ranked("https://correctiv.org/c", SourceTier.FACT_CHECKER, True),
        _make_ranked("https://tagesschau.de/a", SourceTier.QUALITY_JOURNALISM, True),
    ]
    scraped = [
        ScrapedSource(
            url="https://correctiv.org/c", tier_label="FC",
            passage="Inhalt", low_relevance=False,
            fetch_success=True, error=None,
        ),
        ScrapedSource(
            url="https://tagesschau.de/a", tier_label="QJ",
            passage="", low_relevance=False,
            fetch_success=False, error="403",
        ),
    ]
    needs, reason = _evaluate_scrape_quality(ranked, scraped)
    assert needs is False


def test_evaluate_empty_ranked_triggers_retry():
    needs, reason = _evaluate_scrape_quality([], [])
    assert needs is True
    assert reason == "no_scrapable_sources"


# ── _build_fallback_queries ──────────────────────────────────────


def test_fallback_queries_basic():
    claim = Claim(
        id="C1",
        text="Die Kriminalität in Deutschland ist um 50% gestiegen seit 2015",
        type=ClaimType.FACTUAL,
    )
    original = ["Die Kriminalität in Deutschland ist um 50% gestiegen seit 2015"]
    fallback = _build_fallback_queries(claim, original)

    assert len(fallback) >= 1
    # Should not duplicate originals
    for q in fallback:
        assert q not in original
    # Should contain keywords
    combined = " ".join(fallback).lower()
    assert "kriminalität" in combined


def test_fallback_queries_includes_numbers():
    claim = Claim(
        id="C1",
        text="42% der Wohnungseinbrüche werden von Ausländern begangen",
        type=ClaimType.STATISTICAL,
    )
    fallback = _build_fallback_queries(claim, [])

    # At least one query should contain "42%"
    combined = " ".join(fallback)
    assert "42%" in combined


def test_fallback_queries_deduplicates():
    claim = Claim(id="C1", text="Berlin ist Hauptstadt", type=ClaimType.FACTUAL)
    original_query = " ".join(sorted({"berlin", "hauptstadt"}))
    fallback = _build_fallback_queries(claim, [original_query])

    # The keyword query is identical to original → should be excluded
    for q in fallback:
        assert q != original_query


def test_fallback_queries_empty_keywords():
    """Claim with only stopwords/short words → no fallback possible."""
    claim = Claim(id="C1", text="Das ist es", type=ClaimType.FACTUAL)
    fallback = _build_fallback_queries(claim, [])
    assert fallback == []


def test_fallback_queries_contains_faktencheck():
    claim = Claim(
        id="C1",
        text="Flüchtlinge bekommen mehr Geld als Rentner",
        type=ClaimType.FACTUAL,
    )
    fallback = _build_fallback_queries(claim, [])

    combined = " ".join(fallback).lower()
    assert "faktencheck" in combined


class TestIsCurrentStateClaim:
    """Tests für _is_current_state_claim() – Erkennung zeitkritischer Amts-Claims."""

    def test_bundeskanzler_ist(self):
        assert _is_current_state_claim("Friedrich Merz ist Bundeskanzler von Deutschland.")

    def test_praesident_ist(self):
        assert _is_current_state_claim("Joe Biden ist Präsident der USA.")

    def test_buergermeister_ist_aktuell(self):
        assert _is_current_state_claim("Peter Tschentscher ist aktuell Bürgermeister von Hamburg.")

    def test_ceo_leitet(self):
        assert _is_current_state_claim("Elon Musk leitet Tesla als CEO.")

    def test_kanzler_wurde_zum(self):
        assert _is_current_state_claim("Olaf Scholz wurde zum Bundeskanzler gewählt.")

    def test_no_match_generic_ist(self):
        """Generisches 'ist' ohne Positionsbegriff → kein Match."""
        assert not _is_current_state_claim("Berlin ist die Hauptstadt Deutschlands.")

    def test_no_match_position_without_verb(self):
        """Positionsbegriff ohne Zustandsverb → kein Match."""
        assert not _is_current_state_claim("Der Bundeskanzler hat das Gesetz unterzeichnet.")

    def test_no_match_historical_description(self):
        """Historische Beschreibung ohne eindeutiges Zustandsverb → kein Match."""
        assert not _is_current_state_claim("Konrad Adenauer gründete die CDU.")

    def test_minister_ist(self):
        assert _is_current_state_claim("Karl Lauterbach ist Gesundheitsminister.")

    def test_vorsitzender_bleibt(self):
        assert _is_current_state_claim("Friedrich Merz bleibt Parteivorsitzender.")


# ── _build_queries_for_underspecified_claim ──────────────────────


class TestBuildQueriesForUnderspecifiedClaim:
    """Tests für _build_queries_for_underspecified_claim()."""

    def _make_claim(self, text: str, type_: "ClaimType" = None) -> "Claim":
        from models.schemas import Claim, ClaimType
        return Claim(
            id="C_test",
            text=text,
            type=type_ or ClaimType.FACTUAL,
        )

    def test_always_includes_direct_family(self):
        """Familie 1 (direct claim) ist immer enthalten."""
        claim = self._make_claim("Geheimes Regierungsdokument belegt Massenüberwachung")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        assert len(queries) >= 1
        # Erste Query enthält Claim-Keywords, kein voller Satz
        combined = queries[0].lower()
        assert "überwachung" in combined or "regierungsdokument" in combined or "geheimes" in combined

    def test_includes_factcheck_family(self):
        """Familie 3 (fact-check) enthält Faktencheck-Suffix."""
        claim = self._make_claim("Eine Behörde hat geheime Überwachung angeordnet")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        combined = " ".join(queries).lower()
        assert "faktencheck" in combined

    def test_includes_official_response_family(self):
        """Familie 4 (official response) enthält Stellungnahme-Suffix."""
        claim = self._make_claim("Eine Behörde hat geheime Überwachung angeordnet")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        combined = " ".join(queries).lower()
        assert "stellungnahme" in combined

    def test_artifact_family_included_when_signal_present(self):
        """Familie 2 (document/artifact) erscheint bei missing_artifact_evidence."""
        claim = self._make_claim(
            "Ein geleaktes Dokument zeigt staatliche Zensur"
        )
        queries = _build_queries_for_underspecified_claim(
            claim, ["missing_artifact_evidence"]
        )
        # Mindestens eine Query sollte Artefakt-Begriff enthalten
        combined = " ".join(queries).lower()
        assert "dokument" in combined or "geleakt" in combined

    def test_artifact_family_absent_without_signal(self):
        """Familie 2 erscheint NICHT wenn missing_artifact_evidence fehlt."""
        claim = self._make_claim("Eine Behörde überwacht alle Bürger heimlich")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        # Kein Artefakt-Query ohne Signal – "Dokument" darf nicht isoliert auftauchen
        # (es sei denn, es steckt im Claim-Text)
        assert "dokument" not in claim.text.lower()
        assert not any(q.lower() == "dokument" for q in queries)

    def test_no_hallucinated_actors_underspec(self):
        """Keine Länder, Behörden oder Akteure die nicht im Claim stehen."""
        # Vage Claim ohne spezifische Institution
        claim = self._make_claim("Regierungen überwachen heimlich alle Bürger")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        combined = " ".join(queries).lower()
        # Erfundene Akteure dürfen nicht auftauchen
        invented = ["bfv", "bka", "bamf", "bmi", "verfassungsschutz", "bundestag"]
        for actor in invented:
            assert actor not in combined, f"Halluzinierter Akteur '{actor}' gefunden"

    def test_no_hallucinated_actors_artifact(self):
        """Kein erfundenes Land/keine Behörde bei Artefakt-Claim."""
        claim = self._make_claim(
            "Ein internes Dokument beweist Korruption in der Verwaltung"
        )
        queries = _build_queries_for_underspecified_claim(
            claim, ["missing_artifact_evidence", "underspecified_actor"]
        )
        combined = " ".join(queries).lower()
        invented = ["usa", "deutschland", "frankreich", "fbi", "cia", "bfv"]
        for actor in invented:
            assert actor not in combined, f"Halluzinierter Akteur '{actor}' gefunden"

    def test_empty_keywords_returns_empty(self):
        """Claim mit nur Stoppwörtern → leere Liste (kein Crash)."""
        claim = self._make_claim("Das ist es")
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        assert queries == []

    def test_both_signals_produces_four_families(self):
        """Beide Signale → alle vier Familien vorhanden."""
        claim = self._make_claim(
            "Ein geleaktes Geheimprotokoll belegt staatliche Überwachung von Bürgern"
        )
        queries = _build_queries_for_underspecified_claim(
            claim, ["missing_artifact_evidence", "underspecified_actor"]
        )
        combined = " ".join(queries).lower()
        assert "faktencheck" in combined
        assert "stellungnahme" in combined
        # Artifact-family ebenfalls vorhanden
        assert "protokoll" in combined or "geleakt" in combined or "geheimprotokoll" in combined
        assert len(queries) == 4

    def test_queries_are_compact_not_full_sentences(self):
        """Queries sollen Keyword-Kompakt-Formen sein, kein unveränderter Claim-Satz."""
        claim = self._make_claim(
            "Ein anonymer Insider hat enthüllt dass Behörden systematisch lügen"
        )
        queries = _build_queries_for_underspecified_claim(claim, ["underspecified_actor"])
        # Keine Query darf identisch mit dem rohen Claim-Text sein
        for q in queries:
            assert q != claim.text, f"Query ist identischer Claim-Satz: {q!r}"
        # Stoppwörter (die, das, ist, hat, …) sollen herausgefiltert sein
        stop_markers = {"ein ", "hat ", "dass "}
        for q in queries:
            q_lower = q.lower()
            for stop in stop_markers:
                assert stop not in q_lower, (
                    f"Stoppwort {stop!r} in kompakter Query: {q!r}"
                )

    def test_build_search_queries_uses_families_for_underspecified(self):
        """_build_search_queries() delegiert für underspecified ProcessedClaims."""
        from models.schemas import ClaimType, ProcessedClaim

        claim = ProcessedClaim(
            id="C_us",
            text="Eine Behörde überwacht heimlich alle Mobiltelefone",
            type=ClaimType.FACTUAL,
            quality_signals=["underspecified_actor"],
        )
        queries = _build_search_queries(claim)
        combined = " ".join(queries).lower()
        # Soll Faktencheck-Familie enthalten
        assert "faktencheck" in combined
        # Kein voller Claim-Satz als Query (Direktsuche entfällt bei underspecified)
        assert "eine behörde überwacht heimlich alle mobiltelefone" not in combined

    def test_build_search_queries_unaffected_for_normal_claim(self):
        """Normale Claims (ohne quality_signals) behalten Direktsuche."""
        from models.schemas import Claim, ClaimType

        claim = Claim(
            id="C_norm",
            text="Die Bundesregierung hat 2023 das Gebäudeenergiegesetz verabschiedet",
            type=ClaimType.FACTUAL,
        )
        queries = _build_search_queries(claim)
        # Volltextsuche soll erhalten bleiben für klar spezifizierte Claims
        assert any(
            "gebäudeenergiegesetz" in q.lower() or claim.text in q
            for q in queries
        )


# ── TestFactCheckerPathSeparation ────────────────────────────────────────────


class TestFactCheckerPathSeparation:
    """Stellt sicher, dass v2-Hauptpfad und Legacy-Fallback sauber getrennt sind."""

    def test_standard_path_bypasses_legacy(
        self, minimal_config, mocker, mock_search_client, sample_factual_claim, sample_evidence_pack
    ):
        """Wenn EvidenceBuilder + VerdictAgent erfolgreich sind, wird Legacy NICHT aufgerufen."""
        from models.schemas import FactCheckResult, FactRating

        expected = FactCheckResult(
            claim_id=sample_factual_claim.id,
            rating=FactRating.TRUE,
            evidence="v2-Evidenz",
            sources=["https://destatis.de"],
        )

        agent = FactCheckerAgent(minimal_config, mocker.MagicMock(), mock_search_client)
        agent._evidence_builder.run_safe = mocker.MagicMock(return_value=(sample_evidence_pack, None))
        agent._verdict_agent.run_safe = mocker.MagicMock(return_value=(expected, None))

        result = agent.execute(sample_factual_claim)

        assert result.rating == FactRating.TRUE
        assert result.evidence == "v2-Evidenz"

    def test_fallback_on_evidence_builder_failure(
        self, minimal_config, mocker, mock_llm_client, mock_search_client, sample_factual_claim
    ):
        """Wenn EvidenceBuilder fehlschlägt, wird UNVERIFIABLE zurückgegeben."""
        agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)
        agent._evidence_builder.run_safe = mocker.MagicMock(return_value=(None, "Simulated EvidenceBuilder error"))

        result = agent.execute(sample_factual_claim)

        assert result.rating == FactRating.UNVERIFIABLE
        assert "Simulated EvidenceBuilder error" in result.evidence

    def test_fallback_on_verdict_agent_failure(
        self, minimal_config, mocker, mock_llm_client, mock_search_client,
        sample_factual_claim, sample_evidence_pack
    ):
        """Wenn VerdictAgent fehlschlägt (aber EvidenceBuilder OK), wird UNVERIFIABLE zurückgegeben."""
        agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)
        agent._evidence_builder.run_safe = mocker.MagicMock(return_value=(sample_evidence_pack, None))
        agent._verdict_agent.run_safe = mocker.MagicMock(return_value=(None, "Simulated VerdictAgent error"))

        result = agent.execute(sample_factual_claim)

        assert result.rating == FactRating.UNVERIFIABLE
        assert "Simulated VerdictAgent error" in result.evidence

    async def test_async_fallback_on_evidence_builder_failure(
        self, minimal_config, mocker, mock_llm_client, mock_search_client, sample_factual_claim
    ):
        """Async: Wenn EvidenceBuilder fehlschlägt, wird UNVERIFIABLE zurückgegeben."""
        agent = FactCheckerAgent(minimal_config, mock_llm_client, mock_search_client)

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("Simulated async EvidenceBuilder error")

        agent._evidence_builder.execute_async = _raise

        result = await agent.execute_async(sample_factual_claim)

        assert result.rating == FactRating.UNVERIFIABLE
        assert "EvidenceBuilder" in result.evidence
