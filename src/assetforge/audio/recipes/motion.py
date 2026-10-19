"""Movement sounds: jumps, landings, bounces and whooshes."""

from __future__ import annotations

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import WAVES, RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm


@recipe(
    "jump",
    "Springy rising jump",
    duration=0.3,
    duration_range=(0.08, 1.0),
    params=(
        num("start_hz", 260, 60, 1200, "Starting pitch"),
        num("rise", 12, 1, 36, "Pitch rise in semitones"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.5, 0.1, 0.9, "Pulse width for the square wave", 0.1),
    ),
    tags=("jump", "hop", "spring", "player", "platformer", "movement"),
)
def jump(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, d = ctx.n, ctx.sr, ctx.duration
    x = np.linspace(0.0, 1.0, n)
    freq = p["start_hz"] * ctx.pitch * s.semitones(p["rise"] * (1 - (1 - x) ** 2))
    env = s.adsr(n, sr, attack=0.004, decay=d * 0.2, sustain=0.6, release=d * 0.5)
    return s.lowpass(s.osc(p["wave"], freq, n, sr, duty=p["duty"]) * env, 6000, sr)


@recipe(
    "land",
    "Soft thud of landing on the ground",
    duration=0.18,
    duration_range=(0.06, 0.8),
    params=(
        num("thump_hz", 120, 40, 300, "Body pitch"),
        num("drop", 12, 0, 36, "Pitch drop of the body in semitones"),
        num("noise", 0.5, 0, 1, "Amount of dusty noise"),
        num("dust_hz", 1200, 200, 6000, "Brightness of the dust noise"),
    ),
    tags=("land", "landing", "thud", "footstep", "step", "impact", "movement"),
)
def land(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    f = p["thump_hz"] * ctx.pitch
    body = s.osc("sine", s.sweep(f, f / s.semitones(p["drop"]), n), n, sr) * s.perc(n, sr, attack=0.001, curve=5)
    dust = norm(s.lowpass(s.noise(n, ctx.rng), p["dust_hz"], sr)) * s.perc(n, sr, attack=0.001, curve=7)
    return body + p["noise"] * dust


@recipe(
    "bounce",
    "Cartoon boing with a wobbly spring",
    duration=0.4,
    duration_range=(0.12, 1.5),
    params=(
        num("base_hz", 320, 100, 1500, "Base pitch"),
        num("rise", 12, 0, 24, "How far the pitch springs up, in semitones"),
        num("wobble", 0.5, 0, 1, "Spring vibrato amount"),
        choice("wave", "sine", WAVES, "Oscillator waveform"),
    ),
    tags=("bounce", "boing", "spring", "cartoon", "trampoline", "movement"),
)
def bounce(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0.0, 1.0, n)
    t = np.arange(n) / sr
    contour = p["rise"] * (1 - np.exp(-30 * x)) * np.exp(-2.5 * x)
    wobble = p["wobble"] * 2.0 * np.exp(-3 * x) * np.sin(2 * np.pi * 14 * t)
    freq = p["base_hz"] * ctx.pitch * s.semitones(contour + wobble)
    return s.osc(p["wave"], freq, n, sr) * s.perc(n, sr, attack=0.003, curve=1.8)


@recipe(
    "whoosh",
    "Airy swoosh for transitions, dashes and swings",
    duration=0.6,
    duration_range=(0.15, 2.5),
    params=(
        num("low_hz", 250, 60, 2000, "Filter centre when quiet"),
        num("high_hz", 2600, 400, 9000, "Filter centre at the loudest point"),
        num("peak", 0.55, 0.1, 0.9, "Where the swell peaks, as a share of the duration"),
        num("width", 1.2, 0.3, 3.0, "Filter bandwidth in octaves"),
        choice("color", "pink", ("white", "pink", "brown"), "Noise colour"),
    ),
    tags=("whoosh", "swoosh", "swish", "transition", "dash", "swing", "wind", "movement"),
)
def whoosh(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0.0, 1.0, n)
    pk = p["peak"]
    env = np.where(x < pk, (x / pk) ** 2, ((1 - x) / (1 - pk)) ** 1.5)
    centre = p["low_hz"] * ctx.pitch * (p["high_hz"] / p["low_hz"]) ** env
    shaped = s.filter_track(s.noise(n, ctx.rng, p["color"]), "bandpass", centre, sr, bandwidth_octaves=p["width"])
    return norm(shaped) * env
