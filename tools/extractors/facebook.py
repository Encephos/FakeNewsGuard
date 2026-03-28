"""Facebook content extraction."""

from __future__ import annotations

import httpx

from tools.extractors.html_helpers import _extract_json_ld, _extract_meta
from tools.extractors.models import ExtractedContent


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
