"""LLM-Abstraktionsschicht – unterstützt Anthropic, OpenAI und Ollama."""

from __future__ import annotations

import json
import re
from typing import Any

from config import LLMConfig, RetryConfig
from tools.retry import retry_call


class LLMClient:
    """Einheitliches Interface für verschiedene LLM-Provider."""

    # Max seconds for a single LLM API call before giving up
    REQUEST_TIMEOUT = 120.0

    def __init__(self, config: LLMConfig, retry: RetryConfig | None = None) -> None:
        self.config = config
        self._retry = retry or RetryConfig()
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        if self.config.provider == "anthropic":
            import anthropic
            import httpx as _httpx

            self._client = anthropic.Anthropic(
                api_key=self.config.api_key,
                timeout=_httpx.Timeout(self.REQUEST_TIMEOUT, connect=10.0),
            )

        elif self.config.provider == "openai":
            import openai

            kwargs: dict[str, Any] = {
                "api_key": self.config.api_key,
                "timeout": self.REQUEST_TIMEOUT,
            }
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = openai.OpenAI(**kwargs)

        elif self.config.provider == "openrouter":
            import openai

            self._client = openai.OpenAI(
                base_url=self.config.base_url or "https://openrouter.ai/api/v1",
                api_key=self.config.api_key,
                timeout=self.REQUEST_TIMEOUT,
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
                timeout=self.REQUEST_TIMEOUT,
            )

    def complete(
        self,
        system_prompt: str,
        user_message: str,
        response_format: str = "text",
    ) -> str:
        """Sende eine Nachricht an das LLM und erhalte die Antwort.

        Args:
            system_prompt: System-Prompt für den Agenten.
            user_message: Die Nachricht / der Input.
            response_format: "text" oder "json" – bei json wird
                             das Modell angewiesen, JSON zu liefern.

        Returns:
            Die Antwort als String.
        """
        if response_format == "json":
            system_prompt += (
                "\n\nAntworte AUSSCHLIESSLICH mit validem JSON. "
                "Kein Markdown, keine Erklärungen, kein ```json Block. Nur das JSON-Objekt."
            )

        if self.config.provider == "anthropic":
            return self._complete_anthropic(system_prompt, user_message)
        else:
            return self._complete_openai(system_prompt, user_message, response_format)

    def _complete_anthropic(self, system_prompt: str, user_message: str) -> str:
        def _call():
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def _complete_openai(
        self, system_prompt: str, user_message: str, response_format: str
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        # OpenAI supports structured JSON mode
        if response_format == "json" and self.config.provider in ("openai", "openrouter"):
            kwargs["response_format"] = {"type": "json_object"}

        if self.config.provider == "openrouter":
            kwargs["extra_body"] = {
                "provider": {"sort": "price", "allow_fallbacks": True},
            }

        def _call():
            response = self._client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""

        return retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )

    def complete_json(self, system_prompt: str, user_message: str) -> dict:
        """Convenience: LLM-Call der direkt ein dict zurückgibt."""
        raw = self.complete(system_prompt, user_message, response_format="json")
        return self._parse_json(raw)

    def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str = "output",
        tool_description: str = "Strukturierter Output",
    ) -> dict:
        """LLM-Call mit nativem Structured Output (Anthropic tool_use / OpenAI json_schema).

        Fällt bei Fehler graceful auf complete_json() zurück.

        Args:
            system_prompt: System-Prompt.
            user_message: User-Nachricht.
            schema: JSON-Schema des erwarteten Outputs.
            tool_name: Name des Anthropic-Tools / OpenAI-Schemas.
            tool_description: Beschreibung für das Modell.

        Returns:
            Geparster dict.
        """
        try:
            if self.config.provider == "anthropic":
                return self._complete_structured_anthropic(
                    system_prompt, user_message, schema, tool_name, tool_description
                )
            elif self.config.provider in ("openai", "openrouter"):
                return self._complete_structured_openai(
                    system_prompt, user_message, schema, tool_name, tool_description
                )
        except Exception:
            pass  # Fallback auf JSON-Mode

        return self.complete_json(system_prompt, user_message)

    def _complete_structured_anthropic(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str,
        tool_description: str,
    ) -> dict:
        def _call():
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
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
    ) -> dict:
        import json as _json

        extra = {}
        if self.config.provider == "openrouter":
            extra["extra_body"] = {
                "provider": {"sort": "price", "allow_fallbacks": True},
            }

        def _call():
            response = self._client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
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
