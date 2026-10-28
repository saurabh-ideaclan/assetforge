"""Creature voices via formant synthesis: growls, roars, squeaks, chirps, hisses, slimes, insects."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import fade_tail, rms_norm

# Vowel formants (Hz) of an adult voice; creatures scale them by body size.
VOWELS = {"a": (800, 1150, 2900), "o": (450, 800, 2830), "u": (325, 700, 2530), "e": (400, 1600, 2700),
          "i": (300, 2300, 3000)}  # fmt: skip


def glottal_flow(phase: np.ndarray, open_quotient: float = 0.6) -> np.ndarray:
    """Rosenberg glottal pulse: the folds open smoothly, close faster, then stay shut. Its derivative is the
    excitation a voice actually radiates; unlike a sawtooth it has no step, so no click on every cycle."""
    tp, tn = open_quotient * 0.7, open_quotient * 0.3
    flow = np.zeros_like(phase)
    rising = phase < tp
    flow[rising] = 0.5 * (1 - np.cos(np.pi * phase[rising] / tp))
    falling = (phase >= tp) & (phase < tp + tn)
    flow[falling] = np.cos(np.pi * (phase[falling] - tp) / (2 * tn))
    return flow


def voice(ctx: RenderContext, f0: np.ndarray, vowel: str, size: float, rough: float, breath: float,
          *, opening: np.ndarray | None = None) -> np.ndarray:  # fmt: skip
    """An animal/monster voice built like a real one: smooth glottal pulses (with cycle-to-cycle amplitude
    shimmer and alternating strong/weak cycles for the growly rasp), a little breath in each open phase,
    then the vocal tract's formant resonances.

    `opening` (per sample, ~0.6..1.4) widens the mouth over time: formants rise as the jaw opens.
    """
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    f = np.broadcast_to(np.asarray(f0, dtype=float), (n,)) * (1 + 0.004 * rough * dsp.drift(n, sr, 15, rng)
                                                                + 0.02 * dsp.drift(n, sr, 3, rng))  # fmt: skip
    cycles = dsp.phase_osc(f, n, sr)
    index = cycles.astype(int)
    phase = cycles - index
    shimmer = rng.normal(1.0, 0.04 + 0.08 * rough, index.max() + 2)
    alternate = np.where(np.arange(index.max() + 2) % 2 == 0, 1.0, 1.0 - 0.55 * rough)  # period doubling = rasp
    flow = glottal_flow(phase, 0.65 - 0.2 * rough) * (shimmer * alternate)[index]
    excitation = np.diff(flow, prepend=0.0) * sr / np.maximum(f, 1)  # flow derivative, level independent of pitch
    open_phase = flow > 0.05
    breathiness = dsp.filt(s.noise(n, rng, "pink"), "bandpass", 1800, sr, q=0.7) * open_phase * (0.02 + 0.05 * breath)
    source = excitation / (np.max(np.abs(excitation)) + 1e-12) + breathiness
    scale = (1.4 - 0.7 * size) * (opening if opening is not None else 1.0)
    out = np.zeros(n)
    bandwidths = (90, 120, 200)  # Hz, like a real vocal tract
    for formant, gain, bw in zip(VOWELS[vowel], (1.0, 0.6, 0.3), bandwidths, strict=True):
        centre = formant * scale
        out += gain * dsp.sweep_filter(source, "bandpass", centre, sr, q=float(np.mean(centre)) / (bw * (1.5 - 0.5 * size)), block=64)
    out = dsp.filt(out, "highpass", 80 + 40 * (1 - size), sr, q=0.6)
    out = dsp.filt(dsp.filt(out, "lowpass", 4500, sr), "lowpass", 4500, sr)  # 4th order: no fizz on top
    return out


def beast(ctx: RenderContext, size: float, rough: float, *, pulse_hz: np.ndarray, f0: np.ndarray,
          opening: np.ndarray, vowel: str = "a", chest: float = 1.0) -> np.ndarray:  # fmt: skip
    """A big animal's roar or growl, matched to recordings (bear, tiger): mostly pulsed turbulence, not a
    clean voice. Bursts of breath noise at ~20-45 per second (each a little different in strength), a
    weaker voiced component under them, through broad mouth resonances near 0.6-1 kHz and 1.8-2.2 kHz,
    on a broadband floor that reaches well into the highs."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    cycles = dsp.phase_osc(pulse_hz * (1 + 0.08 * dsp.drift(n, sr, 6, rng)), n, sr)
    index = cycles.astype(int)
    frac = cycles - index
    strength = rng.lognormal(0, 0.5 + 0.3 * rough, index.max() + 2)[index]
    bursts = (np.minimum(frac / 0.06, 1.0) * (1 - frac) ** (4.0 - rough)) * strength
    turbulence = s.noise(n, rng, "pink") * bursts
    voiced = voice(ctx, f0, vowel, size, rough, 0.2, opening=opening)
    source = rms_norm(turbulence) + (0.25 - 0.15 * rough) * rms_norm(voiced) * bursts ** 0.5
    scale = (1.25 - 0.45 * size) * opening
    out = 0.7 * source  # the broadband floor real roars have
    chest_level = chest
    chest = dsp.filt(source, "lowpass", 260 * (1.3 - 0.4 * size), sr, q=0.9)  # body resonance: the weight
    out += (1.6 + 0.8 * size) * chest_level * chest
    for formant, gain, q in ((700, 0.6, 1.6), (1300, 0.5, 2.0), (2100, 0.3, 3.0), (3100, 0.1, 4.0)):
        out += gain * dsp.sweep_filter(source, "bandpass", formant * scale, sr, q=q, block=128)
    out = dsp.filt(dsp.filt(out, "highpass", 65, sr), "lowpass", 7000, sr)
    return out


VOWEL_CHOICES = tuple(VOWELS)


@recipe(
    "growl",
    "Low menacing creature growl",
    duration=1.2,
    duration_range=(0.3, 4.0),
    params=(
        num("size", 0.7, 0, 1, "Body size: bigger is lower and darker"),
        num("rough", 0.7, 0, 1, "Gravel in the voice"),
        choice("vowel", "o", VOWEL_CHOICES, "Mouth shape"),
    ),
    tags=("growl", "snarl", "monster", "beast", "creature", "dog", "wolf", "bear", "enemy", "threat", "animal"),
)
def growl(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    surge = 0.75 + 0.25 * dsp.drift(n, sr, 1.5, ctx.rng)  # the growl swells and eases
    pulse = (30 - 10 * p["size"]) * surge
    f0 = (120 - 55 * p["size"]) * ctx.pitch * (1 + 0.06 * dsp.drift(n, sr, 2.5, ctx.rng))
    v = beast(ctx, p["size"], 0.5 + 0.5 * p["rough"], pulse_hz=pulse, f0=f0, opening=0.75 + 0.15 * surge,
              vowel=p["vowel"], chest=0.45)  # fmt: skip
    env = s.adsr(n, sr, attack=0.12, decay=0.2, sustain=0.85, release=n / sr * 0.3) * surge
    return fade_tail(norm(v) * env, sr)

@recipe(
    "roar",
    "Big creature roar: rising then falling, open-mouthed",
    duration=1.6,
    duration_range=(0.5, 5.0),
    params=(
        num("size", 0.8, 0, 1, "Body size"),
        num("rough", 0.6, 0, 1, "Gravel in the voice"),
        num("breath", 0.4, 0, 1, "Breathy noise"),
    ),
    tags=("roar", "dragon", "dinosaur", "monster", "beast", "boss", "lion", "creature", "scream", "enemy"),
)
def roar(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    swell = np.sin(np.pi * np.clip(x / 0.85, 0, 1)) ** 0.6  # rises, holds, falls
    pulse = (40 - 14 * p["size"]) * (0.8 + 0.3 * swell)
    f0 = (170 - 90 * p["size"]) * ctx.pitch * s.semitones(4 * swell - 4 * x)
    v = beast(ctx, p["size"], 0.5 + 0.5 * p["rough"], pulse_hz=pulse * ctx.pitch ** 0.5, f0=f0,
              opening=0.75 + 0.45 * swell)  # fmt: skip
    env = np.minimum(1, x * 8) * (1 - x) ** 0.5 * (0.35 + 0.65 * swell)
    return fade_tail(norm(v) * env, sr)

@recipe(
    "squeak",
    "Small creature squeak: mouse, rodent or cute critter",
    duration=0.25,
    duration_range=(0.05, 1.0),
    params=(num("height", 2400, 800, 6000, "Pitch"), num("bend", 5, -12, 12, "Pitch bend in semitones")),
    tags=("squeak", "mouse", "rat", "critter", "small", "cute", "pet", "creature", "animal"),
)
def squeak(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = p["height"] * ctx.pitch * s.semitones(p["bend"] * np.sin(np.pi * x))
    return s.osc("sine", s.vibrato(f, n, sr, rate_hz=18, depth_semitones=0.4), n, sr) * np.sin(np.pi * x) ** 0.5


@recipe(
    "chirp",
    "Bird chirp or tweet",
    duration=0.3,
    duration_range=(0.05, 1.5),
    params=(
        num("height", 3200, 1000, 8000, "Pitch"),
        num("notes", 2, 1, 6, "Chirps in the call", 0),
        num("sweep", 8, -24, 24, "Pitch sweep per chirp, semitones"),
    ),
    tags=("chirp", "tweet", "bird", "birdsong", "songbird", "nature", "forest", "animal", "critter"),
)
def chirp(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    count = int(p["notes"])
    slot = n // count
    for k in range(count):
        cn = int(slot * 0.7)
        x = np.linspace(0, 1, cn)
        f = p["height"] * ctx.pitch * ctx.rng.uniform(0.9, 1.1) * s.semitones(p["sweep"] * x)
        out[k * slot : k * slot + cn] = dsp.fm(f, 2.0, 0.6, cn, sr) * np.sin(np.pi * x)
    return out


@recipe(
    "hiss",
    "Hiss of a snake, cat or steam",
    duration=0.9,
    duration_range=(0.2, 3.0),
    params=(num("pitch_hz", 5000, 1500, 9000, "Centre of the hiss"), num("rattle", 0.0, 0, 1, "Rattlesnake rattle")),
    tags=("hiss", "snake", "serpent", "cat", "steam", "spit", "creature", "reptile"),
)
def hiss(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    h = s.bandpass(s.noise(n, ctx.rng), p["pitch_hz"] * 0.6, p["pitch_hz"] * 1.6, sr)
    rattle = 1 - p["rattle"] * 0.8 * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 45 * np.arange(n) / sr)))
    return norm(h) * rattle * s.adsr(n, sr, attack=0.05, decay=0.1, sustain=0.9, release=n / sr * 0.3)


@recipe(
    "monster_hurt",
    "Creature pain yelp: short cry that falls in pitch",
    duration=0.5,
    duration_range=(0.12, 1.5),
    params=(num("size", 0.5, 0, 1, "Body size"), choice("vowel", "a", VOWEL_CHOICES, "Mouth shape")),
    tags=("hurt", "pain", "yelp", "monster", "enemy", "damage", "cry", "hit", "creature", "death", "die"),
)
def monster_hurt(ctx: RenderContext, p: dict) -> np.ndarray:
    n = ctx.n
    x = np.linspace(0, 1, n)
    f0 = (380 - 280 * p["size"]) * ctx.pitch * s.semitones(4 - 14 * x)
    return norm(voice(ctx, f0, p["vowel"], p["size"], 0.5, 0.2)) * np.minimum(1, x * 25) * (1 - x) ** 1.2


@recipe(
    "slime",
    "Wet squelch: slime, goo or mud",
    duration=0.5,
    duration_range=(0.1, 2.0),
    params=(num("wetness", 0.6, 0, 1, "More bubbles and squish"), num("size", 0.5, 0, 1, "Bigger blobs sound lower")),
    tags=("slime", "squelch", "goo", "gooey", "mud", "wet", "splat", "blob", "ooze", "squish", "creature"),
)
def slime(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    for _ in range(int(3 + 12 * p["wetness"])):
        start = int(ctx.rng.uniform(0, 0.75) * n)
        bn = min(n - start, int(ctx.rng.uniform(0.02, 0.08) * sr))
        if bn < 8:
            continue
        f = (500 - 300 * p["size"]) * ctx.pitch * ctx.rng.uniform(0.7, 1.4)
        out[start : start + bn] += s.osc("sine", s.sweep(f, f * 2.5, bn), bn, sr) * s.perc(bn, sr, attack=0.003, curve=2)
    squish = s.filter_track(s.noise(n, ctx.rng, "pink"), "bandpass", 400 + 900 * dsp.drift(n, sr, 12, ctx.rng) ** 2, sr)
    return norm(out) + 0.5 * norm(squish) * s.perc(n, sr, attack=0.01, curve=1.5)


@recipe(
    "insect_buzz",
    "Insect buzz: fly, bee or mosquito",
    duration=1.0,
    duration_range=(0.2, 5.0),
    params=(num("wing_hz", 220, 80, 700, "Wingbeat pitch"), num("doppler", 0.4, 0, 1, "Flying past (pitch and level swell)")),
    tags=("buzz", "insect", "fly", "bee", "wasp", "mosquito", "bug", "swarm", "creature"),
)
def insect_buzz(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = p["wing_hz"] * ctx.pitch * (1 + 0.04 * dsp.drift(n, sr, 5, ctx.rng)) * s.semitones(p["doppler"] * 2 * (0.5 - x))
    tone = s.bandpass(s.osc("saw", f, n, sr), 300, 4000, sr)
    env = (1 - p["doppler"]) + p["doppler"] * np.sin(np.pi * x) ** 2
    return norm(tone) * env * s.adsr(n, sr, attack=0.03, decay=0.01, sustain=1, release=0.05)
