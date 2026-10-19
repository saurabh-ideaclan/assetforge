"""Combat and destruction: hits, punches, crunches, explosions, damage and lasers."""

from __future__ import annotations

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import WAVES, RenderContext, choice, integer, num, recipe
from assetforge.audio.recipes.music import norm


@recipe(
    "cartoon_hit",
    "Cartoon bonk: a pitched knock with a soft crunch",
    duration=0.35,
    duration_range=(0.08, 1.2),
    params=(
        num("bonk_hz", 520, 150, 2000, "Pitch of the knock"),
        num("drop", 14, 0, 36, "Pitch drop in semitones"),
        num("crunch", 0.3, 0, 1, "Crunchy noise layer"),
        num("wobble", 0.3, 0, 1, "Rubbery vibrato"),
    ),
    tags=("hit", "bonk", "impact", "cartoon", "knock", "bump", "enemy", "combat"),
)
def cartoon_hit(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0.0, 1.0, n)
    f = s.vibrato(p["bonk_hz"] * ctx.pitch * s.semitones(-p["drop"] * np.sqrt(x)), n, sr,
                  rate_hz=22, depth_semitones=p["wobble"] * 1.2)  # fmt: skip
    tone = s.osc("sine", f, n, sr) * s.perc(n, sr, attack=0.001, curve=3)
    tone += 0.35 * s.osc("sine", f * 2.4, n, sr) * s.perc(n, sr, attack=0.001, curve=6)
    tn = min(n, int(0.03 * sr))
    transient = s.place(norm(s.bandpass(s.noise(tn, ctx.rng), 800, 5000, sr)) * s.perc(tn, sr, curve=4), n, 0)
    crunch = s.soft_clip(norm(s.lowpass(s.noise(n, ctx.rng), 3000, sr)) * s.perc(n, sr, curve=5), 0.5)
    return tone + 0.6 * transient + 0.8 * p["crunch"] * crunch


@recipe(
    "punch",
    "Weighty punch or kick impact",
    duration=0.25,
    duration_range=(0.08, 0.8),
    params=(
        num("low_hz", 85, 40, 200, "Pitch of the body thump"),
        num("snap", 0.6, 0, 1, "Bright slap at the start"),
        num("weight", 0.7, 0, 1, "Low-end body"),
    ),
    tags=("punch", "kick", "hit", "impact", "smack", "melee", "combat"),
)
def punch(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    f = p["low_hz"] * ctx.pitch
    body = s.osc("sine", s.sweep(f * 2, f, n), n, sr) * s.perc(n, sr, attack=0.001, curve=4)
    smack = norm(s.bandpass(s.noise(n, ctx.rng), 300, 2000, sr)) * s.perc(n, sr, attack=0.0005, curve=9)
    sn = min(n, int(0.025 * sr))
    snap = s.place(norm(s.highpass(s.noise(sn, ctx.rng), 3000, sr)) * s.perc(sn, sr, curve=3), n, 0)
    return s.soft_clip(p["weight"] * body + 0.7 * smack + p["snap"] * snap, 0.3)


@recipe(
    "crunch",
    "Crumbling break or destruction made of debris grains",
    duration=0.45,
    duration_range=(0.12, 2.0),
    params=(
        integer("grains", 16, 3, 60, "Number of debris grains", jitter=0.08),
        num("grain_ms", 20, 3, 80, "Average grain length in milliseconds"),
        num("tone", 0.5, 0, 1, "Material: 0 is dull wood/dirt, 1 is bright glass/stone"),
        num("roughness", 0.5, 0, 1, "Saturation and grit"),
    ),
    tags=("crunch", "break", "destroy", "shatter", "crumble", "smash", "debris", "destruction"),
)
def crunch(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    out = np.zeros(n)
    starts = np.sort((rng.beta(1.2, 3.0, p["grains"]) * n * 0.8).astype(int))
    for start in starts:
        g = max(8, int(p["grain_ms"] / 1000 * sr * (0.5 + rng.random())))
        centre = 400 * 10 ** p["tone"] * (0.6 + 0.8 * rng.random()) * ctx.pitch
        grain = norm(s.bandpass(s.noise(g, rng), centre / 1.6, centre * 1.6, sr)) * s.perc(g, sr, curve=3)
        out += s.place(grain * (1 - start / n) ** 1.5 * (0.5 + 0.5 * rng.random()), n, int(start))
    thud = s.osc("sine", 150 * ctx.pitch, n, sr) * s.perc(n, sr, attack=0.001, curve=6)
    return s.soft_clip(norm(out) + 0.5 * thud, p["roughness"])


@recipe(
    "explosion",
    "Explosion with a low boom, rumbling tail and flying debris",
    duration=1.2,
    duration_range=(0.3, 4.0),
    params=(
        num("size", 0.6, 0, 1, "Small pop (0) to huge blast (1)"),
        num("boom_hz", 55, 25, 150, "Pitch of the sub boom"),
        num("debris", 0.4, 0, 1, "Amount of crackling debris"),
        choice("color", "brown", ("white", "pink", "brown"), "Noise colour of the rumble"),
    ),
    tags=("explosion", "explode", "boom", "blast", "bomb", "destroy", "destruction"),
)
def explosion(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    x = np.linspace(0.0, 1.0, n)
    top = (300 + 7000 * p["size"]) * ctx.pitch
    cutoff = top * (120 / top) ** np.sqrt(x)
    rumble = norm(s.filter_track(s.noise(n, rng, p["color"]), "lowpass", cutoff, sr))
    rumble *= s.perc(n, sr, attack=0.004, curve=2.0 + (1 - p["size"]) * 2)
    f = p["boom_hz"] * ctx.pitch
    boom = s.osc("sine", s.sweep(f * 2.2, f * 0.7, n), n, sr) * s.perc(n, sr, attack=0.002, curve=3.5)
    debris = np.zeros(n)
    g = max(8, int(0.006 * sr))
    for _ in range(int(p["debris"] * 60 * ctx.duration)):
        start = int(rng.beta(1.5, 3.0) * n)
        crack = norm(s.highpass(s.noise(g, rng), 2500, sr)) * s.perc(g, sr, curve=4)
        debris += s.place(crack * (1 - start / n) ** 2 * rng.uniform(0.3, 1.0), n, start)
    return s.soft_clip(rumble + (0.5 + 0.5 * p["size"]) * boom + 0.6 * debris, 0.25)


@recipe(
    "hurt",
    "Short damage or 'ouch' sound",
    duration=0.25,
    duration_range=(0.08, 0.8),
    params=(
        num("start_hz", 650, 200, 1500, "Starting pitch"),
        num("drop", 10, 0, 24, "Pitch drop in semitones"),
        num("grit", 0.5, 0, 1, "Noisy, distorted layer"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
    ),
    tags=("hurt", "damage", "ouch", "hit", "player", "lose-life", "combat"),
)
def hurt(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, d = ctx.n, ctx.sr, ctx.duration
    f = p["start_hz"] * ctx.pitch
    tone = s.osc(p["wave"], s.sweep(f, f / s.semitones(p["drop"]), n), n, sr)
    tone = s.lowpass(tone * s.adsr(n, sr, attack=0.002, decay=d * 0.3, sustain=0.5, release=d * 0.5), 5000, sr)
    grit = norm(s.bandpass(s.noise(n, ctx.rng), 500, 4000, sr)) * s.perc(n, sr, curve=3)
    return s.soft_clip(tone + 0.6 * p["grit"] * grit, 0.4 * p["grit"])


@recipe(
    "laser",
    "Sci-fi laser zap or shot",
    duration=0.25,
    duration_range=(0.06, 1.0),
    params=(
        num("start_hz", 2000, 300, 6000, "Starting pitch"),
        num("drop", 24, 6, 48, "Pitch drop in semitones"),
        choice("wave", "saw", WAVES, "Oscillator waveform"),
        num("fm", 0.3, 0, 1, "Sci-fi warble"),
    ),
    tags=("laser", "zap", "shoot", "shot", "blaster", "pew", "sci-fi", "weapon", "combat"),
)
def laser(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    f = p["start_hz"] * ctx.pitch
    freq = s.vibrato(s.sweep(f, f / s.semitones(p["drop"]), n), n, sr, rate_hz=30, depth_semitones=p["fm"] * 6)
    return s.lowpass(s.osc(p["wave"], freq, n, sr) * s.perc(n, sr, attack=0.001, curve=1.6), 9000, sr)
