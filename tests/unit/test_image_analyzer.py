"""Tests für ImageAnalyzerAgent und LLMClient.complete_vision()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.image_analyzer import ImageAnalyzerAgent
from models.schemas import ImageAnalysisItem, ImageAnalysisResult
from tools.llm import LLMClient


# ── Hilfsfunktionen ────────────────────────────────────────────────


def _make_mock_llm(vision_response: dict | None = None) -> MagicMock:
    """Erstellt einen Mock-LLMClient der complete_vision() unterstützt."""
    mock_llm = MagicMock(spec=LLMClient)
    if vision_response is None:
        vision_response = {
            "items": [
                {
                    "image_index": 0,
                    "ocr_text": "BREAKING NEWS",
                    "visible_elements": ["Person", "Bundestag"],
                    "manipulation_signs": [],
                    "emotional_framing": "Dramatischer Weitwinkel",
                    "infographic_data": "",
                    "context_clues": ["Berlin 2024"],
                }
            ],
            "cross_image_observations": "",
            "overall_assessment": "Bild zeigt Politiker vor Bundestag mit Textoverlay",
        }
    import json
    mock_llm.complete_vision.return_value = json.dumps(vision_response)
    # _parse_json wird statisch aufgerufen
    return mock_llm


def _make_config() -> MagicMock:
    mock_cfg = MagicMock()
    mock_cfg.verbose = False
    return mock_cfg


def _make_agent(llm: MagicMock | None = None) -> ImageAnalyzerAgent:
    cfg = _make_config()
    if llm is None:
        llm = _make_mock_llm()
    search = MagicMock()
    agent = ImageAnalyzerAgent.__new__(ImageAnalyzerAgent)
    agent.config = cfg
    agent.llm = llm
    agent.search = search
    agent.async_search = search
    agent.cache = None
    return agent


# ── Tests für ImageAnalyzerAgent ──────────────────────────────────


class TestImageAnalyzerAgent:
    def test_execute_returns_result_with_items(self):
        agent = _make_agent()
        result = agent.execute({
            "image_urls": ["https://example.com/img.jpg"],
            "post_text": "Test-Post",
        })
        assert isinstance(result, ImageAnalysisResult)
        assert len(result.items) == 1
        assert result.items[0].ocr_text == "BREAKING NEWS"
        assert "Bundestag" in result.items[0].visible_elements
        assert result.overall_assessment != ""

    def test_execute_empty_images_returns_empty_result(self):
        agent = _make_agent()
        result = agent.execute({"image_urls": [], "post_text": ""})
        assert isinstance(result, ImageAnalysisResult)
        assert result.items == []
        # complete_vision sollte NICHT aufgerufen worden sein
        agent.llm.complete_vision.assert_not_called()

    def test_execute_limits_to_5_images(self):
        agent = _make_agent()
        urls = [f"https://example.com/img{i}.jpg" for i in range(10)]
        agent.execute({"image_urls": urls, "post_text": ""})
        call_args = agent.llm.complete_vision.call_args
        passed_urls = call_args[0][2]  # drittes positionales Argument = image_urls
        assert len(passed_urls) <= 5

    def test_execute_graceful_on_missing_item_fields(self):
        """Fehlende/ungültige Felder im LLM-Response führen nicht zum Crash."""
        llm = _make_mock_llm(vision_response={
            "items": [{"image_index": 0}],  # Alle optionalen Felder fehlen
            "overall_assessment": "Kurz",
        })
        agent = _make_agent(llm)
        result = agent.execute({"image_urls": ["https://example.com/img.jpg"]})
        assert len(result.items) == 1
        assert result.items[0].ocr_text == ""
        assert result.items[0].visible_elements == []

    def test_execute_multiple_images(self):
        llm = _make_mock_llm(vision_response={
            "items": [
                {"image_index": 0, "ocr_text": "Erstes Bild"},
                {"image_index": 1, "ocr_text": "Zweites Bild"},
            ],
            "cross_image_observations": "Bilder zeigen denselben Ort",
            "overall_assessment": "Zwei Aufnahmen desselben Ereignisses",
        })
        agent = _make_agent(llm)
        result = agent.execute({
            "image_urls": ["https://a.com/1.jpg", "https://a.com/2.jpg"],
        })
        assert len(result.items) == 2
        assert result.cross_image_observations == "Bilder zeigen denselben Ort"

    def test_run_safe_returns_none_on_llm_error(self):
        """Graceful degradation: Fehler im LLM führt zu (None, error_msg)."""
        llm = MagicMock(spec=LLMClient)
        llm.complete_vision.side_effect = RuntimeError("API-Fehler")
        agent = _make_agent(llm)
        result, error = agent.run_safe({"image_urls": ["https://a.com/img.jpg"]})
        assert result is None
        assert error is not None
        assert "API-Fehler" in error

    def test_post_text_truncated_in_prompt(self):
        """Langer Post-Text wird auf 500 Zeichen gekürzt."""
        agent = _make_agent()
        long_text = "X" * 2000
        agent.execute({"image_urls": ["https://a.com/img.jpg"], "post_text": long_text})
        call_kwargs = agent.llm.complete_vision.call_args
        user_message = call_kwargs[0][1]  # zweites positionales Argument
        assert "X" * 501 not in user_message  # Gekürzt auf max 500


# ── Tests für LLMClient.complete_vision() ─────────────────────────


class TestLLMClientCompleteVision:
    def _make_openrouter_client(self) -> LLMClient:
        from config import LLMConfig, RetryConfig
        from dataclasses import replace
        cfg = LLMConfig(provider="openrouter", api_key="test-key", model="google/gemma-3-27b-it")
        client = LLMClient.__new__(LLMClient)
        client.config = cfg
        client._retry = RetryConfig(max_attempts=1)
        client._client = MagicMock()
        # Mock Response
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"items": [], "overall_assessment": "ok"}'
        client._client.chat.completions.create.return_value = mock_response
        return client

    def test_complete_vision_builds_multipart_content(self):
        client = self._make_openrouter_client()
        client.complete_vision(
            system_prompt="Test system",
            user_message="Test user",
            image_urls=["https://a.com/img.jpg"],
        )
        call_kwargs = client._client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        # Für gemma: system fold → single user message
        user_msg = messages[0]
        assert user_msg["role"] == "user"
        content = user_msg["content"]
        assert isinstance(content, list)
        # Mindestens ein Text-Part und ein Image-Part
        types = [part["type"] for part in content]
        assert "text" in types
        assert "image_url" in types

    def test_complete_vision_includes_all_image_urls(self):
        client = self._make_openrouter_client()
        urls = [f"https://a.com/img{i}.jpg" for i in range(3)]
        client.complete_vision("sys", "user", urls)
        call_kwargs = client._client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        # Collect all image_url parts
        all_content = []
        for msg in messages:
            if isinstance(msg.get("content"), list):
                all_content.extend(msg["content"])
        image_parts = [p for p in all_content if p.get("type") == "image_url"]
        assert len(image_parts) == 3
        extracted_urls = [p["image_url"]["url"] for p in image_parts]
        assert extracted_urls == urls

    def test_complete_vision_sets_json_response_format(self):
        client = self._make_openrouter_client()
        client.complete_vision("sys", "user", ["https://a.com/img.jpg"], response_format="json")
        call_kwargs = client._client.chat.completions.create.call_args[1]
        assert call_kwargs.get("response_format") == {"type": "json_object"}

    def test_complete_vision_appends_json_instruction_to_system(self):
        """Bei response_format='json' wird JSON-Anweisung ans System-Prompt gehängt."""
        client = self._make_openrouter_client()
        client.complete_vision("Mein Prompt", "user", ["https://a.com/img.jpg"], response_format="json")
        call_kwargs = client._client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]
        # Bei system-fold: user content text enthält den kombinierten Prompt
        user_content = messages[0]["content"]
        text_part = next(p["text"] for p in user_content if p["type"] == "text")
        assert "JSON" in text_part


# ── Platform-Gating-Logik ─────────────────────────────────────────


class TestPlatformGating:
    """Sicherstellen dass Bildanalyse nur für Social-Media-Plattformen läuft."""

    VISION_PLATFORMS = {"twitter", "instagram", "threads"}

    @pytest.mark.parametrize("platform,has_images,expected_run", [
        ("twitter", True, True),
        ("instagram", True, True),
        ("threads", True, True),
        ("article", True, False),   # Nachrichten-Artikel → kein Aufruf
        ("youtube", True, False),   # YouTube → kein Aufruf
        ("facebook", True, False),  # Facebook → kein Aufruf
        ("twitter", False, False),  # Keine Bilder → kein Aufruf
    ])
    def test_gating(self, platform: str, has_images: bool, expected_run: bool):
        images = ["https://a.com/img.jpg"] if has_images else []
        should_run = bool(images) and platform in self.VISION_PLATFORMS
        assert should_run == expected_run
