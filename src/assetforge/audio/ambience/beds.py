"""Ambience beds: continuous stereo textures (wind, rain, water, fire, rooms, machines, crowds, drones…).

Every bed is `fn(n, sr, rng, intensity, params, period) -> (n, 2)`. Slow modulation uses `period`
(the loop length) so level and colour line up across the loop seam; the engine renders a little
past the loop and crossfades. Left and right use independent noise, so beds are genuinely wide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s

BedFn = Callable[[int, int, np.random.Generator, float, dict[str, Any], int], np.ndarray]


@dataclass(frozen=True)
class Bed:
    name: str
    description: str
    render: BedFn
    params: dict[str, float]  # parameter → default (all numeric)
    tags: tuple[str, ...]


BEDS: dict[str, Bed] = {}


def bed(name: str, description: str, params: dict[str, float] | None = None, tags: tuple[str, ...] = ()):
    def decorate(fn: BedFn) -> BedFn:
        BEDS[name] = Bed(name, description, fn, params or {}, tags)
        return fn

    return decorate


def _norm(x: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    return x / peak if peak > 0 else x


def _rms_norm(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    return x / (rms * 4) if rms > 0 else x


def stereo(fn: Callable[[np.random.Generator], np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Render a mono generator twice with independent randomness → (n, 2)."""
    seeds = rng.integers(0, 2**31, 2)
    return np.stack([fn(np.random.default_rng(int(seeds[0]))), fn(np.random.default_rng(int(seeds[1])))], axis=1)


def impulses(n: int, rate_per_s: float, sr: int, rng: np.random.Generator, *, shape: float = 1.5) -> np.ndarray:
    """Random clicks at `rate_per_s` with heavy-tailed amplitudes (a few loud, many soft)."""
    x = np.zeros(n)
    count = rng.poisson(rate_per_s * n / sr)
    if count:
        x[rng.integers(0, n, count)] += rng.pareto(shape, count).clip(0, 8) * rng.choice([-1, 1], count)
    return x


def grains(n: int, sr: int, rng: np.random.Generator, rate: float, f_lo: float, f_hi: float, length: float,
           *, sweep: float = 0.0) -> np.ndarray:  # fmt: skip
    """Short sine grains (drops, bubbles, chirps) with random pitch in [f_lo, f_hi]."""
    out = np.zeros(n)
    count = rng.poisson(rate * n / sr)
    k = max(8, int(length * sr))
    t = np.arange(k) / sr
    env = np.exp(-t / (length / 4))
    for _ in range(count):
        i = int(rng.integers(0, max(1, n - k)))
        f = rng.uniform(f_lo, f_hi)
        phase = 2 * np.pi * f * (t + sweep * t * t / max(length, 1e-3))
        out[i : i + k] += np.sin(phase) * env * rng.uniform(0.2, 1.0)
    return out


# ---------------------------------------------------------------------- weather and nature


@bed("wind", "Wind: band-limited noise whose pitch and level wander", {"strength": 0.5, "howl": 0.2},
     ("wind", "breeze", "air", "storm", "outdoor", "weather"))  # fmt: skip
def wind(n, sr, rng, intensity, p, period):
    strength = p["strength"] * (0.4 + 0.8 * intensity)

    def one(r):
        centre = (220 + 900 * strength) * (1 + 0.45 * dsp.drift(n, sr, 0.12, r, period=period))
        air = dsp.sweep_filter(s.noise(n, r, "pink"), "bandpass", centre, sr, q=0.7, block=512)
        level = 0.55 + 0.45 * dsp.drift(n, sr, 0.18, r, period=period)
        howl = dsp.sweep_filter(s.noise(n, r), "bandpass", centre * 2.2, sr, q=14, block=512)
        return (_rms_norm(air) + p["howl"] * 0.6 * _rms_norm(howl)) * level

    return stereo(one, rng)


@bed("rain", "Rain: droplet patter, hiss and a soft roof rumble", {"heaviness": 0.5, "surface": 0.5},
     ("rain", "storm", "drizzle", "downpour", "weather", "wet"))  # fmt: skip
def rain(n, sr, rng, intensity, p, period):
    heavy = np.clip(p["heaviness"] * (0.3 + 1.0 * intensity), 0, 1.5)

    def one(r):
        drops = dsp.filt(impulses(n, 300 + 4000 * heavy, sr, r), "bandpass", 2500 + 2500 * p["surface"], sr, q=1.2)
        hiss = s.highpass(s.noise(n, r), 3000, sr) * (0.3 + 0.7 * heavy)
        rumble = s.lowpass(s.noise(n, r, "pink"), 500, sr) * heavy
        return _rms_norm(drops) + 0.6 * _rms_norm(hiss) * 0.5 + 0.4 * _rms_norm(rumble)

    return stereo(one, rng)


@bed("water_stream", "Stream or brook: babbling water with bubbles", {"flow": 0.5},
     ("stream", "brook", "river", "creek", "water", "nature"))  # fmt: skip
def water_stream(n, sr, rng, intensity, p, period):
    flow = p["flow"] * (0.5 + intensity)

    def one(r):
        babble = sum(_rms_norm(s.bandpass(s.noise(n, r), lo, hi, sr)) * (0.5 + 0.5 * dsp.drift(n, sr, rate, r))
                     for lo, hi, rate in ((300, 900, 5), (900, 2500, 8), (2500, 6000, 11)))  # fmt: skip
        bubbles = grains(n, sr, r, 6 + 25 * flow, 400, 1600, 0.03, sweep=1.2)
        return _rms_norm(babble) + 0.4 * _rms_norm(bubbles) * flow

    return stereo(one, rng)


@bed("ocean_waves", "Ocean waves rolling in and washing out", {"wave_seconds": 7.0, "surf": 0.5},
     ("ocean", "sea", "waves", "beach", "shore", "surf", "coast", "water"))  # fmt: skip
def ocean_waves(n, sr, rng, intensity, p, period):
    waves = max(1, round(period / sr / max(p["wave_seconds"], 2)))
    phase = (np.arange(n) % period) / period * waves % 1.0
    swell = np.where(phase < 0.35, (phase / 0.35) ** 2, np.exp(-(phase - 0.35) * 5))

    def one(r):
        shift = int(r.integers(0, sr))
        env = np.roll(swell, shift)
        body = dsp.sweep_filter(s.noise(n, r, "pink"), "lowpass", 300 + 3500 * env * (0.5 + p["surf"]), sr, block=512)
        foam = s.highpass(s.noise(n, r), 2500, sr) * env**2 * p["surf"]
        return (_rms_norm(body) * (0.25 + 0.75 * env) + 0.3 * _rms_norm(foam)) * (0.6 + 0.4 * intensity)

    return stereo(one, rng)


@bed("fire_crackle", "Campfire or hearth: low roar with crackles and pops", {"size": 0.5},
     ("fire", "campfire", "hearth", "fireplace", "torch", "burn", "crackle", "flame"))  # fmt: skip
def fire_crackle(n, sr, rng, intensity, p, period):
    size = p["size"] * (0.5 + intensity)

    def one(r):
        roar = s.lowpass(s.noise(n, r, "brown"), 300 + 300 * size, sr) * (0.7 + 0.3 * dsp.drift(n, sr, 1.5, r))
        crackle = dsp.filt(impulses(n, 15 + 60 * size, sr, r, shape=1.2), "highpass", 1800, sr)
        pops = dsp.filt(impulses(n, 2 + 6 * size, sr, r, shape=1.0), "bandpass", 900, sr, q=2)
        return 0.6 * _rms_norm(roar) * size + _rms_norm(crackle) + 0.5 * _rms_norm(pops)

    return stereo(one, rng)


@bed("forest_air", "Quiet outdoor air with leaf rustle", {"rustle": 0.5},
     ("forest", "woods", "leaves", "trees", "outdoor", "nature", "park"))  # fmt: skip
def forest_air(n, sr, rng, intensity, p, period):
    def one(r):
        air = s.lowpass(s.noise(n, r, "pink"), 2500, sr)
        rustle = s.highpass(s.noise(n, r), 2500, sr) * np.clip(dsp.drift(n, sr, 0.6, r, period=period), 0, 1) ** 2
        return _rms_norm(air) * 0.7 + p["rustle"] * (0.5 + intensity) * _rms_norm(rustle)

    return stereo(one, rng)


@bed("insects_night", "Night insects: crickets chirping and a cicada buzz", {"crickets": 4, "cicada": 0.3},
     ("crickets", "insects", "night", "cicada", "summer", "nature", "evening"))  # fmt: skip
def insects_night(n, sr, rng, intensity, p, period):
    out = np.zeros((n, 2))
    t = np.arange(n) / sr
    for _ in range(int(p["crickets"] * (0.5 + intensity))):
        f, rate = rng.uniform(3800, 5200), rng.uniform(2.5, 4.5)
        pulses = (np.sin(2 * np.pi * 30 * t) > 0.2) * ((t * rate + rng.uniform()) % 1.0 < 0.3)
        voice = np.sin(2 * np.pi * f * t) * dsp.one_pole_lowpass(pulses.astype(float), 400, sr) * rng.uniform(0.3, 1)
        out += dsp.pan(voice, rng.uniform(-0.9, 0.9))
    cicada = stereo(lambda r: s.bandpass(s.noise(n, r), 4000, 7000, sr) * (0.5 + 0.5 * np.sin(2 * np.pi * 90 * t)), rng)
    return _rms_norm(out) + p["cicada"] * _rms_norm(cicada)


@bed("swamp", "Swamp: humid drone, croaks and wet bubbles", {"croak": 0.5},
     ("swamp", "marsh", "bog", "frog", "wetland", "jungle", "nature"))  # fmt: skip
def swamp(n, sr, rng, intensity, p, period):
    def one(r):
        hum = s.bandpass(s.noise(n, r, "pink"), 150, 900, sr) * (0.7 + 0.3 * dsp.drift(n, sr, 0.3, r, period=period))
        bubbles = grains(n, sr, r, 3 + 4 * intensity, 120, 380, 0.06, sweep=1.5)
        return _rms_norm(hum) + 0.6 * _rms_norm(bubbles)

    return stereo(one, rng)


@bed("blizzard", "Blizzard: howling wind with blown snow hiss", {"strength": 0.8},
     ("blizzard", "snow", "storm", "ice", "winter", "arctic", "cold", "wind"))  # fmt: skip
def blizzard(n, sr, rng, intensity, p, period):
    gusts = wind(n, sr, rng, intensity, {"strength": p["strength"], "howl": 0.6}, period)
    hiss = stereo(lambda r: s.highpass(s.noise(n, r), 4500, sr) * (0.6 + 0.4 * dsp.drift(n, sr, 0.5, r)), rng)
    return gusts + 0.25 * _rms_norm(hiss) * (0.5 + intensity)


@bed("desert_wind", "Dry desert wind with sand hiss", {"strength": 0.4},
     ("desert", "sand", "dunes", "dry", "wind", "wasteland", "heat"))  # fmt: skip
def desert_wind(n, sr, rng, intensity, p, period):
    base = wind(n, sr, rng, intensity, {"strength": p["strength"], "howl": 0.1}, period)
    sand = stereo(lambda r: s.bandpass(s.noise(n, r), 3000, 9000, sr) * np.clip(dsp.drift(n, sr, 0.4, r, period=period), 0, 1), rng)
    return base + 0.3 * _rms_norm(sand)


# ---------------------------------------------------------------------- places and machines


@bed("room_tone", "Quiet room tone: air, faint electrical hum", {"hum": 0.2},
     ("room", "indoor", "interior", "quiet", "office", "house", "silence"))  # fmt: skip
def room_tone(n, sr, rng, intensity, p, period):
    t = np.arange(n) / sr
    hum = sum(np.sin(2 * np.pi * 50 * k * t) / k for k in (1, 2, 3)) * p["hum"] * 0.1
    return stereo(lambda r: _rms_norm(s.lowpass(s.noise(n, r, "pink"), 900, sr)) + hum, rng)


@bed("machine_hum", "Machinery hum with a rhythmic mechanical pulse", {"hum_hz": 55, "pulse_hz": 1.5, "grit": 0.4},
     ("machine", "factory", "engine", "generator", "industrial", "hum", "reactor", "engine room"))  # fmt: skip
def machine_hum(n, sr, rng, intensity, p, period):
    t = np.arange(n) / sr
    f = p["hum_hz"]
    tone = sum(s.osc("saw", f * k * (1 + 0.002 * k), n, sr) / k for k in (1, 2, 3, 4))
    tone = dsp.filt(tone, "lowpass", 800 + 1500 * intensity, sr, q=1.5)
    cycles = max(1, round(period / sr * p["pulse_hz"]))
    pulse = (0.5 + 0.5 * np.cos(2 * np.pi * cycles * (np.arange(n) % period) / period)) ** 4
    clank = stereo(lambda r: s.bandpass(s.noise(n, r), 400, 3000, sr), rng) * pulse[:, None] * p["grit"]
    return dsp.pan(_rms_norm(tone) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.3 * t)), 0) * 1.4 + _rms_norm(clank) * (0.4 + intensity)


@bed("electrical_buzz", "Electrical buzz: transformer or flickering lights", {"hz": 60, "flicker": 0.4},
     ("electric", "buzz", "power", "transformer", "neon", "lights", "lab", "hum"))  # fmt: skip
def electrical_buzz(n, sr, rng, intensity, p, period):
    buzz = dsp.filt(s.osc("saw", p["hz"] * 2, n, sr), "bandpass", 1200, sr, q=0.8)
    gate = 1 - p["flicker"] * (dsp.drift(n, sr, 9, rng) > 0.75)
    return dsp.pan(_rms_norm(buzz) * gate, 0.1)


@bed("crowd_murmur", "Crowd murmur: many indistinct voices", {"voices": 8, "excitement": 0.3},
     ("crowd", "people", "tavern", "market", "chatter", "murmur", "city", "inn", "town", "voices"))  # fmt: skip
def crowd_murmur(n, sr, rng, intensity, p, period):
    out = np.zeros((n, 2))
    for _ in range(int(p["voices"] * (0.5 + intensity))):
        r = np.random.default_rng(int(rng.integers(2**31)))
        f0 = r.uniform(95, 240) * (1 + 0.08 * dsp.drift(n, sr, 3, r))
        source = s.osc("saw", f0, n, sr) * 0.4 + s.noise(n, r, "pink") * 0.3
        formant = 500 + 1500 * (0.5 + 0.5 * dsp.drift(n, sr, 5, r))
        voiced = dsp.sweep_filter(source, "bandpass", formant, sr, q=3, block=512)
        syllables = np.clip(dsp.drift(n, sr, 4 + 3 * p["excitement"], r) * 1.5, 0, 1)
        talk = np.clip((dsp.drift(n, sr, 0.3, r) - 0.05) * 4, 0, 1)  # voices fade in and out of the chatter
        out += dsp.pan(_rms_norm(voiced) * syllables * talk, r.uniform(-0.8, 0.8))
    return dsp.filt(_rms_norm(out), "lowpass", 3500, sr)


@bed("traffic", "Distant city traffic: rumble and passing cars", {"cars_per_minute": 12},
     ("traffic", "city", "street", "cars", "urban", "road", "highway"))  # fmt: skip
def traffic(n, sr, rng, intensity, p, period):
    rumble = stereo(lambda r: s.lowpass(s.noise(n, r, "brown"), 250, sr), rng)
    out = _rms_norm(rumble)
    for _ in range(rng.poisson(p["cars_per_minute"] * (0.5 + intensity) * n / sr / 60)):
        k = int(rng.uniform(3, 7) * sr)
        start = int(rng.integers(0, n))
        x = np.linspace(-1, 1, k)
        env = np.exp(-(x / 0.35) ** 2)
        car = dsp.sweep_filter(s.noise(k, rng, "pink"), "lowpass", 400 + 1500 * env, sr, block=512) * env
        seg = dsp.pan(_rms_norm(car), x * rng.choice([-0.8, 0.8]))
        dsp.add_at(out, seg[: n - start], start)
    return out


@bed("space_drone", "Deep space drone: slow detuned pad with a faint shimmer", {"root_hz": 55, "shimmer": 0.3},
     ("space", "drone", "cosmic", "void", "station", "scifi", "ambient", "eerie", "spaceship"))  # fmt: skip
def space_drone(n, sr, rng, intensity, p, period):
    f = p["root_hz"]

    def one(r):
        pad = sum(s.osc("saw", f * ratio * (1 + r.uniform(-0.004, 0.004)), n, sr) for ratio in (1, 1.5, 2, 3))
        pad = dsp.sweep_filter(pad, "lowpass", 300 + 700 * (0.5 + 0.5 * dsp.drift(n, sr, 0.05, r, period=period)), sr, q=2)
        t = np.arange(n) / sr
        shimmer = sum(np.sin(2 * np.pi * f * k * t + r.uniform(0, 6)) for k in (16, 24, 32)) * (
            0.5 + 0.5 * dsp.drift(n, sr, 0.2, r, period=period))  # fmt: skip
        return _rms_norm(pad) + p["shimmer"] * 0.15 * shimmer

    return stereo(one, rng)


@bed("cave_resonance", "Cave air: resonant low rumble of a big stone space", {"size": 0.6},
     ("cave", "cavern", "underground", "dungeon", "mine", "tunnel", "crypt"))  # fmt: skip
def cave_resonance(n, sr, rng, intensity, p, period):
    modes = [rng.uniform(60, 120) * (1.6 - p["size"]) * k for k in (1, 1.7, 2.6, 3.4)]

    def one(r):
        src = s.noise(n, r, "brown")
        x = sum(dsp.filt(src, "bandpass", m, sr, q=10) / (1 + i) for i, m in enumerate(modes))
        return _rms_norm(x) * (0.8 + 0.2 * dsp.drift(n, sr, 0.1, r, period=period))

    return stereo(one, rng)


@bed("lava_bubble", "Lava: deep rumble, thick bubbling and hiss", {"bubbles": 0.6},
     ("lava", "magma", "volcano", "molten", "fire", "hell", "inferno"))  # fmt: skip
def lava_bubble(n, sr, rng, intensity, p, period):
    def one(r):
        rumble = s.lowpass(s.noise(n, r, "brown"), 150, sr)
        bubbles = grains(n, sr, r, 1 + 6 * p["bubbles"] * (0.5 + intensity), 60, 200, 0.12, sweep=2)
        hiss = s.highpass(s.noise(n, r), 3500, sr) * np.clip(dsp.drift(n, sr, 0.5, r), 0, 1)
        return _rms_norm(rumble) + 0.8 * _rms_norm(bubbles) + 0.15 * _rms_norm(hiss)

    return stereo(one, rng)


@bed("underwater", "Underwater: muffled pressure, distant movement, bubbles", {"bubbles": 0.4},
     ("underwater", "ocean", "sea", "deep", "submarine", "diving", "aquatic"))  # fmt: skip
def underwater(n, sr, rng, intensity, p, period):
    def one(r):
        body = s.lowpass(s.noise(n, r, "brown"), 350, sr) * (0.7 + 0.3 * dsp.drift(n, sr, 0.15, r, period=period))
        bubbles = s.lowpass(grains(n, sr, r, 1 + 8 * p["bubbles"], 250, 900, 0.05, sweep=1.5), 1500, sr)
        return _rms_norm(body) + 0.5 * _rms_norm(bubbles)

    return stereo(one, rng)
