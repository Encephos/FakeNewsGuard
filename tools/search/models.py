"""Datenmodelle für Such-Ergebnisse."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""  # Volltext, falls verfügbar (LangSearch, Tavily)
