"""HTML parsing helpers shared across platform extractors."""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

try:
    from newspaper import Article as _Newspaper4kArticle  # type: ignore[import-untyped]
    _HAS_NEWSPAPER4K = True
except ImportError:
    _HAS_NEWSPAPER4K = False


# ── HTTP Client helpers ──────────────────────────────────────────

import httpx

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


# ── HTML Helpers (BeautifulSoup-basiert) ──────────────────────────

def _parse_soup(html: str) -> BeautifulSoup:
    """Parse HTML einmal und wiederverwendbar."""
    return BeautifulSoup(html, "html.parser")


def _extract_meta(html: str, properties: list[str]) -> dict[str, str]:
    """Extract OpenGraph / meta tag values from HTML via BeautifulSoup."""
    soup = _parse_soup(html)
    result: dict[str, str] = {}
    for prop in properties:
        tag = (
            soup.find("meta", attrs={"property": prop})
            or soup.find("meta", attrs={"name": prop})
        )
        if tag and tag.get("content"):
            result[prop] = tag["content"].strip()
    return result


def _extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Extract JSON-LD blocks from HTML via BeautifulSoup."""
    soup = _parse_soup(html)
    results: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


def _strip_html_tags(html: str) -> str:
    """Remove all HTML tags and return plain text via BeautifulSoup."""
    soup = _parse_soup(html)
    # Entferne script und style Elemente
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_article_text_with_date(html: str) -> tuple[str, str]:
    """Artikel-Text + Publikationsdatum extrahieren.

    Returns:
        (text, publication_date) -- publication_date ist "" wenn nicht gefunden.
    """
    pub_date = ""

    # -- Strategie 1: trafilatura (beste Qualitaet + Metadaten) ------
    if _HAS_TRAFILATURA:
        try:
            result = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
                output_format="python",  # gibt dict zurueck mit Metadaten
            )
            if isinstance(result, dict):
                text = result.get("text", "") or ""
                pub_date = result.get("date", "") or ""
                if text and len(text) > 100:
                    return text, pub_date
            elif result and len(result) > 100:
                # Aeltere trafilatura-Versionen geben String zurueck
                return result, pub_date
        except Exception:
            pass

        # Fallback: Metadaten separat extrahieren
        try:
            metadata = trafilatura.extract_metadata(html)
            if metadata and hasattr(metadata, "date"):
                pub_date = metadata.date or ""
        except Exception:
            pass

    # -- Strategie 2: Newspaper4k Fallback (robusteres Date-Parsing) --
    if not pub_date and _HAS_NEWSPAPER4K:
        try:
            article = _Newspaper4kArticle("", language="de")
            article.download(input_html=html)
            article.parse()
            if article.publish_date:
                pub_date = article.publish_date.isoformat()[:10]
        except Exception:
            pass

    return "", pub_date


def _extract_article_text(html: str) -> str:
    """Artikel-Text extrahieren: trafilatura -> BeautifulSoup Fallback.

    trafilatura nutzt ML-basierte Heuristiken fuer saubere Extraktion.
    Bei Fehler/fehlender Installation: BeautifulSoup-Fallback.
    """
    # -- Strategie 1: trafilatura (beste Qualitaet) ------------------
    if _HAS_TRAFILATURA:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            if text and len(text) > 100:
                return text
        except Exception:
            pass

    # -- Strategie 2: Newspaper4k (robuster Parser) ------------------
    if _HAS_NEWSPAPER4K:
        try:
            article = _Newspaper4kArticle("", language="de")
            article.download(input_html=html)
            article.parse()
            if article.text and len(article.text) > 100:
                return article.text
        except Exception:
            pass

    # -- Strategie 3: BeautifulSoup strukturierte Extraktion ---------
    soup = _parse_soup(html)

    # Entferne Noise-Elemente
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
        tag.decompose()

    # Versuch <article> Tag
    article = soup.find("article")
    if article:
        text = article.get_text(separator="\n", strip=True)
        if len(text) > 200:
            return text

    # Versuch typische Content-Container
    for selector in ["main", "[role='main']"]:
        container = soup.select_one(selector)
        if container:
            text = container.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    for class_pattern in ("article", "post", "entry", "content", "story"):
        container = soup.find(class_=re.compile(class_pattern, re.I))
        if container:
            text = container.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    # Fallback: Alle <p>-Tags zusammenfassen
    paragraphs = soup.find_all("p")
    if paragraphs:
        texts = [p.get_text(strip=True) for p in paragraphs]
        joined = "\n\n".join(t for t in texts if len(t) > 30)
        if joined:
            return joined

    # Letzter Fallback: gesamter Text
    return soup.get_text(separator="\n", strip=True)[:5000]


def _extract_images_from_html(html: str) -> list[str]:
    """Extract image URLs from og:image and <img> tags via BeautifulSoup."""
    soup = _parse_soup(html)
    images: list[str] = []

    # OG/Twitter images aus Meta-Tags
    for attr_name in ("og:image", "twitter:image"):
        tag = soup.find("meta", attrs={"property": attr_name}) or soup.find("meta", attrs={"name": attr_name})
        if tag and tag.get("content"):
            images.append(tag["content"])

    # Bilder aus <img> Tags (Noise filtern)
    _SKIP_PATTERNS = {"logo", "icon", "avatar", "pixel", "tracking", ".svg", "1x1"}
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if any(skip in src.lower() for skip in _SKIP_PATTERNS):
            continue
        if src not in images:
            images.append(src)

    return images[:10]
