"""Basis-Klasse für alle Agenten."""

from __future__ import annotations

import asyncio
import os
import sys
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from config import AppConfig
from tools.cache import ClaimCache
from tools.llm import LLMClient
from tools.web_search import AsyncWebSearchClient, WebSearchClient

# Geteilter Thread-Pool für sync→async Brücke
_thread_pool = ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4))


class BaseAgent(ABC):
    """Abstrakte Basisklasse – jeder Agent erbt hiervon."""

    name: str = "BaseAgent"
    emoji: str = "🤖"

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        search_client: WebSearchClient | None = None,
        cache: ClaimCache | None = None,
    ) -> None:
        self.config = config
        # Geteilte Clients aus dem Orchestrator verwenden (spart Connection-Pools)
        self.llm = llm_client or LLMClient(config.llm, config.retry)
        self.search = search_client or WebSearchClient(config.search, config.retry)
        self.async_search = AsyncWebSearchClient(config.search, config.retry)
        self.cache = cache  # Optional – wird vom Orchestrator gesetzt

    # ── Public Interface ─────────────────────────────────────────

    def run(self, input_data: Any, context: str = "") -> Any:
        """Führe den Agenten aus.  Logging + Error Handling um execute()."""
        self._log("Starte ...")
        try:
            result = self.execute(input_data, context)
            self._log("Fertig.")
            return result
        except Exception as e:
            # Nur Typ + Message loggen, keine vollständige Exception (verhindert Key-Leaks)
            self._log(f"FEHLER: {type(e).__name__}: {e}")
            raise

    async def execute_async(self, input_data: Any, context: str = "") -> Any:
        """Async-Version von execute().

        Standardimplementierung: führt synchrones execute() im Thread-Pool aus.
        Subklassen können das überschreiben, um echte async I/O zu nutzen.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_thread_pool, lambda: self.execute(input_data, context))

    @property
    def _agent_timeout(self) -> float:
        """Agent-Timeout aus zentraler Konfiguration."""
        return self.config.timeouts.agent

    async def run_async(self, input_data: Any, context: str = "") -> Any:
        """Async-Version von run() – Logging + Error Handling + Timeout um execute_async()."""
        self._log("Starte (async) ...")
        timeout = self._agent_timeout
        try:
            result = await asyncio.wait_for(
                self.execute_async(input_data, context),
                timeout=timeout,
            )
            self._log("Fertig.")
            return result
        except asyncio.TimeoutError:
            self._log(f"TIMEOUT nach {timeout}s")
            raise TimeoutError(f"{self.name}: Timeout nach {timeout}s")
        except Exception as e:
            self._log(f"FEHLER: {type(e).__name__}: {e}")
            raise

    async def run_safe_async(self, input_data: Any, context: str = "") -> tuple[Any | None, str | None]:
        """Async-Version von run_safe()."""
        try:
            result = await self.run_async(input_data, context)
            return result, None
        except Exception as e:
            error_msg = f"{self.name}: {type(e).__name__}: {e}"
            return None, error_msg

    def run_safe(self, input_data: Any, context: str = "") -> tuple[Any | None, str | None]:
        """Führe den Agenten aus – fängt Fehler ab (Graceful Degradation).

        Returns:
            (result, None) bei Erfolg, (None, error_message) bei Fehler.
        """
        try:
            result = self.run(input_data, context)
            return result, None
        except Exception as e:
            error_msg = f"{self.name}: {type(e).__name__}: {e}"
            return None, error_msg

    @abstractmethod
    def execute(self, input_data: Any, context: str = "") -> Any:
        """Implementierung der Agent-Logik.  Muss von Subklassen überschrieben werden."""
        ...

    # ── Hilfsmethoden ────────────────────────────────────────────

    def _llm_json(self, system_prompt: str, user_message: str) -> dict:
        """LLM-Call der JSON zurückgibt, mit Retry bei Parse-Fehler."""
        for attempt in range(2):
            try:
                return self.llm.complete_json(system_prompt, user_message)
            except ValueError:
                if attempt == 0:
                    self._log("JSON-Parse fehlgeschlagen, versuche erneut...")
                    continue
                raise

        # unreachable, but keeps mypy happy
        return {}

    def _llm_structured(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        tool_name: str = "output",
        tool_description: str = "Strukturierter Output",
    ) -> dict:
        """LLM-Call mit nativem Structured Output.  Fällt auf JSON-Mode zurück."""
        return self.llm.complete_structured(
            system_prompt, user_message, schema, tool_name, tool_description
        )

    def _llm_text(self, system_prompt: str, user_message: str) -> str:
        """LLM-Call der Freitext zurückgibt."""
        return self.llm.complete(system_prompt, user_message, response_format="text")

    def _llm_vision(self, system_prompt: str, user_message: str, image_urls: list[str]) -> dict:
        """Vision-LLM-Call mit Bildern – gibt geparsten dict zurück."""
        raw = self.llm.complete_vision(system_prompt, user_message, image_urls, response_format="json")
        return LLMClient._parse_json(raw)

    def _web_search(self, query: str, max_results: int = 5) -> str:
        """Websuche → formatierter String für LLM-Kontext."""
        results = self.search.search(query, max_results)
        return self.search.format_results_for_llm(results)

    def _web_multi_search(self, queries: list[str], max_results: int = 5) -> str:
        """Mehrere Websuchen → kombinierter String für LLM-Kontext."""
        all_results = self.search.multi_search(queries, max_results)
        parts: list[str] = []
        for query, results in all_results.items():
            parts.append(f"=== Suche: '{query}' ===")
            parts.append(self.search.format_results_for_llm(results))
        return "\n\n".join(parts)

    def _cache_get(self, claim_text: str, context: str = "") -> dict | None:
        """Lies gecachtes Ergebnis für diesen Agenten. Gibt None zurück wenn kein Treffer."""
        if self.cache is None:
            return None
        result = self.cache.get(claim_text, self.name, context)
        if result is not None:
            self._log(f"Cache-Treffer für '{claim_text[:60]}...'")
        return result

    def _cache_set(self, claim_text: str, result: dict, context: str = "") -> None:
        """Speichere Ergebnis für diesen Agenten im Cache."""
        if self.cache is not None:
            self.cache.set(claim_text, self.name, result, context)

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(f"  {self.emoji} [{self.name}] {message}", file=sys.stderr)
