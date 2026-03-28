"""Content extraction package -- re-exports public API and internal helpers.

Public symbols:
    ContentExtractor, ExtractedContent, detect_platform, extract_urls, is_url

Internal helpers (used by tools/source_scraper.py and tests):
    _HEADERS, _extract_article_text, _extract_article_text_with_date,
    _extract_meta, _extract_json_ld, _strip_html_tags,
    _extract_images_from_html, _extract_article_text
"""

from tools.extractors.detection import detect_platform, extract_urls, is_url
from tools.extractors.html_helpers import (
    _HEADERS,
    _extract_article_text,
    _extract_article_text_with_date,
    _extract_images_from_html,
    _extract_json_ld,
    _extract_meta,
    _strip_html_tags,
)
from tools.extractors.models import ExtractedContent
from tools.extractors.orchestrator import ContentExtractor

__all__ = [
    # Public API
    "ContentExtractor",
    "ExtractedContent",
    "detect_platform",
    "extract_urls",
    "is_url",
    # Internal helpers (backward compat)
    "_HEADERS",
    "_extract_article_text",
    "_extract_article_text_with_date",
    "_extract_images_from_html",
    "_extract_json_ld",
    "_extract_meta",
    "_strip_html_tags",
]
