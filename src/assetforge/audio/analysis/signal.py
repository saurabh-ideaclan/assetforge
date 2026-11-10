"""Technical (signal-level) analysis: what is objectively in the file.

Everything here is a measurement with a definition a sound engineer would recognise: true peak (BS.1770-4, 4×
oversampled), integrated / short-term / momentary loudness and loudness range (EBU R128 / Tech 3342), crest
factor, clipping runs, DC offset, silence distribution, sample-level discontinuities (clicks), stereo balance,
width and phase correlation, and the loop seam. None of these is an emotional judgement: a loud file is not an
intense one, and a correlated stereo file is not a dull one. NumPy/SciPy only.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.signal import lfilter, resample_poly

from assetforge.audio.loudness import ABSOLUTE_GATE, k_weighting
from assetforge.validation.audio import loop_seam, seam_ok

CLIP_LEVEL = 0.999
SILENCE_DB = -60.0


def db(x: float, floor: float = -120.0) -> float:
    return round(20.0 * math.log10(x), 2) if x > 0 else floor


def _lufs(mean_square: float) -> float:
    return -0.691 + 10 * math.log10(mean_square) if mean_square > 0 else -120.0


def true_peak_dbtp(x: np.ndarray, sr: int) -> float:
    """Inter-sample peak after 4× oversampling (BS.1770-4 Annex 2). 0 dBTP = full scale."""
    up = resample_poly(x, 4, 1, axis=0) if sr < 192000 else x
    return db(float(np.max(np.abs(up))) if len(up) else 0.0)


def loudness_timeline(x: np.ndarray, sr: int) -> dict[str, Any]:
    """Momentary (400 ms) and short-term (3 s) K-weighted loudness at 100 ms steps, the integrated value
    (gated, as in `audio.loudness`) and the loudness range (LRA: 10th–95th percentile of gated short-term
    values, EBU Tech 3342)."""
    data = x if x.ndim == 2 else x[:, None]
    w = data
    for b, a in k_weighting(sr):
        w = lfilter(b, a, w, axis=0)
    power = (w**2).sum(axis=1)  # channel weights all 1.0 (no surround)
    step = int(0.1 * sr)
    if len(power) < step:
        return {"momentary": [], "short_term": [], "integrated": -120.0, "lra": 0.0, "step_s": 0.1}
    cs = np.concatenate(([0.0], np.cumsum(power)))

    def windowed(seconds: float) -> np.ndarray:
        n = int(seconds * sr)
        if len(power) < n:
            return np.array([_lufs(float(power.mean()))])
        starts = np.arange(0, len(power) - n + 1, step)
        ms = (cs[starts + n] - cs[starts]) / n
        return np.array([_lufs(float(v)) for v in ms])

    momentary = windowed(0.4)
    short = windowed(3.0)
    # integrated: 400 ms blocks with 75 % overlap, absolute then relative gate (BS.1770-4)
    blocks = windowed(0.4)
    above = blocks[blocks > ABSOLUTE_GATE]
    if above.size:
        rel = _lufs(float(np.mean(10 ** ((above + 0.691) / 10)))) - 10.0
        gated = blocks[(blocks > ABSOLUTE_GATE) & (blocks > rel)]
        integrated = _lufs(float(np.mean(10 ** ((gated + 0.691) / 10)))) if gated.size else -120.0
    else:
        integrated = -120.0
    # LRA on short-term values: absolute gate -70, relative gate -20 LU below their gated mean
    st = short[short > -70.0]
    lra = 0.0
    if st.size >= 2:
        rel = _lufs(float(np.mean(10 ** ((st + 0.691) / 10)))) - 20.0
        st = st[st > rel]
        if st.size >= 2:
            lra = float(np.percentile(st, 95) - np.percentile(st, 10))
    return {"momentary": [round(float(v), 1) for v in momentary], "short_term": [round(float(v), 1) for v in short],
            "integrated": round(float(integrated), 2), "lra": round(lra, 1), "step_s": 0.1}  # fmt: skip


def clipping(x: np.ndarray) -> dict[str, Any]:
    """Samples at or above full scale, and the longest run of them (a run of 3+ is a flattened waveform)."""
    peak = np.max(np.abs(x), axis=1) if x.ndim == 2 else np.abs(x)
    hot = peak >= CLIP_LEVEL
    count = int(hot.sum())
    longest = 0
    if count:
        edges = np.diff(np.concatenate(([0], hot.astype(int), [0])))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        longest = int((ends - starts).max())
    return {"clipped_samples": count, "longest_run": longest, "clipping": bool(count >= 3 and longest >= 2)}


def silence_map(mono: np.ndarray, sr: int, *, threshold_db: float = SILENCE_DB) -> dict[str, Any]:
    """Where the file is silent: leading / trailing silence and internal gaps longer than 200 ms."""
    win = max(1, int(0.02 * sr))
    n = len(mono) // win * win
    if n == 0:
        return {"leading_ms": 0, "trailing_ms": 0, "gaps": [], "silent_share": 1.0}
    rms = np.sqrt(np.mean(mono[:n].reshape(-1, win) ** 2, axis=1))
    quiet = rms < 10 ** (threshold_db / 20)
    if quiet.all():
        return {"leading_ms": round(1000 * len(mono) / sr), "trailing_ms": 0, "gaps": [], "silent_share": 1.0}
    idx = np.flatnonzero(~quiet)
    leading, trailing = int(idx[0]), int(len(quiet) - 1 - idx[-1])
    gaps = []
    run_start = None
    for i in range(idx[0], idx[-1] + 1):
        if quiet[i] and run_start is None:
            run_start = i
        elif not quiet[i] and run_start is not None:
            if (i - run_start) * win / sr >= 0.2:
                gaps.append({"start_s": round(run_start * win / sr, 2), "end_s": round(i * win / sr, 2)})
            run_start = None
    return {"leading_ms": round(leading * win * 1000 / sr), "trailing_ms": round(trailing * win * 1000 / sr),
            "gaps": gaps, "silent_share": round(float(quiet.mean()), 3)}  # fmt: skip


def discontinuities(mono: np.ndarray, sr: int) -> dict[str, Any]:
    """Clicks: sample-to-sample jumps far above the local envelope (|Δx| > 8 × the 6 ms mean absolute level),
    merged within 5 ms. Returns their count and the first few positions."""
    if len(mono) < 512:
        return {"count": 0, "positions_s": []}
    env = np.convolve(np.abs(mono), np.ones(256) / 256, mode="same") + 1e-5
    jump = np.abs(np.diff(mono))
    hits = np.flatnonzero(jump > 8 * env[1:])
    merged: list[int] = []
    for h in hits:
        if not merged or h - merged[-1] > 0.005 * sr:
            merged.append(int(h))
    return {"count": len(merged), "positions_s": [round(h / sr, 3) for h in merged[:20]]}


def stereo(x: np.ndarray, sr: int) -> dict[str, Any]:
    """Balance (L/R level difference), width (side-to-mid energy), phase correlation (Pearson r of L and R;
    < 0 means mono summing cancels), and the share of 1 s windows with correlation under 0.2."""
    if x.ndim == 1 or x.shape[1] < 2:
        return {"channels": 1, "balance_db": 0.0, "width": 0.0, "correlation": 1.0, "low_correlation_share": 0.0}
    left, right = x[:, 0], x[:, 1]
    lr = db(float(np.sqrt(np.mean(left**2)))) - db(float(np.sqrt(np.mean(right**2))))
    mid, side = (left + right) / 2, (left - right) / 2
    width = float(np.sqrt(np.mean(side**2)) / (np.sqrt(np.mean(mid**2)) + 1e-9))
    denom = math.sqrt(float(np.sum(left**2) * np.sum(right**2))) + 1e-12
    corr = float(np.sum(left * right) / denom)
    win = int(sr)
    low = 0.0
    if len(left) >= 2 * win:
        cs = []
        for i in range(0, len(left) - win + 1, win):
            l_, r_ = left[i:i + win], right[i:i + win]
            d = math.sqrt(float(np.sum(l_**2) * np.sum(r_**2))) + 1e-12
            cs.append(float(np.sum(l_ * r_) / d))
        low = float(np.mean(np.array(cs) < 0.2))
    return {"channels": int(x.shape[1]), "balance_db": round(lr, 2), "width": round(width, 3),
            "correlation": round(corr, 3), "low_correlation_share": round(low, 3)}  # fmt: skip


def analyze(x: np.ndarray, sr: int) -> dict[str, Any]:
    """The technical report for float audio shaped (n,) or (n, channels)."""
    data = np.asarray(x, dtype=np.float64)
    mono = data.mean(axis=1) if data.ndim == 2 else data
    n = len(mono)
    peak = float(np.max(np.abs(data))) if n else 0.0
    rms = float(np.sqrt(np.mean(data**2))) if n else 0.0
    out: dict[str, Any] = {
        "sample_rate": sr, "channels": 1 if data.ndim == 1 else int(data.shape[1]), "duration_s": round(n / sr, 3),
        "peak_dbfs": db(peak), "true_peak_dbtp": true_peak_dbtp(data, sr) if n else -120.0, "rms_dbfs": db(rms),
        "crest_db": round(db(peak) - db(rms), 2) if rms > 0 else 0.0, "dc_offset": round(float(np.mean(data)), 5) if n else 0.0,
    }  # fmt: skip
    out |= clipping(data)
    out["loudness"] = loudness_timeline(data, sr)
    out["silence"] = silence_map(mono, sr)
    out["discontinuities"] = discontinuities(mono, sr)
    out["stereo"] = stereo(data, sr)
    if n >= 2 * sr:
        seam = loop_seam(data, sr)
        out["loop"] = {**seam, "seam_ok": seam_ok(seam)}
    return out
