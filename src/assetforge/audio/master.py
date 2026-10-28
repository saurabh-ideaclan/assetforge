"""Mastering for designed sounds, ambience and music: DC removal, loudness target, peak ceiling.

Loudness is matched to a per-kind target (BS.1770 integrated LUFS) so a game's sounds sit at
consistent levels. Peaks above the ceiling go through a sample-wise soft limiter: no lookahead
and no state, so it is deterministic and never breaks a loop seam.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from assetforge.audio.loudness import integrated_loudness

LOUDNESS_TARGETS: dict[str, float] = {"sfx": -14.0, "stinger": -14.0, "music": -16.0, "ambience": -24.0}
CEILING_DB = -1.0
TRIM_DB = -60.0
MAX_SQUASH_DB = 4.0


def soft_limit(x: np.ndarray, ceiling: float, knee: float = 0.8) -> np.ndarray:
    """Leave samples below `knee × ceiling` alone; squash the rest smoothly toward the ceiling."""
    threshold = ceiling * knee
    mag = np.abs(x)
    over = mag > threshold
    if not over.any():
        return x
    y = x.copy()
    room = ceiling - threshold
    y[over] = np.sign(x[over]) * (threshold + room * np.tanh((mag[over] - threshold) / room))
    return y


def _trim(x: np.ndarray, sr: int) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 0:
        return x
    level = np.max(np.abs(x), axis=1) if x.ndim == 2 else np.abs(x)
    audible = np.flatnonzero(level >= peak * 10 ** (TRIM_DB / 20))
    y = x[: max(int(audible[-1]) + 1, int(0.01 * sr))].copy()
    fade_in, fade_out = min(len(y) // 4, int(0.001 * sr)), min(len(y) // 4, int(0.008 * sr))
    shape = (slice(None), None) if y.ndim == 2 else (slice(None),)
    if fade_in:
        y[:fade_in] *= np.linspace(0.0, 1.0, fade_in)[shape]
    if fade_out:
        y[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)[shape]
    return y


def master(x: np.ndarray, sr: int, *, target_lufs: float, ceiling_db: float = CEILING_DB,
           loop: bool = False) -> tuple[np.ndarray, dict[str, Any]]:  # fmt: skip
    """Return the mastered signal and a report. Loops are neither trimmed nor faded."""
    y = np.asarray(x, dtype=np.float64)
    y = y - y.mean(axis=0)
    if not loop:
        y = _trim(y, sr)
    before = integrated_loudness(y, sr)
    report: dict[str, Any] = {"target_lufs": target_lufs, "input_lufs": before, "gain_db": 0.0, "limited": False}
    if before <= -70.0:
        return y, report
    ceiling = 10 ** (ceiling_db / 20)
    # Never push peaks more than MAX_SQUASH_DB into the limiter: quieter beats distorted.
    headroom_db = 20 * math.log10(ceiling / max(float(np.max(np.abs(y))), 1e-12)) + MAX_SQUASH_DB
    gain_db = min(target_lufs - before, headroom_db)
    y = y * 10 ** (gain_db / 20)
    peak = float(np.max(np.abs(y)))
    if peak > ceiling * 0.8:
        y = soft_limit(y, ceiling)
        report["limited"] = peak > ceiling
    report["gain_db"] = round(gain_db, 2)
    report["output_lufs"] = integrated_loudness(y, sr)
    report["peak_dbfs"] = round(20 * math.log10(max(float(np.max(np.abs(y))), 1e-12)), 2)
    return y, report
