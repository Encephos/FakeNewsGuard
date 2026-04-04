"""Unit-Tests für tools/llm.py – LLMClient.

Strategie: LLMClient.__new__ + manuelle Attributzuweisung, damit kein
Provider-Library-Import in __init__ ausgeführt wird. self._client ist ein MagicMock.
"""

from __future__ import annotations

from unittest.mock import MagicMock


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────


def _make_client(provider: str = "anthropic", model: str = "claude-3-haiku-20240307"):
    from config import LLMConfig, RetryConfig
    from config.infrastructure import HTTPTimeoutsConfig
    from tools.llm import LLMClient

    c = LLMClient.__new__(LLMClient)
    c.config = LLMConfig(provider=provider, api_key="test-key", model=model)
    c._retry = RetryConfig(max_attempts=1, base_delay_s=0.0)
    c._timeouts = HTTPTimeoutsConfig()
    c._client = MagicMock()
    return c


def _make_anthropic_response(text: str = '{"key": "value"}'):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    return resp


def _make_openai_response(content: str = '{"key": "value"}'):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


# ── _parse_json (static method) ───────────────────────────────────────────────


class TestParseJson:
    def test_valid_json_returns_dict(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('{"foo": "bar"}')
        assert result == {"foo": "bar"}

    def test_strips_markdown_fence(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('```\n{"foo": 1}\n```')
        assert result == {"foo": 1}

    def test_strips_json_fenced_block(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('```json\n{"x": true}\n```')
        assert result == {"x": True}

    def test_finds_embedded_json_in_text(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('Some preamble {"answer": 42} trailing text')
        assert result == {"answer": 42}

    def test_bracket_counting_stops_at_first_complete_object(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('{"a": 1} {"b": 2}')
        assert result == {"a": 1}

    def test_array_fallback_returns_items_dict(self):
        from tools.llm import LLMClient
        result = LLMClient._parse_json('Here is a list: [1, 2, 3]')
        assert result == {"items": [1, 2, 3]}

    def test_raises_value_error_on_garbage_input(self):
        import pytest
        from tools.llm import LLMClient
        with pytest.raises(ValueError, match="Konnte kein valides JSON"):
            LLMClient._parse_json("this is just plain text with no json")


# ── _needs_system_fold ────────────────────────────────────────────────────────


class TestNeedsSystemFold:
    def test_gemma_model_needs_fold(self):
        c = _make_client(model="google/gemma-3-27b-it")
        assert c._needs_system_fold() is True

    def test_free_model_needs_fold(self):
        c = _make_client(model="openrouter/free")
        assert c._needs_system_fold() is True

    def test_standard_model_does_not_need_fold(self):
        c = _make_client(model="claude-3-haiku-20240307")
        assert c._needs_system_fold() is False

    def test_case_insensitive_check(self):
        c = _make_client(model="Google/Gemma-3-4B-IT")
        assert c._needs_system_fold() is True


# ── _build_messages ───────────────────────────────────────────────────────────


class TestBuildMessages:
    def test_standard_model_produces_two_messages(self):
        c = _make_client(model="claude-3-haiku-20240307")
        msgs = c._build_messages("sys", "usr")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_gemma_model_folds_into_single_user_message(self):
        c = _make_client(model="google/gemma-3-4b-it")
        msgs = c._build_messages("sys", "usr")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_folded_message_contains_system_content(self):
        c = _make_client(model="google/gemma-3-4b-it")
        msgs = c._build_messages("MY_SYSTEM_PROMPT", "user message")
        assert "MY_SYSTEM_PROMPT" in msgs[0]["content"]

    def test_folded_message_contains_user_content(self):
        c = _make_client(model="google/gemma-3-4b-it")
        msgs = c._build_messages("sys", "MY_USER_MESSAGE")
        assert "MY_USER_MESSAGE" in msgs[0]["content"]


# ── complete() – Anthropic ────────────────────────────────────────────────────


class TestCompleteAnthropicRouting:
    def test_complete_calls_messages_create(self):
        c = _make_client(provider="anthropic")
        c._client.messages.create.return_value = _make_anthropic_response("hello")
        result = c.complete("sys", "usr", response_format="text")
        assert c._client.messages.create.called
        assert result == "hello"

    def test_json_mode_appends_instruction_to_system_prompt(self):
        c = _make_client(provider="anthropic")
        c._client.messages.create.return_value = _make_anthropic_response("{}")
        c.complete("original_system", "usr", response_format="json")
        call_kwargs = c._client.messages.create.call_args[1]
        assert "AUSSCHLIESSLICH" in call_kwargs["system"]

    def test_complete_returns_content_text(self):
        c = _make_client(provider="anthropic")
        c._client.messages.create.return_value = _make_anthropic_response("result text")
        result = c.complete("sys", "usr")
        assert result == "result text"


# ── complete() – OpenAI / OpenRouter ─────────────────────────────────────────


class TestCompleteOpenAIRouting:
    def test_complete_calls_chat_completions(self):
        c = _make_client(provider="openai", model="gpt-4o")
        c._client.chat.completions.create.return_value = _make_openai_response("ok")
        result = c.complete("sys", "usr")
        assert c._client.chat.completions.create.called
        assert result == "ok"

    def test_openai_sets_json_response_format(self):
        c = _make_client(provider="openai", model="gpt-4o")
        c._client.chat.completions.create.return_value = _make_openai_response("{}")
        c.complete("sys", "usr", response_format="json")
        call_kwargs = c._client.chat.completions.create.call_args[1]
        assert call_kwargs.get("response_format") == {"type": "json_object"}

    def test_openrouter_sets_extra_body(self):
        c = _make_client(provider="openrouter", model="qwen/qwen-72b")
        c._client.chat.completions.create.return_value = _make_openai_response("{}")
        c.complete("sys", "usr", response_format="json")
        call_kwargs = c._client.chat.completions.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert "provider" in call_kwargs["extra_body"]

    def test_ollama_uses_openai_compat_client(self):
        c = _make_client(provider="ollama", model="llama3")
        c._client.chat.completions.create.return_value = _make_openai_response("ok")
        result = c.complete("sys", "usr")
        assert c._client.chat.completions.create.called
        assert result == "ok"


# ── complete_json() ───────────────────────────────────────────────────────────


class TestCompleteJson:
    def test_complete_json_parses_response(self):
        c = _make_client(provider="anthropic")
        c._client.messages.create.return_value = _make_anthropic_response('{"rating": "TRUE"}')
        result = c.complete_json("sys", "usr")
        assert result == {"rating": "TRUE"}

    def test_complete_json_raises_on_invalid_json(self):
        import pytest
        c = _make_client(provider="anthropic")
        c._client.messages.create.return_value = _make_anthropic_response("not json at all")
        with pytest.raises(ValueError):
            c.complete_json("sys", "usr")


# ── complete_structured() ─────────────────────────────────────────────────────


class TestCompleteStructured:
    def test_structured_anthropic_extracts_tool_use_block(self):
        c = _make_client(provider="anthropic")
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "output"
        tool_block.input = {"rating": "MISLEADING"}
        resp = MagicMock()
        resp.content = [tool_block]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        c._client.messages.create.return_value = resp
        result = c.complete_structured("sys", "usr", schema={}, tool_name="output")
        assert result == {"rating": "MISLEADING"}

    def test_structured_raises_if_no_tool_use_block(self):
        # Wenn kein tool_use block gefunden → ValueError → Fallback auf complete_json
        c = _make_client(provider="anthropic")
        text_block = MagicMock()
        text_block.type = "text"
        resp = MagicMock()
        resp.content = [text_block]
        resp.usage.input_tokens = 5
        resp.usage.output_tokens = 2
        # Erster Call (structured) → kein tool_use → ValueError
        # Fallback auf complete_json → zweiter Call gibt JSON zurück
        c._client.messages.create.side_effect = [
            resp,
            _make_anthropic_response('{"fallback": true}'),
        ]
        result = c.complete_structured("sys", "usr", schema={}, tool_name="output")
        assert result == {"fallback": True}

    def test_structured_falls_back_to_complete_json_on_exception(self):
        c = _make_client(provider="anthropic")
        # Erster Call schlägt mit Exception fehl → Fallback
        c._client.messages.create.side_effect = [
            RuntimeError("API down"),
            _make_anthropic_response('{"fallback": true}'),
        ]
        result = c.complete_structured("sys", "usr", schema={})
        assert result == {"fallback": True}

    def test_structured_openai_uses_json_schema_format(self):
        c = _make_client(provider="openai", model="gpt-4o")
        c._client.chat.completions.create.return_value = _make_openai_response('{"x": 1}')
        result = c.complete_structured(
            "sys", "usr", schema={"type": "object"}, tool_name="my_tool"
        )
        assert result == {"x": 1}
        call_kwargs = c._client.chat.completions.create.call_args[1]
        rf = call_kwargs.get("response_format", {})
        assert rf.get("type") == "json_schema"
        assert rf.get("json_schema", {}).get("name") == "my_tool"
