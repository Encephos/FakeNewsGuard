"""Tests für tools/content_extractor.py."""

from __future__ import annotations

import pytest

from tools.content_extractor import (
    ContentExtractor,
    _extract_article_text,
    _extract_images_from_html,
    _extract_json_ld,
    _extract_meta,
    _strip_html_tags,
    detect_platform,
    extract_urls,
    is_url,
)


# ── detect_platform ──────────────────────────────────────────────


def test_detect_twitter():
    assert detect_platform("https://x.com/user/status/123456") == "twitter"
    assert detect_platform("https://twitter.com/user/status/123456") == "twitter"


def test_detect_youtube():
    assert detect_platform("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert detect_platform("https://youtu.be/abc123") == "youtube"
    assert detect_platform("https://www.youtube.com/shorts/06kuP0B53Lc") == "youtube"
    assert detect_platform("https://youtube.com/shorts/abc123") == "youtube"
    assert detect_platform("https://www.youtube.com/live/abc123") == "youtube"
    assert detect_platform("https://www.youtube.com/embed/abc123") == "youtube"


def test_detect_instagram():
    assert detect_platform("https://www.instagram.com/p/abc123/") == "instagram"
    assert detect_platform("https://www.instagram.com/reel/abc123/") == "instagram"


def test_detect_threads():
    assert detect_platform("https://www.threads.net/@user/post/abc-123") == "threads"


def test_detect_facebook():
    assert detect_platform("https://www.facebook.com/some/post") == "facebook"


def test_detect_article_fallback():
    assert detect_platform("https://www.tagesschau.de/inland/news") == "article"
    assert detect_platform("https://example.com") == "article"


# ── extract_urls / is_url ────────────────────────────────────────


def test_extract_urls_from_text():
    text = "Schau auf https://example.com und http://test.org/page"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://example.com" in urls[0]


def test_is_url_positive():
    assert is_url("https://example.com") is True
    assert is_url("  http://test.org  ") is True


def test_is_url_negative():
    assert is_url("kein link hier") is False
    assert is_url("") is False


# ── _extract_meta (BeautifulSoup) ────────────────────────────────


def test_extract_meta_og_tags():
    html = '<html><head><meta property="og:title" content="Test Title"></head></html>'
    result = _extract_meta(html, ["og:title"])
    assert result["og:title"] == "Test Title"


def test_extract_meta_name_attr():
    html = '<html><head><meta name="author" content="Max Mustermann"></head></html>'
    result = _extract_meta(html, ["author"])
    assert result["author"] == "Max Mustermann"


def test_extract_meta_missing():
    html = "<html><head></head></html>"
    result = _extract_meta(html, ["og:title"])
    assert result == {}


# ── _extract_json_ld (BeautifulSoup) ─────────────────────────────


def test_extract_json_ld_valid():
    html = """
    <html><head>
    <script type="application/ld+json">{"@type": "Article", "author": {"name": "Test"}}</script>
    </head></html>
    """
    results = _extract_json_ld(html)
    assert len(results) == 1
    assert results[0]["@type"] == "Article"


def test_extract_json_ld_invalid_json():
    html = '<script type="application/ld+json">not valid json</script>'
    results = _extract_json_ld(html)
    assert results == []


def test_extract_json_ld_array():
    html = '<script type="application/ld+json">[{"@type":"A"},{"@type":"B"}]</script>'
    results = _extract_json_ld(html)
    assert len(results) == 2


# ── _strip_html_tags (BeautifulSoup) ─────────────────────────────


def test_strip_html_tags_basic():
    html = "<p>Hello <b>World</b></p>"
    result = _strip_html_tags(html)
    assert "Hello" in result
    assert "World" in result
    assert "<" not in result


def test_strip_html_tags_removes_scripts():
    html = "<p>Text</p><script>alert('xss')</script><p>More</p>"
    result = _strip_html_tags(html)
    assert "alert" not in result
    assert "Text" in result
    assert "More" in result


# ── _extract_article_text ────────────────────────────────────────


def test_extract_article_text_from_article_tag():
    html = """
    <html><body>
    <nav>Navigation</nav>
    <article><p>Dies ist der Artikeltext mit mehr als 200 Zeichen.
    Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
    tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam.</p></article>
    <footer>Footer</footer>
    </body></html>
    """
    text = _extract_article_text(html)
    assert "Artikeltext" in text
    assert "Navigation" not in text or len(text) > 100  # article tag bevorzugt


def test_extract_article_text_from_paragraphs():
    html = """
    <html><body>
    <p>Erster langer Absatz mit mehr als dreißig Zeichen Inhalt hier.</p>
    <p>Zweiter langer Absatz mit ebenfalls mehr als dreißig Zeichen.</p>
    </body></html>
    """
    text = _extract_article_text(html)
    assert "Erster" in text
    assert "Zweiter" in text


# ── _extract_images_from_html ────────────────────────────────────


def test_extract_images_og():
    html = '<html><head><meta property="og:image" content="https://img.example.com/photo.jpg"></head></html>'
    images = _extract_images_from_html(html)
    assert "https://img.example.com/photo.jpg" in images


def test_extract_images_filters_icons():
    html = """
    <html><body>
    <img src="https://example.com/logo.png">
    <img src="https://example.com/real-photo.jpg">
    <img src="https://example.com/tracking-pixel.gif">
    </body></html>
    """
    images = _extract_images_from_html(html)
    assert "https://example.com/real-photo.jpg" in images
    assert all("logo" not in img for img in images)
    assert all("tracking" not in img for img in images)


def test_extract_images_max_10():
    imgs = "".join(f'<img src="https://example.com/img{i}.jpg">' for i in range(20))
    html = f"<html><body>{imgs}</body></html>"
    images = _extract_images_from_html(html)
    assert len(images) <= 10


# ── ContentExtractor.format_for_analysis ─────────────────────────


def test_format_for_analysis():
    from tools.content_extractor import ExtractedContent

    content = ExtractedContent(
        url="https://example.com",
        platform="article",
        title="Test Artikel",
        text="Artikeltext hier",
        author="Max",
    )
    extractor = ContentExtractor()
    formatted = extractor.format_for_analysis(content)
    assert "Test Artikel" in formatted
    assert "Artikeltext hier" in formatted
    assert "Max" in formatted
