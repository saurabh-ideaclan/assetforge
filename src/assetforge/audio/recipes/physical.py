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
    return dsp.filt(dsp.filt(x, "bandpass", np.sqrt(lo * hi), ctx.sr, q=np.sqrt(lo * hi) / (hi - lo) * 1.5), "lowpass", hi, ctx.sr)


def flame(ctx: RenderContext, n: int, size: float, envelope: np.ndarray) -> np.ndarray:
    """Fire: a lapping roar (low-passed noise whose brightness and level gust), a little hiss, and crackle."""
    sr = ctx.sr
    gust = np.clip(0.6 + 0.4 * dsp.drift(n, sr, 6, ctx.rng), 0.1, 1.2)
    cutoff = (350 + 900 * (1 - size * 0.6)) * (0.5 + gust) * (0.4 + 0.6 * envelope)
    roar = dsp.sweep_filter(s.noise(n, ctx.rng, "pink"), "lowpass", cutoff, sr, q=0.9, block=64)
    body = dsp.filt(roar, "peak", 180 + 120 * (1 - size), sr, q=0.8, gain_db=6)
    hiss = dsp.filt(s.noise(n, ctx.rng, "pink"), "bandpass", 3500, sr, q=0.7) * 0.08
    pops = crackle(ctx, n, 25 + 30 * size, lo=1200, hi=5000, envelope=envelope)
    lvl = lambda v: v / (np.sqrt(np.mean(v**2)) + 1e-12)  # noqa: E731
    return (lvl(body) * gust + 0.4 * lvl(hiss) + 0.25 * lvl(pops)) * envelope


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
