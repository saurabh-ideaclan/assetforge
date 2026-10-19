"""Synthesis primitives: oscillators, noise, envelopes, sweeps and filters.

Everything works on mono float64 NumPy arrays. Frequencies may be scalars or per-sample
arrays, so pitch sweeps and vibrato are just arrays fed to an oscillator. Randomness only
comes from the `numpy.random.Generator` passed in, which keeps renders reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

TAU = 2.0 * np.pi
Array = np.ndarray
Shape = Literal["sine", "square", "saw", "triangle"]
NoiseColor = Literal["white", "pink", "brown"]
FilterKind = Literal["lowpass", "highpass", "bandpass"]


def num_samples(duration: float, sr: int) -> int:
    return max(1, int(round(duration * sr)))


def semitones(st: float | Array) -> float | Array:
    """Frequency ratio for a pitch offset in semitones."""
    return 2.0 ** (np.asarray(st, dtype=float) / 12.0)


def note_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def _freq_array(freq: float | Array, n: int) -> Array:
    return np.broadcast_to(np.asarray(freq, dtype=float), (n,)).astype(float)


def _poly_blep(t: Array, dt: Array) -> Array:
    """Band-limited step correction that removes most aliasing from square and saw waves."""
    out = np.zeros_like(t)
    lo = t < dt
    x = t[lo] / dt[lo]
    out[lo] = x + x - x * x - 1.0
    hi = t > 1.0 - dt
    x = (t[hi] - 1.0) / dt[hi]
    out[hi] = x * x + x + x + 1.0
    return out


def osc(shape: Shape, freq: float | Array, n: int, sr: int, *, duty: float = 0.5, phase: float = 0.0) -> Array:
    f = _freq_array(freq, n)
    cycles = phase + np.concatenate(([0.0], np.cumsum(f[:-1]))) / sr
    t = cycles % 1.0
    if shape == "sine":
        return np.sin(TAU * cycles)
    if shape == "triangle":
        return 1.0 - 4.0 * np.abs(t - 0.5)
    dt = np.clip(f / sr, 1e-9, 0.5)
    if shape == "saw":
        return 2.0 * t - 1.0 - _poly_blep(t, dt)
    if shape == "square":
        naive = np.where(t < duty, 1.0, -1.0)
        return naive + _poly_blep(t, dt) - _poly_blep((t + 1.0 - duty) % 1.0, dt)
    raise ValueError(f"unknown oscillator shape {shape!r}")


def noise(n: int, rng: np.random.Generator, color: NoiseColor = "white") -> Array:
    white = rng.uniform(-1.0, 1.0, n)
    if color == "white" or n < 4:
        return white
    spectrum = np.fft.rfft(white)
    freqs = np.arange(len(spectrum), dtype=float)
    freqs[0] = 1.0
    spectrum /= np.sqrt(freqs) if color == "pink" else freqs
    shaped = np.fft.irfft(spectrum, n)
    shaped -= shaped.mean()
    peak = np.max(np.abs(shaped))
    return shaped / peak if peak > 0 else shaped


def sweep(f0: float, f1: float, n: int, *, curve: Literal["exp", "linear"] = "exp") -> Array:
    x = np.linspace(0.0, 1.0, n)
    if curve == "linear" or f0 <= 0 or f1 <= 0:
        return f0 + (f1 - f0) * x
    return f0 * (f1 / f0) ** x


def steps(freqs: Sequence[float], n: int) -> Array:
    """Piecewise-constant frequency track: equal-length steps through `freqs`."""
    idx = np.minimum((np.arange(n) * len(freqs)) // n, len(freqs) - 1)
    return np.asarray(freqs, dtype=float)[idx]


def vibrato(freq: float | Array, n: int, sr: int, *, rate_hz: float, depth_semitones: float | Array) -> Array:
    t = np.arange(n) / sr
    return _freq_array(freq, n) * semitones(np.asarray(depth_semitones) * np.sin(TAU * rate_hz * t))


def perc(n: int, sr: int, *, attack: float = 0.002, curve: float = 4.0) -> Array:
    """Percussive envelope: linear attack, then (1 - x)^curve decay reaching silence at the end."""
    a = min(n - 1, int(attack * sr))
    env = np.empty(n)
    env[:a] = np.linspace(0.0, 1.0, a, endpoint=False)
    env[a:] = (1.0 - np.linspace(0.0, 1.0, n - a)) ** curve
    return env


def adsr(
    n: int, sr: int, *, attack: float = 0.005, decay: float = 0.05, sustain: float = 0.7, release: float = 0.1
) -> Array:
    a, d, r = (int(v * sr) for v in (attack, decay, release))
    r = min(r, n)
    s = max(0, n - a - d - r)
    env = np.concatenate(
        [
            np.linspace(0.0, 1.0, a, endpoint=False),
            np.linspace(1.0, sustain, d, endpoint=False),
            np.full(s, sustain),
            np.linspace(sustain, 0.0, r),
        ]
    )
    return fit(env, n)


def fit(x: Array, n: int) -> Array:
    """Pad with silence or truncate to exactly n samples."""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x))])


def mix(*layers: Array) -> Array:
    n = max(len(layer) for layer in layers)
    return sum((fit(layer, n) for layer in layers), np.zeros(n))


def place(segment: Array, n: int, start: int) -> Array:
    """A length-n buffer holding `segment` starting at sample `start`."""
    out = np.zeros(n)
    if start >= n:
        return out
    end = min(n, start + len(segment))
    out[start:end] = segment[: end - start]
    return out


def _sos(kind: FilterKind, cutoff: float | tuple[float, float], sr: int, order: int) -> Array:
    nyquist = sr / 2.0
    if kind == "bandpass":
        lo, hi = cutoff  # type: ignore[misc]
        lo = float(np.clip(lo, 10.0, nyquist * 0.95))
        hi = float(np.clip(hi, lo * 1.05, nyquist * 0.98))
        return butter(order, [lo, hi], btype="bandpass", fs=sr, output="sos")
    c = float(np.clip(cutoff, 10.0, nyquist * 0.98))
    return butter(order, c, btype=kind, fs=sr, output="sos")


def lowpass(x: Array, cutoff: float, sr: int, order: int = 2) -> Array:
    return sosfilt(_sos("lowpass", cutoff, sr, order), x)


def highpass(x: Array, cutoff: float, sr: int, order: int = 2) -> Array:
    return sosfilt(_sos("highpass", cutoff, sr, order), x)


def bandpass(x: Array, low: float, high: float, sr: int, order: int = 2) -> Array:
    return sosfilt(_sos("bandpass", (low, high), sr, order), x)


def filter_track(
    x: Array,
    kind: FilterKind,
    cutoff: Array,
    sr: int,
    *,
    bandwidth_octaves: float = 1.0,
    block: int = 128,
) -> Array:
    """Time-varying filter: per-sample `cutoff` (centre frequency for bandpass), updated every block."""
    out = np.empty_like(x)
    zi = None
    half = 2.0 ** (bandwidth_octaves / 2.0)
    for start in range(0, len(x), block):
        c = float(cutoff[min(start + block // 2, len(cutoff) - 1)])
        sos = _sos(kind, (c / half, c * half) if kind == "bandpass" else c, sr, 1 if kind == "bandpass" else 2)
        if zi is None or zi.shape != (sos.shape[0], 2):
            zi = sosfilt_zi(sos) * 0.0
        out[start : start + block], zi = sosfilt(sos, x[start : start + block], zi=zi)
    return out


def soft_clip(x: Array, drive: float) -> Array:
    """tanh saturation; drive 0 leaves the signal unchanged."""
    if drive <= 0:
        return x
    g = 1.0 + 9.0 * drive
    return np.tanh(g * x) / np.tanh(g)
