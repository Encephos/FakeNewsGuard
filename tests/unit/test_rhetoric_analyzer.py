"""Unit-Tests für agents/rhetoric_analyzer.py – RhetoricAnalyzerAgent.

Strategie: RhetoricAnalyzerAgent(minimal_config, mock_llm_client, mock_search_client).
mock_llm_client.complete_json.return_value steuert die LLM-Antwort.
Die autouse-Fixture mock_network_calls (tests/unit/conftest.py) blockiert alle HTTP-Calls.
"""

from __future__ import annotations


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────


def _make_agent(minimal_config, mock_llm_client, mock_search_client):
    from agents.rhetoric_analyzer import RhetoricAnalyzerAgent
    return RhetoricAnalyzerAgent(minimal_config, mock_llm_client, mock_search_client)


def _valid_technique(technique: str = "Loaded Language", severity: str = "MEDIUM") -> dict:
    return {
        "technique": technique,
        "example": "Beispiel aus dem Text",
        "explanation": "Erklaerung der Technik",
        "severity": severity,
    }


def _llm_response(
    techniques: list[dict],
    overall_framing: str = "Neutrale Einschaetzung",
    narrative_patterns: list[dict] | None = None,
    audience_manipulation: dict | None = None,
) -> dict:
    result = {"techniques": techniques, "overall_framing": overall_framing}
    if narrative_patterns is not None:
        result["narrative_patterns"] = narrative_patterns
    if audience_manipulation is not None:
        result["audience_manipulation"] = audience_manipulation
    return result


# ── Basis-Ausführung ──────────────────────────────────────────────────────────


class TestRhetoricAnalyzerExecution:
    def test_returns_rhetoric_analysis_result_type(self, minimal_config, mock_llm_client, mock_search_client):
        from models.schemas import RhetoricAnalysisResult
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Test text")
        assert isinstance(result, RhetoricAnalysisResult)

    def test_returns_techniques_from_llm_output(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response(
            [_valid_technique("Loaded Language"), _valid_technique("Appeal to Fear", "HIGH")]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Test text")
        assert len(result.techniques) == 2
        assert result.techniques[0].technique == "Loaded Language"
        assert result.techniques[1].technique == "Appeal to Fear"

    def test_returns_overall_framing(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response(
            [], overall_framing="Stark nationalistisches Framing"
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.overall_framing == "Stark nationalistisches Framing"

    def test_techniques_list_empty_when_llm_returns_empty(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Kein manipulativer Text")
        assert result.techniques == []

    def test_missing_overall_framing_defaults_to_empty_string(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = {"techniques": []}
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.overall_framing == ""

    def test_uses_claim_text_when_input_has_text_attr(self, minimal_config, mock_llm_client, mock_search_client):
        from models.schemas import Claim, ClaimType
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        claim = Claim(id="C1", text="Claim-Text fuer Test", type=ClaimType.FACTUAL)
        agent.execute(claim)
        # Der user_message-Parameter muss den Claim-Text enthalten
        call_args = mock_llm_client.complete_json.call_args
        user_msg = call_args[0][1]
        assert "Claim-Text fuer Test" in user_msg

    def test_uses_str_input_when_no_text_attr(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        agent.execute("direkter String als Input")
        call_args = mock_llm_client.complete_json.call_args
        user_msg = call_args[0][1]
        assert "direkter String als Input" in user_msg


# ── Technique-Filtering ───────────────────────────────────────────────────────


class TestRhetoricAnalyzerTechniqueFiltering:
    def test_valid_severity_low_is_accepted(self, minimal_config, mock_llm_client, mock_search_client):
        from models.schemas import Severity
        mock_llm_client.complete_json.return_value = _llm_response(
            [_valid_technique("Whataboutism", "LOW")]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 1
        assert result.techniques[0].severity == Severity.LOW

    def test_valid_severity_medium_is_accepted(self, minimal_config, mock_llm_client, mock_search_client):
        from models.schemas import Severity
        mock_llm_client.complete_json.return_value = _llm_response(
            [_valid_technique("Cherry-Picking", "MEDIUM")]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.techniques[0].severity == Severity.MEDIUM

    def test_valid_severity_high_is_accepted(self, minimal_config, mock_llm_client, mock_search_client):
        from models.schemas import Severity
        mock_llm_client.complete_json.return_value = _llm_response(
            [_valid_technique("Appeal to Fear", "HIGH")]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.techniques[0].severity == Severity.HIGH

    def test_invalid_severity_string_is_skipped(self, minimal_config, mock_llm_client, mock_search_client):
        tech = _valid_technique("Dog Whistles")
        tech["severity"] = "EXTREME"  # ungültig → ValueError → überspringen
        mock_llm_client.complete_json.return_value = _llm_response([tech])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 0

    def test_missing_technique_key_is_skipped(self, minimal_config, mock_llm_client, mock_search_client):
        tech = {
            "example": "Beispiel",
            "explanation": "Erklaerung",
            "severity": "MEDIUM",
            # "technique" Key fehlt → KeyError → überspringen
        }
        mock_llm_client.complete_json.return_value = _llm_response([tech])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 0

    def test_technique_with_optional_fields_absent_still_accepted(self, minimal_config, mock_llm_client, mock_search_client):
        tech = {"technique": "Strohmann", "severity": "LOW"}
        # Kein "example", kein "explanation" → sollte trotzdem akzeptiert werden
        mock_llm_client.complete_json.return_value = _llm_response([tech])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 1
        assert result.techniques[0].example == ""
        assert result.techniques[0].explanation == ""

    def test_multiple_valid_techniques_all_included(self, minimal_config, mock_llm_client, mock_search_client):
        techs = [
            _valid_technique("Loaded Language", "HIGH"),
            _valid_technique("Cherry-Picking", "MEDIUM"),
            _valid_technique("Whataboutism", "LOW"),
        ]
        mock_llm_client.complete_json.return_value = _llm_response(techs)
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 3

    def test_mix_of_valid_and_invalid_techniques_filters_invalid(self, minimal_config, mock_llm_client, mock_search_client):
        techs = [
            _valid_technique("Loaded Language", "HIGH"),        # valid
            {"technique": "X", "severity": "INVALID"},         # invalid severity
            {"example": "no key", "severity": "MEDIUM"},       # missing technique key
            _valid_technique("Appeal to Fear", "LOW"),         # valid
        ]
        mock_llm_client.complete_json.return_value = _llm_response(techs)
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 2
        names = [t.technique for t in result.techniques]
        assert "Loaded Language" in names
        assert "Appeal to Fear" in names


# ── context-Parameter ─────────────────────────────────────────────────────────


class TestRhetoricAnalyzerContext:
    def test_context_appended_to_user_message(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        agent.execute("Text", context="Wichtiger Kontext fuer den Test")
        call_args = mock_llm_client.complete_json.call_args
        user_msg = call_args[0][1]
        assert "Wichtiger Kontext fuer den Test" in user_msg

    def test_empty_context_not_appended(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        agent.execute("Text", context="")
        call_args = mock_llm_client.complete_json.call_args
        user_msg = call_args[0][1]
        assert "Zusätzlicher Kontext" not in user_msg


# ── LLM Retry-Verhalten ───────────────────────────────────────────────────────


class TestRhetoricAnalyzerLLMRetry:
    def test_retry_on_first_value_error_then_success(self, minimal_config, mock_llm_client, mock_search_client):
        # _llm_json in BaseAgent: bei erstem ValueError wird retry gemacht
        mock_llm_client.complete_json.side_effect = [
            ValueError("Parse-Fehler"),
            _llm_response([_valid_technique("Loaded Language")]),
        ]
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 1
        assert mock_llm_client.complete_json.call_count == 2

    def test_raises_after_two_consecutive_value_errors(self, minimal_config, mock_llm_client, mock_search_client):
        import pytest
        mock_llm_client.complete_json.side_effect = [
            ValueError("Fehler 1"),
            ValueError("Fehler 2"),
        ]
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        with pytest.raises(ValueError):
            agent.execute("Text")


# ── Narrative-Tracking & Audience-Manipulation ───────────────────────────────


def _valid_narrative(
    narrative_id: str = "great_replacement",
    narrative_label: str = "Great Replacement",
    confidence: float = 0.8,
) -> dict:
    return {
        "narrative_id": narrative_id,
        "narrative_label": narrative_label,
        "confidence": confidence,
        "matching_signals": ["Umvolkung", "Bevoelkerungsaustausch"],
        "explanation": "Text bedient das Narrativ",
    }


def _valid_audience() -> dict:
    return {
        "target_audience_signals": ["informelles Du", "patriotische Marker"],
        "emotional_targeting": ["Angst vor Kontrollverlust"],
        "platform_signals": ["Clickbait-Ueberschrift"],
        "vulnerability_indicators": ["aeltere Zielgruppe"],
        "assessment": "Zielt auf konservatives aelteres Publikum",
    }


class TestRhetoricAnalyzerNarrativeTracking:
    def test_returns_narrative_patterns_from_llm_output(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response(
            [], narrative_patterns=[_valid_narrative()]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.narrative_patterns) == 1
        assert result.narrative_patterns[0].narrative_id == "great_replacement"
        assert result.narrative_patterns[0].confidence == 0.8

    def test_multiple_narratives(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response(
            [],
            narrative_patterns=[
                _valid_narrative("great_replacement"),
                _valid_narrative("election_fraud", "Wahlbetrug", 0.6),
            ],
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.narrative_patterns) == 2

    def test_empty_narrative_patterns_default(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.narrative_patterns == []

    def test_invalid_narrative_pattern_is_skipped(self, minimal_config, mock_llm_client, mock_search_client):
        bad_narrative = {"confidence": 0.5}  # missing narrative_id → KeyError
        mock_llm_client.complete_json.return_value = _llm_response(
            [], narrative_patterns=[bad_narrative, _valid_narrative()]
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.narrative_patterns) == 1

    def test_returns_audience_manipulation_from_llm_output(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response(
            [], audience_manipulation=_valid_audience()
        )
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.audience_manipulation is not None
        assert result.audience_manipulation.assessment == "Zielt auf konservatives aelteres Publikum"
        assert "Angst vor Kontrollverlust" in result.audience_manipulation.emotional_targeting

    def test_missing_audience_manipulation_defaults_to_none(self, minimal_config, mock_llm_client, mock_search_client):
        mock_llm_client.complete_json.return_value = _llm_response([])
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert result.audience_manipulation is None

    def test_backwards_compatible_old_format_response(self, minimal_config, mock_llm_client, mock_search_client):
        """Old-format LLM response (only techniques + overall_framing) still works."""
        mock_llm_client.complete_json.return_value = {
            "techniques": [_valid_technique()],
            "overall_framing": "Neutrales Framing",
        }
        agent = _make_agent(minimal_config, mock_llm_client, mock_search_client)
        result = agent.execute("Text")
        assert len(result.techniques) == 1
        assert result.narrative_patterns == []
        assert result.audience_manipulation is None
