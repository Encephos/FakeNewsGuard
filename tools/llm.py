"""LLM-Abstraktionsschicht – unterstützt Anthropic, OpenAI und Ollama."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from opentelemetry.trace import StatusCode

from config import LLMConfig, RetryConfig
from config.infrastructure import HTTPTimeoutsConfig
from tools.cost_tracker import LLMUsage, record_usage
from tools.retry import retry_call
from tools.telemetry import LLM_DURATION, LLM_REQUEST_COUNT, get_tracer

_tracer = get_tracer("fng.llm")


class LLMClient:
    """Einheitliches Interface für verschiedene LLM-Provider."""

    def __init__(
        self,
        config: LLMConfig,
        retry: RetryConfig | None = None,
        timeouts: HTTPTimeoutsConfig | None = None,
    ) -> None:
        self.config = config
        self._retry = retry or RetryConfig()
        self._timeouts = timeouts or HTTPTimeoutsConfig()
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        if self.config.provider == "anthropic":
            import anthropic
            import httpx as _httpx

            self._client = anthropic.Anthropic(
                api_key=self.config.api_key,
                timeout=_httpx.Timeout(self._timeouts.llm, connect=self._timeouts.llm_connect),
            )

        elif self.config.provider == "openai":
            import openai

            kwargs: dict[str, Any] = {
                "api_key": self.config.api_key,
                "timeout": self._timeouts.llm,
            }
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = openai.OpenAI(**kwargs)

        elif self.config.provider == "openrouter":
            import openai

            self._client = openai.OpenAI(
                base_url=self.config.base_url or "https://openrouter.ai/api/v1",
                api_key=self.config.api_key,
                timeout=self._timeouts.llm,
                default_headers={
                    "HTTP-Referer": "https://github.com/Encephos/FakeNewsGuard",
                    "X-Title": "FakeNewsGuard",
                },
            )

        elif self.config.provider == "ollama":
            import openai

            self._client = openai.OpenAI(
                base_url=self.config.base_url or "http://localhost:11434/v1",
                api_key="ollama",
                timeout=self._timeouts.llm,
            )

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        response_format: str = "text",
        agent_name: str = "unknown",
    ) -> str:
        """Sende eine Nachricht an das LLM und erhalte die Antwort.

        Args:
            system_prompt: System-Prompt für den Agenten.
            user_message: Die Nachricht / der Input.
            response_format: "text" oder "json" – bei json wird
                             das Modell angewiesen, JSON zu liefern.
            agent_name: Name des aufrufenden Agenten (fuer Tracing).

        Returns:
            Die Antwort als String.
        """
        if response_format == "json":
            system_prompt += (
                "\n\nAntworte AUSSCHLIESSLICH mit validem JSON. "
                "Kein Markdown, keine Erklärungen, kein ```json Block. Nur das JSON-Objekt."
            )

        with _tracer.start_as_current_span("llm.complete") as span:
            span.set_attribute("llm.model", self.config.model)
            span.set_attribute("llm.agent", agent_name)
            span.set_attribute("llm.response_format", response_format)
            start = time.monotonic()
            try:
                if self.config.provider == "anthropic":
                    result = self._complete_anthropic(system_prompt, user_message, agent_name)
                else:
                    result = self._complete_openai(system_prompt, user_message, response_format, agent_name)
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(StatusCode.ERROR, str(e))
                raise
            finally:
                duration = time.monotonic() - start
                LLM_REQUEST_COUNT.labels(model=self.config.model, agent=agent_name).inc()
                LLM_DURATION.labels(model=self.config.model, agent=agent_name).observe(duration)

    def _complete_anthropic(self, system_prompt: str, user_message: str, agent_name: str = "unknown") -> str:
        def _call():
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    call_type="complete",
                ))
            except Exception:
                pass
            return response.content[0].text

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def _needs_system_fold(self) -> bool:
        """Check if the model needs system prompt folded into user message."""
        model = self.config.model.lower()
        return "gemma" in model or "free" in model

    def _build_messages(self, system_prompt: str, user_message: str) -> list[dict[str, str]]:
        """Build message list, folding system prompt for models that don't support it."""
        if self._needs_system_fold():
            combined = f"[INSTRUKTIONEN]\n{system_prompt}\n[/INSTRUKTIONEN]\n\n{user_message}"
            return [{"role": "user", "content": combined}]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def _complete_openai(
        self, system_prompt: str, user_message: str, response_format: str, agent_name: str = "unknown"
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": self._build_messages(system_prompt, user_message),
        }
        # OpenAI supports structured JSON mode
        if response_format == "json" and self.config.provider in ("openai", "openrouter"):
            kwargs["response_format"] = {"type": "json_object"}

        if self.config.provider == "openrouter":
            sort_pref = "throughput" if "gemma-3" in self.config.model.lower() else "price"
            kwargs["extra_body"] = {
                "provider": {"sort": sort_pref, "allow_fallbacks": True},
            }

        def _call():
            response = self._client.chat.completions.create(**kwargs)
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
                    call_type="complete",
                ))
            except Exception:
                pass
            return response.choices[0].message.content or ""

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def complete_vision(
        self,
        system_prompt: str,
        user_message: str,
        image_urls: list[str],
        response_format: str = "json",
        agent_name: str = "unknown",
    ) -> str:
        """Vision-Call: sendet Text + Bilder an ein multimodales LLM.

        Args:
            system_prompt: System-Prompt für den Agenten.
            user_message: Textuelle Nachricht / Analyseanweisung.
            image_urls: Liste von Bild-URLs (max. 5 empfohlen).
            response_format: "text" oder "json".
            agent_name: Name des aufrufenden Agenten (fuer Tracing).

        Returns:
            Die Antwort als String.
        """
        if response_format == "json":
            system_prompt += (
                "\n\nAntworte AUSSCHLIESSLICH mit validem JSON. "
                "Kein Markdown, keine Erklärungen, kein ```json Block. Nur das JSON-Objekt."
            )

        if self.config.provider == "anthropic":
            return self._complete_vision_anthropic(system_prompt, user_message, image_urls, agent_name)
        else:
            return self._complete_vision_openai(system_prompt, user_message, image_urls, response_format, agent_name)

    def _complete_vision_openai(
        self,
        system_prompt: str,
        user_message: str,
        image_urls: list[str],
        response_format: str,
        agent_name: str = "unknown",
    ) -> str:
        """Vision-Call über OpenAI-kompatible API (openrouter/openai/ollama)."""
        image_parts: list[dict] = [
            {"type": "image_url", "image_url": {"url": url}}
            for url in image_urls
        ]

        if self._needs_system_fold():
            combined_text = f"[INSTRUKTIONEN]\n{system_prompt}\n[/INSTRUKTIONEN]\n\n{user_message}"
            user_content: list[dict] = [{"type": "text", "text": combined_text}] + image_parts
            messages: list[dict] = [{"role": "user", "content": user_content}]
        else:
            user_content = [{"type": "text", "text": user_message}] + image_parts
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": messages,
        }
        if response_format == "json" and self.config.provider in ("openai", "openrouter"):
            kwargs["response_format"] = {"type": "json_object"}
        if self.config.provider == "openrouter":
            sort_pref = "throughput" if "gemma-3" in self.config.model.lower() else "price"
            kwargs["extra_body"] = {
                "provider": {"sort": sort_pref, "allow_fallbacks": True},
            }

        def _call():
            response = self._client.chat.completions.create(**kwargs)
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
                    call_type="complete_vision",
                ))
            except Exception:
                pass
            return response.choices[0].message.content or ""

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def _complete_vision_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        image_urls: list[str],
        agent_name: str = "unknown",
    ) -> str:
        """Vision-Call über Anthropic API."""
        image_parts_anthropic: list[dict] = [
            {"type": "image", "source": {"type": "url", "url": url}}
            for url in image_urls
        ]
        user_content_anthropic: list[dict] = image_parts_anthropic + [
            {"type": "text", "text": user_message}
        ]

        def _call():
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content_anthropic}],
            )
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    call_type="complete_vision",
                ))
            except Exception:
                pass
            return response.content[0].text

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def complete_json(self, system_prompt: str, user_message: str, agent_name: str = "unknown") -> dict:
        """Convenience: LLM-Call der direkt ein dict zurückgibt."""
        raw = self.complete(system_prompt, user_message, response_format="json", agent_name=agent_name)
        return self._parse_json(raw)

    def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str = "output",
        tool_description: str = "Strukturierter Output",
        agent_name: str = "unknown",
        temperature: float | None = None,
    ) -> dict:
        """LLM-Call mit nativem Structured Output (Anthropic tool_use / OpenAI json_schema).

        Fällt bei Fehler graceful auf complete_json() zurück.

        Args:
            system_prompt: System-Prompt.
            user_message: User-Nachricht.
            schema: JSON-Schema des erwarteten Outputs.
            tool_name: Name des Anthropic-Tools / OpenAI-Schemas.
            tool_description: Beschreibung für das Modell.
            agent_name: Name des aufrufenden Agenten (fuer Tracing).

        Returns:
            Geparster dict.
        """
        with _tracer.start_as_current_span("llm.complete_structured") as span:
            span.set_attribute("llm.model", self.config.model)
            span.set_attribute("llm.agent", agent_name)
            start = time.monotonic()
            try:
                _temp = temperature if temperature is not None else self.config.temperature
                if self.config.provider == "anthropic":
                    return self._complete_structured_anthropic(
                        system_prompt, user_message, schema, tool_name, tool_description, agent_name,
                        temperature=_temp,
                    )
                elif self.config.provider in ("openai", "openrouter"):
                    return self._complete_structured_openai(
                        system_prompt, user_message, schema, tool_name, tool_description, agent_name,
                        temperature=_temp,
                    )
            except Exception:
                pass  # Fallback auf JSON-Mode
            finally:
                duration = time.monotonic() - start
                LLM_REQUEST_COUNT.labels(model=self.config.model, agent=agent_name).inc()
                LLM_DURATION.labels(model=self.config.model, agent=agent_name).observe(duration)

        return self.complete_json(system_prompt, user_message, agent_name=agent_name)

    def _complete_structured_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str,
        tool_description: str,
        agent_name: str = "unknown",
        temperature: float | None = None,
    ) -> dict:
        def _call():
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                system=system_prompt,
                tools=[
                    {
                        "name": tool_name,
                        "description": tool_description,
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[{"role": "user", "content": user_message}],
            )
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    call_type="complete_structured",
                ))
            except Exception:
                pass
            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return block.input
            raise ValueError("Kein tool_use Block in Anthropic Response")

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def _complete_structured_openai(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str,
        tool_description: str,
        agent_name: str = "unknown",
        temperature: float | None = None,
    ) -> dict:
        import json as _json

        extra = {}
        if self.config.provider == "openrouter":
            sort_pref = "throughput" if "gemma-3" in self.config.model.lower() else "price"
            extra["extra_body"] = {
                "provider": {"sort": sort_pref, "allow_fallbacks": True},
            }

        messages = self._build_messages(system_prompt, user_message)

        def _call():
            response = self._client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": tool_name,
                        "description": tool_description,
                        "schema": schema,
                        "strict": True,
                    },
                },
                **extra,
            )
            try:
                record_usage(LLMUsage(
                    model=self.config.model, agent=agent_name,
                    input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
                    call_type="complete_structured",
                ))
            except Exception:
                pass
            content = response.choices[0].message.content or "{}"
            return _json.loads(content)

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Robustes JSON-Parsing – entfernt Markdown-Fences und repariert häufige Fehler."""
        # Markdown code fences entfernen
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Ersten validen JSON-Block suchen (nicht-greedy via Bracket-Zählung)
        start = text.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Letzter Fallback: Array
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                return {"items": json.loads(match.group())}
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Konnte kein valides JSON parsen aus:\n{text[:500]}")
