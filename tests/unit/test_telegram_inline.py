"""Tests for Telegram bot inline keyboard functionality."""

from __future__ import annotations

import time

import pytest


# ── Formatting Tests ────────────────────────────────────────────

from telegram_formatting import (
    format_claim_detail,
    format_corrections_section,
    format_fairness_section,
    format_result_overview,
    format_rhetoric_section,
    format_sources_section,
    format_tier_selection,
)


@pytest.fixture()
def sample_result():
    return {
        "overall_rating": "Irreführend",
        "confidence": 65,
        "summary": "Der Text enthält mehrere irreführende Behauptungen.",
        "claims": [
            {
                "id": "C1",
                "text": "Deutschland hat die höchste Inflationsrate in Europa",
                "type": "FACTUAL",
                "rating": "FALSE",
                "confidence": 90,
                "evidence": "Laut Eurostat liegt die höchste Inflationsrate in Ungarn.",
                "correction": "Deutschland lag 2024 bei 2.2%, Ungarn bei 5.5%.",
                "missing_context": "Der Zeitraum wurde nicht spezifiziert.",
                "sources": ["https://ec.europa.eu/eurostat"],
            },
            {
                "id": "C2",
                "text": "Die Arbeitslosenquote ist auf 10% gestiegen",
                "type": "STATISTICAL",
                "rating": "MOSTLY_FALSE",
                "confidence": 85,
                "evidence": "Die Quote lag bei 5.7%.",
                "correction": "",
                "missing_context": "",
                "number_audit": {
                    "manipulation": "ABSOLUTE_VS_RELATIVE",
                    "calculation": "Vergleich mit Vorjahr fehlerhaft.",
                    "correct_value": "5.7%",
                },
                "sources": [],
            },
        ],
        "rhetoric": [
            {
                "name": "Emotionales Framing",
                "severity": "HIGH",
                "description": "Angstmachende Sprache.",
                "example": "katastrophale Zahlen",
            }
        ],
        "corrections": ["Inflationsrate Deutschland: 2.2%", "Arbeitslosenquote: 5.7%"],
        "fairness": ["Die Erwähnung steigender Energiepreise ist korrekt."],
        "sources": ["https://ec.europa.eu/eurostat", "https://www.destatis.de"],
    }


class TestFormatResultOverview:
    def test_contains_rating(self, sample_result):
        text = format_result_overview(sample_result)
        assert "IRREFÜHREND" in text

    def test_contains_summary(self, sample_result):
        text = format_result_overview(sample_result)
        assert "Zusammenfassung" in text

    def test_contains_claim_count(self, sample_result):
        text = format_result_overview(sample_result)
        # Should contain "(2)" for 2 claims (escaped for MarkdownV2)
        assert "\\(2\\)" in text

    def test_contains_claim_lines(self, sample_result):
        text = format_result_overview(sample_result)
        assert "#1" in text
        assert "#2" in text

    def test_does_not_contain_full_evidence(self, sample_result):
        text = format_result_overview(sample_result)
        assert "Eurostat liegt" not in text

    def test_truncates_long_claim(self):
        result = {
            "overall_rating": "Wahr",
            "confidence": 95,
            "summary": "OK",
            "claims": [
                {
                    "text": "A" * 200,
                    "rating": "TRUE",
                }
            ],
        }
        text = format_result_overview(result)
        # The claim text should be truncated
        assert "A" * 100 not in text


class TestFormatClaimDetail:
    def test_contains_evidence(self, sample_result):
        text = format_claim_detail(sample_result["claims"][0], 0)
        assert "Eurostat" in text

    def test_contains_correction(self, sample_result):
        text = format_claim_detail(sample_result["claims"][0], 0)
        assert "2\\.2%" in text or "2.2%" in text

    def test_contains_missing_context(self, sample_result):
        text = format_claim_detail(sample_result["claims"][0], 0)
        assert "Kontext" in text

    def test_number_audit(self, sample_result):
        text = format_claim_detail(sample_result["claims"][1], 1)
        assert "Zahlenmanipulation" in text
        assert "5\\.7%" in text or "5.7%" in text

    def test_sources_shown(self, sample_result):
        text = format_claim_detail(sample_result["claims"][0], 0)
        assert "eurostat" in text.lower()

    def test_skips_empty_fields(self):
        claim = {"text": "Test", "rating": "TRUE", "evidence": "", "correction": "", "missing_context": ""}
        text = format_claim_detail(claim, 0)
        assert "Korrektur" not in text
        assert "Kontext" not in text


class TestFormatSections:
    def test_rhetoric_section(self, sample_result):
        text = format_rhetoric_section(sample_result["rhetoric"])
        assert "Emotionales Framing" in text
        assert "katastrophale" in text

    def test_sources_section(self, sample_result):
        text = format_sources_section(sample_result["sources"])
        assert "eurostat" in text.lower()
        assert "destatis" in text.lower()

    def test_corrections_section(self, sample_result):
        text = format_corrections_section(sample_result["corrections"])
        assert "Korrekturen" in text

    def test_fairness_section(self, sample_result):
        text = format_fairness_section(sample_result["fairness"])
        assert "korrekt" in text.lower()

    def test_tier_selection(self):
        text = format_tier_selection()
        assert "Analyse" in text


# ── Keyboard Building Tests ─────────────────────────────────────

from bot.keyboards import (
    TIER_LEVELS,
    _build_result_keyboard,
    _build_tier_keyboard,
)


class TestBuildTierKeyboard:
    def test_lite_user_sees_only_lite(self):
        kb = _build_tier_keyboard("lite", "abc12345")
        buttons = kb["inline_keyboard"]
        all_texts = [b["text"] for row in buttons for b in row]
        assert "LITE" in all_texts
        assert "PRO" not in all_texts
        assert "MAX" not in all_texts

    def test_pro_user_sees_lite_and_pro(self):
        kb = _build_tier_keyboard("pro", "abc12345")
        buttons = kb["inline_keyboard"]
        all_texts = [b["text"] for row in buttons for b in row]
        assert "LITE" in all_texts
        assert "PRO" in all_texts
        assert "MAX" not in all_texts

    def test_max_user_sees_all_tiers(self):
        kb = _build_tier_keyboard("max", "abc12345")
        buttons = kb["inline_keyboard"]
        all_texts = [b["text"] for row in buttons for b in row]
        assert "LITE" in all_texts
        assert "PRO" in all_texts
        assert "MAX" in all_texts

    def test_max_user_sees_commander(self):
        kb = _build_tier_keyboard("max", "abc12345")
        buttons = kb["inline_keyboard"]
        all_texts = [b["text"] for row in buttons for b in row]
        assert any("CMD PRO" in t for t in all_texts)
        assert any("CMD MAX" in t for t in all_texts)

    def test_callback_data_contains_hash(self):
        kb = _build_tier_keyboard("lite", "abc12345")
        data = kb["inline_keyboard"][0][0]["callback_data"]
        assert "abc12345" in data

    def test_callback_data_format(self):
        kb = _build_tier_keyboard("max", "xyz99999")
        data = kb["inline_keyboard"][0][0]["callback_data"]
        assert data.startswith("t:")
        assert data.endswith(":xyz99999")


class TestBuildResultKeyboard:
    def test_claim_buttons(self, sample_result):
        kb = _build_result_keyboard(sample_result, "job12345")
        rows = kb["inline_keyboard"]
        # First two rows should be claim buttons
        assert "c:job12345:0" in rows[0][0]["callback_data"]
        assert "c:job12345:1" in rows[1][0]["callback_data"]

    def test_section_buttons_present(self, sample_result):
        kb = _build_result_keyboard(sample_result, "job12345")
        all_data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert "s:job12345:rhet" in all_data
        assert "s:job12345:src" in all_data
        assert "s:job12345:corr" in all_data
        assert "s:job12345:fair" in all_data

    def test_new_analysis_button(self, sample_result):
        kb = _build_result_keyboard(sample_result, "job12345")
        all_data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert "new" in all_data

    def test_empty_sections_hidden(self):
        result = {
            "claims": [{"text": "Test", "rating": "TRUE"}],
            "rhetoric": [],
            "sources": [],
            "corrections": [],
            "fairness": [],
        }
        kb = _build_result_keyboard(result, "job12345")
        all_data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert not any(d.startswith("s:") for d in all_data)

    def test_no_claims_no_claim_buttons(self):
        result = {"claims": [], "rhetoric": [], "sources": [], "corrections": [], "fairness": []}
        kb = _build_result_keyboard(result, "job12345")
        all_data = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        assert not any(d.startswith("c:") for d in all_data)

    def test_claim_text_truncated(self):
        result = {
            "claims": [{"text": "A" * 100, "rating": "TRUE"}],
            "rhetoric": [],
            "sources": [],
            "corrections": [],
            "fairness": [],
        }
        kb = _build_result_keyboard(result, "job12345")
        btn_text = kb["inline_keyboard"][0][0]["text"]
        assert len(btn_text) < 50  # emoji + #1 + 25 chars + ...


# ── Cache Tests ─────────────────────────────────────────────────

from bot.cache import (
    _cleanup_caches,
    _pending_texts,
    _result_cache,
)


class TestCacheCleanup:
    def setup_method(self):
        _pending_texts.clear()
        _result_cache.clear()

    def teardown_method(self):
        _pending_texts.clear()
        _result_cache.clear()

    def test_expired_entries_removed(self):
        _pending_texts["old"] = {"expires": time.time() - 10}
        _pending_texts["new"] = {"expires": time.time() + 300}
        _cleanup_caches()
        assert "old" not in _pending_texts
        assert "new" in _pending_texts

    def test_hard_cap_enforced(self):
        for i in range(250):
            _pending_texts[f"k{i}"] = {"expires": time.time() + 300 + i}
        _cleanup_caches()
        assert len(_pending_texts) <= 200

    def test_result_cache_cleanup(self):
        _result_cache["expired"] = {"expires": time.time() - 10}
        _result_cache["valid"] = {"expires": time.time() + 1800}
        _cleanup_caches()
        assert "expired" not in _result_cache
        assert "valid" in _result_cache
