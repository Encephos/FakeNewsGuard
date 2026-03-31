"""YouTube content extraction (+ captions / media ingestion)."""

from __future__ import annotations

import json
import logging
import re
from html import unescape as _html_unescape
from typing import TYPE_CHECKING

import httpx

from config.infrastructure import HTTPTimeoutsConfig
from tools.extractors.html_helpers import _extract_meta, _strip_html_tags
from tools.extractors.models import ExtractedContent, MediaContent

_SOURCE_CLIENT_TIMEOUT = HTTPTimeoutsConfig().source_client

if TYPE_CHECKING:
    from config.processing import MediaIngestionConfig

logger = logging.getLogger(__name__)

# YouTube-Consent-Cookies um die EU-Cookie-Wall zu umgehen.
_YT_CONSENT_COOKIES = {
    "CONSENT": "YES+cb.20210720-07-p0.de+FX+634",
    "SOCS": "CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AwGgJkZSACGgYIgJnSmgY",
}

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|live/|embed/|v/)([\w-]+)")


def _extract_youtube(
    url: str,
    client: httpx.Client,
    *,
    media_config: MediaIngestionConfig | None = None,
) -> ExtractedContent:
    """Extract YouTube video info: title, description, transcript if available.

    Extraction strategy (in priority order):
    1. oEmbed API -> title, author (always works, no consent needed)
    2. Watch page HTML scraping -> description, caption tracks XML
    3. Media ingestion (yt-dlp + Whisper) -> transcript, keyframe OCR
    4. youtube-transcript-api -> captions (lightweight, Innertube API)
    5. yt-dlp subtitle extraction -> captions (fast, no Whisper needed)
    6. HTML caption fallback -> captions from page data
    """
    match = _VIDEO_ID_RE.search(url)
    video_id = match.group(1) if match else ""

    # ── Phase 1: oEmbed (reliable, no consent issues) ────────────────────
    title = ""
    author = ""
    images = []

    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
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

    # ── Phase 2: Watch page HTML scraping ────────────────────────────────
    # Always use canonical /watch?v= URL (shorts/embed pages lack player data).
    # Add hl=en to reduce consent page issues on EU servers.
    watch_url = (
        f"https://www.youtube.com/watch?v={video_id}&hl=en" if video_id else url
    )
    description = ""
    html = ""
    try:
        resp = client.get(watch_url, cookies=_YT_CONSENT_COOKIES)
        html = resp.text

        # Quick check: did we get the actual video page or consent wall?
        if "ytInitialPlayerResponse" in html or 'og:video' in html:
            meta = _extract_meta(html, ["og:description", "og:title", "og:image"])
            description = meta.get("og:description", "")
            if not title:
                title = meta.get("og:title", "")

            # Try to find fuller description in page data
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
        else:
            logger.warning(
                "YouTube watch page returned consent/redirect for %s – "
                "HTML scraping skipped",
                video_id,
            )
            html = ""  # Don't use consent page for caption extraction
    except Exception as exc:
        logger.warning("YouTube HTML scrape failed for %s: %s", video_id, exc)

    # ── Phase 3: Media Ingestion (primary transcript path) ───────────────
    media_content = None
    transcript_text = ""
    frame_ocr_text = ""

    if media_config and media_config.enabled:
        try:
            media_content, transcript_text, frame_ocr_text = _media_ingest_youtube(
                url, media_config,
            )
            if transcript_text:
                logger.info(
                    "Whisper transcript extracted for %s (%d chars)",
                    video_id, len(transcript_text),
                )
            else:
                logger.warning(
                    "Media ingestion returned no transcript for %s", video_id,
                )
        except Exception as exc:
            logger.warning("Media ingestion failed for %s: %s", url, exc)

    # ── Phase 4: youtube-transcript-api (lightweight, Innertube API) ─────
    if not transcript_text and video_id:
        logger.info("Phase 4: trying youtube-transcript-api for %s", video_id)
        captions_text = _extract_captions_via_transcript_api(
            video_id, media_config,
        )
        if captions_text:
            transcript_text = captions_text
            if media_content:
                media_content.transcript_source = "transcript-api"
            logger.info(
                "youtube-transcript-api used for %s (%d chars)",
                video_id, len(captions_text),
            )
        else:
            logger.warning(
                "youtube-transcript-api returned empty for %s", video_id,
            )

    # ── Phase 5: yt-dlp subtitle extraction (fast fallback) ──────────────
    if not transcript_text and video_id:
        try:
            captions_text = _extract_captions_via_ytdlp(url, media_config)
            if captions_text:
                transcript_text = captions_text
                if media_content:
                    media_content.transcript_source = "captions-ytdlp"
                logger.info("yt-dlp caption fallback used for %s", video_id)
        except Exception as exc:
            logger.debug("yt-dlp caption extraction failed for %s: %s", video_id, exc)

    # ── Phase 6: HTML caption fallback (least reliable) ──────────────────
    if not transcript_text and html:
        captions_text = _extract_youtube_captions(html, client, video_id)
        if captions_text:
            transcript_text = captions_text
            if media_content:
                media_content.transcript_source = "captions"
            logger.info("HTML caption fallback used for %s", video_id)
        else:
            logger.warning("No captions found via any method for %s", video_id)

    # ── Text zusammenfuehren ──────────────────────────────────────────────
    text = description or "YouTube-Beschreibung konnte nicht extrahiert werden."

    if transcript_text:
        text += f"\n\n[Transkript]:\n{transcript_text}"

    if frame_ocr_text:
        text += f"\n\n[Text aus Videoframes]:\n{frame_ocr_text}"

    metadata: dict = {"video_id": video_id}
    if media_content:
        metadata["media"] = media_content.model_dump()

    return ExtractedContent(
        url=url,
        platform="youtube",
        title=title or "YouTube Video",
        text=text,
        author=author,
        images=images,
        metadata=metadata,
    )


def _media_ingest_youtube(
    url: str,
    config: MediaIngestionConfig,
) -> tuple[MediaContent | None, str, str]:
    """Run full media ingestion pipeline for a YouTube video.

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
        logger.info("Audio downloaded: %s", audio_download.audio_path)
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
    else:
        logger.warning("Audio download returned None for %s", url)

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


def _extract_captions_via_transcript_api(
    video_id: str,
    media_config: MediaIngestionConfig | None = None,
) -> str:
    """Extract captions using youtube-transcript-api.

    This library uses YouTube's internal Innertube API. Supports cookie_path
    and proxy for cloud-server IP bypass.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api not installed – skipping")
        return ""

    try:
        # Build constructor kwargs from config
        api_kwargs: dict = {}
        if media_config:
            if media_config.yt_cookies_file:
                import os
                if os.path.isfile(media_config.yt_cookies_file):
                    api_kwargs["cookie_path"] = media_config.yt_cookies_file
                    logger.info(
                        "Using cookie file for transcript API: %s",
                        media_config.yt_cookies_file,
                    )
            if media_config.yt_proxy:
                api_kwargs["proxies"] = {
                    "https": media_config.yt_proxy,
                    "http": media_config.yt_proxy,
                }
                logger.info("Using proxy for transcript API: %s", media_config.yt_proxy)

        ytt_api = YouTubeTranscriptApi(**api_kwargs)

        # Try German first, then English, then any available language
        transcript = None
        for lang in ["de", "en"]:
            try:
                transcript = ytt_api.fetch(video_id, languages=[lang])
                logger.info("Transcript found for %s in language: %s", video_id, lang)
                break
            except Exception as lang_exc:
                logger.info("No %s transcript for %s: %s", lang, video_id, lang_exc)
                continue

        if transcript is None:
            # Try fetching any available transcript
            try:
                transcript = ytt_api.fetch(video_id)
                logger.info("Transcript found for %s (any language)", video_id)
            except Exception as any_exc:
                logger.warning(
                    "No transcript available for %s: %s", video_id, any_exc,
                )
                return ""

        # Build text from snippet list
        texts = [
            snippet.text.strip()
            for snippet in transcript.snippets
            if snippet.text.strip()
        ]
        result = " ".join(texts)[:5000]
        logger.info(
            "Transcript API extracted %d chars for %s", len(result), video_id,
        )
        return result

    except Exception as exc:
        logger.warning(
            "youtube-transcript-api error for %s: %s: %s",
            video_id, type(exc).__name__, exc,
        )
        return ""


def _extract_captions_via_ytdlp(
    url: str,
    media_config: MediaIngestionConfig | None = None,
) -> str:
    """Extract subtitles/captions using yt-dlp without downloading the video.

    Supports cookie file and proxy from config.
    """
    try:
        import yt_dlp
    except ImportError:
        return ""

    try:
        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["de", "en"],
            "no_exec": True,
        }

        # Cookie/Proxy from config
        if media_config:
            import os
            if media_config.yt_cookies_file and os.path.isfile(media_config.yt_cookies_file):
                ydl_opts["cookiefile"] = media_config.yt_cookies_file
            if media_config.yt_proxy:
                ydl_opts["proxy"] = media_config.yt_proxy

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return ""

            # Check for subtitles in extracted info
            for sub_key in ("subtitles", "automatic_captions"):
                subs = info.get(sub_key, {})
                if not subs:
                    continue

                # Prefer German, then English, then any available
                for lang in ["de", "en"]:
                    if lang in subs:
                        return _download_subtitle_text(subs[lang], lang)

                # Use first available language
                first_lang = next(iter(subs))
                return _download_subtitle_text(subs[first_lang], first_lang)

    except Exception as exc:
        logger.debug("yt-dlp subtitle extraction error: %s", exc)

    return ""


def _download_subtitle_text(formats: list[dict], lang: str) -> str:
    """Download and parse subtitle text from yt-dlp subtitle format list."""
    # Prefer srv1/srv2/srv3 (XML-based, easy to parse), then vtt, then json3
    preferred = ["srv1", "srv2", "srv3", "vtt", "json3"]

    # Sort formats by preference
    fmt_map = {f.get("ext", ""): f for f in formats}
    chosen = None
    for pref in preferred:
        if pref in fmt_map:
            chosen = fmt_map[pref]
            break
    if not chosen and formats:
        chosen = formats[0]
    if not chosen:
        return ""

    sub_url = chosen.get("url", "")
    if not sub_url:
        return ""

    try:
        resp = httpx.get(sub_url, timeout=_SOURCE_CLIENT_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return ""

        content = resp.text
        ext = chosen.get("ext", "")

        if ext in ("srv1", "srv2", "srv3"):
            texts = re.findall(r"<text[^>]*>(.*?)</text>", content, re.S)
            lines = [_html_unescape(_strip_html_tags(t)) for t in texts]
            return " ".join(lines)[:5000]

        if ext == "vtt":
            lines = []
            for line in content.split("\n"):
                line = line.strip()
                if not line or "-->" in line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:") or re.match(r"^\d+$", line):
                    continue
                clean = re.sub(r"<[^>]+>", "", line)
                if clean.strip():
                    lines.append(clean.strip())
            deduped = []
            for line in lines:
                if not deduped or line != deduped[-1]:
                    deduped.append(line)
            return " ".join(deduped)[:5000]

        if ext == "json3":
            try:
                data = json.loads(content)
                events = data.get("events", [])
                texts = []
                for event in events:
                    segs = event.get("segs", [])
                    for seg in segs:
                        t = seg.get("utf8", "").strip()
                        if t and t != "\n":
                            texts.append(t)
                return " ".join(texts)[:5000]
            except (json.JSONDecodeError, ValueError):
                return ""

        return ""

    except Exception as exc:
        logger.debug("Subtitle download failed for %s: %s", lang, exc)
        return ""


def _extract_youtube_captions(html: str, client: httpx.Client, video_id: str) -> str:
    """Try to extract YouTube auto-captions/subtitles from page HTML."""
    try:
        cap_match = re.search(
            r'"captionTracks":\s*(\[.*?\])',
            html,
            re.S,
        )
        if not cap_match:
            return ""

        tracks = json.loads(cap_match.group(1))
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

        cap_resp = client.get(caption_url)
        if cap_resp.status_code != 200:
            return ""

        texts = re.findall(r"<text[^>]*>(.*?)</text>", cap_resp.text, re.S)
        if not texts:
            return ""

        lines = [_html_unescape(_strip_html_tags(t)) for t in texts]
        return " ".join(lines)[:5000]

    except Exception:
        return ""
