"""Audio QC: level, clipping, DC offset and silence measurements with a list of issues."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from assetforge.errors import InvalidInputError

FLOOR_DB = -120.0
SILENCE_DB = -50.0  # below this a sample counts as silence for leading/trailing measurements
CLIP_LEVEL = 0.9999

# Errors make a sound unusable; warnings are worth a look but don't block anything.
ERROR_ISSUES = {"silent", "clipping"}


def _db(value: float) -> float:
    return round(20.0 * np.log10(value), 2) if value > 0 else FLOOR_DB


def analyze(samples: np.ndarray, sr: int) -> dict[str, Any]:
    """`samples` is float audio in -1..1, shaped (n,) or (n, channels)."""
    data = np.asarray(samples, dtype=np.float64)
    mono_peak = np.max(np.abs(data), axis=1) if data.ndim == 2 else np.abs(data)
    n = len(mono_peak)
    peak = float(mono_peak.max()) if n else 0.0
    rms = float(np.sqrt(np.mean(np.square(data)))) if n else 0.0
    dc = float(np.mean(data)) if n else 0.0
    clipped = int(np.count_nonzero(mono_peak >= CLIP_LEVEL))
    audible = np.flatnonzero(mono_peak >= 10 ** (SILENCE_DB / 20))
    leading = int(audible[0]) if audible.size else n
    trailing = int(n - 1 - audible[-1]) if audible.size else n

    issues: list[str] = []
    if peak < 10 ** (-60 / 20):
        issues.append("silent")
    if clipped >= 3:
        issues.append("clipping")
    if abs(dc) > 0.01:
        issues.append("dc_offset")
    if "silent" not in issues and rms < 10 ** (-40 / 20):
        issues.append("too_quiet")
    if leading * 1000 / sr > 50 and "silent" not in issues:
        issues.append("leading_silence")
    return {
        "ok": not (set(issues) & ERROR_ISSUES),
        "issues": issues,
        "duration_ms": round(n * 1000 / sr),
        "sample_rate": sr,
        "channels": 1 if data.ndim == 1 else int(data.shape[1]),
        "peak_dbfs": _db(peak),
        "rms_dbfs": _db(rms),
        "crest_db": round(_db(peak) - _db(rms), 2) if rms > 0 else 0.0,
        "clipped_samples": clipped,
        "dc_offset": round(dc, 5),
        "leading_silence_ms": round(leading * 1000 / sr),
        "trailing_silence_ms": round(trailing * 1000 / sr),
    }


def loop_seam(samples: np.ndarray, sr: int) -> dict[str, Any]:
    """How cleanly the end joins the start when the sound loops: a click and/or a level jump.

    Both are judged against the sound itself: a jump counts as a click only when it is larger than
    any sample-to-sample step near the seam, and the level jump is reported next to how much the level
    normally changes between neighbouring 250 ms windows (noisy textures move a lot on their own).
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    window = max(2, int(0.05 * sr))
    if len(data) < 2 * window:
        return {"loop_click": False, "loop_level_jump_db": 0.0, "level_change_p95_db": 0.0}
    jump = float(np.max(np.abs(data[-1] - data[0])))
    largest_step = max(float(np.max(np.abs(np.diff(data[-window:], axis=0)))),
                       float(np.max(np.abs(np.diff(data[:window], axis=0)))))  # fmt: skip
    # Level is judged over 250 ms (what the ear follows), so single crackles or drips don't count as a jump.
    span = min(len(data) // 2, int(0.25 * sr))
    head = float(np.sqrt(np.mean(data[:span] ** 2)))
    tail = float(np.sqrt(np.mean(data[-span:] ** 2)))
    blocks = data[: len(data) // span * span].reshape(-1, span, data.shape[1])
    levels = np.array([_db(float(np.sqrt(np.mean(b**2)))) for b in blocks])
    changes = np.abs(np.diff(levels))
    return {
        "loop_click": bool(jump > max(0.05, 1.5 * largest_step)),
        "loop_level_jump_db": round(float(abs(_db(head) - _db(tail))), 2),
        "level_change_p95_db": round(float(np.percentile(changes, 95)), 2) if changes.size else 0.0,
    }


def seam_ok(seam: dict[str, Any]) -> bool:
    """No click, and the level jump at the seam is within what the sound does anyway."""
    return bool(not seam["loop_click"] and seam["loop_level_jump_db"] <= max(4.0, seam.get("level_change_p95_db", 0) + 3))


def analyze_file(path: Path, *, loopable: bool = False) -> dict[str, Any]:
    from assetforge.audio.codecs import read_audio
    from assetforge.audio.loudness import integrated_loudness

    try:
        data, sr = read_audio(path, always_2d=False)
    except InvalidInputError as exc:
        raise InvalidInputError(exc.message, path=str(path)) from exc
    report = analyze(data, sr)
    report["loudness_lufs"] = integrated_loudness(data, sr) if len(data) else FLOOR_DB
    if report["peak_dbfs"] > -0.1 and "clipping" not in report["issues"]:
        report["issues"].append("no_headroom")
    if report["trailing_silence_ms"] > 500 and "silent" not in report["issues"]:
        report["issues"].append("trailing_silence")
    if loopable:
        seam = loop_seam(data, sr)
        report |= seam
        if seam["loop_click"]:
            report["issues"].append("loop_click")
        if seam["loop_level_jump_db"] > max(6.0, seam["level_change_p95_db"] + 3):
            report["issues"].append("loop_level_jump")
    return report
