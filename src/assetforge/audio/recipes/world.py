"""World one-shots: thunder, gusts, drips, ignitions, rumbles, distant booms."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, num, recipe
from assetforge.audio.recipes.music import norm


@recipe(
    "thunder",
    "Thunder clap and rolling rumble",
    duration=4.0,
    duration_range=(1.0, 10.0),
    params=(num("distance", 0.4, 0, 1, "Far thunder is duller and softer at the start"), num("roll", 0.6, 0, 1, "Rolling length")),
    tags=("thunder", "storm", "lightning", "weather", "rumble", "rain", "sky", "nature"),
)
def thunder(ctx: RenderContext, p: dict) -> np.ndarray:
    """A crack (when close) and a rolling rumble: echoes from every part of the lightning channel arrive over
    seconds, each a burst of low noise, later ones duller and quieter, so the level surges irregularly."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    far = p["distance"]
    out = np.zeros(n)
    count = int(25 + 70 * p["roll"])
    arrivals = np.sort(np.concatenate([rng.uniform(0, 0.25, 4), rng.gamma(1.6, 0.18 + 0.5 * p["roll"], count)]))
    for t in arrivals:
        start = int((0.03 + 0.25 * far + t) * sr)
        if start >= n:
            continue
        k = min(n - start, int(rng.uniform(0.15, 0.6) * sr))
        lateness = min(1.0, t / max(ctx.duration * 0.6, 0.1))
        cutoff = (2200 - 1500 * far) * (1 - 0.6 * lateness) * rng.uniform(0.7, 1.3)
        burst = s.lowpass(s.noise(k, rng, "pink"), max(cutoff, 150), sr, order=2)
        burst = norm(burst) * s.adsr(k, sr, attack=0.02 + 0.05 * rng.uniform(), decay=0.05, sustain=0.7, release=k / sr * 0.7)
        out[start : start + k] += burst * rng.lognormal(0, 0.5) * (1 - 0.7 * lateness)
    bed = s.lowpass(s.noise(n, rng, "brown"), 180, sr) * np.exp(-np.linspace(0, 1, n) * (4 - 2.5 * p["roll"]))
    out += 0.35 * norm(bed) * np.max(np.abs(out))
    out = s.highpass(out, 35, sr)  # nothing useful below hearing/speaker range, only headroom
    if far < 0.6:  # the close-up crack: a few sharp tearing snaps
        crack = np.zeros(n)
        for _ in range(int(3 + 6 * (1 - far))):
            i = int(rng.uniform(0.0, 0.18) * sr)
            k = min(n - i, int(rng.uniform(0.008, 0.03) * sr))
            snap = s.bandpass(s.noise(k, rng, "pink"), 300, 5000 - 3000 * far, sr) * s.perc(k, sr, attack=0.0005, curve=3)
            crack[i : i + k] += norm(snap) * rng.uniform(0.5, 1.0)
        out += crack * (1 - far / 0.6) * np.max(np.abs(out)) * 0.7
    return s.soft_clip(norm(out), 0.25)


@recipe(
    "gust",
    "A gust of wind swelling and fading",
    duration=2.5,
    duration_range=(0.4, 8.0),
    params=(num("strength", 0.6, 0, 1, "Stronger gusts are louder and higher"), num("howl", 0.3, 0, 1, "Whistling tone")),
    tags=("wind", "gust", "breeze", "blow", "air", "storm", "weather", "nature", "whoosh"),
)
def gust(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    centre = (300 + 900 * p["strength"]) * (0.6 + 0.4 * np.sin(np.pi * x)) * (1 + 0.2 * dsp.drift(n, sr, 2, ctx.rng))
    air = s.filter_track(s.noise(n, ctx.rng, "pink"), "bandpass", centre, sr, bandwidth_octaves=1.5)
    howl = dsp.sweep_filter(s.noise(n, ctx.rng), "bandpass", centre * 2, sr, q=12)
    return (norm(air) + p["howl"] * norm(howl)) * np.sin(np.pi * x) ** 1.5


@recipe(
    "water_drop",
    "Single water drip in a cave or sink",
    duration=0.4,
    duration_range=(0.08, 1.5),
    params=(num("height", 1400, 400, 4000, "Drop pitch"),),
    tags=("drip", "drop", "water", "cave", "leak", "plink", "dungeon", "sewer", "wet"),
)
def water_drop(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    k = min(n, int(0.06 * sr))
    f = p["height"] * ctx.pitch
    blip = s.osc("sine", s.sweep(f * 0.7, f * 1.9, k), k, sr) * s.perc(k, sr, attack=0.001, curve=2)
    return s.place(blip, n, 0)


@recipe(
    "fire_ignite",
    "Fire catching: match strike or torch whoomph",
    duration=0.9,
    duration_range=(0.2, 3.0),
    params=(num("size", 0.5, 0, 1, "Match (0) to bonfire (1)"),),
    tags=("ignite", "fire", "torch", "match", "light", "flame", "burn", "campfire", "whoomph"),
)
def fire_ignite(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    strike = s.place(norm(s.highpass(s.noise(int(0.05 * sr), ctx.rng), 2000, sr)) * s.perc(int(0.05 * sr), sr, curve=2), n, 0)
    whoomph = s.filter_track(s.noise(n, ctx.rng, "pink"), "lowpass", 300 + 2500 * np.sin(np.pi * np.clip(x * 2, 0, 1)), sr)
    env = np.clip((x - 0.05) * 10, 0, 1) * np.exp(-x * (4 - 2 * p["size"]))
    return (1 - p["size"]) * 0.8 * strike + norm(whoomph) * env * (0.4 + 0.6 * p["size"])


@recipe(
    "rumble",
    "Deep earth rumble: quake, collapse or huge machinery",
    duration=3.0,
    duration_range=(0.5, 10.0),
    params=(num("depth", 0.7, 0, 1, "Lower and heavier"), num("shake", 0.5, 0, 1, "Rattling debris")),
    tags=("rumble", "earthquake", "quake", "collapse", "tremor", "shake", "boss", "earth", "ground"),
)
def rumble(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    low = s.lowpass(s.noise(n, ctx.rng, "brown"), 160 - 100 * p["depth"], sr) * (1 + 0.6 * dsp.drift(n, sr, 4, ctx.rng))
    rattle = s.bandpass(s.noise(n, ctx.rng), 800, 4000, sr) * (dsp.drift(n, sr, 20, ctx.rng) > 0.3)
    env = np.sin(np.pi * x) ** 0.6
    return (norm(low) + p["shake"] * 0.3 * norm(rattle)) * env


@recipe(
    "distant_boom",
    "Far-off explosion or artillery: a muffled thud with a long echo",
    duration=2.5,
    duration_range=(0.6, 8.0),
    params=(num("distance", 0.7, 0, 1, "How far away"),),
    tags=("boom", "distant", "explosion", "artillery", "cannon", "war", "battle", "far", "echo"),
)
def distant_boom(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    thud = s.lowpass(s.noise(n, ctx.rng, "brown"), 400 - 250 * p["distance"], sr) * s.perc(n, sr, attack=0.01, curve=5)
    echo = dsp.comb_delay(norm(thud), 0.25 + 0.3 * p["distance"], sr, feedback=0.45, mix=0.4)[:n]
    return norm(echo)
