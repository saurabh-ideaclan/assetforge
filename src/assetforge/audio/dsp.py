"""DSP helpers for long-form and layered audio (designs, ambience, music).

Signals are float64; stereo is shaped (n, 2). Filters are RBJ-cookbook biquads computed directly,
so a filter whose cutoff moves over minutes of audio costs one small `lfilter` call per block
instead of a filter design per block. Randomness only comes from the Generator passed in.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from scipy.signal import lfilter

from assetforge.audio.synth import TAU

Array = np.ndarray
BiquadKind = Literal["lowpass", "highpass", "bandpass", "notch", "peak", "lowshelf", "highshelf"]


# ---------------------------------------------------------------------- shape helpers


def as_stereo(x: Array) -> Array:
    return x if x.ndim == 2 else np.stack([x, x], axis=1)


def as_mono(x: Array) -> Array:
    return x.mean(axis=1) if x.ndim == 2 else x


def fit_len(x: Array, n: int) -> Array:
    """Pad with silence or truncate along time to exactly n samples (mono or stereo)."""
    if len(x) >= n:
        return x[:n]
    pad = [(0, n - len(x))] + [(0, 0)] * (x.ndim - 1)
    return np.pad(x, pad)


def add_at(buffer: Array, segment: Array, start: int) -> None:
    """Mix `segment` into `buffer` from sample `start` (in place). Mono segments go to both channels."""
    if start >= len(buffer) or start + len(segment) <= 0:
        return
    skip = max(0, -start)
    start = max(0, start)
    end = min(len(buffer), start + len(segment) - skip)
    part = segment[skip : skip + end - start]
    if buffer.ndim == 2 and part.ndim == 1:
        part = part[:, None]
    buffer[start:end] += part


def pan(x: Array, position: float | Array) -> Array:
    """Equal-power pan of a mono signal; position -1 (left) .. +1 (right), scalar or per-sample."""
    angle = (np.clip(np.asarray(position, dtype=float), -1.0, 1.0) + 1.0) * (math.pi / 4)
    return np.stack([x * np.cos(angle), x * np.sin(angle)], axis=1)


def width(x: Array, amount: float) -> Array:
    """Stereo width via mid/side: 0 = mono, 1 = unchanged, up to 2 = extra wide."""
    if x.ndim == 1:
        return x
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2 * amount
    return np.stack([mid + side, mid - side], axis=1)


def db(value: float) -> float:
    return 10 ** (value / 20)


# ---------------------------------------------------------------------- modulation


def lfo(n: int, sr: int, rate_hz: float, *, shape: str = "sine", phase: float = 0.0) -> Array:
    """-1..1 low-frequency oscillator."""
    cycles = phase + np.arange(n) * rate_hz / sr
    if shape == "triangle":
        return 1.0 - 4.0 * np.abs((cycles % 1.0) - 0.5)
    if shape == "square":
        return np.where((cycles % 1.0) < 0.5, 1.0, -1.0)
    return np.sin(TAU * cycles)


def drift(n: int, sr: int, rate_hz: float, rng: np.random.Generator, *, period: int | None = None) -> Array:
    """A smooth random curve in -1..1 that changes about `rate_hz` times a second.

    With `period` (samples) the curve repeats exactly every period, which keeps loops seamless.
    """
    span = period or n
    points = max(2, int(round(span / sr * rate_hz)) + 1)
    values = rng.uniform(-1.0, 1.0, points)
    if period:
        values[-1] = values[0]
    x = (np.arange(n) % span) / span * (points - 1)
    i = np.minimum(x.astype(int), points - 2)
    t = x - i
    t = t * t * (3 - 2 * t)  # smoothstep between control points
    return values[i] * (1 - t) + values[i + 1] * t


# ---------------------------------------------------------------------- filters


def biquad(kind: BiquadKind, freq: float, sr: int, q: float = 0.707, gain_db: float = 0.0) -> tuple[Array, Array]:
    """RBJ audio-EQ-cookbook coefficients (b, a), normalized so a[0] == 1."""
    f = min(max(freq, 10.0), sr * 0.49)
    w = TAU * f / sr
    cos_w, alpha = math.cos(w), math.sin(w) / (2 * max(q, 0.05))
    amp = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - cos_w) / 2, 1 - cos_w, (1 - cos_w) / 2]
        a = [1 + alpha, -2 * cos_w, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cos_w) / 2, -(1 + cos_w), (1 + cos_w) / 2]
        a = [1 + alpha, -2 * cos_w, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cos_w, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * cos_w, 1.0]
        a = [1 + alpha, -2 * cos_w, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * amp, -2 * cos_w, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cos_w, 1 - alpha / amp]
    else:
        sq = 2 * math.sqrt(amp) * alpha
        if kind == "lowshelf":
            b = [amp * ((amp + 1) - (amp - 1) * cos_w + sq), 2 * amp * ((amp - 1) - (amp + 1) * cos_w),
                 amp * ((amp + 1) - (amp - 1) * cos_w - sq)]  # fmt: skip
            a = [(amp + 1) + (amp - 1) * cos_w + sq, -2 * ((amp - 1) + (amp + 1) * cos_w),
                 (amp + 1) + (amp - 1) * cos_w - sq]  # fmt: skip
        else:
            b = [amp * ((amp + 1) + (amp - 1) * cos_w + sq), -2 * amp * ((amp - 1) + (amp + 1) * cos_w),
                 amp * ((amp + 1) + (amp - 1) * cos_w - sq)]  # fmt: skip
            a = [(amp + 1) - (amp - 1) * cos_w + sq, 2 * ((amp - 1) - (amp + 1) * cos_w),
                 (amp + 1) - (amp - 1) * cos_w - sq]  # fmt: skip
    b_arr, a_arr = np.asarray(b), np.asarray(a)
    return b_arr / a_arr[0], a_arr / a_arr[0]


def filt(x: Array, kind: BiquadKind, freq: float, sr: int, q: float = 0.707, gain_db: float = 0.0) -> Array:
    b, a = biquad(kind, freq, sr, q, gain_db)
    return lfilter(b, a, x, axis=0)


def sweep_filter(x: Array, kind: BiquadKind, cutoff: float | Array, sr: int, *, q: float = 0.707,
                 block: int = 256) -> Array:  # fmt: skip
    """A biquad whose cutoff follows `cutoff` (Hz, scalar or one value per sample), updated every block."""
    track = np.broadcast_to(np.asarray(cutoff, dtype=float), (len(x),))
    if np.ptp(track) < 1e-9:
        return filt(x, kind, float(track[0]), sr, q)
    out = np.empty_like(x, dtype=float)
    zi = np.zeros((2,) + x.shape[1:])
    for start in range(0, len(x), block):
        stop = min(len(x), start + block)
        b, a = biquad(kind, float(track[(start + stop) // 2]), sr, q)
        out[start:stop], zi = lfilter(b, a, x[start:stop], axis=0, zi=zi)
    return out


def one_pole_lowpass(x: Array, cutoff: float, sr: int) -> Array:
    """Gentle 6 dB/oct smoothing (envelopes, dark noise)."""
    k = 1 - math.exp(-TAU * cutoff / sr)
    return lfilter([k], [1, k - 1], x, axis=0)


# ---------------------------------------------------------------------- generators


def phase_osc(freq: float | Array, n: int, sr: int, *, phase: float = 0.0) -> Array:
    """Cumulative phase in cycles for a (possibly varying) frequency."""
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,))
    return phase + np.concatenate(([0.0], np.cumsum(f[:-1]))) / sr


def fm(carrier_hz: float | Array, ratio: float, index: float | Array, n: int, sr: int) -> Array:
    """Two-operator FM: a sine carrier phase-modulated by a sine at carrier × ratio.

    The index is capped so the strong sidebands (about index + 1 of them) stay below Nyquist; above it they
    would fold back as inharmonic, noisy aliasing on high notes.
    """
    fc = np.broadcast_to(np.asarray(carrier_hz, dtype=float), (n,))
    carrier = phase_osc(fc, n, sr)
    modulator = np.sin(TAU * phase_osc(fc * ratio, n, sr))
    limit = np.maximum((0.42 * sr - fc) / np.maximum(fc * ratio, 1e-9) - 1.0, 0.0)
    return np.sin(TAU * carrier + np.minimum(np.asarray(index, dtype=float), limit) * modulator)


def karplus(freq: float, n: int, sr: int, rng: np.random.Generator, *, decay: float = 0.996,
            brightness: float = 0.6) -> Array:  # fmt: skip
    """Karplus-Strong plucked string: a shaped excitation circulating in a tuned delay line.

    The excitation is mostly a smooth pluck shape (a triangle, as a finger leaves the string) with a little
    noise, low-passed relative to the note, and windowed: a raw white-noise burst would put a hissy scratch
    on every note, which is loudest exactly where it hurts most (low notes, long periods).
    """
    period = max(2, int(round(sr / max(freq, 20.0) - 0.5)))
    position = 0.12 + 0.2 * (1 - brightness)  # where the string is plucked
    shape = np.interp(np.arange(period) / period, [0, position, 1], [0.0, 1.0, 0.0])
    burst = shape - shape.mean() + 0.25 * rng.uniform(-1.0, 1.0, period)
    cutoff = min(sr * 0.35, freq * (3 + 30 * brightness ** 2), 200 + 9000 * brightness)
    burst = one_pole_lowpass(one_pole_lowpass(burst, cutoff, sr), cutoff, sr)
    burst *= np.hanning(period + 2)[1:-1] ** 0.5
    peak = float(np.max(np.abs(burst)))
    burst = burst / peak if peak > 0 else burst
    excitation = np.zeros(n)
    excitation[: min(n, period)] = burst[: min(n, period)]
    a = np.zeros(period + 2)
    a[0] = 1.0
    a[period] = -decay * 0.5
    a[period + 1] = -decay * 0.5
    return lfilter([1.0], a, excitation)


def comb_delay(x: Array, seconds: float, sr: int, *, feedback: float = 0.35, mix: float = 0.3) -> Array:
    """Feedback echo; the result is longer than x by the echo tail."""
    d = max(1, int(seconds * sr))
    repeats = max(1, int(math.log(0.001) / math.log(min(max(feedback, 1e-3), 0.99))))
    padded = fit_len(x, len(x) + d * repeats)
    wet = np.zeros_like(padded)
    for k in range(1, repeats + 1):  # each echo is the dry signal shifted by k·d, scaled by feedback^(k-1)
        wet[k * d :] += padded[: len(padded) - k * d] * feedback ** (k - 1)
    return padded * (1 - mix) + wet * mix
