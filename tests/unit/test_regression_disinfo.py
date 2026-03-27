"""Regression-Tests für reale Desinformationsfälle.

Testet das Zusammenspiel von:
    - Claim Processing (inkl. Validator)
    - Evidence-Ranking (Off-topic Filterung)
    - Confidence-Kalibrierung

Nutzt Mocks statt echter API-Calls.
"""

from __future__ import annotations

import pytest

from agents.claim_processor import ClaimValidator, ClaimDecomposer, ClaimSelector
from agents.evidence_builder import (
    _is_offtopic_url,
    _rank_evidence_items,
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
from models.schemas import ClaimType, ProcessedClaim
from tools.web_search import SearchResult


# ── Fixture: Reale Desinfo-Fälle ─────────────────────────────────────────────


class TestRegressionMetaClaims:
    """Regression: Meta-Claims dürfen nicht weiterverarbeitet werden."""

    def setup_method(self):
        self.validator = ClaimValidator()

    def test_case_migration_meta_claim(self):
        """Fall: LLM erzeugt Meta-Claim statt echtem Claim bei Migration."""
        meta_claims = [
            ProcessedClaim(
                id="C1",
                text="Es gibt Informationen darüber, wann das Bürgergeld eingeführt wurde.",
                type=ClaimType.FACTUAL,
            ),
            ProcessedClaim(
                id="C2",
                text="Es gibt Informationen darüber, wie viele Ukrainer Bürgergeld beziehen.",
                type=ClaimType.STATISTICAL,
            ),
            ProcessedClaim(
                id="C3",
                text="Die Zahl der Bürgergeld-Empfänger unter Ukrainern lag im März 2024 bei rund 700.000.",
                type=ClaimType.STATISTICAL,
            ),
        ]
        results = self.validator.validate(meta_claims)

        # C1 und C2 sind Meta-Claims → ungültig
        assert results[0].is_valid_claim is False
        assert results[1].is_valid_claim is False
        # C3 ist ein echter Claim → gültig
        assert results[2].is_valid_claim is True

    def test_case_search_dimensions_not_claims(self):
        """Fall: Decomposer zerlegt in Suchdimensionen statt echte Behauptungen."""
        search_dimensions = [
            ProcessedClaim(
                id="C1a",
                text="Wie hoch ist die Kriminalitätsrate unter Ausländern?",
                type=ClaimType.FACTUAL,
            ),
            ProcessedClaim(
                id="C1b",
                text="Wann wurde die PKS 2023 veröffentlicht?",
                type=ClaimType.FACTUAL,
            ),
            ProcessedClaim(
                id="C1c",
                text="Ob der Anteil nichtdeutscher Tatverdächtiger gestiegen ist, lässt sich prüfen.",
                type=ClaimType.FACTUAL,
            ),
        ]
        results = self.validator.validate(search_dimensions)

        for r in results:
            assert r.is_valid_claim is False, f"{r.id}: '{r.text}' sollte ungültig sein"

    def test_case_valid_claims_not_filtered(self):
        """Echte Claims aus dem selben Kontext dürfen nicht fälschlich gefiltert werden."""
        valid_claims = [
            ProcessedClaim(
                id="C1",
                text="Der Anteil nichtdeutscher Tatverdächtiger in der PKS 2023 beträgt 41,1%.",
                type=ClaimType.STATISTICAL,
            ),
            ProcessedClaim(
                id="C2",
                text="Die Zahl der Asylbewerber in Deutschland hat 2023 um 50% zugenommen.",
                type=ClaimType.STATISTICAL,
            ),
        ]
        results = self.validator.validate(valid_claims)

        for r in results:
            assert r.is_valid_claim is True, f"{r.id}: '{r.text}' sollte gültig sein"
            assert r.claim_quality_score > 0.5


class TestRegressionOffTopicEvidence:
    """Regression: Off-topic Treffer dürfen nicht in Top-Evidenz landen."""

    def test_case_grammar_pages_filtered(self):
        """Fall: Grammatikseiten in Suchtreffer bei Kriminalitäts-Claim."""
        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://bka.de/pks-2023", title="PKS 2023: Polizeiliche Kriminalstatistik"),
                excerpt="Die Kriminalität in Deutschland stieg 2023 um 5,5%.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://duden.de/konjugation/steigen", title="Grammatik: Konjugation 'steigen'"),
                excerpt="Das Verb steigen wird wie folgt konjugiert...",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://restaurant-berlin.de/zum-kriminellen", title="Restaurant 'Zum Kriminellen' in Berlin"),
                excerpt="Genießen Sie unsere Spezialitäten in gemütlicher Atmosphäre.",
            ),
        ]
        claim = "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen."
        ranked = _rank_evidence_items(results, claim, [])

        urls = [item.source.url for item in ranked]
        assert "https://bka.de/pks-2023" in urls
        assert "https://duden.de/konjugation/steigen" not in urls
        assert "https://restaurant-berlin.de/zum-kriminellen" not in urls

    def test_case_bussgeld_pages_deprioritized(self):
        """Fall: Bußgeld-Seiten bei Geschwindigkeits-Claim."""
        results = [
            EvidenceItem(
                source=EvidenceSource(url="https://correctiv.org/tempolimit-faktencheck", title="Tempolimit-Debatte: Faktencheck"),
                excerpt="Correctiv prüft die Behauptung zum Tempolimit auf deutschen Autobahnen.",
            ),
            EvidenceItem(
                source=EvidenceSource(url="https://bussgeldkatalog.de/geschwindigkeit", title="Bußgeldkatalog 2024: Alle Strafen"),
                excerpt="Hier finden Sie alle Bußgelder für Geschwindigkeitsüberschreitungen.",
            ),
        ]
        claim = "Ein Tempolimit von 130 km/h würde die CO2-Emissionen um 5% senken."
        ranked = _rank_evidence_items(results, claim, [])

        # Correctiv-Treffer muss höher ranken als Bußgeldkatalog
        if len(ranked) >= 2:
            correctiv_idx = next(
                (i for i, item in enumerate(ranked) if "correctiv" in item.source.url),
                None,
            )
            bussgeld_idx = next(
                (i for i, item in enumerate(ranked) if "bussgeld" in item.source.url),
                None,
            )
            if correctiv_idx is not None and bussgeld_idx is not None:
                assert correctiv_idx < bussgeld_idx


class TestRegressionConfidenceCalibration:
    """Regression: Confidence darf bei schwacher Evidenz nicht zu hoch sein."""

    def test_case_no_primary_source_caps_confidence(self):
        """Fall: Hohe LLM-Confidence ohne jede Primärquelle."""
        weak_pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=[
                EvidenceItem(
                    source=EvidenceSource(
                        url="https://blog.example.com/post",
                        title="Blog-Post",
                        domain="example.com",
                        domain_tier=5,
                    ),
                    excerpt="Irgendein Meinungsblog",
                    relevance_score=0.3,
                ),
            ],
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=False,
                has_fact_check_org_result=False,
                source_consensus=SourceConsensus.INSUFFICIENT,
                overall_quality=0.15,
                top_tier_count=0,
            ),
        )
        # LLM gibt 0.92 Confidence → muss gesenkt werden
        confidence, reasons = _calibrate_confidence(0.92, weak_pack, None)
        assert confidence <= 0.70
        assert len(reasons) >= 2  # Mehrere Gründe

    def test_case_mixed_evidence_moderate_confidence(self):
        """Fall: Gemischte Evidenz → moderate Confidence, nicht hoch."""
        mixed_pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            web_results=[
                EvidenceItem(
                    source=EvidenceSource(
                        url="https://tagesschau.de/test",
                        title="Tagesschau",
                        domain="tagesschau.de",
                        domain_tier=3,
                    ),
                    excerpt="Relevanter Inhalt",
                    relevance_score=0.7,
                ),
                EvidenceItem(
                    source=EvidenceSource(
                        url="https://random.de/test",
                        title="Random",
                        domain="random.de",
                        domain_tier=5,
                    ),
                    excerpt="Weniger relevant",
                    relevance_score=0.1,
                ),
            ],
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=False,
                has_fact_check_org_result=False,
                source_consensus=SourceConsensus.MIXED,
                overall_quality=0.4,
                top_tier_count=0,
            ),
        )
        confidence, _ = _calibrate_confidence(0.85, mixed_pack, None)
        assert confidence <= 0.82  # Ohne Primärquelle

    def test_case_strong_factcheck_allows_high_confidence(self):
        """Fall: Starker Fact-Check vorhanden → höhere Confidence erlaubt."""
        strong_pack = EvidencePack(
            claim_id="C1",
            claim_text="Test",
            google_fact_check_matches=[
                GoogleFactCheckMatch(
                    claim_reviewed="Test-Claim",
                    rating="Falsch",
                    publisher="Correctiv",
                    url="https://correctiv.org/test",
                ),
            ],
            web_results=[
                EvidenceItem(
                    source=EvidenceSource(
                        url="https://destatis.de/test",
                        title="Statistik",
                        domain="destatis.de",
                        domain_tier=1,
                        is_primary_source=True,
                    ),
                    excerpt="Offizielle Daten",
                    relevance_score=0.9,
                    extraction_confidence=0.9,
                ),
                EvidenceItem(
                    source=EvidenceSource(
                        url="https://correctiv.org/test",
                        title="Faktencheck",
                        domain="correctiv.org",
                        domain_tier=4,
                        is_fact_check_org=True,
                    ),
                    excerpt="Faktencheck-Ergebnis",
                    relevance_score=0.85,
                    extraction_confidence=0.9,
                ),
            ],
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=True,
                has_fact_check_org_result=True,
                source_consensus=SourceConsensus.AGREEING,
                freshness_score=0.9,
                overall_quality=0.9,
                top_tier_count=1,
            ),
        )
        confidence, reasons = _calibrate_confidence(0.90, strong_pack, None)
        assert confidence >= 0.75  # Starke Evidenz = Confidence bleibt hoch


class TestRegressionEndToEnd:
    """End-to-End Regression: vollständige Szenarien."""

    def test_no_meta_claims_in_valid_output(self):
        """Nach Validierung dürfen keine Meta-Claims als valid markiert sein."""
        validator = ClaimValidator()

        # Simuliere typischen LLM-Output mit Meta-Claims
        claims = [
            ProcessedClaim(id="C1", text="Es gibt Informationen darüber, wann die Reform beschlossen wurde.", type=ClaimType.FACTUAL),
            ProcessedClaim(id="C2", text="Die Reform wurde im Juni 2023 beschlossen.", type=ClaimType.FACTUAL),
            ProcessedClaim(id="C3", text="Es gibt Hinweise, wie sich die Zahlen entwickelt haben.", type=ClaimType.FACTUAL),
            ProcessedClaim(id="C4", text="Die Arbeitslosenquote sank 2023 auf 5,7%.", type=ClaimType.STATISTICAL),
            ProcessedClaim(id="C5", text="Es wird behauptet, dass die Regierung versagt hat.", type=ClaimType.FACTUAL),
        ]

        results = validator.validate(claims)
        valid = [r for r in results if r.is_valid_claim]
        invalid = [r for r in results if not r.is_valid_claim]

        # Nur C2 und C4 sind echte Claims
        assert len(valid) == 2
        valid_ids = {r.id for r in valid}
        assert "C2" in valid_ids
        assert "C4" in valid_ids

        # C1, C3, C5 sind Meta-Claims
        assert len(invalid) == 3
        invalid_ids = {r.id for r in invalid}
        assert "C1" in invalid_ids
        assert "C3" in invalid_ids
        assert "C5" in invalid_ids

    def test_no_offtopic_in_top_evidence(self):
        """Off-topic Treffer dürfen nicht in den Top-5 Evidence Items sein."""
        results = [
            EvidenceItem(source=EvidenceSource(url="https://bka.de/pks", title="BKA PKS 2023"), excerpt="Kriminalstatistik 2023 Deutschland Straftaten gestiegen 5,5%"),
            EvidenceItem(source=EvidenceSource(url="https://correctiv.org/faktencheck/kriminalitaet", title="Correctiv Faktencheck"), excerpt="Faktencheck: Kriminalität Deutschland 2023"),
            EvidenceItem(source=EvidenceSource(url="https://chefkoch.de/pasta-rezepte", title="Pasta-Rezepte"), excerpt="Die besten Nudel-Rezepte für jeden Tag"),
            EvidenceItem(source=EvidenceSource(url="https://duden.de/grammatik/dativ", title="Duden Grammatik"), excerpt="Der Dativ ist dem Genitiv sein Tod"),
            EvidenceItem(source=EvidenceSource(url="https://amazon.de/shop/buch-kriminalitaet", title="Amazon Buch"), excerpt="Kaufen Sie das Buch über Kriminalität"),
        ]
        claim = "Die Kriminalität in Deutschland ist 2023 um 50% gestiegen."
        ranked = _rank_evidence_items(results, claim, [])

        top5_domains = [item.source.domain for item in ranked[:5]]
        assert "chefkoch.de" not in top5_domains
        assert "duden.de" not in top5_domains
        assert "amazon.de" not in top5_domains

    def test_confidence_ceiling_no_false_high(self):
        """Confidence darf nie > 0.82 sein wenn keine Primärquelle vorhanden."""
        weak_pack = EvidencePack(
            claim_id="C1",
            claim_text="Test-Behauptung",
            web_results=[
                EvidenceItem(
                    source=EvidenceSource(url=f"https://blog{i}.de", domain=f"blog{i}.de", domain_tier=5),
                    excerpt="Blog-Inhalt",
                    relevance_score=0.4,
                )
                for i in range(5)
            ],
            evidence_quality=EvidenceQualitySignals(
                has_primary_sources=False,
                has_fact_check_org_result=False,
                source_consensus=SourceConsensus.AGREEING,
                overall_quality=0.3,
                top_tier_count=0,
            ),
        )

        # Selbst bei 0.99 LLM-Confidence → wird gedeckelt
        confidence, _ = _calibrate_confidence(0.99, weak_pack, None)
        assert confidence <= 0.82
