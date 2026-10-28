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
    """Matched to field recordings: thunder is a few long rolls (each a cluster of overlapping booms that
    swells in ~0.3 s and fades over 2-3 s) on a sustained rumble, its level fluttering 4-10 times a second.
    Spectrally it is pink noise rolled off above ~300 Hz (about -9 dB/octave) with little true sub-bass;
    a close strike starts with a brief bright crack that reaches several kHz."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    far = p["distance"]
    d = ctx.duration
    t = np.arange(n) / sr
    roll_count = max(1, int(round(1 + p["roll"] * d / 3)))
    starts = np.sort(np.concatenate([[0.05 + 0.3 * far], rng.uniform(1.0, max(1.2, d - 2.5), roll_count - 1)]))
    rolls = np.zeros(n)
    for i, st in enumerate(starts):
        size = (1.0 if i == 0 else rng.uniform(0.45, 0.9)) * (1 - 0.3 * far)
        attack, fade = rng.uniform(0.15, 0.4), rng.uniform(1.8, 3.2) * (0.6 + 0.6 * p["roll"])
        env = np.clip((t - st) / attack, 0, 1) * np.exp(-np.maximum(t - st - attack, 0) / fade)
        rolls = np.maximum(rolls, env * size)
    bed = 0.18 + 0.12 * p["roll"]
    level = np.maximum(rolls, bed * np.exp(-t / (d * 0.8)) * (t > starts[0]))
    flutter = 10 ** ((4 + 4 * (1 - far)) * dsp.drift(n, sr, 7, rng) / 20)  # booms arriving: ±4-8 dB at ~7 Hz
    noise = s.noise(n, rng, "pink")
    rumble = dsp.one_pole_lowpass(noise, 260 - 120 * far, sr)  # pink + 6 dB/oct above ~260 Hz ≈ -9 dB/oct
    rumble = dsp.filt(dsp.filt(rumble, "highpass", 55, sr), "lowpass", 6000 - 4500 * far, sr)
    out = rms_norm(rumble) * level * flutter
    if far < 0.7:
        crack = np.zeros(n)
        st = int(starts[0] * sr)
        for i in range(int(4 + 8 * (1 - far))):
            at = st + int((i * 0.02 + rng.uniform(0, 0.015)) * sr)
            crack += n_wave(ctx, n, at=at, ms=rng.uniform(2, 6), tone_hz=5000 - 2500 * far) * rng.uniform(0.5, 1.0)
        tear = dsp.filt(s.noise(n, rng, "pink"), "highpass", 800, sr) * np.exp(-np.maximum(t - starts[0], 0) / 0.25) * (t >= starts[0])
        out += (1 - far / 0.7) * (0.6 * crack / (np.max(np.abs(crack)) + 1e-12) * np.max(np.abs(out))
                                  + 0.5 * rms_norm(tear) * np.max(level))  # fmt: skip
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
    duration=4.5,
    duration_range=(0.8, 8.0),
    params=(
        num("size", 0.7, 0, 1, "Grenade (0) to huge blast (1)"),
        num("debris", 0.5, 0, 1, "Falling debris and rubble"),
        num("distance", 0.1, 0, 1, "Far blasts lose their crack and highs"),
    ),
    tags=("explosion", "blast", "bomb", "grenade", "detonate", "explode", "boom", "destroy", "war", "realistic"),
)
def blast(ctx: RenderContext, p: dict) -> np.ndarray:
    """Matched to explosion recordings: a shock front and deep boom, then a broadband roar of fire, air and
    debris that reaches 4-8 kHz and stays loud for seconds before tailing off, with debris pattering down."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    x = np.linspace(0, 1, n)
    t = np.arange(n) / sr
    size, far = p["size"], p["distance"]
    peak = lambda v: v / (np.max(np.abs(v)) + 1e-12)  # noqa: E731  (mix by peaks: spikes can't swamp bodies)
    shock = dsp.filt(n_wave(ctx, n, ms=4 + 10 * size, tone_hz=7000 - 5000 * far), "lowpass", 8000, sr)
    boom_f = (60 - 25 * size) * ctx.pitch
    boom = s.osc("sine", s.sweep(boom_f * 2.5, boom_f * 0.7, n), n, sr) * s.perc(n, sr, attack=0.003, curve=6)
    hold = 0.6 + 1.6 * size  # seconds the roar stays near full level
    sustain = np.exp(-np.maximum(t - hold, 0) / (0.5 + 0.8 * size)) * np.minimum(1, t / 0.01)
    flutter = 10 ** (3 * dsp.drift(n, sr, 8, rng) / 20)
    cutoff = ((7000 - 3500 * size) * np.exp(-t / (hold + 1.0)) + 600) * (1 - 0.6 * far)
    roar = dsp.sweep_filter(s.noise(n, rng, "white") * 0.5 + s.noise(n, rng, "pink"), "lowpass", cutoff, sr, q=0.7, block=128)
    roar = dsp.filt(roar, "lowshelf", 250, sr, gain_db=6) * sustain * flutter
    rumble = peak(s.lowpass(s.noise(n, rng, "brown"), 120, sr)) * np.exp(-t / (1.0 + 1.5 * size)) * np.minimum(1, t / 0.05)
    rubble = crackle(ctx, n, 60 * p["debris"], lo=700, hi=6000, envelope=np.clip((t - 0.3) * 3, 0, 1) * sustain)
    out = (1.0 * (1 - far) * peak(shock) + 0.2 * peak(boom) + 0.7 * peak(roar) + 0.2 * rumble
           + 0.3 * p["debris"] * peak(rubble))  # fmt: skip
    out = s.highpass(out, 30, sr)
    return fade_tail(peak(out), sr)
