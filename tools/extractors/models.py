"""Data models for content extraction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractedContent(BaseModel):
    """Result of extracting content from a URL."""
    url: str
    platform: str = Field(description="twitter|threads|instagram|facebook|youtube|article")
    title: str = ""
    text: str = Field(description="Extracted text content")
    author: str = ""
    images: list[str] = Field(default_factory=list, description="Image URLs")
    timestamp: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
