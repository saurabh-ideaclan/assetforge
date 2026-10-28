"""Creature voices via formant synthesis: growls, roars, squeaks, chirps, hisses, slimes, insects."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import fade_tail

# Vowel formants (Hz) of an adult voice; creatures scale them by body size.
VOWELS = {"a": (800, 1150, 2900), "o": (450, 800, 2830), "u": (325, 700, 2530), "e": (400, 1600, 2700),
          "i": (300, 2300, 3000)}  # fmt: skip


def voice(ctx: RenderContext, f0: np.ndarray, vowel: str, size: float, rough: float, breath: float,
          *, opening: np.ndarray | None = None) -> np.ndarray:  # fmt: skip
    """An animal/monster voice: jittered vocal pulses, a subharmonic for rasp, breath noise that pulses with
    each glottal cycle (the "gravel"), irregular vocal-fry tremor, then wide formants and a chest resonance.

    `opening` (per sample, ~0.6..1.4) widens the mouth over time: formants rise as the jaw opens.
    """
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    # Pitch wanders slowly (intonation) with only a little fast jitter: fast random jitter smears every
    # harmonic into a band of noise, which is what made earlier voices sound hissy.
    f = np.broadcast_to(np.asarray(f0, dtype=float), (n,)) * (1 + 0.006 * rough * dsp.drift(n, sr, 18, rng)
                                                                + 0.02 * dsp.drift(n, sr, 3, rng))  # fmt: skip
    phase = dsp.phase_osc(f, n, sr) % 1.0
    pulses = s.osc("saw", f, n, sr) + 0.45 * rough * s.osc("saw", f * 0.5, n, sr)  # period doubling = rasp
    glottal = (1.0 - phase) ** 6  # a little breath on each pulse
    aspiration = dsp.filt(s.noise(n, rng, "pink"), "lowpass", 2500, sr) * glottal * (0.15 + 0.25 * rough)
    fry = 1.0 - 0.25 * rough * np.clip(dsp.drift(n, sr, 24, rng), 0, 1)
    source = pulses * fry + aspiration + 0.08 * breath * dsp.filt(s.noise(n, rng, "pink"), "lowpass", 3000, sr)
    source = s.soft_clip(source * (1 + 2 * rough), 0.3 + 0.4 * rough) / (1 + rough)  # grit from saturation, not noise
    scale = (1.55 - 0.8 * size) * (opening if opening is not None else 1.0)
    out = np.zeros(n)
    for k, (formant, gain) in enumerate(zip(VOWELS[vowel], (1.0, 0.7, 0.35), strict=True)):
        out += gain * dsp.sweep_filter(source, "bandpass", formant * scale, sr, q=2.2 + 1.5 * k, block=64)
    out += 0.08 * dsp.filt(source, "lowpass", 200 * (1.6 - size), sr, q=1.0)  # a hint of chest
    # Keep the weight in the mouth resonances: energy below ~100 Hz is inaudible on small speakers and
    # turns into intermodulation grit when the voice is saturated.
    out = dsp.filt(out, "highpass", 95 + 40 * (1 - size), sr, q=0.6)
    return dsp.filt(out, "lowpass", 5000, sr)


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
    f0 = (95 - 60 * p["size"]) * ctx.pitch * (1 + 0.12 * dsp.drift(n, sr, 2.5, ctx.rng))
    surge = 0.75 + 0.25 * dsp.drift(n, sr, 1.5, ctx.rng)  # the growl swells and eases
    v = voice(ctx, f0, p["vowel"], p["size"], 0.5 + 0.5 * p["rough"], 0.3, opening=0.85 + 0.15 * surge)
    env = s.adsr(n, sr, attack=0.12, decay=0.2, sustain=0.85, release=n / sr * 0.3) * surge
    return fade_tail(s.soft_clip(norm(v) * env, 0.25 * p["rough"]), sr)


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
    f0 = (150 - 105 * p["size"]) * ctx.pitch * s.semitones(5 * swell - 3 * x)
    v = voice(ctx, f0, "a", p["size"], 0.6 + 0.4 * p["rough"], p["breath"], opening=0.7 + 0.55 * swell)
    env = np.minimum(1, x * 8) * (1 - x) ** 0.5 * (0.35 + 0.65 * swell)
    return fade_tail(s.soft_clip(norm(v) * env * 1.3, 0.25 + 0.3 * p["rough"]), sr)


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
