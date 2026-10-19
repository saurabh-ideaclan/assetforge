"""File-kind detection and technical metadata extraction (dimensions, duration, ...)."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assetforge.core.models import FileKind
from assetforge.errors import InvalidInputError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga", ".tif", ".tiff"}
VECTOR_EXTS = {".svg"}
AUDIO_EXTS = {".wav", ".ogg", ".oga", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".opus"}
FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2", ".fnt"}
SOURCE_EXTS = {".psd", ".aseprite", ".ase", ".kra", ".xcf", ".blend", ".mid", ".midi"}

_MIME_OVERRIDES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".webp": "image/webp",
    ".tga": "image/x-tga",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".aseprite": "application/x-aseprite",
    ".ase": "application/x-aseprite",
    ".psd": "image/vnd.adobe.photoshop",
}


@dataclass
class FileMeta:
    kind: FileKind
    mime_type: str
    width: int | None = None
    height: int | None = None
    has_alpha: bool | None = None
    frames: int | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def kind_for_ext(ext: str) -> FileKind:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return FileKind.IMAGE
    if ext in VECTOR_EXTS:
        return FileKind.VECTOR
    if ext in AUDIO_EXTS:
        return FileKind.AUDIO
    if ext in FONT_EXTS:
        return FileKind.FONT
    if ext in SOURCE_EXTS:
        return FileKind.SOURCE
    return FileKind.OTHER


def is_importable(ext: str) -> bool:
    return kind_for_ext(ext) is not FileKind.OTHER


def mime_for_ext(ext: str) -> str:
    ext = ext.lower()
    return _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(f"x{ext}")[0] or "application/octet-stream"


def extract_metadata(path: Path, ext: str | None = None) -> FileMeta:
    """Inspect a file. Raises InvalidInputError when an image or audio file cannot be decoded."""
    ext = (ext if ext is not None else path.suffix).lower()
    kind = kind_for_ext(ext)
    meta = FileMeta(kind=kind, mime_type=mime_for_ext(ext))
    if kind is FileKind.IMAGE:
        _read_image(path, meta)
    elif kind is FileKind.VECTOR:
        _read_svg(path, meta)
    elif kind is FileKind.AUDIO:
        _read_audio(path, meta)
    return meta


def _read_image(path: Path, meta: FileMeta) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            meta.width, meta.height = image.size
            meta.frames = getattr(image, "n_frames", 1)
            meta.extra["format"] = image.format
            meta.extra["mode"] = image.mode
            meta.has_alpha = _has_transparency(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidInputError(f"Cannot decode image {path.name}: {exc}", path=str(path)) from exc


def _has_transparency(image: Any) -> bool:
    """True only when some pixel is actually transparent, not merely when an alpha channel exists."""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255
    return False


_SVG_DIM = re.compile(r'\b(width|height)\s*=\s*"([\d.]+)(?:px)?"')
_SVG_VIEWBOX = re.compile(r'viewBox\s*=\s*"[\d.\-]+[\s,]+[\d.\-]+[\s,]+([\d.]+)[\s,]+([\d.]+)"')


def _read_svg(path: Path, meta: FileMeta) -> None:
    head = path.read_bytes()[:4096].decode("utf-8", errors="replace")
    if "<svg" not in head:
        raise InvalidInputError(f"{path.name} is not an SVG document", path=str(path))
    dims = dict(_SVG_DIM.findall(head))
    if "width" in dims and "height" in dims:
        meta.width, meta.height = round(float(dims["width"])), round(float(dims["height"]))
    elif match := _SVG_VIEWBOX.search(head):
        meta.width, meta.height = round(float(match.group(1))), round(float(match.group(2)))
    meta.has_alpha = True


def _read_audio(path: Path, meta: FileMeta) -> None:
    try:
        import soundfile

        info = soundfile.info(str(path))
        meta.sample_rate = int(info.samplerate)
        meta.channels = int(info.channels)
        meta.duration_ms = round(info.frames * 1000 / info.samplerate) if info.samplerate else None
        meta.extra["format"] = info.format
        meta.extra["subtype"] = info.subtype
        return
    except Exception as exc:  # libsndfile cannot read every container (e.g. m4a)
        soundfile_error = str(exc)
    if _read_audio_ffprobe(path, meta):
        return
    raise InvalidInputError(f"Cannot decode audio {path.name}: {soundfile_error}", path=str(path))


def _read_audio_ffprobe(path: Path, meta: FileMeta) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return False
    data = json.loads(proc.stdout or "{}")
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if stream is None:
        return False
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    meta.sample_rate = int(stream["sample_rate"]) if stream.get("sample_rate") else None
    meta.channels = stream.get("channels")
    meta.duration_ms = round(float(duration) * 1000) if duration else None
    meta.extra["format"] = data.get("format", {}).get("format_name")
    meta.extra["codec"] = stream.get("codec_name")
    return True
