"""Audio formats soundfile can't handle: M4A/AAC (the Safari/iOS fallback next to OGG), via ffmpeg.

`write_m4a` encodes with ffmpeg's native AAC encoder into an MP4 container. That encoder writes an edit
list for its priming samples, so a decoded file has exactly the original length and no offset: loop points
and seamless loops survive (tests check this). `read_audio` decodes anything soundfile can read, and falls
back to ffmpeg for the rest. Without ffmpeg, M4A is skipped with a warning, never an error.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile

from assetforge.errors import InvalidInputError

M4A = "m4a"
SOUNDFILE_FORMATS = ("wav", "ogg", "mp3", "flac")
_warned: set[str] = set()


@lru_cache(maxsize=1)
def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def can_encode(fmt: str) -> bool:
    return fmt in SOUNDFILE_FORMATS or (fmt == M4A and ffmpeg() is not None)


def warn_once(key: str, message: str) -> None:
    """Diagnostics go to stderr (stdout carries the JSON document)."""
    if key not in _warned:
        _warned.add(key)
        print(f"warning: {message}", file=sys.stderr)


def skip_unavailable(formats: list[str]) -> list[str]:
    """`formats` minus any this machine can't encode (M4A without ffmpeg), warning once."""
    kept = [f for f in formats if can_encode(f)]
    if len(kept) < len(formats):
        warn_once("m4a", "ffmpeg not found: skipping M4A copies (install it, e.g. `brew install ffmpeg`)")
    return kept


def write_m4a(data: np.ndarray, sr: int, path: Path) -> Path:
    """`data` is int16 PCM or float in -1..1, shaped (n,) or (n, channels)."""
    exe = ffmpeg()
    if exe is None:
        raise InvalidInputError("M4A needs ffmpeg", hint="Install ffmpeg (brew install ffmpeg) or choose ogg")
    channels = 1 if np.ndim(data) == 1 else int(np.shape(data)[1])
    bitrate = "96k" if channels == 1 else "160k"
    with tempfile.TemporaryDirectory(prefix="assetforge-m4a-") as tmp:
        wav = Path(tmp) / "in.wav"
        pcm = data if np.asarray(data).dtype == np.int16 else np.round(np.clip(data, -1, 1) * 32767).astype(np.int16)
        soundfile.write(str(wav), pcm, sr, subtype="PCM_16")
        proc = subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", bitrate,
                               "-movflags", "+faststart", str(path)], capture_output=True, timeout=600)  # fmt: skip
        if proc.returncode != 0 or not path.is_file():
            raise InvalidInputError(f"ffmpeg could not encode {path.name}", stderr=proc.stderr.decode(errors="replace")[-300:])
    return path


def read_audio(path: Path | str, *, dtype: str = "float64", always_2d: bool = True) -> tuple[np.ndarray, int]:
    """Decode any audio file: soundfile first, ffmpeg for formats it can't read (M4A/AAC)."""
    try:
        return soundfile.read(str(path), dtype=dtype, always_2d=always_2d)
    except Exception as exc:
        exe = ffmpeg()
        if exe is None:
            raise InvalidInputError(f"Cannot decode audio {Path(path).name}: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="assetforge-dec-") as tmp:
        wav = Path(tmp) / "out.wav"
        proc = subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(path), str(wav)], capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise InvalidInputError(f"Cannot decode audio {Path(path).name}", stderr=proc.stderr.decode(errors="replace")[-300:])
        return soundfile.read(str(wav), dtype=dtype, always_2d=always_2d)
