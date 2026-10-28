"""Physically informed building blocks for realistic sound effects (after Farnell, *Designing Sound*).

Real objects ring at a handful of resonant modes; impacts excite them with a short, smooth pulse;
explosions and thunder start with an N-shaped pressure wave; sparks and fire are sparse impulses
through resonances, not continuous hiss. Using these instead of broadband white noise keeps sounds
clear: the noise is shaped, short and where the physics puts it.
"""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext

# Mode ratios (relative to the fundamental) of common objects.
STEEL_BAR = (1.0, 2.756, 5.404, 8.933, 13.34)
PLATE = (1.0, 1.594, 2.136, 2.296, 2.653, 2.918, 3.156, 3.501)
WOOD_PLANK = (1.0, 2.57, 4.0, 5.9)
GLASS = (1.0, 2.32, 4.25, 6.63)


def modal(ctx: RenderContext, n: int, base_hz: float, ratios: tuple[float, ...], *, decay_s: float, damping: float = 1.0,
          detune: float = 0.002, brightness: float = 0.6, at: int = 0) -> np.ndarray:  # fmt: skip
    """Ring of an object struck at sample `at`: exponentially decaying sines at its mode frequencies.

    Higher modes decay faster (`damping`), pairs of slightly detuned modes beat like real metal, and
    `brightness` tilts how much the upper modes are excited.
    """
    out = np.zeros(n)
    m = n - at
    if m <= 0:
        return out
    t = np.arange(m) / ctx.sr
    nyquist = ctx.sr * 0.45
    for k, ratio in enumerate(ratios):
        f = base_hz * ctx.pitch * ratio
        if f >= nyquist:
            break
        amp = (brightness ** k) * ctx.rng.uniform(0.7, 1.0)
        tau = decay_s / (1 + damping * k * 0.6)
        for d in ((-detune, detune) if detune else (0.0,)):
            out[at:] += amp * np.sin(2 * np.pi * f * (1 + d) * t + ctx.rng.uniform(0, 6.28)) * np.exp(-t / tau) / 2
    out[at : at + min(m, 32)] *= np.linspace(0, 1, min(m, 32))  # no click at the strike
    return out


def strike(ctx: RenderContext, n: int, *, at: int = 0, ms: float = 2.0, tone_hz: float = 3000.0) -> np.ndarray:
    """The contact itself: a short smooth bump (raised cosine) low-passed at `tone_hz`, not white noise."""
    out = np.zeros(n)
    k = max(4, int(ms / 1000 * ctx.sr))
    if at >= n:
        return out
    pulse = np.hanning(k) * (1 + 0.3 * ctx.rng.uniform(-1, 1, k))
    seg = dsp.filt(np.concatenate([pulse, np.zeros(k * 4)]), "lowpass", tone_hz, ctx.sr, q=0.7)
    end = min(n, at + len(seg))
    out[at:end] = seg[: end - at]
    return out


def n_wave(ctx: RenderContext, n: int, *, at: int = 0, ms: float = 6.0, tone_hz: float = 2500.0) -> np.ndarray:
    """A shock front: sharp rise, linear fall below zero, sharp return (explosions, thunder cracks, gunshots)."""
    out = np.zeros(n)
    k = max(8, int(ms / 1000 * ctx.sr))
    if at >= n:
        return out
    wave = np.linspace(1.0, -1.0, k)
    seg = dsp.filt(np.concatenate([wave, np.zeros(k * 3)]), "lowpass", tone_hz, ctx.sr, q=0.6)
    end = min(n, at + len(seg))
    out[at:end] = seg[: end - at]
    return out


def crackle(ctx: RenderContext, n: int, rate: float, *, lo: float = 1500.0, hi: float = 6000.0,
            envelope: np.ndarray | None = None) -> np.ndarray:  # fmt: skip
    """Sparse sharp pops (sparks, embers, arcs): random impulses with heavy-tailed sizes, each ringing briefly."""
    x = np.zeros(n)
    count = ctx.rng.poisson(rate * n / ctx.sr)
    if count:
        where = ctx.rng.integers(0, n, count)
        x[where] += ctx.rng.pareto(1.6, count).clip(0, 6) * ctx.rng.choice([-1.0, 1.0], count)
    if envelope is not None:
        x *= envelope
    # Each pop is a short windowed grain (≈1.5 ms), then a band-pass and a 4th-order low-pass: a single-sample
    # impulse through gentle filters stays broadband and reads as a click/hiss instead of a pop.
    k = max(4, int(0.0015 * ctx.sr))
    x = np.convolve(x, np.hanning(k), mode="same")
    centre = float(np.sqrt(lo * hi))
    x = dsp.filt(x, "bandpass", centre, ctx.sr, q=max(0.5, centre / (hi - lo) * 1.5))
    return dsp.filt(dsp.filt(x, "lowpass", hi, ctx.sr), "lowpass", hi, ctx.sr)


def clicks(ctx: RenderContext, n: int, rate: float, *, at: float = 0.0, span: float | None = None, hp: float = 1500.0,
           lp: float = 10000.0, shape: float = 1.6, envelope: np.ndarray | None = None) -> np.ndarray:  # fmt: skip
    """Sharp broadband clicks (snapping embers, breaking snow crust): 0.3 ms grains, high-passed.

    Unlike `crackle`, nothing smooths them into thuds; real fire and snow crackles reach 10 kHz.
    """
    x = np.zeros(n)
    lo = min(n, int(at * ctx.sr))
    hi = n if span is None else min(n, lo + int(span * ctx.sr))
    count = ctx.rng.poisson(rate * (hi - lo) / ctx.sr) if hi > lo else 0
    if count:
        x[ctx.rng.integers(lo, hi, count)] += ctx.rng.pareto(shape, count).clip(0, 8) * ctx.rng.choice([-1.0, 1.0], count)
    if envelope is not None:
        x *= envelope
    x = np.convolve(x, np.hanning(max(3, int(0.0003 * ctx.sr))), mode="same")
    return dsp.filt(dsp.filt(x, "highpass", hp, ctx.sr, q=0.6), "lowpass", lp, ctx.sr)


def exp_env(n: int, sr: int, tau: float, *, at: float = 0.0, attack: float = 0.002) -> np.ndarray:
    """Linear attack from `at`, then exponential decay with time constant `tau` seconds."""
    t = np.arange(n) / sr - at
    a = max(attack, 1e-4)
    return np.where(t < 0, 0.0, np.minimum(1, t / a) * np.exp(-np.maximum(t - a, 0) / tau))


def flame(ctx: RenderContext, n: int, size: float, envelope: np.ndarray) -> np.ndarray:
    """Fire, measured on real flare-ups: a deep gusting roar below ~250 Hz, weak mids, hiss bands near 2.4 and
    6.5 kHz that gust harder than the roar, and sharp broadband crackles (not low thuds)."""
    sr = ctx.sr
    gust = np.clip(0.75 + 0.25 * dsp.drift(n, sr, 8, ctx.rng), 0.3, 1.2)
    roar = dsp.filt(dsp.filt(s.noise(n, ctx.rng, "brown"), "lowpass", 180 + 80 * (1 - size), sr, q=0.8), "highpass", 45, sr)
    mids = s.bandpass(s.noise(n, ctx.rng, "pink"), 250, 1800, sr)
    hiss = (dsp.filt(s.noise(n, ctx.rng), "bandpass", 2300, sr, q=2.5)
            + 1.2 * dsp.filt(s.noise(n, ctx.rng), "bandpass", 6500, sr, q=1.2))  # fmt: skip
    pops = clicks(ctx, n, 30, hp=1500, lp=10000)
    body = (rms_norm(roar) * gust + 0.15 * rms_norm(mids) * gust + 0.25 * rms_norm(hiss) * gust**2) * envelope
    return body + 0.12 * rms_norm(pops) * np.clip(envelope * 3, 0, 1)


def fade_tail(x: np.ndarray, sr: int, seconds: float | None = None) -> np.ndarray:
    """Guarantee the sound reaches silence (an abrupt end is heard as a tick). Default: 12% of it, at most 150 ms."""
    k = min(len(x), int((seconds if seconds is not None else min(0.15, 0.12 * len(x) / sr)) * sr))
    if k:
        x = x.copy()
        x[-k:] *= np.cos(np.linspace(0, np.pi / 2, k)) ** 2
    return x


def rms_norm(x: np.ndarray) -> np.ndarray:
    r = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    return x / r if r > 0 else x
