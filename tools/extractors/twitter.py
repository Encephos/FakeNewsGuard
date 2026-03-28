"""Twitter/X content extraction."""

from __future__ import annotations

import re

import httpx

from tools.extractors.html_helpers import _strip_html_tags
from tools.extractors.models import ExtractedContent


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
    # Remove trailing "-- Author (@handle) Date" line
    text = re.sub(r"\s*—\s*.+$", "", text, flags=re.M).strip()

    return ExtractedContent(
        url=url,
        platform="twitter",
        title="Tweet",
        text=text,
        author=data.get("author_name", ""),
        metadata={"oembed": True},
    )
