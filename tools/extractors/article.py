"""Generic article/web page content extraction."""

from __future__ import annotations

import httpx

from tools.extractors.html_helpers import (
    _extract_article_text,
    _extract_images_from_html,
    _extract_json_ld,
    _extract_meta,
    _parse_soup,
)
from tools.extractors.models import ExtractedContent


def _extract_article(url: str, client: httpx.Client) -> ExtractedContent:
    """Extract content from a generic news article / web page.

    Nutzt trafilatura fuer hochwertige Text-Extraktion und BeautifulSoup
    fuer Metadaten (Titel, Autor, Bilder).
    """
    resp = client.get(url)
    html = resp.text

    meta = _extract_meta(html, [
        "og:title", "og:description", "og:image", "og:site_name",
        "article:author", "author", "twitter:title", "twitter:description",
    ])

    # Titel: Meta-Tags -> <title> Tag
    title = meta.get("og:title") or meta.get("twitter:title", "")
    if not title:
        soup = _parse_soup(html)
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

    # Artikel-Text: trafilatura -> BeautifulSoup Fallback
    text = _extract_article_text(html)

    # Autor: Meta-Tags -> JSON-LD
    author = meta.get("article:author") or meta.get("author", "")
    if not author:
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
