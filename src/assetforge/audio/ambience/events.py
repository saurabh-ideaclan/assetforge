"""Ambience events: sparse one-shots scattered over a bed (birds, drips, thunder, howls, creaks…).

Each event is `fn(rng, sr) -> mono`, a fresh random instance every time it fires. Many reuse the SFX
recipes with randomized parameters, so improving a recipe improves the scenes that use it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s

EventFn = Callable[[np.random.Generator, int], np.ndarray]


@dataclass(frozen=True)
class Event:
    name: str
    description: str
    render: EventFn
    tags: tuple[str, ...]


EVENTS: dict[str, Event] = {}


def event(name: str, description: str, tags: tuple[str, ...] = ()):
    def decorate(fn: EventFn) -> EventFn:
        EVENTS[name] = Event(name, description, fn, tags)
        return fn

    return decorate


def from_recipe(name: str, rng: np.random.Generator, sr: int, ranges: dict[str, Any]) -> np.ndarray:
    """Render recipe `name` with each param drawn from `ranges` (tuple → uniform, list → choice, else fixed)."""
    from assetforge.audio.generate import apply_brightness
    from assetforge.audio.recipes import get_recipe, render_recipe, resolve_params

    recipe = get_recipe(name)
    chosen: dict[str, Any] = {}
    for key, spec in ranges.items():
        if isinstance(spec, tuple):
            value = rng.uniform(*spec)
        elif isinstance(spec, list):
            value = spec[int(rng.integers(len(spec)))]
        else:
            value = spec
        p = recipe.param(key)
        if p is not None and p.kind == "int":
            value = int(round(float(value)))
        if p is not None and p.kind != "choice":
            assert p.min is not None and p.max is not None
            value = float(np.clip(value, p.min, p.max)) if p.kind == "float" else int(np.clip(value, p.min, p.max))
        chosen[key] = value
    params, _ = resolve_params(recipe, overrides=chosen)
    x = render_recipe(recipe, params, sr, int(rng.integers(2**31)))
    return apply_brightness(x, float(params["brightness"]), sr)


@event("bird_chirp", "Songbird chirps and short calls", ("bird", "birds", "chirp", "songbird", "forest", "day", "nature"))
def bird_chirp(rng, sr):
    return from_recipe("chirp", rng, sr, {"height": (2500, 6500), "notes": (1, 6), "sweep": (-14, 14),
                                          "duration": (0.15, 0.8)})  # fmt: skip


@event("songbird", "A longer warbling bird song phrase", ("bird", "song", "warble", "dawn", "forest", "nature"))
def songbird(rng, sr):
    parts = [from_recipe("chirp", rng, sr, {"height": (2000, 5000), "notes": (1, 3), "sweep": (-8, 8),
                                            "duration": (0.08, 0.25)}) for _ in range(int(rng.integers(4, 9)))]  # fmt: skip
    gap = np.zeros(int(0.04 * sr))
    return np.concatenate([np.concatenate([p, gap]) for p in parts])


@event("crow", "Crow or raven caw", ("crow", "raven", "caw", "bird", "spooky", "graveyard"))
def crow(rng, sr):
    from assetforge.audio.recipes.base import RenderContext
    from assetforge.audio.recipes.creatures import voice

    out = []
    for _ in range(int(rng.integers(1, 4))):
        n = int(rng.uniform(0.25, 0.4) * sr)
        ctx = RenderContext(sr=sr, duration=n / sr, pitch=1.0, rng=rng)
        x = np.linspace(0, 1, n)
        f0 = rng.uniform(500, 700) * s.semitones(-4 * x)
        out += [voice(ctx, f0, "a", 0.2, 0.9, 0.3) * np.sin(np.pi * x) ** 0.5, np.zeros(int(0.15 * sr))]
    return np.concatenate(out)


@event("owl", "Owl hoots", ("owl", "hoot", "night", "forest", "spooky", "nature"))
def owl(rng, sr):
    f = rng.uniform(320, 420)
    out = []
    for k in range(int(rng.integers(2, 5))):
        n = int((0.35 if k else 0.18) * sr)
        x = np.linspace(0, 1, n)
        out += [s.osc("sine", f * s.semitones(-1.5 * x), n, sr) * np.sin(np.pi * x) ** 1.5, np.zeros(int(0.12 * sr))]
    return s.lowpass(np.concatenate(out), 1500, sr)


@event("frog", "Frog croaks", ("frog", "toad", "croak", "swamp", "pond", "night", "nature"))
def frog(rng, sr):
    rate, f = rng.uniform(18, 40), rng.uniform(150, 400)
    n = int(rng.uniform(0.2, 0.6) * sr)
    t = np.arange(n) / sr
    pulses = (np.sin(2 * np.pi * rate * t) > 0.3).astype(float)
    tone = dsp.filt(s.osc("saw", f, n, sr) * pulses, "bandpass", f * 3, sr, q=3)
    return tone * np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5


@event("cricket_chirp", "A single cricket's chirp", ("cricket", "insect", "night", "chirp"))
def cricket_chirp(rng, sr):
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    return np.sin(2 * np.pi * rng.uniform(3800, 5200) * t) * (np.sin(2 * np.pi * 30 * t) > 0.2) * np.sin(np.pi * t / t[-1])


@event("drip", "Water drip", ("drip", "drop", "water", "cave", "leak", "dungeon", "sewer"))
def drip(rng, sr):
    return from_recipe("water_drop", rng, sr, {"height": (700, 2600), "duration": 0.3})


@event("thunder", "Thunder clap or distant roll", ("thunder", "storm", "lightning", "weather"))
def thunder(rng, sr):
    return from_recipe("thunder", rng, sr, {"distance": (0.2, 0.95), "roll": (0.3, 0.9), "duration": (3, 7)})


@event("creak", "Wood creak: floorboards, beams or a ship's hull", ("creak", "wood", "house", "ship", "old", "haunted"))
def creak(rng, sr):
    n = int(rng.uniform(0.4, 1.4) * sr)
    f = rng.uniform(120, 300) * (1 + 0.3 * dsp.drift(n, sr, 3, rng))
    stick = (rng.uniform(0, 1, n // 128 + 1) < 0.6).repeat(128)[:n].astype(float)
    x = dsp.filt(s.osc("saw", f, n, sr) * dsp.one_pole_lowpass(stick, 80, sr), "bandpass", rng.uniform(600, 1400), sr, q=3)
    return x * np.sin(np.pi * np.linspace(0, 1, n))


@event("distant_howl", "Wolf or creature howling in the distance", ("howl", "wolf", "night", "distant", "spooky", "werewolf"))
def distant_howl(rng, sr):
    from assetforge.audio.recipes.base import RenderContext
    from assetforge.audio.recipes.creatures import voice

    n = int(rng.uniform(1.8, 3.2) * sr)
    ctx = RenderContext(sr=sr, duration=n / sr, pitch=1.0, rng=rng)
    x = np.linspace(0, 1, n)
    f0 = rng.uniform(330, 420) * s.semitones(7 * np.sin(np.pi * np.clip(x * 1.4, 0, 1)) - 3 * x)
    f0 = s.vibrato(f0, n, sr, rate_hz=5, depth_semitones=0.25)
    return voice(ctx, f0, "u", 0.3, 0.1, 0.15) * np.minimum(1, x * 5) * (1 - x) ** 0.7


@event("chime", "Wind chimes", ("chime", "chimes", "bell", "wind", "garden", "temple", "peaceful"))
def chime(rng, sr):
    notes = [72, 74, 76, 79, 81, 84, 86]
    n = int(3 * sr)
    out = np.zeros(n)
    for _ in range(int(rng.integers(2, 6))):
        start = int(rng.uniform(0, 1.2) * sr)
        k = n - start
        out[start:] += dsp.fm(s.note_hz(notes[int(rng.integers(len(notes)))]), 3.5, 2.0, k, sr) * s.perc(k, sr, attack=0.002, curve=3)
    return out


@event("bell_toll", "Distant church or temple bell", ("bell", "church", "toll", "temple", "town", "village", "medieval"))
def bell_toll(rng, sr):
    n = int(4 * sr)
    f = rng.uniform(180, 320)
    t = np.arange(n) / sr
    partials = ((0.5, 1.0), (1.0, 0.8), (1.19, 0.5), (1.5, 0.4), (2.0, 0.6), (2.5, 0.25), (3.0, 0.2))
    return sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t * (0.8 + r)) for r, a in partials)


@event("radio_chatter", "Radio chatter: muffled voices through a radio", ("radio", "chatter", "comms", "military", "police", "scifi"))
def radio_chatter(rng, sr):
    n = int(rng.uniform(1.0, 3.0) * sr)
    f0 = rng.uniform(100, 200) * (1 + 0.1 * dsp.drift(n, sr, 3, rng))
    voiced = dsp.sweep_filter(s.osc("saw", f0, n, sr) + 0.3 * s.noise(n, rng), "bandpass",
                              600 + 1400 * (0.5 + 0.5 * dsp.drift(n, sr, 6, rng)), sr, q=3)  # fmt: skip
    syllables = np.clip(dsp.drift(n, sr, 6, rng) * 1.6, 0, 1)
    x = s.soft_clip(s.bandpass(voiced * syllables, 450, 3000, sr) * 3, 0.6)
    click = np.zeros(int(0.03 * sr))
    click[0] = 1
    return np.concatenate([click, x, s.highpass(s.noise(int(0.15 * sr), rng), 1000, sr) * 0.3])


@event("footsteps_far", "Someone walking nearby", ("footsteps", "steps", "walking", "people", "town", "dungeon"))
def footsteps_far(rng, sr):
    surface = ["stone", "wood", "gravel", "grass"][int(rng.integers(4))]
    step = int(rng.uniform(0.45, 0.65) * sr)
    out = np.zeros(step * 8)
    for k in range(int(rng.integers(3, 8))):
        dsp.add_at(out, from_recipe("footstep", rng, sr, {"surface": surface, "weight": (0.3, 0.7)}), k * step)
    return out


@event("metal_clank", "Metal clank: pipes, chains or machinery", ("clank", "metal", "pipe", "chain", "factory", "dungeon", "ship"))
def metal_clank(rng, sr):
    return from_recipe("blade_hit", rng, sr, {"hardness": (0.2, 0.7), "ring": (0.3, 0.9), "pitch": (-14, -4)})


@event("steam_hiss", "Steam vent hiss", ("steam", "hiss", "vent", "pipe", "factory", "machine"))
def steam_hiss(rng, sr):
    return from_recipe("hiss", rng, sr, {"pitch_hz": (3000, 7000), "duration": (0.6, 2.5)})


@event("computer_beeps", "Computer and console beeps", ("computer", "beep", "console", "lab", "station", "scifi", "terminal"))
def computer_beeps(rng, sr):
    return from_recipe("beep_sequence", rng, sr, {"beeps": (2, 7), "height": (700, 2400), "duration": (0.2, 0.8),
                                                  "wave": ["square", "sine", "triangle"]})  # fmt: skip


@event("bubble", "Bubbles rising", ("bubble", "bubbles", "underwater", "swamp", "lava", "potion"))
def bubble(rng, sr):
    return from_recipe("bubble", rng, sr, {"count": (2, 10), "height": (250, 1200), "duration": (0.3, 1.2)})


@event("distant_boom", "Far-off explosion or artillery", ("boom", "explosion", "war", "artillery", "battle", "distant"))
def distant_boom(rng, sr):
    return from_recipe("distant_boom", rng, sr, {"distance": (0.5, 1.0), "duration": (1.5, 3.5)})


@event("monster_far", "A creature growling somewhere out of sight", ("growl", "monster", "creature", "beast", "dungeon", "horror"))
def monster_far(rng, sr):
    return from_recipe("growl", rng, sr, {"size": (0.5, 1.0), "rough": (0.4, 0.9), "duration": (0.8, 2.0)})


@event("bat_squeak", "Bat squeaks", ("bat", "squeak", "cave", "night", "spooky"))
def bat_squeak(rng, sr):
    return from_recipe("squeak", rng, sr, {"height": (4000, 6000), "bend": (-6, 6), "duration": (0.04, 0.12)})


@event("glass_clink", "Glasses and mugs clinking", ("glass", "clink", "tavern", "inn", "bar", "pub", "cheers"))
def glass_clink(rng, sr):
    return from_recipe("pickup_item", rng, sr, {"material": ["glass", "metal"], "duration": (0.2, 0.5)})


@event("laugh_far", "Distant laughter", ("laugh", "laughter", "tavern", "crowd", "party", "people"))
def laugh_far(rng, sr):
    from assetforge.audio.recipes.base import RenderContext
    from assetforge.audio.recipes.creatures import voice

    out = []
    f = rng.uniform(140, 260)
    for k in range(int(rng.integers(3, 7))):
        n = int(rng.uniform(0.1, 0.16) * sr)
        ctx = RenderContext(sr=sr, duration=n / sr, pitch=1.0, rng=rng)
        out += [voice(ctx, np.full(n, f * s.semitones(-k * 0.6)), "a", 0.5, 0.2, 0.5) * np.sin(np.pi * np.linspace(0, 1, n)),
                np.zeros(int(0.06 * sr))]  # fmt: skip
    return np.concatenate(out)
