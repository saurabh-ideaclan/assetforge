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
    """A close strike: a short tearing crack (a run of low-passed shock fronts) that opens straight into a
    continuous, rolling rumble. The rumble is many overlapping low booms (echoes from along the channel) on
    a never-silent bed, swelling and fading irregularly and getting duller as it rolls away."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    far = p["distance"]
    x = np.linspace(0, 1, n)
    onset = int((0.02 + 0.35 * far) * sr)
    # continuous bed: brown noise whose level and brightness wander (never drops to silence)
    swell = np.clip(0.55 + 0.45 * dsp.drift(n, sr, 1.2, rng), 0.15, 1.0)
    decay = np.exp(-x * (2.2 - 1.6 * p["roll"]))
    bed = dsp.sweep_filter(s.noise(n, rng, "brown"), "lowpass", (220 + 380 * (1 - far)) * (0.6 + 0.6 * swell) * (0.4 + 0.6 * decay),
                           sr, q=0.7, block=128)  # fmt: skip
    bed = rms_norm(bed) * swell * decay
    # overlapping booms
    booms = np.zeros(n)
    for t in rng.gamma(1.5, 0.3 + 0.8 * p["roll"], int(20 + 40 * p["roll"])):
        start = onset + int(t * sr)
        if start >= n:
            continue
        k = min(n - start, int(rng.uniform(0.25, 0.9) * sr))
        late = min(1.0, t / 3)
        b = s.lowpass(s.noise(k, rng, "brown"), (700 - 400 * far) * (1 - 0.6 * late) + 80, sr)
        booms[start : start + k] += rms_norm(b) * np.hanning(k) * rng.lognormal(0, 0.4) * (1 - 0.6 * late)
    rumble = bed + 0.8 * booms
    rumble *= np.clip((np.arange(n) - onset) / (0.06 * sr), 0, 1)  # starts with the crack
    rumble = s.highpass(rumble, 35, sr)
    rumble /= np.max(np.abs(rumble)) + 1e-12
    crack = np.zeros(n)
    if far < 0.7:
        for i in range(int(4 + 8 * (1 - far))):
            at = onset + int((i * 0.018 + rng.uniform(0, 0.012)) * sr)
            crack += n_wave(ctx, n, at=at, ms=rng.uniform(3, 8), tone_hz=2500 - 1500 * far) * rng.uniform(0.5, 1.0)
        crack = dsp.filt(crack, "lowpass", 3000, sr)
        crack /= np.max(np.abs(crack)) + 1e-12
    out = rumble + 0.9 * (1 - far / 0.7) * crack if far < 0.7 else rumble
    return fade_tail(out / (np.max(np.abs(out)) + 1e-12), sr)


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
    peak = lambda v: v / (np.max(np.abs(v)) + 1e-12)  # noqa: E731  (mix by peaks: spikes can't swamp bodies)
    shock = dsp.filt(n_wave(ctx, n, ms=4 + 10 * size, tone_hz=3500 - 2500 * far), "lowpass", 4000, sr)
    boom_f = (65 - 25 * size) * ctx.pitch
    boom = s.osc("sine", s.sweep(boom_f * 2.5, boom_f * 0.7, n), n, sr) * s.perc(n, sr, attack=0.003, curve=6)
    roar_cut = (1800 - 700 * size) * np.exp(-x * 3) + 250
    roar = dsp.sweep_filter(s.noise(n, rng, "brown"), "lowpass", roar_cut * (1 - 0.5 * far), sr, q=0.7, block=128)
    roar = peak(roar) * np.minimum(1, x * 40) * np.exp(-x * (4.5 - 2.5 * size))
    rumble = peak(s.lowpass(s.noise(n, rng, "brown"), 110, sr)) * np.exp(-x * (3 - 1.5 * size)) * np.minimum(1, x * 8)
    rubble = crackle(ctx, n, 25 * p["debris"], lo=500, hi=2500, envelope=np.clip((x - 0.12) * 5, 0, 1) * np.exp(-x * 3))
    roar = dsp.filt(roar, "peak", 300, sr, q=0.7, gain_db=6)  # the audible body of the fireball
    out = (0.8 * (1 - far) * peak(shock) + 0.35 * peak(boom) + 1.0 * peak(roar) + 0.35 * rumble
           + 0.18 * p["debris"] * peak(rubble))  # fmt: skip
    out = s.highpass(out, 30, sr)
    return fade_tail(s.soft_clip(peak(out), 0.15), sr)
