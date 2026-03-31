"""ContentExtractor class -- main entry point for content extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.extractors.article import _extract_article
from tools.extractors.detection import detect_platform
from tools.extractors.facebook import _extract_facebook
from tools.extractors.html_helpers import _get_client
from tools.extractors.instagram import _extract_instagram
from tools.extractors.models import ExtractedContent
from tools.extractors.threads import _extract_threads
from tools.extractors.twitter import _extract_twitter
from tools.extractors.youtube import _extract_youtube

if TYPE_CHECKING:
    from config.processing import MediaIngestionConfig


# Plattformen, die media_config als kwarg akzeptieren
_MEDIA_AWARE_PLATFORMS = {"youtube", "instagram"}


class ContentExtractor:
    """Extracts text and images from URLs across platforms."""

    _EXTRACTORS = {
        "twitter": _extract_twitter,
        "threads": _extract_threads,
        "instagram": _extract_instagram,
        "facebook": _extract_facebook,
        "youtube": _extract_youtube,
        "article": _extract_article,
    }

    def __init__(self, media_config: MediaIngestionConfig | None = None) -> None:
        self._media_config = media_config

    def extract(self, url: str) -> ExtractedContent:
        """Extract content from a URL (sync).

        Args:
            url: The URL to extract content from.

        Returns:
            ExtractedContent with text, images, metadata.

        Raises:
            ValueError: If URL is invalid or content cannot be extracted.
        """
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        platform = detect_platform(url)
        extractor_fn = self._EXTRACTORS[platform]

        with _get_client() as client:
            if platform in _MEDIA_AWARE_PLATFORMS and self._media_config:
                return extractor_fn(url, client, media_config=self._media_config)
            return extractor_fn(url, client)

    async def extract_async(self, url: str) -> ExtractedContent:
        """Extract content from a URL (async).

        Runs sync extraction in a thread pool to avoid blocking the event loop.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.extract, url)

    def extract_multiple(self, urls: list[str]) -> list[ExtractedContent]:
        """Extract content from multiple URLs."""
        results = []
        with _get_client() as client:
            for url in urls:
                try:
                    url = url.strip()
                    if not url.startswith(("http://", "https://")):
                        url = "https://" + url
                    platform = detect_platform(url)
                    extractor_fn = self._EXTRACTORS[platform]
                    if platform in _MEDIA_AWARE_PLATFORMS and self._media_config:
                        results.append(extractor_fn(url, client, media_config=self._media_config))
                    else:
                        results.append(extractor_fn(url, client))
                except Exception as e:
                    results.append(ExtractedContent(
                        url=url,
                        platform="unknown",
                        text=f"Fehler beim Extrahieren: {e}",
                    ))
        return results

    def format_for_analysis(self, content: ExtractedContent) -> str:
        """Format extracted content as text for the analysis pipeline."""
        parts: list[str] = []

        if content.title:
            parts.append(f"Titel: {content.title}")
        if content.author:
            parts.append(f"Autor: {content.author}")
        if content.platform != "article":
            parts.append(f"Plattform: {content.platform.capitalize()}")

        parts.append("")  # Empty line
        parts.append(content.text)

        if content.images:
            parts.append("")
            parts.append(f"[{len(content.images)} Bild(er) enthalten]")

        if content.metadata.get("likes"):
            parts.append(f"[{content.metadata['likes']} Likes, {content.metadata.get('retweets', 0)} Retweets]")

        return "\n".join(parts)
