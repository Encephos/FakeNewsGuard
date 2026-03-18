"""Content Extractor – Extracts text and images from URLs.

Supports:
  - Twitter/X (via fxtwitter API)
  - Threads (via meta tags + JSON-LD scraping, handles nested threads)
  - Instagram (via meta tags / oembed)
  - Facebook (via meta tags scraping)
  - YouTube (via oembed + transcript extraction)
  - News articles (via readability-style extraction)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field


# ── Data Models ──────────────────────────────────────────────────

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


# ── URL Pattern Detection ────────────────────────────────────────

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


# ── HTTP Client helpers ──────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _get_client() -> httpx.Client:
    return httpx.Client(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=20.0,
    )


async def _get_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=20.0,
    )


# ── HTML Helpers ─────────────────────────────────────────────────

def _extract_meta(html: str, properties: list[str]) -> dict[str, str]:
    """Extract OpenGraph / meta tag values from HTML without BeautifulSoup."""
    result: dict[str, str] = {}
    for prop in properties:
        # Try property="..." and name="..."
        for attr in ("property", "name"):
            pattern = re.compile(
                rf'<meta\s+[^>]*{attr}=["\']?{re.escape(prop)}["\']?\s+content=["\']([^"\']*)["\']',
                re.I | re.S,
            )
            m = pattern.search(html)
            if not m:
                # Reversed attribute order
                pattern2 = re.compile(
                    rf'<meta\s+[^>]*content=["\']([^"\']*)["\']?\s+{attr}=["\']?{re.escape(prop)}["\']?',
                    re.I | re.S,
                )
                m = pattern2.search(html)
            if m:
                result[prop] = _html_unescape(m.group(1).strip())
                break
    return result


def _extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Extract JSON-LD blocks from HTML."""
    results: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.I | re.S,
    )
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def _html_unescape(text: str) -> str:
    """Unescape common HTML entities."""
    replacements = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&apos;": "'",
        "&#x27;": "'", "&#x2F;": "/", "&nbsp;": " ",
    }
    for entity, char in replacements.items():
        text = text.replace(entity, char)
    # Numeric entities
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    return text


def _strip_html_tags(html: str) -> str:
    """Remove all HTML tags and return plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html_unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_article_text(html: str) -> str:
    """Simple readability-style extraction: find the largest text block."""
    # Try <article> tag first
    article_match = re.search(r"<article[^>]*>(.*?)</article>", html, re.S | re.I)
    if article_match:
        return _strip_html_tags(article_match.group(1))

    # Try common content containers
    for selector in [
        r'<div[^>]*class="[^"]*(?:article|post|entry|content|story)[^"]*"[^>]*>(.*?)</div>',
        r'<main[^>]*>(.*?)</main>',
        r'<div[^>]*role="main"[^>]*>(.*?)</div>',
    ]:
        m = re.search(selector, html, re.S | re.I)
        if m:
            text = _strip_html_tags(m.group(1))
            if len(text) > 200:
                return text

    # Fallback: find all <p> tags and join
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S | re.I)
    if paragraphs:
        texts = [_strip_html_tags(p) for p in paragraphs]
        joined = "\n\n".join(t for t in texts if len(t) > 30)
        if joined:
            return joined

    # Last resort: strip everything
    return _strip_html_tags(html)[:5000]


def _extract_images_from_html(html: str) -> list[str]:
    """Extract image URLs from og:image and <img> tags."""
    images: list[str] = []
    # OG image
    meta = _extract_meta(html, ["og:image", "twitter:image"])
    for key in ["og:image", "twitter:image"]:
        if key in meta and meta[key]:
            images.append(meta[key])
    # Large images from <img> tags
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1)
        if any(skip in src.lower() for skip in ["logo", "icon", "avatar", "pixel", "tracking", ".svg", "1x1"]):
            continue
        if src not in images:
            images.append(src)
    return images[:10]  # Cap at 10


# ── Platform-Specific Extractors ─────────────────────────────────

def _extract_twitter(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract tweet content using fxtwitter API."""
    match = re.search(r"status/(\d+)", url)
    if not match:
        raise ValueError(f"Konnte Tweet-ID nicht aus URL extrahieren: {url}")

    tweet_id = match.group(1)

    # Try fxtwitter API (no auth needed)
    api_url = url.replace("twitter.com", "api.fxtwitter.com").replace("x.com", "api.fxtwitter.com")
    try:
        resp = client.get(api_url)
        if resp.status_code == 200:
            data = resp.json()
            tweet = data.get("tweet", {})
            text = tweet.get("text", "")
            author = tweet.get("author", {}).get("name", "")
            author_handle = tweet.get("author", {}).get("screen_name", "")
            images = []

            # Extract media
            media = tweet.get("media", {})
            if media:
                for photo in media.get("photos", []):
                    if photo.get("url"):
                        images.append(photo["url"])
                for video in media.get("videos", []):
                    if video.get("thumbnail_url"):
                        images.append(video["thumbnail_url"])

            # Check for quote tweet (thread-like)
            quote = tweet.get("quote")
            if quote:
                text += f"\n\n[Zitierter Tweet von @{quote.get('author', {}).get('screen_name', '?')}]:\n{quote.get('text', '')}"

            return ExtractedContent(
                url=url,
                platform="twitter",
                title=f"Tweet von @{author_handle}" if author_handle else "Tweet",
                text=text,
                author=f"{author} (@{author_handle})" if author else "",
                images=images,
                timestamp=tweet.get("created_at", ""),
                metadata={
                    "likes": tweet.get("likes", 0),
                    "retweets": tweet.get("retweets", 0),
                    "replies": tweet.get("replies", 0),
                },
            )
    except Exception:
        pass

    # Fallback: Use oembed
    return _extract_twitter_oembed(url, client)


def _extract_twitter_oembed(url: str, client: httpx.Client) -> ExtractedContent:
    """Fallback: Use Twitter oembed endpoint."""
    oembed_url = f"https://publish.twitter.com/oembed?url={url}"
    resp = client.get(oembed_url)
    if resp.status_code != 200:
        raise ValueError(f"Twitter oembed fehlgeschlagen: {resp.status_code}")

    data = resp.json()
    # oembed html contains the tweet text in a blockquote
    html = data.get("html", "")
    text = _strip_html_tags(html)
    # Remove trailing "— Author (@handle) Date" line
    text = re.sub(r"\s*—\s*.+$", "", text, flags=re.M).strip()

    return ExtractedContent(
        url=url,
        platform="twitter",
        title="Tweet",
        text=text,
        author=data.get("author_name", ""),
        metadata={"oembed": True},
    )


def _extract_threads(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract Threads content – handles nested/chained threads.

    Threads posts can be chained (multiple posts in a thread due to character limits).
    We attempt to extract the full thread by following reply chains.
    """
    resp = client.get(url)
    html = resp.text

    meta = _extract_meta(html, [
        "og:title", "og:description", "og:image",
        "twitter:title", "twitter:description",
    ])

    # Try JSON-LD for structured data
    json_ld = _extract_json_ld(html)
    text_parts: list[str] = []
    author = ""
    images: list[str] = []

    # Extract from JSON-LD
    for item in json_ld:
        if item.get("@type") in ("SocialMediaPosting", "Article", "BlogPosting"):
            body = item.get("articleBody") or item.get("text") or ""
            if body:
                text_parts.append(body)
            auth = item.get("author", {})
            if isinstance(auth, dict):
                author = auth.get("name", "") or auth.get("identifier", "")
            img = item.get("image")
            if isinstance(img, str):
                images.append(img)
            elif isinstance(img, list):
                images.extend([i for i in img if isinstance(i, str)])

    # Also grab from meta tags
    desc = meta.get("og:description") or meta.get("twitter:description", "")
    if desc and desc not in text_parts:
        text_parts.append(desc)

    og_img = meta.get("og:image", "")
    if og_img and og_img not in images:
        images.append(og_img)

    title = meta.get("og:title") or meta.get("twitter:title", "")
    if not author and title:
        # Title is often "@username on Threads"
        m = re.match(r"@([\w.]+)", title)
        if m:
            author = f"@{m.group(1)}"

    # --- Handle nested / chained threads ---
    # Look for thread reply links in the page
    thread_links = re.findall(
        r'href="(/(?:@[\w.]+/post/[\w-]+|t/[\w-]+))"',
        html, re.I,
    )
    seen_urls = {url}
    for link in thread_links[:10]:  # Max 10 chained posts
        full_url = f"https://www.threads.net{link}"
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        try:
            child_resp = client.get(full_url)
            child_meta = _extract_meta(child_resp.text, ["og:description"])
            child_desc = child_meta.get("og:description", "")
            if child_desc and child_desc not in text_parts:
                text_parts.append(child_desc)

            child_json_ld = _extract_json_ld(child_resp.text)
            for item in child_json_ld:
                body = item.get("articleBody") or item.get("text") or ""
                if body and body not in text_parts:
                    text_parts.append(body)
                child_img = item.get("image")
                if isinstance(child_img, str) and child_img not in images:
                    images.append(child_img)
        except Exception:
            continue

    text = "\n\n".join(text_parts) if text_parts else desc

    return ExtractedContent(
        url=url,
        platform="threads",
        title=title,
        text=text or "Threads-Inhalt konnte nicht extrahiert werden.",
        author=author,
        images=images[:10],
    )


def _extract_instagram(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract Instagram post content.

    Instagram only serves OG meta tags to recognised bot user-agents.
    We use a Twitterbot UA for the meta-tag request, then fall back to
    the regular client for oembed / generic scraping.
    """
    # ── Strategy 1: fetch with bot UA to get OG tags ────────────────
    try:
        bot_headers = {
            "User-Agent": "Twitterbot/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        with httpx.Client(
            headers=bot_headers, follow_redirects=True, timeout=20.0,
        ) as bot_client:
            resp = bot_client.get(url)
        if resp.status_code == 200:
            html = resp.text
            meta = _extract_meta(html, [
                "og:title", "og:description", "og:image",
                "og:url", "description",
            ])

            og_desc = meta.get("og:description") or meta.get("description", "")

            # Parse author from og:description pattern:
            # "N likes, M comments - USERNAME on DATE: "CAPTION""
            author = ""
            caption = og_desc
            author_match = re.search(
                r"[\d,.]+ likes?,\s*[\d,.]+ comments?\s*-\s*(\S+)\s+on\s+.+?:\s*\"(.+)\"",
                og_desc,
                re.DOTALL,
            )
            if author_match:
                author = author_match.group(1)
                caption = author_match.group(2).strip().rstrip('"').strip()

            # Also try extracting author from og:url
            if not author:
                url_match = re.search(r"instagram\.com/([^/]+)/p/", meta.get("og:url", ""))
                if url_match:
                    author = url_match.group(1)

            images: list[str] = []
            if meta.get("og:image"):
                images.append(meta["og:image"])

            if caption and caption != og_desc:
                text = caption
            elif og_desc:
                text = og_desc
            else:
                text = ""

            if text:
                return ExtractedContent(
                    url=url,
                    platform="instagram",
                    title=meta.get("og:title", f"Instagram Post von @{author}" if author else "Instagram Post"),
                    text=text,
                    author=f"@{author}" if author else "",
                    images=images,
                )
    except Exception:
        pass

    # ── Strategy 2: oEmbed API ──────────────────────────────────────
    try:
        oembed_url = f"https://api.instagram.com/oembed/?url={url}"
        oembed_resp = client.get(oembed_url)
        if oembed_resp.status_code == 200:
            data = oembed_resp.json()
            title = data.get("title", "")
            author = data.get("author_name", "")
            images = []
            if data.get("thumbnail_url"):
                images.append(data["thumbnail_url"])

            return ExtractedContent(
                url=url,
                platform="instagram",
                title=f"Instagram Post von @{author}" if author else "Instagram Post",
                text=title,
                author=f"@{author}" if author else "",
                images=images,
            )
    except Exception:
        pass

    # ── Strategy 3: scrape meta tags with normal UA ─────────────────
    resp = client.get(url)
    html = resp.text
    meta = _extract_meta(html, [
        "og:title", "og:description", "og:image",
        "twitter:description",
    ])

    text = meta.get("og:description") or meta.get("twitter:description", "")
    images = []
    if meta.get("og:image"):
        images.append(meta["og:image"])

    return ExtractedContent(
        url=url,
        platform="instagram",
        title=meta.get("og:title", "Instagram Post"),
        text=text or "Instagram-Inhalt konnte nicht extrahiert werden.",
        images=images,
    )


def _extract_facebook(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract Facebook post content via meta tags."""
    resp = client.get(url)
    html = resp.text

    meta = _extract_meta(html, [
        "og:title", "og:description", "og:image",
        "twitter:description", "description",
    ])

    # Try JSON-LD
    json_ld = _extract_json_ld(html)
    text_parts: list[str] = []
    author = ""

    for item in json_ld:
        body = item.get("articleBody") or item.get("text") or item.get("description") or ""
        if body:
            text_parts.append(body)
        auth = item.get("author", {})
        if isinstance(auth, dict):
            author = auth.get("name", "")
        elif isinstance(auth, str):
            author = auth

    desc = meta.get("og:description") or meta.get("twitter:description") or meta.get("description", "")
    if desc and desc not in text_parts:
        text_parts.append(desc)

    images = []
    if meta.get("og:image"):
        images.append(meta["og:image"])

    return ExtractedContent(
        url=url,
        platform="facebook",
        title=meta.get("og:title", "Facebook Post"),
        text="\n\n".join(text_parts) or "Facebook-Inhalt konnte nicht extrahiert werden.",
        author=author,
        images=images,
    )


def _extract_youtube(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract YouTube video info: title, description, transcript if available."""
    match = re.search(r"(?:v=|youtu\.be/)([\w-]+)", url)
    video_id = match.group(1) if match else ""

    # Get oembed data
    title = ""
    author = ""
    images = []

    try:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        oembed_resp = client.get(oembed_url)
        if oembed_resp.status_code == 200:
            data = oembed_resp.json()
            title = data.get("title", "")
            author = data.get("author_name", "")
    except Exception:
        pass

    # Thumbnail
    if video_id:
        images.append(f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg")

    # Scrape the watch page for description
    description = ""
    try:
        resp = client.get(url)
        html = resp.text

        # Description is in the meta tag
        meta = _extract_meta(html, ["og:description", "og:title", "og:image"])
        description = meta.get("og:description", "")
        if not title:
            title = meta.get("og:title", "")

        # Try to find fuller description in page data
        # YouTube stores data in ytInitialData or ytInitialPlayerResponse
        yt_match = re.search(
            r"var\s+ytInitialPlayerResponse\s*=\s*(\{.*?\});\s*</script>",
            html,
            re.S,
        )
        if yt_match:
            try:
                yt_data = json.loads(yt_match.group(1))
                video_details = yt_data.get("videoDetails", {})
                full_desc = video_details.get("shortDescription", "")
                if full_desc and len(full_desc) > len(description):
                    description = full_desc
                if not title:
                    title = video_details.get("title", "")
                if not author:
                    author = video_details.get("author", "")
            except (json.JSONDecodeError, ValueError):
                pass

        # Try to get auto-generated transcript / captions
        captions_text = _extract_youtube_captions(html, client, video_id)
        if captions_text:
            description += f"\n\n[Transkript]:\n{captions_text}"

    except Exception:
        pass

    return ExtractedContent(
        url=url,
        platform="youtube",
        title=title or "YouTube Video",
        text=description or "YouTube-Beschreibung konnte nicht extrahiert werden.",
        author=author,
        images=images,
        metadata={"video_id": video_id},
    )


def _extract_youtube_captions(html: str, client: httpx.Client, video_id: str) -> str:
    """Try to extract YouTube auto-captions/subtitles."""
    try:
        # Find captions URL in player response
        cap_match = re.search(
            r'"captionTracks":\s*(\[.*?\])',
            html,
            re.S,
        )
        if not cap_match:
            return ""

        tracks = json.loads(cap_match.group(1))
        # Prefer German, then English
        caption_url = ""
        for lang_pref in ["de", "en"]:
            for track in tracks:
                if track.get("languageCode", "").startswith(lang_pref):
                    caption_url = track.get("baseUrl", "")
                    break
            if caption_url:
                break

        if not caption_url and tracks:
            caption_url = tracks[0].get("baseUrl", "")

        if not caption_url:
            return ""

        # Fetch caption XML
        cap_resp = client.get(caption_url)
        if cap_resp.status_code != 200:
            return ""

        # Parse XML captions
        texts = re.findall(r"<text[^>]*>(.*?)</text>", cap_resp.text, re.S)
        if not texts:
            return ""

        lines = [_html_unescape(_strip_html_tags(t)) for t in texts]
        return " ".join(lines)[:5000]  # Cap transcript length

    except Exception:
        return ""


def _extract_article(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract content from a generic news article / web page."""
    resp = client.get(url)
    html = resp.text

    meta = _extract_meta(html, [
        "og:title", "og:description", "og:image", "og:site_name",
        "article:author", "author", "twitter:title", "twitter:description",
    ])

    title = meta.get("og:title") or meta.get("twitter:title", "")
    if not title:
        # Try <title> tag
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if title_match:
            title = _html_unescape(_strip_html_tags(title_match.group(1)))

    # Extract article text
    text = _extract_article_text(html)

    # Get author
    author = meta.get("article:author") or meta.get("author", "")
    if not author:
        # Try JSON-LD
        for item in _extract_json_ld(html):
            auth = item.get("author")
            if isinstance(auth, dict):
                author = auth.get("name", "")
            elif isinstance(auth, str):
                author = auth
            if author:
                break

    images = _extract_images_from_html(html)
    site_name = meta.get("og:site_name", "")

    return ExtractedContent(
        url=url,
        platform="article",
        title=title,
        text=text or meta.get("og:description", "Artikelinhalt konnte nicht extrahiert werden."),
        author=author,
        images=images,
        metadata={"site_name": site_name} if site_name else {},
    )


# ── Main Extractor Class ────────────────────────────────────────

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
