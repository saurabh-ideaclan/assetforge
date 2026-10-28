"""Finishing (DC removal, tail trim, fades, peak normalization) and encoding to WAV/OGG/MP3."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile

from assetforge.audio.synth import highpass

FORMATS = {
    "wav": ("WAV", "PCM_16"),
    "ogg": ("OGG", "VORBIS"),
    "mp3": ("MP3", "MPEG_LAYER_III"),
}
TRIM_DB = -60.0
MIN_DURATION_S = 0.01
FADE_IN_S = 0.001
FADE_OUT_S = 0.008


def finish(x: np.ndarray, sr: int, *, peak_db: float = -1.0) -> np.ndarray:
    """Make a render game-ready: no DC, no dead tail, no clicks, consistent peak level."""
    y = highpass(np.asarray(x, dtype=np.float64), 15.0, sr, order=1)
    peak = np.max(np.abs(y)) if len(y) else 0.0
    if peak <= 0:
        return y
    audible = np.flatnonzero(np.abs(y) >= peak * 10 ** (TRIM_DB / 20))
    end = max(int(audible[-1]) + 1, int(MIN_DURATION_S * sr))
    y = y[:end].copy()
    fade_in = min(len(y) // 4, int(FADE_IN_S * sr))
    fade_out = min(len(y) // 4, int(FADE_OUT_S * sr))
    if fade_in:
        y[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    if fade_out:
        y[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    peak = np.max(np.abs(y))
    return y * (10 ** (peak_db / 20) / peak) if peak > 0 else y


def to_pcm16(x: np.ndarray) -> np.ndarray:
    return np.round(np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def pcm_sha256(pcm: np.ndarray) -> str:
    """Identity of the rendered sound itself, independent of container and encoder."""
    return hashlib.sha256(np.ascontiguousarray(pcm, dtype="<i2").tobytes()).hexdigest()


def write_audio(pcm: np.ndarray, sr: int, path: Path, fmt: str) -> Path:
    if fmt == "m4a":
        from assetforge.audio.codecs import write_m4a

        return write_m4a(pcm, sr, path)
    container, subtype = FORMATS[fmt]
    data = pcm if fmt == "wav" else pcm.astype(np.float32) / 32767.0
    soundfile.write(str(path), data, sr, format=container, subtype=subtype)
    return path
