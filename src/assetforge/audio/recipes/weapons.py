"""Weapons: gunshots, reloads, blades, bows and shields."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm


def ring(ctx: RenderContext, freqs: tuple[float, ...], n: int, decay: float) -> np.ndarray:
    """Inharmonic metal ring: decaying partials (higher ones die faster)."""
    t = np.arange(n) / ctx.sr
    return sum(np.sin(2 * np.pi * f * ctx.pitch * t + ctx.rng.uniform(0, 6.28)) * np.exp(-t * decay * (1 + i * 0.6))
               / (1 + i) for i, f in enumerate(freqs))  # fmt: skip


@recipe(
    "gunshot",
    "Firearm shot: sharp crack, body and a room or outdoor tail",
    duration=0.9,
    duration_range=(0.2, 3.0),
    params=(
        choice("weapon", "pistol", ("pistol", "rifle", "shotgun", "smg", "sniper", "blaster"), "Kind of gun"),
        num("power", 0.7, 0, 1, "Loudness of the body and low end"),
        num("tail", 0.5, 0, 1, "Echo tail length"),
        num("distance", 0.0, 0, 1, "0 close, 1 far away (duller, less crack)"),
    ),
    tags=("gun", "gunshot", "shot", "fire", "shoot", "pistol", "rifle", "shotgun", "weapon", "combat", "bullet"),
)
def gunshot(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    w = p["weapon"]
    body_hz = {"pistol": 140, "rifle": 110, "shotgun": 80, "smg": 160, "sniper": 90, "blaster": 600}[w] * ctx.pitch
    cn = min(n, int(0.006 * sr))
    crack = s.place(norm(s.highpass(s.noise(cn, ctx.rng), 2500, sr)) * s.perc(cn, sr, attack=0.0002, curve=2), n, 0)
    bn = min(n, int((0.18 if w != "shotgun" else 0.28) * sr))
    burst = norm(s.lowpass(s.noise(bn, ctx.rng), 4000 if w != "shotgun" else 2500, sr)) * s.perc(bn, sr, attack=0.0005, curve=5)
    thump = s.osc("sine", s.sweep(body_hz * 2.2, body_hz, bn), bn, sr) * s.perc(bn, sr, attack=0.0005, curve=6)
    body = s.place(burst + p["power"] * thump, n, 0)
    if w == "blaster":
        body = s.place(s.osc("saw", s.sweep(body_hz * 3, body_hz * 0.6, bn), bn, sr) * s.perc(bn, sr, curve=3), n, 0)
    tail = norm(s.lowpass(s.noise(n, ctx.rng, "pink"), 1800, sr)) * s.perc(n, sr, attack=0.02, curve=3 + 6 * (1 - p["tail"]))
    x = (1 - p["distance"] * 0.8) * crack * 1.2 + body + 0.35 * p["tail"] * tail
    if p["distance"] > 0:
        x = s.lowpass(x, 9000 - 7000 * p["distance"], sr)
    return s.soft_clip(x, 0.3 + 0.4 * p["power"])


@recipe(
    "reload",
    "Weapon reload: magazine out, in, and a slide or bolt rack",
    duration=0.9,
    duration_range=(0.3, 2.5),
    params=(
        num("weight", 0.5, 0, 1, "Heavier parts sound lower and duller"),
        num("clicks", 3, 1, 5, "Mechanical clicks", 0),
    ),
    tags=("reload", "magazine", "clip", "rack", "bolt", "gun", "weapon", "click", "cock"),
)
def reload(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.zeros(n)
    count = int(p["clicks"])
    for k in range(count):
        start = int((0.05 + 0.85 * k / max(1, count)) * n * ctx.rng.uniform(0.95, 1.0))
        cn = min(n - start, int(0.05 * sr))
        if cn <= 0:
            continue
        f = (2600 - 1400 * p["weight"]) * ctx.rng.uniform(0.8, 1.2) * ctx.pitch
        click = norm(s.bandpass(s.noise(cn, ctx.rng), f * 0.6, f * 1.6, sr)) * s.perc(cn, sr, attack=0.0003, curve=8)
        click += 0.5 * ring(ctx, (f * 1.1, f * 1.9), cn, 90)
        x += s.place(click, n, start)
    return x


@recipe(
    "sword_swing",
    "Blade swish through the air",
    duration=0.4,
    duration_range=(0.12, 1.2),
    params=(
        num("speed", 0.6, 0, 1, "Faster swings are higher and sharper"),
        num("ring", 0.2, 0, 1, "Metallic shimmer"),
    ),
    tags=("sword", "swing", "slash", "swish", "blade", "melee", "attack", "whoosh", "weapon"),
)
def sword_swing(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    centre = (700 + 2600 * p["speed"]) * ctx.pitch * (0.5 + np.sin(np.pi * x))
    swish = s.filter_track(s.noise(n, ctx.rng), "bandpass", centre, sr, bandwidth_octaves=0.8)
    env = np.sin(np.pi * x) ** (2 - p["speed"])
    return norm(swish) * env + p["ring"] * 0.3 * ring(ctx, (3100, 4700, 6900), n, 6) * env


@recipe(
    "blade_hit",
    "Blade striking metal or armour: a clang with ringing overtones",
    duration=0.7,
    duration_range=(0.15, 2.5),
    params=(
        num("hardness", 0.7, 0, 1, "Brighter, harder impact"),
        num("ring", 0.6, 0, 1, "How long the metal rings"),
    ),
    tags=("clang", "blade", "sword", "hit", "metal", "parry", "armor", "armour", "clash", "weapon", "impact"),
)
def blade_hit(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    tn = min(n, int(0.02 * sr))
    transient = s.place(norm(s.highpass(s.noise(tn, ctx.rng), 1500 + 3000 * p["hardness"], sr)) * s.perc(tn, sr, curve=3), n, 0)
    base = 900 + 900 * p["hardness"]
    metal = ring(ctx, (base, base * 2.41, base * 3.87, base * 5.3, base * 7.1), n, 30 - 25 * p["ring"])
    return transient + 0.7 * norm(metal)


@recipe(
    "bow_release",
    "Bow string twang and arrow whoosh",
    duration=0.5,
    duration_range=(0.15, 1.5),
    params=(
        num("tension", 0.6, 0, 1, "String pitch"),
        num("whoosh", 0.5, 0, 1, "Arrow flight noise"),
    ),
    tags=("bow", "arrow", "shoot", "twang", "string", "archer", "release", "weapon"),
)
def bow_release(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    twang = dsp.karplus((90 + 160 * p["tension"]) * ctx.pitch, n, sr, ctx.rng, decay=0.993, brightness=0.7)
    x = np.linspace(0, 1, n)
    whoosh = s.filter_track(s.noise(n, ctx.rng), "bandpass", 1500 + 2500 * x, sr) * np.exp(-((x - 0.3) / 0.2) ** 2)
    return norm(twang) + p["whoosh"] * 0.6 * norm(whoosh)


@recipe(
    "arrow_impact",
    "Arrow or dart thudding into wood or a target",
    duration=0.35,
    duration_range=(0.1, 1.0),
    params=(num("wobble", 0.5, 0, 1, "Shaft vibration after impact"),),
    tags=("arrow", "impact", "thud", "hit", "dart", "target", "stick", "wood"),
)
def arrow_impact(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    tn = min(n, int(0.05 * sr))
    thud = s.place(norm(s.lowpass(s.noise(tn, ctx.rng), 1200, sr)) * s.perc(tn, sr, curve=6), n, 0)
    t = np.arange(n) / sr
    shaft = np.sin(2 * np.pi * 160 * ctx.pitch * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 28 * t)) * np.exp(-t * 14)
    return thud + p["wobble"] * 0.5 * shaft


@recipe(
    "shield_block",
    "Heavy block on a shield: dull thump with a wooden or metal ring",
    duration=0.45,
    duration_range=(0.12, 1.5),
    params=(choice("material", "wood", ("wood", "metal"), "Shield material"), num("force", 0.7, 0, 1, "Hit strength")),
    tags=("shield", "block", "parry", "defend", "guard", "thump", "hit", "impact"),
)
def shield_block(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    body = s.osc("sine", s.sweep(180 * ctx.pitch, 90 * ctx.pitch, n), n, sr) * s.perc(n, sr, attack=0.001, curve=7)
    knock = norm(s.bandpass(s.noise(n, ctx.rng), 300, 1800, sr)) * s.perc(n, sr, attack=0.0005, curve=12)
    tone = ring(ctx, (420, 1130, 1780), n, 25) if p["material"] == "wood" else ring(ctx, (780, 1890, 3020, 4410), n, 9)
    return s.soft_clip(p["force"] * body + 0.8 * knock + 0.4 * norm(tone), 0.3)
