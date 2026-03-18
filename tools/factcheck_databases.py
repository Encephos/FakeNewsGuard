"""Anbindung externer Faktencheck-Datenbanken.

Durchsucht professionelle Fact-Check-Organisationen nach bereits
geprüften Behauptungen:

  - Google Fact Check Tools API (aggregiert Correctiv, dpa, Snopes, AFP, Reuters u.v.m.)

Ergebnisse werden als zusätzlicher Kontext an den FactChecker übergeben,
damit er professionelle Einschätzungen berücksichtigen kann.

Konfiguration (optional, in .env):
  GOOGLE_FACTCHECK_API_KEY  – Kostenloser Key über Google Cloud Console
                              (API: "Fact Check Tools API" aktivieren)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from config import RetryConfig
from tools.retry import retry_call, retry_call_async


@dataclass
class ExternalFactCheck:
    """Ein Ergebnis aus einer externen Faktencheck-Datenbank."""
    claim_reviewed: str
    rating: str
    publisher: str
    url: str
    source_api: str
    language: str = ""
    title: str = ""


@dataclass
class FactCheckDatabaseConfig:
    """Konfiguration für externe Faktencheck-APIs."""
    google_factcheck_api_key: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.google_factcheck_api_key:
            self.google_factcheck_api_key = os.getenv("GOOGLE_FACTCHECK_API_KEY", "")


class FactCheckDatabaseClient:
    """Durchsucht externe Faktencheck-Datenbanken nach bereits geprüften Claims."""

    def __init__(
        self,
        config: FactCheckDatabaseConfig | None = None,
        retry: RetryConfig | None = None,
    ) -> None:
        self.config = config or FactCheckDatabaseConfig()
        self._retry = retry or RetryConfig(max_attempts=2, base_delay_s=0.5)

    def search(self, claim_text: str, language: str = "de") -> list[ExternalFactCheck]:
        """Durchsuche verfügbare Datenbanken nach einem Claim."""
        if not self.config.enabled or not self.config.google_factcheck_api_key:
            return []

        try:
            return self._search_google_factcheck(claim_text, language)
        except Exception as e:
            _log(f"Google Fact Check API Fehler: {type(e).__name__}: {e}")
            return []

    async def search_async(self, claim_text: str, language: str = "de") -> list[ExternalFactCheck]:
        """Async-Version."""
        if not self.config.enabled or not self.config.google_factcheck_api_key:
            return []

        try:
            return await self._search_google_factcheck_async(claim_text, language)
        except Exception as e:
            _log(f"Google Fact Check API Fehler: {type(e).__name__}: {e}")
            return []

    # ── Google Fact Check Tools API ───────────────────────────────

    def _search_google_factcheck(
        self, claim_text: str, language: str
    ) -> list[ExternalFactCheck]:
        """Google Fact Check Tools API – aggregiert Correctiv, dpa, Snopes, AFP etc."""
        def _call():
            resp = httpx.get(
                "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                params={
                    "query": claim_text[:200],
                    "languageCode": language,
                    "pageSize": 5,
                    "key": self.config.google_factcheck_api_key,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json()

        data = retry_call(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return self._parse_google_response(data)

    async def _search_google_factcheck_async(
        self, claim_text: str, language: str
    ) -> list[ExternalFactCheck]:
        async def _call():
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                    params={
                        "query": claim_text[:200],
                        "languageCode": language,
                        "pageSize": 5,
                        "key": self.config.google_factcheck_api_key,
                    },
                )
                resp.raise_for_status()
                return resp.json()

        data = await retry_call_async(
            _call,
            max_attempts=self._retry.max_attempts,
            base_delay=self._retry.base_delay_s,
            max_delay=self._retry.max_delay_s,
            backoff_factor=self._retry.backoff_factor,
        )
        return self._parse_google_response(data)

    @staticmethod
    def _parse_google_response(data: dict) -> list[ExternalFactCheck]:
        results: list[ExternalFactCheck] = []
        for claim_obj in data.get("claims", []):
            claim_text = claim_obj.get("text", "")
            for review in claim_obj.get("claimReview", []):
                results.append(ExternalFactCheck(
                    claim_reviewed=claim_text,
                    rating=review.get("textualRating", ""),
                    publisher=review.get("publisher", {}).get("name", ""),
                    url=review.get("url", ""),
                    source_api="google_factcheck",
                    language=review.get("languageCode", ""),
                    title=review.get("title", ""),
                ))
        return results

    # ── Formatierung für LLM-Kontext ──────────────────────────────

    @staticmethod
    def format_for_llm(results: list[ExternalFactCheck]) -> str:
        """Formatiere externe Fact-Checks als LLM-Kontext."""
        if not results:
            return ""

        parts: list[str] = [
            "## Bestehende professionelle Faktenchecks\n",
            "Die folgenden Behauptungen wurden bereits von professionellen "
            "Faktencheck-Organisationen geprüft:\n",
        ]

        for i, fc in enumerate(results, 1):
            parts.append(
                f"[Faktencheck {i}] {fc.publisher}\n"
                f"Geprüfter Claim: {fc.claim_reviewed}\n"
                f"Bewertung: {fc.rating}\n"
                f"URL: {fc.url}\n"
            )

        parts.append(
            "WICHTIG: Berücksichtige diese professionellen Einschätzungen "
            "in deiner Bewertung. Wenn eine anerkannte Faktencheck-Organisation "
            "den Claim bereits geprüft hat, sollte deren Einschätzung stark "
            "gewichtet werden.\n"
        )

        return "\n".join(parts)


def _log(msg: str) -> None:
    print(f"  [FactCheckDB] {msg}", file=sys.stderr)
