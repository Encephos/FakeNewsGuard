"""URL pattern detection and extraction utilities."""

from __future__ import annotations

import re


PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("twitter", re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)", re.I)),
    ("threads", re.compile(r"https?://(?:www\.)?threads\.net/(?:@[\w.]+/post/[\w-]+|t/[\w-]+)", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([\w-]+)", re.I)),
    ("facebook", re.compile(r"https?://(?:www\.|m\.)?facebook\.com/.+", re.I)),
    ("youtube", re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)", re.I)),
]

# General URL pattern to detect any link
URL_PATTERN = re.compile(
    r"https?://[^\s<>\"')\]]+",
    re.I,
)


def detect_platform(url: str) -> str:
    """Detect which platform a URL belongs to."""
    for platform, pattern in PLATFORM_PATTERNS:
        if pattern.match(url):
            return platform
    return "article"


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from a text string."""
    return URL_PATTERN.findall(text)


def is_url(text: str) -> bool:
    """Check if the text is (or contains) a URL."""
    return bool(URL_PATTERN.search(text.strip()))
