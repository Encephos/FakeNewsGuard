"""Instagram content extraction (posts + reels with media ingestion)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

from config.infrastructure import HTTPTimeoutsConfig
from tools.extractors.html_helpers import _extract_meta, _extract_meta_all
from tools.extractors.models import ExtractedContent, MediaContent

_SCRAPE_TIMEOUT = HTTPTimeoutsConfig().scrape

if TYPE_CHECKING:
    from config.processing import MediaIngestionConfig

logger = logging.getLogger(__name__)


def _is_reel_url(url: str) -> bool:
    """Check if URL points to an Instagram Reel."""
    return bool(re.search(r"instagram\.com/reel/", url, re.I))


def _media_label(is_reel: bool, is_video: bool) -> str:
    """Return display label: Reel / Video / Post."""
    if is_reel:
        return "Reel"
    if is_video:
        return "Video"
    return "Post"


def _extract_instagram(
    url: str,
    client: httpx.Client,
    *,
    media_config: MediaIngestionConfig | None = None,
) -> ExtractedContent:
    """Extract Instagram post/reel content.

    Instagram only serves OG meta tags to recognised bot user-agents.
    We use a Twitterbot UA for the meta-tag request, then fall back to
    the regular client for oembed / generic scraping.

    When media_config is provided and the URL is a Reel, also runs
    audio transcription and keyframe OCR via yt-dlp + faster-whisper.
    For regular posts, runs OCR on extracted images.
    """
    is_reel = _is_reel_url(url)
    is_video = is_reel  # May be upgraded by content inspection for /p/ URLs

    # ── Metadata extraction (existing logic) ──────────────────────────────
    title = ""
    author = ""
    text = ""
    images: list[str] = []

    # -- Strategy 1: fetch with bot UA to get OG tags ----------------------
    try:
        bot_headers = {
            "User-Agent": "Twitterbot/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        with httpx.Client(
            headers=bot_headers, follow_redirects=True, timeout=_SCRAPE_TIMEOUT,
        ) as bot_client:
            resp = bot_client.get(url)
        if resp.status_code == 200:
            html = resp.text
            meta = _extract_meta(html, [
                "og:title", "og:description", "og:image",
                "og:url", "og:video", "og:type", "description",
            ])

            og_desc = meta.get("og:description") or meta.get("description", "")

            # Parse author from og:description pattern:
            # "N likes, M comments - USERNAME on DATE: "CAPTION""
            author_match = re.search(
                r"[\d,.]+ likes?,\s*[\d,.]+ comments?\s*-\s*(\S+)\s+on\s+.+?:\s*\"(.+)\"",
                og_desc,
                re.DOTALL,
            )
            if author_match:
                author = author_match.group(1)
                text = author_match.group(2).strip().rstrip('"').strip()

            # Also try extracting author from og:url
            if not author:
                url_match = re.search(r"instagram\.com/([^/]+)/(?:p|reel)/", meta.get("og:url", url))
                if url_match:
                    author = url_match.group(1)

            # Carousel: alle og:image Tags extrahieren
            all_og_images = _extract_meta_all(html, "og:image")
            for img in all_og_images:
                if img not in images:
                    images.append(img)

            # Video-Erkennung aus OG-Tags (/p/ URL kann Video sein)
            if not is_video:
                if meta.get("og:video") or meta.get("og:type") == "video.other":
                    is_video = True

            if not text:
                text = og_desc

            if text:
                label = _media_label(is_reel, is_video)
                # Immer strukturierten Titel verwenden — og:title enthält
                # die volle Caption und dupliziert den text-Inhalt.
                title = f"Instagram {label} von @{author}" if author else f"Instagram {label}"
    except Exception:
        pass

    # -- Strategy 2: oEmbed API --------------------------------------------
    if not text:
        try:
            oembed_url = f"https://api.instagram.com/oembed/?url={url}"
            oembed_resp = client.get(oembed_url)
            if oembed_resp.status_code == 200:
                data = oembed_resp.json()
                text = data.get("title", "")
                if not author:
                    author = data.get("author_name", "")
                if data.get("thumbnail_url"):
                    images.append(data["thumbnail_url"])
                if not is_video and data.get("type") == "video":
                    is_video = True
                label = _media_label(is_reel, is_video)
                title = f"Instagram {label} von @{author}" if author else f"Instagram {label}"
        except Exception:
            pass

    # -- Strategy 3: scrape meta tags with normal UA -----------------------
    if not text:
        try:
            resp = client.get(url)
            html = resp.text
            meta = _extract_meta(html, [
                "og:title", "og:description", "og:image",
                "twitter:description",
            ])

            text = meta.get("og:description") or meta.get("twitter:description", "")
            if not title:
                title = f"Instagram {_media_label(is_reel, is_video)}"
            if meta.get("og:image") and meta["og:image"] not in images:
                images.append(meta["og:image"])
        except Exception:
            pass

    # ── Media Ingestion ───────────────────────────────────────────────────
    media_content = None
    media_transcript = ""
    media_frame_ocr = ""
    media_image_ocr = ""

    if is_video and not is_reel:
        logger.debug("Detected video content at non-reel URL: %s", url)

    if media_config and media_config.enabled:
        if is_video:
            media_content, media_transcript, media_frame_ocr = _media_ingest_reel(
                url, media_config,
            )
        elif images and media_config.ocr_enabled:
            media_image_ocr = _ocr_post_images(images, media_config)

    # ── Text zusammenfuehren ──────────────────────────────────────────────
    if not text:
        text = "Instagram-Inhalt konnte nicht extrahiert werden."

    if media_transcript:
        text += f"\n\n[Transkript]:\n{media_transcript}"

    if media_frame_ocr:
        text += f"\n\n[Text aus Videoframes]:\n{media_frame_ocr}"

    if media_image_ocr:
        text += f"\n\n[Text aus Bildern]:\n{media_image_ocr}"

    metadata: dict = {}
    if media_content:
        metadata["media"] = media_content.model_dump()

    return ExtractedContent(
        url=url,
        platform="instagram",
        title=title or f"Instagram {_media_label(is_reel, is_video)}",
        text=text,
        author=f"@{author}" if author else "",
        images=images,
        metadata=metadata,
    )


def _media_ingest_reel(
    url: str,
    config: MediaIngestionConfig,
) -> tuple[MediaContent | None, str, str]:
    """Run media ingestion on an Instagram Reel (audio + keyframes + OCR).

    Returns (MediaContent, transcript_text, frame_ocr_text).
    """
    from tools.extractors.media import (
        cleanup_media,
        download_media,
        extract_keyframes,
        format_frame_ocr,
        format_transcript,
        ocr_frames,
        transcribe_audio,
    )

    transcript_text = ""
    frame_ocr_text = ""
    media_content = MediaContent(media_type="video")

    # ── Audio-Transkription ───────────────────────────────────────────────
    audio_download = download_media(url, config, audio_only=True)
    if audio_download and audio_download.audio_path:
        try:
            segments, language = transcribe_audio(audio_download.audio_path, config)
            if segments:
                media_content.transcript = segments
                media_content.language = language
                media_content.transcript_source = "whisper"
                media_content.duration_seconds = audio_download.duration
                transcript_text = format_transcript(segments)
        finally:
            cleanup_media(audio_download)

    # ── Keyframe-Extraktion + OCR ─────────────────────────────────────────
    if config.keyframe_extraction or config.ocr_enabled:
        video_download = download_media(url, config, audio_only=False)
        if video_download and video_download.video_path:
            try:
                frame_paths = extract_keyframes(video_download.video_path, config)
                if frame_paths:
                    ocr_results = ocr_frames(frame_paths, config)
                    if ocr_results:
                        media_content.frame_ocr = ocr_results
                        frame_ocr_text = format_frame_ocr(ocr_results)
            finally:
                cleanup_media(video_download)

    return media_content, transcript_text, frame_ocr_text


def _ocr_post_images(
    image_urls: list[str],
    config: MediaIngestionConfig,
) -> str:
    """Run OCR on Instagram post images. Returns combined OCR text."""
    from tools.extractors.media import ocr_image_url

    ocr_texts: list[str] = []
    for img_url in image_urls[:5]:  # Max 5 Bilder
        text = ocr_image_url(img_url, config)
        if text:
            ocr_texts.append(text)

    return " | ".join(ocr_texts) if ocr_texts else ""
