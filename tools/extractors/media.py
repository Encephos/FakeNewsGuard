"""Media ingestion utilities: download, transcribe, keyframes, OCR.

All functions degrade gracefully when optional dependencies are missing.
Each function returns None or [] if its dependency is unavailable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from config.processing import MediaIngestionConfig
from tools.extractors.models import FrameOCR, TranscriptSegment

logger = logging.getLogger(__name__)


# ── Internal data ─────────────────────────────────────────────────────────────


@dataclass
class MediaDownload:
    """Result of a yt-dlp media download."""

    audio_path: str | None = None
    video_path: str | None = None
    temp_dir: str = ""
    duration: float = 0.0
    format_info: dict = field(default_factory=dict)


# ── Dependency checks ─────────────────────────────────────────────────────────


def _has_yt_dlp() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _has_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _has_scenedetect() -> bool:
    try:
        import scenedetect  # noqa: F401
        return True
    except ImportError:
        return False


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _has_paddleocr() -> bool:
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        return True
    except ImportError:
        return False


def _has_tesseract() -> bool:
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def _has_imagehash() -> bool:
    try:
        import imagehash  # noqa: F401
        return True
    except ImportError:
        return False


# ── Download ──────────────────────────────────────────────────────────────────


def download_media(
    url: str,
    config: MediaIngestionConfig,
    *,
    audio_only: bool = False,
) -> MediaDownload | None:
    """Download media from URL via yt-dlp.

    Returns None if yt-dlp is not available or download fails.
    Creates a temporary directory that must be cleaned up via cleanup_media().
    """
    if not _has_yt_dlp():
        logger.info("yt-dlp nicht installiert – Media-Download uebersprungen")
        return None

    import yt_dlp

    tmp = tempfile.mkdtemp(prefix="fng_media_", dir=config.temp_dir or None)

    try:
        # Base options: safety-first
        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "match_filter": yt_dlp.utils.match_filter_func(
                f"duration <= {config.max_duration_seconds}"
            ),
            "max_filesize": config.max_file_size_mb * 1024 * 1024,
            # Trust boundary: keine externen Programme ausfuehren
            "no_exec": True,
        }

        # Cookie-Datei fuer YouTube Bot-Detection Bypass
        if config.yt_cookies_file and os.path.isfile(config.yt_cookies_file):
            ydl_opts["cookiefile"] = config.yt_cookies_file
        # Proxy fuer YouTube
        if config.yt_proxy:
            ydl_opts["proxy"] = config.yt_proxy

        if audio_only:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "0",
                }],
                "outtmpl": os.path.join(tmp, "audio.%(ext)s"),
            })
        else:
            ydl_opts.update({
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "outtmpl": os.path.join(tmp, "video.%(ext)s"),
                "merge_output_format": "mp4",
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                logger.warning("yt-dlp: Keine Info fuer %s", url)
                shutil.rmtree(tmp, ignore_errors=True)
                return None

        duration = info.get("duration", 0.0) or 0.0

        # Find downloaded file(s)
        audio_path = None
        video_path = None
        for f in Path(tmp).iterdir():
            suffix = f.suffix.lower()
            if suffix in (".wav", ".m4a", ".opus", ".mp3", ".ogg", ".webm"):
                audio_path = str(f)
            elif suffix in (".mp4", ".mkv", ".avi"):
                video_path = str(f)

        return MediaDownload(
            audio_path=audio_path,
            video_path=video_path,
            temp_dir=tmp,
            duration=duration,
            format_info={
                "title": info.get("title", ""),
                "uploader": info.get("uploader", ""),
                "ext": info.get("ext", ""),
            },
        )

    except Exception as exc:
        logger.warning("Media-Download fehlgeschlagen fuer %s: %s", url, exc)
        shutil.rmtree(tmp, ignore_errors=True)
        return None


def cleanup_media(download: MediaDownload) -> None:
    """Remove temporary files created by download_media()."""
    if download.temp_dir and os.path.isdir(download.temp_dir):
        shutil.rmtree(download.temp_dir, ignore_errors=True)


# ── Transcription ─────────────────────────────────────────────────────────────


def transcribe_audio(
    audio_path: str,
    config: MediaIngestionConfig,
) -> tuple[list[TranscriptSegment], str]:
    """Transcribe audio file using faster-whisper.

    Returns (segments, detected_language). Empty list if faster-whisper
    is not available or transcription fails.
    """
    if not _has_faster_whisper():
        logger.info("faster-whisper nicht installiert – Transkription uebersprungen")
        return [], ""

    if not audio_path or not os.path.isfile(audio_path):
        return [], ""

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
        )

        segments_iter, info = model.transcribe(
            audio_path,
            vad_filter=True,       # Filtert Stille / Halluzinationen
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        segments: list[TranscriptSegment] = []
        for seg in segments_iter:
            segments.append(TranscriptSegment(
                start=round(seg.start, 2),
                end=round(seg.end, 2),
                text=seg.text.strip(),
            ))

        language = info.language if info else ""
        logger.info(
            "Transkription abgeschlossen: %d Segmente, Sprache=%s",
            len(segments), language,
        )
        return segments, language

    except Exception as exc:
        logger.warning("Transkription fehlgeschlagen: %s", exc)
        return [], ""


# ── Keyframe extraction ──────────────────────────────────────────────────────


def extract_keyframes(
    video_path: str,
    config: MediaIngestionConfig,
) -> list[str]:
    """Extract keyframes from a video file.

    Uses scenedetect if available, falls back to uniform interval sampling
    with OpenCV. Returns list of image file paths.
    """
    if not video_path or not os.path.isfile(video_path):
        return []

    if not config.keyframe_extraction:
        return []

    tmp_dir = os.path.dirname(video_path)
    frames_dir = os.path.join(tmp_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    frame_paths: list[str] = []

    if _has_scenedetect():
        frame_paths = _keyframes_scenedetect(video_path, frames_dir, config)
    elif _has_cv2():
        frame_paths = _keyframes_interval(video_path, frames_dir, config)
    else:
        logger.info("Weder scenedetect noch OpenCV installiert – Keyframes uebersprungen")
        return []

    # Deduplizieren via imagehash
    if frame_paths and _has_imagehash():
        frame_paths = _dedup_frames(frame_paths, config.frame_dedup_threshold)

    # Cap bei max_keyframes
    return frame_paths[: config.max_keyframes]


def _keyframes_scenedetect(
    video_path: str, frames_dir: str, config: MediaIngestionConfig,
) -> list[str]:
    """Extract keyframes at scene boundaries using PySceneDetect."""
    try:
        from scenedetect import ContentDetector, open_video, SceneManager

        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0))
        scene_manager.detect_scenes(video)
        scene_list = scene_manager.get_scene_list()

        if not scene_list:
            # Kein Szenenwechsel erkannt → Fallback auf Intervall
            if _has_cv2():
                return _keyframes_interval(video_path, frames_dir, config)
            return []

        # Extrahiere Frame am Anfang jeder Szene
        frame_paths: list[str] = []
        import cv2

        cap = cv2.VideoCapture(video_path)
        try:
            for i, (start, _end) in enumerate(scene_list):
                if i >= config.max_keyframes * 2:  # Vor-Dedup-Cap
                    break
                frame_num = start.get_frames()
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if ret:
                    path = os.path.join(frames_dir, f"scene_{i:04d}.jpg")
                    cv2.imwrite(path, frame)
                    frame_paths.append(path)
        finally:
            cap.release()

        return frame_paths

    except Exception as exc:
        logger.warning("scenedetect Keyframe-Extraktion fehlgeschlagen: %s", exc)
        if _has_cv2():
            return _keyframes_interval(video_path, frames_dir, config)
        return []


def _keyframes_interval(
    video_path: str, frames_dir: str, config: MediaIngestionConfig,
) -> list[str]:
    """Extract frames at uniform intervals using OpenCV."""
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0

        # Ziel: 1 Frame pro 10 Sekunden, max 2x max_keyframes (vor Dedup)
        target_count = min(max(int(duration / 10), 1), config.max_keyframes * 2)
        interval = max(total_frames // target_count, 1)

        frame_paths: list[str] = []
        try:
            for i in range(target_count):
                frame_num = i * interval
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if ret:
                    path = os.path.join(frames_dir, f"interval_{i:04d}.jpg")
                    cv2.imwrite(path, frame)
                    frame_paths.append(path)
        finally:
            cap.release()

        return frame_paths

    except Exception as exc:
        logger.warning("OpenCV Keyframe-Extraktion fehlgeschlagen: %s", exc)
        return []


def _dedup_frames(frame_paths: list[str], threshold: int) -> list[str]:
    """Remove near-duplicate frames using perceptual hashing."""
    try:
        import imagehash
        from PIL import Image

        seen_hashes: list[imagehash.ImageHash] = []
        unique_paths: list[str] = []

        for path in frame_paths:
            img = Image.open(path)
            h = imagehash.phash(img)
            is_dup = any((h - existing) < threshold for existing in seen_hashes)
            if not is_dup:
                seen_hashes.append(h)
                unique_paths.append(path)

        logger.info("Frame-Dedup: %d → %d Frames", len(frame_paths), len(unique_paths))
        return unique_paths

    except Exception as exc:
        logger.warning("Frame-Deduplizierung fehlgeschlagen: %s", exc)
        return frame_paths  # Bei Fehler alle Frames zurueckgeben


# ── OCR ───────────────────────────────────────────────────────────────────────


def ocr_frames(
    frame_paths: list[str],
    config: MediaIngestionConfig,
) -> list[FrameOCR]:
    """Run OCR on keyframe images. Returns list of FrameOCR results."""
    if not config.ocr_enabled or not frame_paths:
        return []

    if config.ocr_engine == "paddleocr" and _has_paddleocr():
        return _ocr_paddleocr(frame_paths, config)
    elif _has_tesseract():
        return _ocr_tesseract(frame_paths, config)
    elif _has_paddleocr():
        return _ocr_paddleocr(frame_paths, config)
    else:
        logger.info("Weder PaddleOCR noch Tesseract installiert – OCR uebersprungen")
        return []


def _ocr_paddleocr(
    frame_paths: list[str], config: MediaIngestionConfig,
) -> list[FrameOCR]:
    """OCR using PaddleOCR."""
    try:
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(use_angle_cls=True, lang="de", show_log=False)
        results: list[FrameOCR] = []

        for path in frame_paths:
            ocr_result = ocr.ocr(path, cls=True)
            texts: list[str] = []
            if ocr_result and ocr_result[0]:
                for line in ocr_result[0]:
                    if line and len(line) >= 2:
                        text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                        if text.strip():
                            texts.append(text.strip())

            if texts:
                # Timestamp aus Dateiname extrahieren (approx)
                timestamp = _timestamp_from_frame_path(path)
                image_hash = _compute_image_hash(path)
                results.append(FrameOCR(
                    timestamp=timestamp,
                    text=" | ".join(texts),
                    image_hash=image_hash,
                ))

        return results

    except Exception as exc:
        logger.warning("PaddleOCR fehlgeschlagen: %s", exc)
        return []


def _ocr_tesseract(
    frame_paths: list[str], config: MediaIngestionConfig,
) -> list[FrameOCR]:
    """OCR using Tesseract."""
    try:
        import pytesseract
        from PIL import Image

        results: list[FrameOCR] = []
        for path in frame_paths:
            img = Image.open(path)
            text = pytesseract.image_to_string(img, lang="deu+eng").strip()
            if text:
                timestamp = _timestamp_from_frame_path(path)
                image_hash = _compute_image_hash(path)
                results.append(FrameOCR(
                    timestamp=timestamp,
                    text=text,
                    image_hash=image_hash,
                ))

        return results

    except Exception as exc:
        logger.warning("Tesseract OCR fehlgeschlagen: %s", exc)
        return []


def ocr_image_url(
    url: str,
    config: MediaIngestionConfig,
) -> str:
    """Download a single image URL and run OCR. Returns extracted text or empty string."""
    if not config.ocr_enabled:
        return ""

    try:
        import httpx

        tmp_dir = tempfile.mkdtemp(prefix="fng_ocr_")
        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=True)
            if resp.status_code != 200:
                return ""

            # Bestimme Dateiendung
            content_type = resp.headers.get("content-type", "")
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"

            img_path = os.path.join(tmp_dir, f"image{ext}")
            with open(img_path, "wb") as f:
                f.write(resp.content)

            results = ocr_frames([img_path], config)
            return results[0].text if results else ""

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as exc:
        logger.warning("OCR fuer Bild-URL fehlgeschlagen (%s): %s", url, exc)
        return ""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _timestamp_from_frame_path(path: str) -> float:
    """Extract approximate timestamp from frame filename (e.g. scene_0003.jpg → 3.0)."""
    import re
    match = re.search(r"(\d+)\.\w+$", os.path.basename(path))
    return float(match.group(1)) if match else 0.0


def _compute_image_hash(path: str) -> str:
    """Compute perceptual hash for an image file."""
    if not _has_imagehash():
        return ""
    try:
        import imagehash
        from PIL import Image
        return str(imagehash.phash(Image.open(path)))
    except Exception:
        return ""


def format_transcript(segments: list[TranscriptSegment], max_chars: int = 8000) -> str:
    """Format transcript segments into a single text string."""
    if not segments:
        return ""
    full_text = " ".join(seg.text for seg in segments if seg.text)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "..."
    return full_text


def format_frame_ocr(frame_ocr_results: list[FrameOCR]) -> str:
    """Format OCR results from frames into a readable text."""
    if not frame_ocr_results:
        return ""
    # Dedupliziere aehnliche Texte
    seen_texts: set[str] = set()
    unique_lines: list[str] = []
    for ocr in frame_ocr_results:
        normalized = ocr.text.strip().lower()
        if normalized and normalized not in seen_texts:
            seen_texts.add(normalized)
            unique_lines.append(ocr.text.strip())
    return " | ".join(unique_lines)
