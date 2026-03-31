"""Tests fuer tools/extractors/media.py – Media-Ingestion-Funktionen."""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from config.processing import MediaIngestionConfig
from tools.extractors.models import FrameOCR, MediaContent, TranscriptSegment


# ── MediaIngestionConfig Tests ────────────────────────────────────────────────


class TestMediaIngestionConfig:
    """Tests fuer MediaIngestionConfig Defaults und Env-Var-Overrides."""

    def test_defaults(self):
        cfg = MediaIngestionConfig()
        assert cfg.enabled is True
        assert cfg.whisper_model == "small"
        assert cfg.whisper_compute_type == "int8"
        assert cfg.whisper_device == "cpu"
        assert cfg.max_duration_seconds == 1800
        assert cfg.max_file_size_mb == 500
        assert cfg.max_keyframes == 20
        assert cfg.ocr_enabled is True
        assert cfg.ocr_engine == "paddleocr"
        assert cfg.keyframe_extraction is True
        assert cfg.frame_dedup_threshold == 8

    def test_env_var_overrides(self, monkeypatch):
        monkeypatch.setenv("MEDIA_INGESTION_ENABLED", "false")
        monkeypatch.setenv("WHISPER_MODEL", "tiny")
        monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
        monkeypatch.setenv("WHISPER_DEVICE", "cuda")
        monkeypatch.setenv("MEDIA_MAX_DURATION", "600")
        monkeypatch.setenv("MEDIA_MAX_FILE_SIZE_MB", "100")
        monkeypatch.setenv("MEDIA_MAX_KEYFRAMES", "10")
        monkeypatch.setenv("MEDIA_OCR_ENABLED", "no")
        monkeypatch.setenv("MEDIA_OCR_ENGINE", "tesseract")
        monkeypatch.setenv("MEDIA_KEYFRAME_EXTRACTION", "0")
        monkeypatch.setenv("MEDIA_FRAME_DEDUP_THRESHOLD", "12")

        cfg = MediaIngestionConfig()
        assert cfg.enabled is False
        assert cfg.whisper_model == "tiny"
        assert cfg.whisper_compute_type == "float16"
        assert cfg.whisper_device == "cuda"
        assert cfg.max_duration_seconds == 600
        assert cfg.max_file_size_mb == 100
        assert cfg.max_keyframes == 10
        assert cfg.ocr_enabled is False
        assert cfg.ocr_engine == "tesseract"
        assert cfg.keyframe_extraction is False
        assert cfg.frame_dedup_threshold == 12

    def test_empty_env_vars_keep_defaults(self, monkeypatch):
        """Leere Env-Vars sollten Defaults nicht ueberschreiben."""
        monkeypatch.setenv("WHISPER_MODEL", "")
        cfg = MediaIngestionConfig()
        assert cfg.whisper_model == "small"


# ── Model Tests ───────────────────────────────────────────────────────────────


class TestMediaModels:
    """Tests fuer TranscriptSegment, FrameOCR, MediaContent."""

    def test_transcript_segment(self):
        seg = TranscriptSegment(start=1.5, end=3.2, text="Hallo Welt")
        assert seg.start == 1.5
        assert seg.end == 3.2
        assert seg.text == "Hallo Welt"

    def test_frame_ocr(self):
        ocr = FrameOCR(timestamp=5.0, text="Breaking News", image_hash="abc123")
        assert ocr.timestamp == 5.0
        assert ocr.text == "Breaking News"
        assert ocr.image_hash == "abc123"

    def test_frame_ocr_default_hash(self):
        ocr = FrameOCR(timestamp=0.0, text="Test")
        assert ocr.image_hash == ""

    def test_media_content(self):
        mc = MediaContent(
            media_type="video",
            duration_seconds=120.5,
            language="de",
            transcript=[TranscriptSegment(start=0, end=5, text="Test")],
            frame_ocr=[FrameOCR(timestamp=10, text="OCR Text")],
            transcript_source="whisper",
        )
        assert mc.media_type == "video"
        assert mc.duration_seconds == 120.5
        assert len(mc.transcript) == 1
        assert len(mc.frame_ocr) == 1
        assert mc.transcript_source == "whisper"

    def test_media_content_defaults(self):
        mc = MediaContent(media_type="image")
        assert mc.duration_seconds == 0.0
        assert mc.language == ""
        assert mc.transcript == []
        assert mc.frame_ocr == []
        assert mc.transcript_source == ""

    def test_media_content_serialization(self):
        mc = MediaContent(
            media_type="video",
            transcript=[TranscriptSegment(start=0, end=1, text="Hi")],
        )
        d = mc.model_dump()
        assert d["media_type"] == "video"
        assert len(d["transcript"]) == 1
        assert d["transcript"][0]["text"] == "Hi"

        # Round-trip
        mc2 = MediaContent.model_validate(d)
        assert mc2 == mc


# ── Graceful Degradation Tests ────────────────────────────────────────────────


class TestGracefulDegradation:
    """Tests dass Funktionen graceful degradieren wenn Dependencies fehlen."""

    def test_download_media_without_ytdlp(self):
        from tools.extractors.media import download_media

        config = MediaIngestionConfig()
        with patch("tools.extractors.media._has_yt_dlp", return_value=False):
            result = download_media("https://youtube.com/watch?v=test", config)
        assert result is None

    def test_transcribe_without_faster_whisper(self):
        from tools.extractors.media import transcribe_audio

        config = MediaIngestionConfig()
        with patch("tools.extractors.media._has_faster_whisper", return_value=False):
            segments, lang = transcribe_audio("/fake/audio.wav", config)
        assert segments == []
        assert lang == ""

    def test_transcribe_missing_file(self):
        from tools.extractors.media import transcribe_audio

        config = MediaIngestionConfig()
        segments, lang = transcribe_audio("/nonexistent/audio.wav", config)
        assert segments == []
        assert lang == ""

    def test_extract_keyframes_without_cv2_and_scenedetect(self):
        from tools.extractors.media import extract_keyframes

        config = MediaIngestionConfig()
        with patch("tools.extractors.media._has_scenedetect", return_value=False), \
             patch("tools.extractors.media._has_cv2", return_value=False):
            result = extract_keyframes("/fake/video.mp4", config)
        assert result == []

    def test_extract_keyframes_disabled(self):
        from tools.extractors.media import extract_keyframes

        config = MediaIngestionConfig(keyframe_extraction=False)
        result = extract_keyframes("/fake/video.mp4", config)
        assert result == []

    def test_ocr_frames_without_engines(self):
        from tools.extractors.media import ocr_frames

        config = MediaIngestionConfig()
        with patch("tools.extractors.media._has_paddleocr", return_value=False), \
             patch("tools.extractors.media._has_tesseract", return_value=False):
            result = ocr_frames(["/fake/frame.jpg"], config)
        assert result == []

    def test_ocr_frames_disabled(self):
        from tools.extractors.media import ocr_frames

        config = MediaIngestionConfig(ocr_enabled=False)
        result = ocr_frames(["/fake/frame.jpg"], config)
        assert result == []

    def test_ocr_empty_list(self):
        from tools.extractors.media import ocr_frames

        config = MediaIngestionConfig()
        result = ocr_frames([], config)
        assert result == []


# ── Format Helpers Tests ──────────────────────────────────────────────────────


class TestFormatHelpers:
    """Tests fuer format_transcript und format_frame_ocr."""

    def test_format_transcript(self):
        from tools.extractors.media import format_transcript

        segments = [
            TranscriptSegment(start=0, end=2, text="Hallo"),
            TranscriptSegment(start=2, end=5, text="Welt"),
        ]
        result = format_transcript(segments)
        assert result == "Hallo Welt"

    def test_format_transcript_empty(self):
        from tools.extractors.media import format_transcript

        assert format_transcript([]) == ""

    def test_format_transcript_truncation(self):
        from tools.extractors.media import format_transcript

        segments = [TranscriptSegment(start=0, end=1, text="A" * 5000)]
        result = format_transcript(segments, max_chars=100)
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")

    def test_format_frame_ocr(self):
        from tools.extractors.media import format_frame_ocr

        ocr_results = [
            FrameOCR(timestamp=0, text="Breaking News"),
            FrameOCR(timestamp=5, text="Live aus Berlin"),
            FrameOCR(timestamp=10, text="breaking news"),  # Duplikat (case-insensitive)
        ]
        result = format_frame_ocr(ocr_results)
        assert "Breaking News" in result
        assert "Live aus Berlin" in result
        # Duplikat sollte nicht doppelt erscheinen
        assert result.count("Breaking News") == 1

    def test_format_frame_ocr_empty(self):
        from tools.extractors.media import format_frame_ocr

        assert format_frame_ocr([]) == ""


# ── Cleanup Tests ─────────────────────────────────────────────────────────────


class TestCleanup:
    """Tests fuer cleanup_media."""

    def test_cleanup_removes_temp_dir(self):
        from tools.extractors.media import MediaDownload, cleanup_media

        tmp = tempfile.mkdtemp(prefix="fng_test_")
        # Erstelle eine Datei darin
        with open(os.path.join(tmp, "test.txt"), "w") as f:
            f.write("test")

        download = MediaDownload(temp_dir=tmp)
        cleanup_media(download)
        assert not os.path.exists(tmp)

    def test_cleanup_nonexistent_dir(self):
        from tools.extractors.media import MediaDownload, cleanup_media

        download = MediaDownload(temp_dir="/nonexistent/dir")
        # Sollte keinen Fehler werfen
        cleanup_media(download)

    def test_cleanup_empty_temp_dir(self):
        from tools.extractors.media import MediaDownload, cleanup_media

        download = MediaDownload(temp_dir="")
        cleanup_media(download)  # Kein Fehler


# ── Download Mock Tests ───────────────────────────────────────────────────────


class TestDownloadMedia:
    """Tests fuer download_media mit gemocktem yt-dlp."""

    def test_download_without_ytdlp_returns_none(self):
        """download_media gibt None zurueck wenn yt-dlp nicht verfuegbar."""
        from tools.extractors.media import download_media

        config = MediaIngestionConfig()
        with patch("tools.extractors.media._has_yt_dlp", return_value=False):
            result = download_media("https://youtube.com/watch?v=test", config)
        assert result is None

    def test_media_download_dataclass(self):
        """MediaDownload Dataclass hat korrekte Defaults."""
        from tools.extractors.media import MediaDownload

        dl = MediaDownload()
        assert dl.audio_path is None
        assert dl.video_path is None
        assert dl.temp_dir == ""
        assert dl.duration == 0.0
        assert dl.format_info == {}

    def test_media_download_with_values(self):
        from tools.extractors.media import MediaDownload, cleanup_media

        tmp = tempfile.mkdtemp(prefix="fng_test_")
        dl = MediaDownload(
            audio_path=os.path.join(tmp, "audio.wav"),
            temp_dir=tmp,
            duration=30.0,
            format_info={"title": "Test"},
        )
        assert dl.duration == 30.0
        assert dl.format_info["title"] == "Test"
        cleanup_media(dl)
        assert not os.path.exists(tmp)


# ── Instagram Reel Detection Tests ────────────────────────────────────────────


class TestInstagramReelDetection:
    """Tests fuer _is_reel_url."""

    def test_reel_url(self):
        from tools.extractors.instagram import _is_reel_url

        assert _is_reel_url("https://www.instagram.com/reel/ABC123/") is True
        assert _is_reel_url("https://instagram.com/reel/XYZ/") is True

    def test_post_url(self):
        from tools.extractors.instagram import _is_reel_url

        assert _is_reel_url("https://www.instagram.com/p/ABC123/") is False

    def test_non_instagram_url(self):
        from tools.extractors.instagram import _is_reel_url

        assert _is_reel_url("https://youtube.com/watch?v=test") is False


# ── Integration: ExtractedContent with MediaContent ──────────────────────────


class TestExtractedContentWithMedia:
    """Tests dass MediaContent korrekt in ExtractedContent.metadata eingebettet wird."""

    def test_metadata_media_roundtrip(self):
        from tools.extractors.models import ExtractedContent

        mc = MediaContent(
            media_type="video",
            duration_seconds=60.0,
            language="de",
            transcript=[TranscriptSegment(start=0, end=5, text="Test")],
            transcript_source="whisper",
        )

        content = ExtractedContent(
            url="https://youtube.com/watch?v=test",
            platform="youtube",
            title="Test",
            text="Beschreibung\n\n[Transkript]:\nTest",
            metadata={"video_id": "test", "media": mc.model_dump()},
        )

        # Verify media can be recovered
        media_data = content.metadata.get("media")
        assert media_data is not None
        recovered = MediaContent.model_validate(media_data)
        assert recovered.media_type == "video"
        assert recovered.language == "de"
        assert len(recovered.transcript) == 1
        assert recovered.transcript_source == "whisper"


# ── Instagram Dynamic Video Detection + Carousel ─────────────────────────────


class TestInstagramVideoDetection:
    """Tests fuer dynamische Video-Erkennung und Carousel-Support."""

    def test_media_label_reel(self):
        from tools.extractors.instagram import _media_label

        assert _media_label(is_reel=True, is_video=True) == "Reel"

    def test_media_label_video_post(self):
        from tools.extractors.instagram import _media_label

        assert _media_label(is_reel=False, is_video=True) == "Video"

    def test_media_label_image_post(self):
        from tools.extractors.instagram import _media_label

        assert _media_label(is_reel=False, is_video=False) == "Post"

    def test_video_detection_from_og_video(self, monkeypatch):
        """A /p/ URL with og:video meta tag should be detected as video."""
        import httpx

        from tools.extractors.instagram import _extract_instagram

        html = (
            '<html><head>'
            '<meta property="og:description" content="5 likes, 1 comments - testuser on Jan 1: &quot;Caption text&quot;">'
            '<meta property="og:video" content="https://scontent.cdninstagram.com/v/video.mp4">'
            '<meta property="og:type" content="video.other">'
            '<meta property="og:image" content="https://scontent.cdninstagram.com/thumb.jpg">'
            '</head></html>'
        )

        def fake_get(self, url, **kwargs):
            resp = httpx.Response(200, text=html, request=httpx.Request("GET", url))
            return resp

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(httpx.Client, "__enter__", lambda self: self)
        monkeypatch.setattr(httpx.Client, "__exit__", lambda self, *a: None)

        result = _extract_instagram(
            "https://www.instagram.com/p/VIDEO123/",
            httpx.Client(),
        )
        assert "Video" in result.title
        assert "Reel" not in result.title

    def test_video_detection_from_oembed(self, monkeypatch):
        """oEmbed type=video should upgrade is_video for /p/ URLs."""
        import httpx

        from tools.extractors.instagram import _extract_instagram

        # Strategy 1 returns empty (no OG tags)
        empty_html = "<html><head></head></html>"
        oembed_data = {
            "title": "Ein Video Post",
            "author_name": "testuser",
            "thumbnail_url": "https://scontent.cdninstagram.com/thumb.jpg",
            "type": "video",
        }

        call_count = {"n": 0}

        def fake_get(self, url, **kwargs):
            call_count["n"] += 1
            if "oembed" in url:
                import json
                return httpx.Response(
                    200,
                    text=json.dumps(oembed_data),
                    request=httpx.Request("GET", url),
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(200, text=empty_html, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(httpx.Client, "__enter__", lambda self: self)
        monkeypatch.setattr(httpx.Client, "__exit__", lambda self, *a: None)

        result = _extract_instagram(
            "https://www.instagram.com/p/VIDEO456/",
            httpx.Client(),
        )
        assert "Video" in result.title

    def test_image_post_stays_post(self, monkeypatch):
        """A /p/ URL without video signals stays classified as Post."""
        import httpx

        from tools.extractors.instagram import _extract_instagram

        html = (
            '<html><head>'
            '<meta property="og:description" content="10 likes, 2 comments - fotouser on Feb 1: &quot;Schönes Bild&quot;">'
            '<meta property="og:type" content="instapp:photo">'
            '<meta property="og:image" content="https://scontent.cdninstagram.com/photo.jpg">'
            '</head></html>'
        )

        def fake_get(self, url, **kwargs):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(httpx.Client, "__enter__", lambda self: self)
        monkeypatch.setattr(httpx.Client, "__exit__", lambda self, *a: None)

        result = _extract_instagram(
            "https://www.instagram.com/p/PHOTO789/",
            httpx.Client(),
        )
        assert "Post" in result.title
        assert "Video" not in result.title
        assert "Reel" not in result.title


class TestInstagramCarousel:
    """Tests fuer Carousel/Multi-Image Extraktion."""

    def test_extract_meta_all_multiple_images(self):
        from tools.extractors.html_helpers import _extract_meta_all

        html = (
            '<html><head>'
            '<meta property="og:image" content="https://img1.jpg">'
            '<meta property="og:image" content="https://img2.jpg">'
            '<meta property="og:image" content="https://img3.jpg">'
            '</head></html>'
        )
        results = _extract_meta_all(html, "og:image")
        assert len(results) == 3
        assert results[0] == "https://img1.jpg"
        assert results[2] == "https://img3.jpg"

    def test_extract_meta_all_single_image(self):
        from tools.extractors.html_helpers import _extract_meta_all

        html = '<html><head><meta property="og:image" content="https://single.jpg"></head></html>'
        results = _extract_meta_all(html, "og:image")
        assert len(results) == 1

    def test_extract_meta_all_empty(self):
        from tools.extractors.html_helpers import _extract_meta_all

        html = "<html><head></head></html>"
        results = _extract_meta_all(html, "og:image")
        assert results == []

    def test_carousel_images_extracted(self, monkeypatch):
        """Multiple og:image tags in HTML should all end up in images list."""
        import httpx

        from tools.extractors.instagram import _extract_instagram

        html = (
            '<html><head>'
            '<meta property="og:description" content="3 likes, 0 comments - carouseluser on Mar 1: &quot;Mein Carousel&quot;">'
            '<meta property="og:image" content="https://scontent.cdninstagram.com/slide1.jpg">'
            '<meta property="og:image" content="https://scontent.cdninstagram.com/slide2.jpg">'
            '<meta property="og:image" content="https://scontent.cdninstagram.com/slide3.jpg">'
            '</head></html>'
        )

        def fake_get(self, url, **kwargs):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(httpx.Client, "__enter__", lambda self: self)
        monkeypatch.setattr(httpx.Client, "__exit__", lambda self, *a: None)

        result = _extract_instagram(
            "https://www.instagram.com/p/CAROUSEL123/",
            httpx.Client(),
        )
        assert len(result.images) == 3
        assert "slide1.jpg" in result.images[0]
        assert "slide3.jpg" in result.images[2]
