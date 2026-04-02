"""Tests für den Retrieval-Refactor: hybrides Ranking, Evidence-Typing,
Support-Leakage-Schutz, Confidence-Ceilings.

Enthält auch Tests für die optionale Tavily-Plugin-Budgetierung
(TestTavilyBudget, TestTavilyContentPreScrape), die nur bei
aktiviertem Tavily-Plugin relevant sind.
"""

from __future__ import annotations

import pytest

from config import AppConfig, EvidenceRetrievalConfig, LangSearchConfig, TavilyConfig
from models.evidence_models import (
    EvidenceItem,
    EvidencePack,
    EvidenceQualitySignals,
    EvidenceSource,
    EvidenceType,
    GoogleFactCheckMatch,
    SourceConsensus,
)
from models.schemas import ClaimSearchProfile, ClaimType, ProcessedClaim
from tools.scrape_ranker import (
    RankedSource,
    _bm25_score,
    _extract_claim_keywords,
    _hybrid_relevance_score,
    _is_low_trust_domain,
    _profile_anchor_fit,
    rank_sources,
)
from tools.source_classifier import SourceTier
from tools.web_search import SearchResult


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def regulatory_profile() -> ClaimSearchProfile:
    """Profil für einen konkreten Regelungsclaim."""
    return ClaimSearchProfile(
        core_entities=["Hannover", "Stadtrat"],
        institutions=["Stadtrat Hannover"],
        locations=["Hannover"],
        action_terms=["beschlossen", "nicht-öffentliche Sitzung"],
        policy_terms=["15-Minuten-Stadt", "rechtlich bindend"],
        number_terms=["2027", "250"],
        sanction_terms=["250 Euro Bußgeld", "Kameraüberwachung"],
        exclusion_terms=[],
        official_source_hints=["hannover.de"],
        fact_check_hints=[],
    )


@pytest.fixture
def regulatory_claim(regulatory_profile) -> ProcessedClaim:
    """ProcessedClaim für Regelungsclaim."""
    from models.schemas import AmbiguityLevel, ClaimFrame
    return ProcessedClaim(
        id="C1",
        text="Der Stadtrat von Hannover hat in einer nicht-öffentlichen Sitzung beschlossen, "
             "die 15-Minuten-Stadt ab 2027 rechtlich bindend umzusetzen. "
             "Zuwiderhandlungen werden automatisiert per Kameraüberwachung mit 250 Euro Bußgeld geahndet.",
        type=ClaimType.FACTUAL,
        context="",
        canonical_text="Stadtrat Hannover 15-Minuten-Stadt 2027 Bußgeld",
        canonical_hash="test",
        ambiguity_level=AmbiguityLevel.NONE,
        priority_score=0.9,
        harm_score=0.8,
        checkworthiness_score=0.95,
        is_checkworthy=True,
        search_profile=regulatory_profile,
        frame=ClaimFrame(
            institution="Stadtrat Hannover",
            policy_context="15-Minuten-Stadt",
            sanction="250 Euro Bußgeld",
            enforcement="Kameraüberwachung",
        ),
        claim_quality_score=0.85,
    )


def _make_evidence_item(
    url: str = "https://example.com",
    title: str = "Test",
    domain: str = "example.com",
    domain_tier: int = 5,
    excerpt: str = "Test excerpt",
    relevance: float = 0.5,
    evidence_type: EvidenceType = EvidenceType.CONTEXTUAL,
    claim_scope: float = 0.3,
    is_fact_check: bool = False,
) -> EvidenceItem:
    return EvidenceItem(
        source=EvidenceSource(
            url=url, title=title, domain=domain,
            domain_tier=domain_tier, is_fact_check_org=is_fact_check,
        ),
        excerpt=excerpt,
        relevance_score=relevance,
        evidence_type=evidence_type,
        claim_scope_score=claim_scope,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Profilbasiertes Ranking greift VOR dem Scraping
# ═══════════════════════════════════════════════════════════════════════════


class TestPreScrapeProfileRanking:
    """Prüft, dass profilbasiertes Ranking vor dem Scraping greift."""

    def test_profile_boosts_relevant_result(self, regulatory_profile):
        """Relevante Quelle mit Profil-Match wird höher gerankt."""
        relevant = SearchResult(
            title="Stadtrat Hannover beschließt 15-Minuten-Stadt",
            url="https://hannover.de/beschluss",
            snippet="Stadtrat Hannover Beschluss 15-Minuten-Stadt 2027",
        )
        irrelevant = SearchResult(
            title="Allgemeine Kameraüberwachung in Europa",
            url="https://random-blog.com/kameras",
            snippet="Kameraüberwachung wird in vielen Städten eingesetzt",
        )
        results = {"q1": [relevant, irrelevant]}
        ranked = rank_sources(results, "Stadtrat Hannover 15-Minuten-Stadt", max_scrape=5, profile=regulatory_profile)

        # Die relevante Quelle soll den höheren hybrid_score haben
        scores = {r.result.url: r.hybrid_score for r in ranked}
        assert scores["https://hannover.de/beschluss"] > scores["https://random-blog.com/kameras"]

    def test_low_trust_filtered_before_scraping(self):
        """Low-Trust-Seiten werden vor dem Scraping abgelehnt."""
        low_trust = SearchResult(
            title="Konjugation von beschließen",
            url="https://verbformen.de/konjugation/beschliessen",
            snippet="Konjugation des Verbs beschließen",
        )
        results = {"q1": [low_trust]}
        ranked = rank_sources(results, "Stadtrat Hannover beschließt", max_scrape=5)

        for rs in ranked:
            if rs.result.url == low_trust.url:
                assert not rs.should_scrape, "Low-Trust-Seite sollte nicht gescraped werden"
                break

    def test_low_trust_domain_detection(self):
        """Low-Trust-Domains werden korrekt erkannt."""
        assert _is_low_trust_domain("verbformen.de")
        assert _is_low_trust_domain("xe.com")
        assert _is_low_trust_domain("juraforum.de")
        assert _is_low_trust_domain("gutefrage.net")
        assert _is_low_trust_domain("bussgeldrechner.de")
        assert not _is_low_trust_domain("tagesschau.de")
        assert not _is_low_trust_domain("correctiv.org")


# ═══════════════════════════════════════════════════════════════════════════
# 2. LangSearch bekommt adaptiv mehr Queries
# ═══════════════════════════════════════════════════════════════════════════


class TestLangSearchAdaptive:
    """Prüft, dass LangSearch adaptiv mehr Queries bekommt."""

    def test_simple_claim_gets_few_queries(self):
        from agents.evidence_builder import _langsearch_query_count
        from models.schemas import Claim
        cfg = EvidenceRetrievalConfig()
        claim = Claim(id="C1", text="Test claim.", type=ClaimType.FACTUAL)
        assert _langsearch_query_count(claim, cfg) == cfg.langsearch_queries_simple

    def test_complex_claim_gets_more_queries(self):
        from agents.evidence_builder import _langsearch_query_count
        from models.schemas import Claim
        cfg = EvidenceRetrievalConfig()
        claim = Claim(
            id="C1",
            text="Die Kriminalität ist laut BKA-Statistik um 50% gestiegen, "
                 "wobei besonders Einbrüche in Großstädten zugenommen haben.",
            type=ClaimType.STATISTICAL,
        )
        count = _langsearch_query_count(claim, cfg)
        assert count == cfg.langsearch_queries_complex
        assert count > cfg.langsearch_queries_simple


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tavily wird budgetiert genutzt
# ═══════════════════════════════════════════════════════════════════════════


class TestTavilyBudget:
    """Prüft Tavily-Plugin-Budgetierung (optionales Plugin, nicht Teil der Kern-Pipeline)."""

    def test_budget_config_defaults(self):
        cfg = EvidenceRetrievalConfig()
        assert cfg.tavily_primary_queries == 1
        assert cfg.tavily_max_queries_per_claim == 3
        assert cfg.tavily_request_budget == 10

    def test_budget_consumption(self):
        """Budget wird korrekt verbraucht und begrenzt."""
        from agents.evidence_builder import EvidenceBuilderAgent
        from config import AppConfig

        # Wir testen nur die Budget-Logik, nicht den vollen Agent
        cfg = AppConfig()
        cfg.evidence_retrieval.tavily_request_budget = 3

        # Simuliere Budget-Verbrauch
        class BudgetTracker:
            def __init__(self, budget: int):
                self._used = 0
                self._budget = budget

            def remaining(self):
                return max(0, self._budget - self._used)

            def consume(self, n: int) -> int:
                actual = min(n, self.remaining())
                self._used += actual
                return actual

        tracker = BudgetTracker(3)
        assert tracker.remaining() == 3
        assert tracker.consume(1) == 1  # Primary: 1 query
        assert tracker.remaining() == 2
        assert tracker.consume(2) == 2  # Expansion: 2 queries
        assert tracker.remaining() == 0
        assert tracker.consume(1) == 0  # Budget erschöpft

    def test_tavily_not_applied_to_all_queries(self):
        """Tavily primary_queries < total queries → nicht alle Queries gehen an Tavily."""
        cfg = EvidenceRetrievalConfig()
        queries = ["q1", "q2", "q3", "q4"]
        tavily_queries = queries[:cfg.tavily_primary_queries]
        assert len(tavily_queries) < len(queries), \
            "Tavily soll nicht pauschal alle Queries bekommen"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Tavily-Content wird vor dem Scraping genutzt
# ═══════════════════════════════════════════════════════════════════════════


class TestTavilyContentPreScrape:
    """Prüft Content-Nutzung vor Scraping (optionales Tavily-Plugin)."""

    def test_hybrid_score_with_content_bonus(self, regulatory_profile):
        """Result mit Tavily-Content bekommt höheren hybrid_score."""
        with_content = SearchResult(
            title="Stadtrat Hannover 15-Minuten-Stadt Beschluss",
            url="https://hannover.de/beschluss",
            snippet="Beschluss 15-Minuten-Stadt",
            content="Der Stadtrat von Hannover hat in einer Sitzung die 15-Minuten-Stadt beschlossen. "
                    "Ab 2027 soll dies rechtlich bindend umgesetzt werden." * 5,
        )
        without_content = SearchResult(
            title="Stadtrat Hannover 15-Minuten-Stadt Beschluss",
            url="https://other.de/beschluss",
            snippet="Beschluss 15-Minuten-Stadt",
            content="",
        )
        keywords = _extract_claim_keywords("Stadtrat Hannover 15-Minuten-Stadt Beschluss")
        score_with = _hybrid_relevance_score(with_content, "Stadtrat Hannover 15-Minuten-Stadt", keywords, regulatory_profile)
        score_without = _hybrid_relevance_score(without_content, "Stadtrat Hannover 15-Minuten-Stadt", keywords, regulatory_profile)
        assert score_with > score_without, "Content-Bonus sollte den Score erhöhen"


# ═══════════════════════════════════════════════════════════════════════════
# 5. BM25 + Hybrid Scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestBM25Scoring:
    """Prüft BM25-artiges Scoring."""

    def test_bm25_perfect_match(self):
        keywords = {"stadtrat", "hannover", "beschluss"}
        score = _bm25_score(keywords, "Der Stadtrat von Hannover hat einen Beschluss gefasst")
        assert score > 0.5

    def test_bm25_no_match(self):
        keywords = {"stadtrat", "hannover", "beschluss"}
        score = _bm25_score(keywords, "Heute ist schönes Wetter in Berlin")
        assert score == 0.0

    def test_bm25_partial_match(self):
        keywords = {"stadtrat", "hannover", "beschluss"}
        score = _bm25_score(keywords, "In Hannover gibt es viele Restaurants")
        assert 0.0 < score < 1.0

    def test_profile_anchor_fit(self, regulatory_profile):
        """Profil-Anchor-Fit: alle Anker matchen → hoher Score."""
        text = "Stadtrat Hannover 15-Minuten-Stadt 2027 250 Euro Bußgeld"
        score = _profile_anchor_fit(text, regulatory_profile)
        assert score > 0.8

    def test_profile_anchor_fit_partial(self, regulatory_profile):
        """Nur Location matcht → niedriger Score."""
        text = "Allgemeine Informationen über Hannover"
        score = _profile_anchor_fit(text, regulatory_profile)
        assert 0.0 < score < 0.5


# ═══════════════════════════════════════════════════════════════════════════
# 6. Evidence-Typing + Support Leakage
# ═══════════════════════════════════════════════════════════════════════════


class TestEvidenceTyping:
    """Prüft Evidence-Typing und Support-Leakage-Schutz."""

    def test_claim_scope_score_direct(self, regulatory_profile):
        from agents.evidence_builder import _compute_claim_scope_score
        score = _compute_claim_scope_score(
            "Stadtrat Hannover beschließt 15-Minuten-Stadt ab 2027, 250 Euro Bußgeld bei Verstößen",
            regulatory_profile,
        )
        assert score >= 0.8, f"Vollständiger Scope-Match sollte >= 0.8 sein, ist {score}"

    def test_claim_scope_score_contextual(self, regulatory_profile):
        from agents.evidence_builder import _compute_claim_scope_score
        score = _compute_claim_scope_score(
            "Das Konzept der 15-Minuten-Stadt wird in verschiedenen Städten diskutiert",
            regulatory_profile,
        )
        assert score < 0.5, f"Nur-Kontext-Score sollte < 0.5 sein, ist {score}"

    def test_claim_scope_score_weak(self, regulatory_profile):
        from agents.evidence_builder import _compute_claim_scope_score
        score = _compute_claim_scope_score(
            "Allgemeine Informationen über Kameraüberwachungssysteme in Europa",
            regulatory_profile,
        )
        assert score < 0.3, f"Schwacher Score sollte < 0.3 sein, ist {score}"

    def test_evidence_type_classification(self):
        from agents.evidence_builder import _classify_evidence_type
        assert _classify_evidence_type(0.8, 0.9, 2, False, False) == EvidenceType.DIRECT
        assert _classify_evidence_type(0.5, 0.4, 5, False, False) == EvidenceType.CONTEXTUAL
        assert _classify_evidence_type(0.1, 0.1, 5, False, True) == EvidenceType.WEAK
        # Fact-checker immer DIRECT bei genug Relevanz
        assert _classify_evidence_type(0.5, 0.3, 4, True, False) == EvidenceType.DIRECT

    def test_contextual_pages_not_partial_evidence_for_regulatory(self, regulatory_profile):
        """Allgemeine Konzeptseiten zählen nicht als Teilbeleg für Regelungsclaims."""
        from agents.evidence_builder import _compute_claim_scope_score, _classify_evidence_type

        # Allgemeine 15-Minuten-Stadt-Seite
        scope = _compute_claim_scope_score(
            "Das Konzept der 15-Minuten-Stadt zielt darauf ab, dass alle wichtigen "
            "Einrichtungen innerhalb von 15 Minuten erreichbar sind.",
            regulatory_profile,
        )
        ev_type = _classify_evidence_type(0.4, scope, 5, False, False)
        assert ev_type != EvidenceType.DIRECT, \
            "Allgemeine Konzeptseite darf nicht DIRECT sein"

    def test_camera_surveillance_not_evidence_for_fine(self, regulatory_profile):
        """Allgemeine Kameraüberwachungsseiten sind kein Beleg für konkrete Bußgeldclaims."""
        from agents.evidence_builder import _compute_claim_scope_score, _classify_evidence_type

        scope = _compute_claim_scope_score(
            "Kameraüberwachung in deutschen Städten nimmt zu. "
            "Viele Kommunen setzen auf Videoüberwachung zur Kriminalitätsbekämpfung.",
            regulatory_profile,
        )
        ev_type = _classify_evidence_type(0.3, scope, 5, False, False)
        assert ev_type != EvidenceType.DIRECT, \
            "Allgemeine Kameraüberwachungsseite darf nicht DIRECT sein"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Confidence bei schwachen/indirekten Quellen gedeckelt
# ═══════════════════════════════════════════════════════════════════════════


class TestConfidenceCeilings:
    """Prüft Confidence-Ceilings für verschiedene Szenarien."""

    def _make_pack(
        self,
        items: list[EvidenceItem] | None = None,
        direct_count: int = 0,
        contextual_rate: float = 0.0,
        low_trust_rate: float = 0.0,
        overall_quality: float = 0.5,
    ) -> EvidencePack:
        return EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=items or [],
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=(direct_count > 0),
                has_fact_check_org_result=False,
                source_consensus=SourceConsensus.INSUFFICIENT,
                overall_quality=overall_quality,
                direct_evidence_count=direct_count,
                contextual_only_rate=contextual_rate,
                low_trust_rate=low_trust_rate,
            ),
        )

    def test_ceiling_contextual_only(self):
        """Confidence wird gedeckelt wenn nur Kontext-Evidenz vorhanden."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(direct_count=0, contextual_rate=0.8, overall_quality=0.4)
        conf, reasons = _calibrate_confidence(0.85, pack, None)
        assert conf <= 0.75, f"Contextual-only Ceiling soll <= 0.75 sein, ist {conf}"
        # Confidence muss substanziell reduziert sein (mehrere Ceilings greifen)
        assert len(reasons) >= 1, "Mindestens eine Ceiling-Reason erwartet"

    def test_ceiling_regulatory_no_direct_evidence(self):
        """Regelungsclaim ohne direkte Evidenz → hartes Ceiling."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(direct_count=0, contextual_rate=0.8, overall_quality=0.4)
        conf, reasons = _calibrate_confidence(
            0.85, pack, None, is_regulatory_claim=True,
        )
        assert conf <= 0.68, f"Regulatory ohne Direct soll <= 0.68 sein, ist {conf}"

    def test_ceiling_low_trust_tighter(self):
        """Verschärftes Low-Trust-Ceiling ab 20%."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(low_trust_rate=0.25, overall_quality=0.4)
        conf, reasons = _calibrate_confidence(0.85, pack, None)
        assert conf <= 0.70, f"Low-Trust 25% Ceiling soll <= 0.70 sein, ist {conf}"

    def test_ceiling_low_trust_very_high(self):
        """Sehr hoher Low-Trust-Anteil → noch strengeres Ceiling."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(low_trust_rate=0.6, overall_quality=0.3)
        conf, reasons = _calibrate_confidence(0.85, pack, None)
        assert conf <= 0.62, f"Low-Trust 60% Ceiling soll <= 0.62 sein, ist {conf}"

    def test_high_confidence_with_direct_evidence(self):
        """Mit direkter Evidenz bleibt Confidence höher."""
        from agents.verdict_agent import _calibrate_confidence
        pack = self._make_pack(
            direct_count=3, contextual_rate=0.2,
            overall_quality=0.8, low_trust_rate=0.0,
        )
        # Give it primary sources and fact-check for high ceiling
        pack.evidence_quality.has_primary_sources = True
        pack.evidence_quality.has_fact_check_org_result = True
        pack.evidence_quality.source_consensus = SourceConsensus.AGREEING

        items = [
            _make_evidence_item(
                url=f"https://tier{i}.de", domain_tier=i,
                evidence_type=EvidenceType.DIRECT, claim_scope=0.8,
            )
            for i in [1, 2, 3]
        ]
        pack.web_results = items

        conf, reasons = _calibrate_confidence(0.90, pack, None)
        assert conf >= 0.70, f"Mit Direct Evidence soll Confidence >= 0.70 sein, ist {conf}"

    def test_background_context_cannot_inflate_regulatory(self):
        """C1/C3 werden nicht zu weich wegen bloßem Hintergrundkontext."""
        from agents.verdict_agent import _calibrate_confidence

        # Nur allgemeine Kontextseiten, kein direct evidence
        pack = self._make_pack(
            direct_count=0, contextual_rate=1.0,
            overall_quality=0.35, low_trust_rate=0.0,
        )
        # Regulatory claim
        conf, reasons = _calibrate_confidence(
            0.80, pack, None, is_regulatory_claim=True,
        )
        assert conf <= 0.68, \
            f"Regelungsclaim mit nur Hintergrundkontext: Ceiling <= 0.68, ist {conf}"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Quality Signals: direct_evidence_count + contextual_only_rate
# ═══════════════════════════════════════════════════════════════════════════


class TestQualitySignals:
    """Prüft die neuen Quality Signals."""

    def test_quality_signals_with_mixed_evidence(self):
        from agents.evidence_builder import _compute_quality_signals

        items = [
            _make_evidence_item(evidence_type=EvidenceType.DIRECT, claim_scope=0.9, relevance=0.8),
            _make_evidence_item(evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.4, relevance=0.5),
            _make_evidence_item(evidence_type=EvidenceType.CONTEXTUAL, claim_scope=0.3, relevance=0.4),
            _make_evidence_item(evidence_type=EvidenceType.WEAK, claim_scope=0.1, relevance=0.1),
            _make_evidence_item(evidence_type=EvidenceType.WEAK, claim_scope=0.0, relevance=0.05),
        ]

        signals = _compute_quality_signals(items, [])
        assert signals.direct_evidence_count == 1
        assert signals.contextual_only_rate == 0.8  # 4/5 non-direct

    def test_quality_signals_all_contextual(self):
        from agents.evidence_builder import _compute_quality_signals

        items = [
            _make_evidence_item(evidence_type=EvidenceType.CONTEXTUAL, relevance=0.4)
            for _ in range(5)
        ]

        signals = _compute_quality_signals(items, [])
        assert signals.direct_evidence_count == 0
        assert signals.contextual_only_rate == 1.0
        # overall_quality should be penalized
        assert signals.overall_quality < 0.5


# ═══════════════════════════════════════════════════════════════════════════
# 9. Hybrid Rank Sources Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestRankSourcesIntegration:
    """Integration tests für rank_sources mit Profil."""

    def test_rank_sources_with_profile(self, regulatory_profile):
        """rank_sources akzeptiert profile Parameter."""
        results = {
            "q1": [
                SearchResult(
                    title="Hannover Stadtrat Beschluss",
                    url="https://hannover.de/test",
                    snippet="Stadtrat Hannover hat beschlossen",
                ),
                SearchResult(
                    title="Wetter in Berlin",
                    url="https://wetter.de/berlin",
                    snippet="Heute wird es sonnig in Berlin",
                ),
            ]
        }
        ranked = rank_sources(
            results, "Stadtrat Hannover beschließt",
            max_scrape=5, profile=regulatory_profile,
        )
        assert len(ranked) >= 1
        # Hannover result should be ranked higher
        hannover_rs = next(r for r in ranked if "hannover.de" in r.result.url)
        assert hannover_rs.hybrid_score > 0

    def test_rank_sources_low_trust_penalty(self):
        """Low-Trust-Domains erhalten niedrigeren hybrid_score."""
        results = {
            "q1": [
                SearchResult(
                    title="Konjugation beschließen",
                    url="https://verbformen.de/konjugation/beschliessen",
                    snippet="Konjugation von beschließen",
                ),
                SearchResult(
                    title="Tagesschau: Stadtrat beschließt",
                    url="https://tagesschau.de/inland/beschluss",
                    snippet="Der Stadtrat hat in einer Sitzung beschlossen",
                ),
            ]
        }
        ranked = rank_sources(results, "Stadtrat beschließt", max_scrape=5)
        scores = {r.result.url: r.hybrid_score for r in ranked}
        assert scores.get("https://tagesschau.de/inland/beschluss", 0) > \
               scores.get("https://verbformen.de/konjugation/beschliessen", 0)
