"""Integrated loudness (ITU-R BS.1770-4, LUFS) for consistent levels across a game's sounds.

K-weighting uses the BS.1770 shelf + high-pass filters, re-derived for any sample rate. Gating
uses 400 ms blocks with 75% overlap, an absolute gate at -70 LUFS and a relative gate at -10 LU.
Sounds shorter than one block (most SFX) are measured as a single ungated block.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import lfilter

ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -10.0
BLOCK_S = 0.4
OVERLAP = 0.75
SILENCE = -120.0


def k_weighting(sr: int) -> list[tuple[np.ndarray, np.ndarray]]:
    # Stage 1: high shelf (+4 dB above ~1.7 kHz) modelling the head.
    gain_db, fc, q = 3.999843853973347, 1681.974450955533, 0.7071752369554196
    fc = min(fc, 0.45 * sr)  # very low sample rates (< 3.7 kHz) would put the shelf above Nyquist
    k = math.tan(math.pi * fc / sr)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf_b = np.array([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0])
    shelf_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    # Stage 2: RLB high-pass (~38 Hz).
    fc, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * fc / sr)
    a0 = 1 + k / q + k * k
    hp_b = np.array([1.0, -2.0, 1.0])
    hp_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    return [(shelf_b, shelf_a), (hp_b, hp_a)]


def _loudness(mean_square: float) -> float:
    return -0.691 + 10 * math.log10(mean_square) if mean_square > 0 else SILENCE


def integrated_loudness(samples: np.ndarray, sr: int) -> float:
    """`samples` shaped (n,) or (n, channels); left/right/mono channels all weigh 1.0."""
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    weighted = data
    for b, a in k_weighting(sr):
        weighted = lfilter(b, a, weighted, axis=0)
    n = len(weighted)
    block = int(BLOCK_S * sr)
    if n < block:
        return round(_loudness(float(np.mean(weighted**2, axis=0).sum())), 2)
    step = int(block * (1 - OVERLAP))
    starts = range(0, n - block + 1, step)
    z = np.array([np.mean(weighted[s : s + block] ** 2, axis=0).sum() for s in starts])
    levels = np.array([_loudness(v) for v in z])
    above_abs = z[levels > ABSOLUTE_GATE]
    if above_abs.size == 0:
        return SILENCE
    relative = _loudness(float(above_abs.mean())) + RELATIVE_GATE
    gated = z[(levels > ABSOLUTE_GATE) & (levels > relative)]
    return round(_loudness(float(gated.mean())), 2) if gated.size else SILENCE
