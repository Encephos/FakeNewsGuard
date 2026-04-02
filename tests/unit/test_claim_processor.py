"""Tests für die mehrstufige Claim-Processing-Pipeline."""

from __future__ import annotations

import hashlib
import pytest

from models.schemas import AmbiguityLevel, ClaimType, ProcessedClaim


# ── Unit Tests: SentenceSplitter ──────────────────────────────────────────────

class TestSentenceSplitter:
    def test_split_single_sentence(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        result = splitter.split("Die Kriminalität ist gestiegen.")
        assert len(result) == 1
        assert result[0]["text"] == "Die Kriminalität ist gestiegen."

    def test_split_multiple_sentences(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        text = "Satz eins. Satz zwei. Satz drei."
        result = splitter.split(text)
        assert len(result) == 3

    def test_context_window(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        text = "Erster Satz. Zweiter Satz. Dritter Satz."
        result = splitter.split(text)
        # Mittlerer Satz sollte Kontext von beiden Seiten haben
        middle = result[1]
        assert "Erster" in middle["context"] or "Dritter" in middle["context"]

    def test_abbreviation_protection(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        text = "Die Kosten stiegen z.B. um 20%. Das ist viel."
        result = splitter.split(text)
        # Abkürzung "z.B." sollte nicht als Satzende erkannt werden
        assert len(result) <= 2  # Max 2 Sätze, nicht 3

    def test_metadata_passed_through(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        result = splitter.split(
            "Behauptung.", metadata={"url": "https://example.com", "title": "Test"}
        )
        assert result[0]["url"] == "https://example.com"
        assert result[0]["title"] == "Test"

    def test_empty_text(self):
        from agents.claim_processor import SentenceSplitter
        splitter = SentenceSplitter()
        result = splitter.split("")
        assert len(result) >= 1  # Mindestens ein Segment, auch bei leerem Text


# ── Unit Tests: Canonical Hash ────────────────────────────────────────────────

class TestCanonicalHash:
    def test_same_text_same_hash(self):
        from agents.claim_processor import _canonical_hash
        h1 = _canonical_hash("Deutschland hat 84 Millionen Einwohner.")
        h2 = _canonical_hash("Deutschland hat 84 Millionen Einwohner.")
        assert h1 == h2

    def test_different_text_different_hash(self):
        from agents.claim_processor import _canonical_hash
        h1 = _canonical_hash("Text A")
        h2 = _canonical_hash("Text B")
        assert h1 != h2

    def test_case_insensitive(self):
        from agents.claim_processor import _canonical_hash
        h1 = _canonical_hash("Deutschland")
        h2 = _canonical_hash("deutschland")
        assert h1 == h2

    def test_whitespace_normalized(self):
        from agents.claim_processor import _canonical_hash
        h1 = _canonical_hash("  Text mit Leerzeichen  ")
        h2 = _canonical_hash("Text mit Leerzeichen")
        assert h1 == h2

    def test_hash_length(self):
        from agents.claim_processor import _canonical_hash
        h = _canonical_hash("Test")
        assert len(h) == 16  # 16 Hex-Zeichen


# ── Unit Tests: ProcessedClaim Modell ─────────────────────────────────────────

class TestProcessedClaim:
    def test_inherits_claim_fields(self, sample_processed_claim):
        """ProcessedClaim hat alle Claim-Felder."""
        assert sample_processed_claim.id == "C1"
        assert sample_processed_claim.type == ClaimType.STATISTICAL
        assert sample_processed_claim.text

    def test_new_fields_have_defaults(self):
        """Neue Felder haben sinnvolle Defaults."""
        from models.schemas import ProcessedClaim
        claim = ProcessedClaim(
            id="T1",
            text="Test",
            type=ClaimType.FACTUAL,
        )
        assert claim.canonical_text == ""
        assert claim.canonical_hash == ""
        assert claim.ambiguity_level == AmbiguityLevel.NONE
        assert claim.priority_score == 0.5
        assert claim.is_checkworthy is True
        assert claim.requires_more_context is False

    def test_model_copy_update(self, sample_processed_claim):
        """model_copy(update=...) funktioniert korrekt."""
        updated = sample_processed_claim.model_copy(update={
            "priority_score": 0.9,
            "harm_score": 0.8,
        })
        assert updated.priority_score == 0.9
        assert updated.harm_score == 0.8
        assert updated.id == sample_processed_claim.id  # Unverändertes Feld

    def test_usable_as_claim(self, sample_processed_claim):
        """ProcessedClaim ist als Claim verwendbar."""
        from models.schemas import Claim
        # Sollte kein Fehler sein (duck typing)
        assert hasattr(sample_processed_claim, "id")
        assert hasattr(sample_processed_claim, "text")
        assert hasattr(sample_processed_claim, "type")


# ── Unit Tests: ClaimProcessingResult ────────────────────────────────────────

class TestClaimProcessingResult:
    def test_to_extraction_result(self, sample_processed_claim):
        """Konvertierung zu ClaimExtractionResult für Abwärtskompatibilität."""
        from models.schemas import ClaimExtractionResult, ClaimProcessingResult

        result = ClaimProcessingResult(
            claims=[sample_processed_claim],
            implicit_claims=["Implizite Aussage"],
            total_segments=5,
        )
        extraction = result.to_extraction_result()
        assert isinstance(extraction, ClaimExtractionResult)
        assert len(extraction.claims) == 1
        assert extraction.implicit_claims == ["Implizite Aussage"]

    def test_claims_are_processed_claims(self, sample_processed_claim):
        """Claims in ClaimProcessingResult sind ProcessedClaim-Objekte."""
        from models.schemas import ClaimProcessingResult

        result = ClaimProcessingResult(claims=[sample_processed_claim])
        assert isinstance(result.claims[0], ProcessedClaim)


# ── Unit Tests: ClaimDecomposer (Mock-basiert) ───────────────────────────────

class TestClaimDecomposer:
    def test_atomic_claim_not_decomposed(self, mocker):
        """Atomare Claims werden nicht weiter zerlegt."""
        from agents.claim_processor import ClaimDecomposer
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete.return_value = {
            "decomposed": [
                {
                    "original_id": "C1",
                    "atomic_claims": [
                        {"id": "C1", "text": "Original Text", "type": "FACTUAL"}
                    ],
                }
            ]
        }
        decomposer = ClaimDecomposer(mock_llm)
        claim = ProcessedClaim(id="C1", text="Original Text", type=ClaimType.FACTUAL)
        result = decomposer.decompose([claim])
        assert len(result) == 1
        assert result[0].id == "C1"

    def test_compound_claim_decomposed(self, mocker):
        """Zusammengesetzte Claims werden in atomare Claims zerlegt."""
        from agents.claim_processor import ClaimDecomposer
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        # Sub-Claims müssen mind. 40 Zeichen + Entity haben (Integrity-Filter)
        mock_llm.complete.return_value = {
            "decomposed": [
                {
                    "original_id": "C1",
                    "atomic_claims": [
                        {
                            "id": "C1a",
                            "text": "Die Zahl der Beschäftigten in Deutschland stieg um 20 Prozent.",
                            "type": "STATISTICAL",
                        },
                        {
                            "id": "C1b",
                            "text": "Die Ausgaben der Bundesregierung sanken im gleichen Zeitraum um 15 Prozent.",
                            "type": "STATISTICAL",
                        },
                    ],
                }
            ]
        }
        decomposer = ClaimDecomposer(mock_llm)
        claim = ProcessedClaim(
            id="C1",
            text="Zahlen stiegen um 20% und Ausgaben sanken um 15%",
            type=ClaimType.STATISTICAL,
        )
        result = decomposer.decompose([claim])
        assert len(result) == 2
        assert result[0].id == "C1a"
        assert result[1].id == "C1b"

    def test_fallback_on_llm_error(self, mocker):
        """Bei LLM-Fehler werden Original-Claims zurückgegeben."""
        from agents.claim_processor import ClaimDecomposer
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete.side_effect = Exception("LLM unavailable")
        decomposer = ClaimDecomposer(mock_llm)
        claim = ProcessedClaim(id="C1", text="Test", type=ClaimType.FACTUAL)
        result = decomposer.decompose([claim])
        assert len(result) >= 1  # Graceful degradation


# ── Unit Tests: ClaimPrioritizerAgent (Mock-basiert) ─────────────────────────

class TestClaimPrioritizerAgent:
    def test_priority_sorting(self, mocker, minimal_config):
        """Claims werden nach recommended_processing_order sortiert."""
        from agents.claim_processor import ClaimPrioritizerAgent
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete_json.return_value = {
            "prioritized": [
                {"id": "C1", "priority_score": 0.3, "harm_score": 0.2,
                 "checkworthiness_score": 0.4, "priority_reason": "Niedrig",
                 "recommended_processing_order": 2},
                {"id": "C2", "priority_score": 0.9, "harm_score": 0.8,
                 "checkworthiness_score": 0.95, "priority_reason": "Hoch",
                 "recommended_processing_order": 1},
            ]
        }
        mock_search = mocker.MagicMock()
        agent = ClaimPrioritizerAgent(minimal_config, mock_llm, mock_search)
        claims = [
            ProcessedClaim(id="C1", text="Trivial", type=ClaimType.FACTUAL),
            ProcessedClaim(id="C2", text="Gesundheitsgefahr", type=ClaimType.STATISTICAL),
        ]
        result = agent.execute(claims)
        # C2 mit order=1 sollte zuerst kommen
        assert result[0].id == "C2"
        assert result[1].id == "C1"

    def test_harm_score_range(self, mocker, minimal_config):
        """Harm-Score liegt zwischen 0 und 1."""
        from agents.claim_processor import ClaimPrioritizerAgent
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete_json.return_value = {
            "prioritized": [
                {"id": "C1", "priority_score": 0.7, "harm_score": 0.6,
                 "checkworthiness_score": 0.8, "priority_reason": "Test",
                 "recommended_processing_order": 1},
            ]
        }
        mock_search = mocker.MagicMock()
        agent = ClaimPrioritizerAgent(minimal_config, mock_llm, mock_search)
        claims = [ProcessedClaim(id="C1", text="Test", type=ClaimType.FACTUAL)]
        result = agent.execute(claims)
        assert 0.0 <= result[0].harm_score <= 1.0
        assert 0.0 <= result[0].priority_score <= 1.0


# ── Unit Tests: _guard_negation ──────────────────────────────────────────────


class TestGuardNegation:
    """Tests für die Negation-Guard-Funktion, die LLM-Negierungen erkennt."""

    def test_no_negation_passthrough(self):
        """Wenn LLM den Claim nicht negiert, wird er durchgelassen."""
        from agents.claim_processor import _guard_negation
        result = _guard_negation(
            "Friedrich Merz ist der Bundeskanzler von Deutschland.",
            "Friedrich Merz ist der Bundeskanzler von Deutschland.",
            {0: "Friedrich Merz ist der Bundeskanzler von Deutschland."},
        )
        assert result == "Friedrich Merz ist der Bundeskanzler von Deutschland."

    def test_kein_negation_detected(self):
        """LLM fügt 'kein' ein → Guard fällt auf Original zurück."""
        from agents.claim_processor import _guard_negation
        result = _guard_negation(
            "Friedrich Merz ist kein Bundeskanzler von Deutschland.",
            "Friedrich Merz ist der Bundeskanzler von Deutschland.",
            {0: "Friedrich Merz ist der Bundeskanzler von Deutschland."},
        )
        assert "kein" not in result.lower()
        assert "Bundeskanzler" in result

    def test_nicht_negation_detected(self):
        """LLM fügt 'nicht' ein → Guard fällt auf Original zurück."""
        from agents.claim_processor import _guard_negation
        result = _guard_negation(
            "Friedrich Merz ist nicht der Bundeskanzler.",
            "Friedrich Merz ist der Bundeskanzler.",
            {0: "Friedrich Merz ist der Bundeskanzler."},
        )
        assert "nicht" not in result.lower()

    def test_negation_in_original_allowed(self):
        """Wenn Original bereits Negation enthält, wird sie beibehalten."""
        from agents.claim_processor import _guard_negation
        result = _guard_negation(
            "Er ist kein Arzt.",
            "Er ist kein Arzt.",
            {0: "Er ist kein Arzt."},
        )
        assert result == "Er ist kein Arzt."

    def test_best_segment_selection(self):
        """Bei mehreren Segmenten wird das passendste ausgewählt."""
        from agents.claim_processor import _guard_negation
        result = _guard_negation(
            "Friedrich Merz ist kein Bundeskanzler.",
            "Friedrich Merz ist Bundeskanzler. Angela Merkel war Kanzlerin.",
            {
                0: "Friedrich Merz ist Bundeskanzler.",
                1: "Angela Merkel war Kanzlerin.",
            },
        )
        # Sollte auf das Merz-Segment zurückfallen (mehr Wortüberlappung)
        assert "Merz" in result
        assert "kein" not in result.lower()


# ── Integration Tests: Disambiguator Negation Guard ──────────────────────────


class TestDisambiguatorNegationGuard:
    """Stellt sicher, dass der Disambiguator Claims nicht negiert."""

    def test_resolved_text_negation_blocked(self, mocker):
        """Disambiguator mit negiertem resolved_text → Guard greift."""
        from agents.claim_processor import Disambiguator
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete.return_value = '{"results": [{"id": "C1", "ambiguity_level": "LOW", "ambiguity_reason": "Unklar ob aktuell", "requires_more_context": false, "resolved_text": "Friedrich Merz ist kein Bundeskanzler von Deutschland."}]}'

        disamb = Disambiguator(mock_llm)
        claims = [
            ProcessedClaim(
                id="C1",
                text="Friedrich Merz ist der Bundeskanzler von Deutschland.",
                type=ClaimType.FACTUAL,
            )
        ]
        result = disamb.disambiguate(claims)
        assert len(result) == 1
        assert "kein" not in result[0].text.lower()
        assert "Bundeskanzler" in result[0].text

    def test_resolved_text_without_negation_accepted(self, mocker):
        """Disambiguator mit sinnvollem resolved_text → wird übernommen."""
        from agents.claim_processor import Disambiguator
        from models.schemas import ClaimType, ProcessedClaim

        mock_llm = mocker.MagicMock()
        mock_llm.complete.return_value = '{"results": [{"id": "C1", "ambiguity_level": "LOW", "ambiguity_reason": "Zeitbezug unklar", "requires_more_context": false, "resolved_text": "Friedrich Merz ist seit 2025 der Bundeskanzler von Deutschland."}]}'

        disamb = Disambiguator(mock_llm)
        claims = [
            ProcessedClaim(
                id="C1",
                text="Friedrich Merz ist der Bundeskanzler von Deutschland.",
                type=ClaimType.FACTUAL,
            )
        ]
        result = disamb.disambiguate(claims)
        assert len(result) == 1
        assert "seit 2025" in result[0].text


# ── Regression Test: Merz-Kanzler-Claim ──────────────────────────────────────


class TestMerzChancellorRegression:
    """Regression: 'Friedrich Merz ist der Bundeskanzler' darf nicht negiert werden."""

    def test_merz_claim_survives_pipeline_without_negation(self, mocker):
        """Merz-Kanzler-Claim durchläuft Disambiguator ohne Negierung."""
        from agents.claim_processor import Disambiguator
        from models.schemas import ClaimType, ProcessedClaim

        # Simuliere LLM das den Claim negiert (worst case)
        mock_llm = mocker.MagicMock()
        mock_llm.complete.return_value = '{"results": [{"id": "C1", "ambiguity_level": "MEDIUM", "ambiguity_reason": "Merz war nicht immer Kanzler", "requires_more_context": true, "resolved_text": "Friedrich Merz ist kein Bundeskanzler von Deutschland."}]}'

        disamb = Disambiguator(mock_llm)
        original_text = "Friedrich Merz ist der Bundeskanzler von Deutschland."
        claims = [
            ProcessedClaim(
                id="C1",
                text=original_text,
                type=ClaimType.FACTUAL,
            )
        ]
        result = disamb.disambiguate(claims)

        assert len(result) == 1
        # Claim-Text darf NICHT negiert sein
        assert "kein" not in result[0].text.lower()
        assert "nicht" not in result[0].text.lower()
        # Originaltext muss erhalten bleiben
        assert result[0].text == original_text
