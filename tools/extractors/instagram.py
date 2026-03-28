"""Instagram content extraction."""

from __future__ import annotations

import re

import httpx

from tools.extractors.html_helpers import _extract_meta
from tools.extractors.models import ExtractedContent


def _extract_instagram(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract Instagram post content.

    Instagram only serves OG meta tags to recognised bot user-agents.
    We use a Twitterbot UA for the meta-tag request, then fall back to
    the regular client for oembed / generic scraping.
    """
    # -- Strategy 1: fetch with bot UA to get OG tags ----------------
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

    # -- Strategy 2: oEmbed API --------------------------------------
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

    # -- Strategy 3: scrape meta tags with normal UA -----------------
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
