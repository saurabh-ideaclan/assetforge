"""Foley: footsteps on many surfaces, cloth, item handling, breaking, splashing, bubbles."""

from __future__ import annotations

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.weapons import ring

SURFACES = ("grass", "stone", "wood", "metal", "snow", "water", "sand", "gravel", "carpet", "mud")


def grains(ctx: RenderContext, n: int, count: int, low: float, high: float, length: float = 0.006) -> np.ndarray:
    """Many tiny filtered clicks: crunch, gravel, snow, grass."""
    out = np.zeros(n)
    sr = ctx.sr
    for _ in range(count):
        i = int(ctx.rng.uniform(0, 0.9) * n)
        k = min(n - i, max(4, int(ctx.rng.uniform(0.3, 1.0) * length * sr)))
        out[i : i + k] += ctx.rng.uniform(-1, 1, k) * np.linspace(1, 0, k) * ctx.rng.uniform(0.3, 1)
    return s.bandpass(out, low, high, sr)


def contact(ctx: RenderContext, n: int, surface: str, weight: float) -> np.ndarray:
    sr = ctx.sr
    thump = s.osc("sine", s.sweep(140 * ctx.pitch, 60 * ctx.pitch, n), n, sr) * s.perc(n, sr, attack=0.002, curve=10)
    if surface == "grass":
        body = grains(ctx, n, 120, 1500, 7000, 0.004) * s.perc(n, sr, attack=0.01, curve=3)
    elif surface == "gravel":
        body = grains(ctx, n, 80, 600, 5000, 0.01) * s.perc(n, sr, attack=0.003, curve=3)
    elif surface == "snow":
        body = grains(ctx, n, 160, 400, 3000, 0.008) * s.perc(n, sr, attack=0.02, curve=2)
    elif surface == "sand":
        body = s.bandpass(s.noise(n, ctx.rng), 1200, 6000, sr) * s.perc(n, sr, attack=0.01, curve=4) * 0.6
    elif surface == "stone":
        body = norm(s.bandpass(s.noise(n, ctx.rng), 1500, 5000, sr)) * s.perc(n, sr, attack=0.0005, curve=14) + 0.15 * ring(
            ctx, (1900, 3100), n, 60)  # fmt: skip
    elif surface == "wood":
        body = norm(ring(ctx, (310, 720, 1240), n, 35)) * 0.8 + norm(s.bandpass(s.noise(n, ctx.rng), 500, 2500, sr)) * s.perc(
            n, sr, attack=0.0005, curve=16)  # fmt: skip
    elif surface == "metal":
        body = norm(ring(ctx, (520, 1430, 2810, 4170), n, 14)) * 0.6 + norm(s.highpass(s.noise(n, ctx.rng), 2000, sr)) * s.perc(
            n, sr, attack=0.0003, curve=18)  # fmt: skip
    elif surface == "water":
        body = norm(s.bandpass(s.noise(n, ctx.rng), 300, 3000, sr)) * s.perc(n, sr, attack=0.01, curve=3) + 0.5 * bubbles(ctx, n, 4)
    elif surface == "mud":
        body = norm(s.lowpass(s.noise(n, ctx.rng, "pink"), 900, sr)) * s.perc(n, sr, attack=0.01, curve=3) + 0.4 * bubbles(ctx, n, 2, 300)
    else:  # carpet
        body = norm(s.lowpass(s.noise(n, ctx.rng), 1200, sr)) * s.perc(n, sr, attack=0.004, curve=8) * 0.5
    return weight * thump + norm(body)


def bubbles(ctx: RenderContext, n: int, count: int, base: float = 600) -> np.ndarray:
    out = np.zeros(n)
    sr = ctx.sr
    for _ in range(count):
        i = int(ctx.rng.uniform(0, 0.8) * n)
        k = min(n - i, int(ctx.rng.uniform(0.01, 0.04) * sr))
        if k < 8:
            continue
        f = base * ctx.pitch * ctx.rng.uniform(0.7, 2.0)
        out[i : i + k] += s.osc("sine", s.sweep(f, f * 1.8, k), k, sr) * s.perc(k, sr, attack=0.002, curve=2)
    return out


@recipe(
    "footstep",
    "Single footstep (heel then toe) on a chosen surface",
    duration=0.3,
    duration_range=(0.08, 1.0),
    params=(
        choice("surface", "stone", SURFACES, "Ground material"),
        num("weight", 0.5, 0, 1, "Heavier steps have more low thump"),
        num("roll", 0.5, 0, 1, "Heel-to-toe gap"),
    ),
    tags=("footstep", "footsteps", "step", "walk", "run", "foot", "boots", "shoes", "foley", "player", "movement"),
)
def footstep(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    heel = contact(ctx, n, p["surface"], p["weight"])
    gap = int((0.03 + 0.06 * p["roll"]) * sr)
    toe = s.place(contact(ctx, max(1, n - gap), p["surface"], p["weight"] * 0.4), n, gap) * 0.5
    return heel + toe


@recipe(
    "cloth",
    "Cloth rustle: clothing, cape, bag or bedding movement",
    duration=0.5,
    duration_range=(0.1, 2.5),
    params=(num("stiffness", 0.4, 0, 1, "Stiffer fabric is brighter and crisper"),),
    tags=("cloth", "rustle", "fabric", "clothes", "cape", "bag", "equip", "inventory", "foley", "movement"),
)
def cloth(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    texture = grains(ctx, n, int(200 + 400 * p["stiffness"]), 800 + 2000 * p["stiffness"], 9000, 0.003)
    air = s.bandpass(s.noise(n, ctx.rng), 500, 3000, sr) * 0.3
    return (norm(texture) + air) * np.sin(np.pi * x) ** 0.8


@recipe(
    "pickup_item",
    "Picking up or handling a physical item (not a game jingle)",
    duration=0.3,
    duration_range=(0.08, 1.0),
    params=(choice("material", "wood", ("wood", "metal", "glass", "paper", "leather", "stone"), "Item material"),),
    tags=("pickup", "grab", "take", "item", "handle", "loot", "inventory", "foley", "object"),
)
def pickup_item(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    m = p["material"]
    if m == "paper":
        return norm(grains(ctx, n, 300, 1500, 9000, 0.004)) * s.perc(n, sr, attack=0.01, curve=2)
    if m == "leather":
        return norm(s.bandpass(s.noise(n, ctx.rng), 300, 2000, sr)) * s.perc(n, sr, attack=0.01, curve=3)
    tones = {"wood": (420, 980, 1700), "metal": (1100, 2650, 4400), "glass": (2200, 5100, 7900), "stone": (380, 900)}[m]
    knock = ring(ctx, tones, n, 25 if m in ("wood", "stone") else 10)
    return norm(knock) + 0.5 * norm(s.bandpass(s.noise(n, ctx.rng), 800, 4000, sr)) * s.perc(n, sr, attack=0.001, curve=12)


@recipe(
    "drop",
    "Object dropped on the floor: impact and a couple of small bounces",
    duration=0.6,
    duration_range=(0.15, 2.0),
    params=(
        choice("material", "wood", ("wood", "metal", "glass", "stone", "plastic"), "Object material"),
        num("bounces", 2, 0, 5, "Small bounces after the first hit", 0),
    ),
    tags=("drop", "fall", "clatter", "thud", "object", "item", "floor", "foley", "impact"),
)
def drop(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    tones = {"wood": (380, 900, 1600), "metal": (900, 2300, 3900, 5600), "glass": (2400, 5300),
             "stone": (240, 610), "plastic": (700, 1600)}[p["material"]]  # fmt: skip
    out = np.zeros(n)
    t, level = 0.0, 1.0
    for _ in range(int(p["bounces"]) + 1):
        start = int(t * sr)
        if start >= n:
            break
        k = n - start
        hit = norm(ring(ctx, tones, k, 20 if p["material"] not in ("metal", "glass") else 8)) * 0.7
        hit += norm(s.bandpass(s.noise(k, ctx.rng), 400, 4000, sr)) * s.perc(k, sr, attack=0.0005, curve=20)
        out += s.place(hit * level, n, start)
        t += 0.12 * level + 0.03
        level *= 0.45
    return out


@recipe(
    "glass_break",
    "Glass shattering into tinkling shards",
    duration=1.2,
    duration_range=(0.3, 3.0),
    params=(num("size", 0.5, 0, 1, "Bigger panes: more crash and more shards"),),
    tags=("glass", "break", "shatter", "smash", "window", "bottle", "crash", "destroy", "foley"),
)
def glass_break(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    cn = min(n, int(0.15 * sr))
    crash = s.place(norm(s.highpass(s.noise(cn, ctx.rng), 2500, sr)) * s.perc(cn, sr, attack=0.0005, curve=3), n, 0)
    shards = np.zeros(n)
    for _ in range(int(15 + 50 * p["size"])):
        i = int(ctx.rng.beta(1.2, 3) * n * 0.9)
        k = min(n - i, int(ctx.rng.uniform(0.02, 0.1) * sr))
        if k < 8:
            continue
        f = ctx.rng.uniform(2500, 7500) * ctx.pitch
        shards[i : i + k] += np.sin(2 * np.pi * f * np.arange(k) / sr) * s.perc(k, sr, attack=0.0005, curve=4) * ctx.rng.uniform(0.2, 1)
    return crash * (0.6 + 0.4 * p["size"]) + 0.6 * norm(shards)


@recipe(
    "wood_break",
    "Wood cracking and splintering: crate or plank breaking",
    duration=0.8,
    duration_range=(0.2, 2.5),
    params=(num("size", 0.5, 0, 1, "Thicker wood is lower and longer"),),
    tags=("wood", "break", "crack", "splinter", "crate", "barrel", "plank", "smash", "destroy", "foley"),
)
def wood_break(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    crack = norm(s.bandpass(s.noise(n, ctx.rng), 300 - 150 * p["size"], 3000, sr)) * s.perc(n, sr, attack=0.0005, curve=8)
    splinters = grains(ctx, n, int(60 + 120 * p["size"]), 800, 5000, 0.01) * s.perc(n, sr, attack=0.005, curve=2)
    body = norm(ring(ctx, (180 * (1.3 - p["size"]), 410, 760), n, 12))
    return crack + 0.7 * norm(splinters) + 0.5 * body


@recipe(
    "stone_crack",
    "Rock cracking or crumbling: boulder, wall or rubble",
    duration=1.0,
    duration_range=(0.2, 3.0),
    params=(num("rubble", 0.5, 0, 1, "Falling debris after the crack"),),
    tags=("stone", "rock", "crack", "crumble", "rubble", "boulder", "wall", "break", "debris", "cave"),
)
def stone_crack(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    crack = norm(s.lowpass(s.noise(n, ctx.rng), 2500, sr)) * s.perc(n, sr, attack=0.0005, curve=10)
    rumble = norm(s.lowpass(s.noise(n, ctx.rng, "brown"), 300, sr)) * s.perc(n, sr, attack=0.01, curve=2)
    debris = grains(ctx, n, int(20 + 80 * p["rubble"]), 300, 3000, 0.02) * s.perc(n, sr, attack=0.05, curve=1.5)
    return crack + 0.6 * rumble + p["rubble"] * norm(debris)


@recipe(
    "splash",
    "Water splash: something landing in water",
    duration=0.9,
    duration_range=(0.15, 3.0),
    params=(num("size", 0.5, 0, 1, "Bigger splashes: lower, longer, more droplets"),),
    tags=("splash", "water", "dive", "swim", "plop", "lake", "sea", "wet", "foley"),
)
def splash(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    wash = s.filter_track(s.noise(n, ctx.rng), "bandpass", (2000 - 1200 * p["size"]) * (1 + x), sr)
    wash = norm(wash) * np.minimum(1, x * 30) * np.exp(-x * (6 - 3 * p["size"]))
    drops = bubbles(ctx, n, int(6 + 20 * p["size"]), 900)
    return wash + 0.5 * norm(drops)


@recipe(
    "bubble",
    "Bubbles: a single blip or a stream",
    duration=0.4,
    duration_range=(0.05, 3.0),
    params=(num("count", 3, 1, 40, "Number of bubbles", 0), num("height", 700, 150, 3000, "Bubble pitch")),
    tags=("bubble", "bubbles", "blub", "underwater", "potion", "brew", "liquid", "water", "pop"),
)
def bubble(ctx: RenderContext, p: dict) -> np.ndarray:
    return bubbles(ctx, ctx.n, int(p["count"]), p["height"])
