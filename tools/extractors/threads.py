"""Threads content extraction."""

from __future__ import annotations

import re

import httpx

from tools.extractors.html_helpers import _extract_json_ld, _extract_meta
from tools.extractors.models import ExtractedContent


def _extract_threads(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract Threads content -- handles nested/chained threads.

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
