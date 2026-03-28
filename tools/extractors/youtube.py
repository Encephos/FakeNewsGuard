"""YouTube content extraction (+ captions)."""

from __future__ import annotations

import json
import re
from html import unescape as _html_unescape

import httpx

from tools.extractors.html_helpers import _extract_meta, _strip_html_tags
from tools.extractors.models import ExtractedContent


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
