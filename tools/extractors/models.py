"""Data models for content extraction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    """Ein einzelnes Transkript-Segment mit Zeitstempel."""
    start: float = Field(description="Startzeit in Sekunden")
    end: float = Field(description="Endzeit in Sekunden")
    text: str = Field(description="Transkribierter Text")


class FrameOCR(BaseModel):
    """OCR-Ergebnis eines einzelnen Video-Keyframes."""
    timestamp: float = Field(description="Frame-Zeitstempel in Sekunden")
    text: str = Field(description="Erkannter Text im Frame")
    image_hash: str = Field(default="", description="Perceptual Hash fuer Dedup")


class MediaContent(BaseModel):
    """Strukturierte Medieninhalte aus Video-/Audio-Analyse."""
    media_type: str = Field(description="video|audio|image")
    duration_seconds: float = 0.0
    language: str = Field(default="", description="Erkannte Sprache (ISO 639-1)")
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    frame_ocr: list[FrameOCR] = Field(default_factory=list)
    transcript_source: str = Field(default="", description="whisper|captions|empty")


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
