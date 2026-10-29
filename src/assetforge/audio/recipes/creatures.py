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


def glottal_pulses(ctx: RenderContext, n: int, f0: np.ndarray, *, jitter: float, shimmer: float, doubling: np.ndarray,
                   open_quotient: float = 0.6) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """A rough voice built one vocal-fold cycle at a time: every period (`jitter`) and every pulse strength
    (`shimmer`) a little different, and in `doubling` stretches every other cycle weak (the subharmonic rattle
    of a growl). Returns (excitation, glottal flow)."""
    sr, rng = ctx.sr, ctx.rng
    starts, amps = [], []
    t, k = 0.0, 0
    while t < n:
        i = min(int(t), n - 1)
        period = max(sr / 800, sr / f0[i] * (1 + jitter * rng.standard_normal()))
        amp = np.exp(shimmer * rng.standard_normal())
        if k % 2 and rng.uniform() < doubling[i]:
            amp *= rng.uniform(0.2, 0.5)
        starts.append(t)
        amps.append(amp)
        t += period
        k += 1
    edges = np.array([*starts, t])
    idx = np.searchsorted(edges, np.arange(n), side="right") - 1
    length = edges[idx + 1] - edges[idx]
    flow = glottal_flow((np.arange(n) - edges[idx]) / length, open_quotient) * np.asarray(amps)[idx]
    return np.diff(flow, prepend=0.0) * length / 20, flow


def vocal_tract(x: np.ndarray, sr: int, formants: list, bandwidths: tuple[float, ...], block: int = 128) -> np.ndarray:
    """Cascade of two-pole resonators (an all-pole vocal tract); each formant may be a per-sample track."""
    from scipy.signal import lfilter

    out = x
    for formant, bw in zip(formants, bandwidths, strict=True):
        track = np.broadcast_to(np.asarray(formant, dtype=float), (len(x),))
        r = np.exp(-np.pi * bw / sr)
        y = np.empty(len(x))
        zi = np.zeros(2)
        for i in range(0, len(x), block):
            th = 2 * np.pi * min(track[min(len(x) - 1, i + block // 2)], sr * 0.45) / sr
            gain = (1 - r) * np.sqrt(1 - 2 * r * np.cos(2 * th) + r * r)  # about unity at the peak
            y[i : i + block], zi = lfilter([gain], [1, -2 * r * np.cos(th), r * r], out[i : i + block], zi=zi)
        out = y
    return out


def disperse(x: np.ndarray, sr: int, sections: int = 8, low: float = 200.0, high: float = 3000.0,
             q: float = 0.8) -> np.ndarray:  # fmt: skip
    """Allpass chain: smears each sharp pulse over a few milliseconds without changing the spectrum, the way a
    throat, mouth and room do, so a rough voice sounds dense instead of clicky (lower crest factor)."""
    from scipy.signal import lfilter

    for f in np.geomspace(low, high, sections):
        w = 2 * np.pi * f / sr
        alpha = np.sin(w) / (2 * q)
        x = lfilter([1 - alpha, -2 * np.cos(w), 1 + alpha], [1 + alpha, -2 * np.cos(w), 1 - alpha], x)
    return x


def beast(ctx: RenderContext, n: int, f0: np.ndarray, *, formants: list, rough: float, breath: float, air: float,
          spread: float = 0.3) -> np.ndarray:  # fmt: skip
    """A monster/animal voice as game creature sounds are made (matched to designed growls and roars): a
    continuous, rough voice whose pitch wanders all the time, breath riding on each pulse, through a moving
    vocal tract. No fixed pitch and no steady pulse rate: those two together are what makes an engine or a
    chainsaw (tests measure both)."""
    sr, rng = ctx.sr, ctx.rng
    f0 = np.asarray(f0, dtype=float) * 2 ** (spread * dsp.drift(n, sr, 6, rng) + 0.3 * spread * dsp.drift(n, sr, 1.2, rng))
    doubling = np.clip((0.2 + 0.45 * rough) * (0.5 + 0.8 * dsp.drift(n, sr, 3, rng)), 0, 1)
    excitation, flow = glottal_pulses(ctx, n, f0, jitter=0.04 + 0.06 * rough, shimmer=0.14 + 0.14 * rough, doubling=doubling)
    noise = dsp.filt(s.noise(n, rng, "white"), "highpass", 300, sr) * (0.25 + flow / (flow.max() + 1e-9))
    soft = dsp.one_pole_lowpass(excitation, 1200, sr)  # a soft glottal closure: no click on every cycle
    source = rms_norm(soft) + breath * rms_norm(noise)
    move = 1 + 0.08 * dsp.drift(n, sr, 1.5, rng)  # the jaw and tongue never hold still
    voice_ = rms_norm(disperse(vocal_tract(source, sr, [f * move for f in formants], (256, 352, 512, 672)), sr, 16))
    hiss = dsp.filt(dsp.filt(noise, "highpass", 1500, sr), "lowpass", 9000, sr)
    out = voice_ + air * rms_norm(hiss)
    return dsp.filt(dsp.filt(out, "highpass", 90, sr), "lowpass", 10000, sr)


VOWEL_CHOICES = tuple(VOWELS)
GROWL_FORMANTS = (550, 1220, 2850, 3500)  # closed-mouth growl at size 0.7
ROAR_FORMANTS = (780, 1700, 2850, 3500)  # open-mouth roar at size 0.8


@recipe(
    "growl",
    "Low menacing creature growl",
    duration=1.2,
    duration_range=(0.3, 4.0),
    params=(
        num("size", 0.7, 0, 1, "Body size: bigger is lower and darker (0.2 a dog, 0.7 a beast, 1 a giant)"),
        num("rough", 0.7, 0, 1, "Gravel in the voice"),
        choice("vowel", "o", VOWEL_CHOICES, "Mouth shape"),
    ),
    tags=("growl", "snarl", "monster", "beast", "creature", "dog", "wolf", "bear", "enemy", "threat", "animal"),
    version=3,
)
def growl(ctx: RenderContext, p: dict) -> np.ndarray:
    """A sustained, mouth-closed rumble that surges and eases, like a game creature's growl."""
    n, sr = ctx.n, ctx.sr
    size, rough = p["size"], p["rough"]
    surge = 0.75 + 0.25 * dsp.drift(n, sr, 1.5, ctx.rng)
    scale = 1 + 0.6 * (0.7 - size)  # a bigger body has a longer vocal tract: lower formants
    shape = [v / o for v, o in zip(VOWELS[p["vowel"]], VOWELS["o"], strict=True)] + [1.0]
    f0 = 130 * 2 ** (1.5 * (0.7 - size)) * ctx.pitch * (1 + 0.05 * surge)
    v = beast(ctx, n, np.full(n, f0), formants=[f * scale * k for f, k in zip(GROWL_FORMANTS, shape, strict=True)],
              rough=rough, breath=1.2, air=0.1)  # fmt: skip
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
    version=3,
)
def roar(ctx: RenderContext, p: dict) -> np.ndarray:
    """Open-mouthed and bright, unlike the growl: the jaw opens as it swells (formants rise), the pitch climbs
    with the effort and sags at the end, and the breath roars with it."""
    n, sr = ctx.n, ctx.sr
    size = p["size"]
    x = np.linspace(0, 1, n)
    swell = np.sin(np.pi * np.clip(x / 0.85, 0, 1)) ** 0.6
    scale = (1 + 0.6 * (0.8 - size)) * (0.85 + 0.2 * swell)
    f0 = 125 * 2 ** (1.5 * (0.8 - size)) * ctx.pitch * s.semitones(5 * swell - 3 * x)
    v = beast(ctx, n, f0, formants=[f * scale for f in ROAR_FORMANTS], rough=p["rough"], breath=0.4 + 2.0 * p["breath"],
              air=0.15, spread=0.25)  # fmt: skip
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
