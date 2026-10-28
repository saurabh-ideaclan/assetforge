"""World one-shots: thunder, gusts, drips, ignitions, rumbles, distant booms."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import crackle, fade_tail, n_wave, rms_norm


@recipe(
    "thunder",
    "Thunder clap and rolling rumble",
    duration=6.0,
    duration_range=(1.0, 12.0),
    params=(num("distance", 0.35, 0, 1, "Far thunder is duller and softer at the start"), num("roll", 0.7, 0, 1, "Rolling length")),
    tags=("thunder", "storm", "lightning", "weather", "rumble", "rain", "sky", "nature"),
)
def thunder(ctx: RenderContext, p: dict) -> np.ndarray:
    """Close lightning rips the air (a run of sharp shock waves), then the roll: echoes from every part of the
    channel arrive over seconds as overlapping low booms, later ones duller and quieter."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    far = p["distance"]
    out = np.zeros(n)
    count = int(30 + 80 * p["roll"])
    arrivals = np.sort(np.concatenate([rng.uniform(0, 0.3, 5), rng.gamma(1.4, 0.25 + 0.7 * p["roll"], count)]))
    for t in arrivals:
        start = int((0.03 + 0.3 * far + t) * sr)
        if start >= n:
            continue
        k = min(n - start, int(rng.uniform(0.2, 0.8) * sr))
        lateness = min(1.0, t / max(ctx.duration * 0.6, 0.1))
        cutoff = (1400 - 900 * far) * (1 - 0.7 * lateness) * rng.uniform(0.7, 1.3)
        burst = s.lowpass(s.noise(k, rng, "brown"), max(cutoff, 120), sr, order=2)
        burst = rms_norm(burst) * np.sin(np.pi * np.linspace(0, 1, k)) ** 1.5
        out[start : start + k] += burst * rng.lognormal(0, 0.45) * (1 - 0.75 * lateness)
    out = s.highpass(out, 38, sr)
    if far < 0.7:  # the rip of a close strike: a fast run of shock fronts
        rip = np.zeros(n)
        for i in range(int(6 + 14 * (1 - far))):
            at = int((0.02 + 0.3 * far + rng.gamma(1.2, 0.03) + i * 0.012) * sr)
            rip += n_wave(ctx, n, at=at, ms=rng.uniform(1.5, 6), tone_hz=4500 - 3000 * far) * rng.uniform(0.4, 1.0)
        out += 2.5 * (1 - far / 0.7) * rms_norm(rip) * np.max(np.abs(out)) / 3
    env = np.minimum(1, np.linspace(0, 1, n) * 40)
    return fade_tail(s.soft_clip(norm(out) * env, 0.2), sr)

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


@recipe(
    "blast",
    "Realistic explosion: shock front, deep boom, fireball roar, debris falling, long low rumble",
    duration=3.0,
    duration_range=(0.8, 8.0),
    params=(
        num("size", 0.7, 0, 1, "Grenade (0) to huge blast (1)"),
        num("debris", 0.5, 0, 1, "Falling debris and rubble"),
        num("distance", 0.1, 0, 1, "Far blasts lose their crack and highs"),
    ),
    tags=("explosion", "blast", "bomb", "grenade", "detonate", "explode", "boom", "destroy", "war", "realistic"),
)
def blast(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    x = np.linspace(0, 1, n)
    size, far = p["size"], p["distance"]
    shock = n_wave(ctx, n, ms=3 + 8 * size, tone_hz=6000 - 4500 * far)
    boom_f = (70 - 30 * size) * ctx.pitch
    boom = s.osc("sine", s.sweep(boom_f * 2.5, boom_f * 0.6, n), n, sr) * s.perc(n, sr, attack=0.002, curve=5 - 2 * size)
    roar_cut = (3200 - 1400 * size) * np.exp(-x * 2.5) + 300
    roar = dsp.sweep_filter(s.noise(n, rng, "pink"), "lowpass", roar_cut * (1 - 0.6 * far), sr, q=0.8, block=64)
    roar = rms_norm(roar) * np.minimum(1, x * 60) * np.exp(-x * (5 - 3 * size))
    rumble = s.lowpass(s.noise(n, rng, "brown"), 120, sr) * np.exp(-x * (2.5 - 1.5 * size))
    rubble = crackle(ctx, n, 60 * p["debris"], lo=600, hi=3500, envelope=np.clip((x - 0.15) * 4, 0, 1) * np.exp(-x * 2.5))
    roar = dsp.filt(roar, "peak", 350, sr, q=0.8, gain_db=5)  # the chesty body of the fireball
    out = (2.0 * (1 - far) * norm(shock) + 0.45 * rms_norm(boom) + rms_norm(roar) + 0.3 * rms_norm(rumble)
           + p["debris"] * 0.45 * rms_norm(rubble))  # fmt: skip
    return fade_tail(s.soft_clip(s.highpass(out, 30, sr) / 2.5, 0.35), sr)
