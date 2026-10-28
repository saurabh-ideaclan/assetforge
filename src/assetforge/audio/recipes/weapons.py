"""Weapons: gunshots, reloads, blades, bows and shields."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import STEEL_BAR, exp_env, fade_tail, modal, n_wave, rms_norm, strike

BLADE = (1.0, 1.27, 1.52, 1.6, 1.84, 2.63)  # blade mode ratios measured from real clashes (2.9-7.6 kHz lines)


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
    crack = n_wave(ctx, n, ms={"pistol": 2.5, "rifle": 1.6, "shotgun": 4.0, "smg": 2.0, "sniper": 1.4, "blaster": 3}[w],
                   tone_hz=9000 - 6000 * p["distance"])  # fmt: skip
    bn = min(n, int((0.18 if w != "shotgun" else 0.28) * sr))
    burst = s.lowpass(s.noise(bn, ctx.rng, "pink"), 3000 if w != "shotgun" else 2000, sr) * s.perc(bn, sr, attack=0.0005, curve=6)
    thump = s.osc("sine", s.sweep(body_hz * 2.2, body_hz, bn), bn, sr) * s.perc(bn, sr, attack=0.0005, curve=6)
    body = s.place(rms_norm(burst) * 0.25 + p["power"] * thump, n, 0)
    if w == "blaster":
        body = s.place(s.osc("saw", s.sweep(body_hz * 3, body_hz * 0.6, bn), bn, sr) * s.perc(bn, sr, curve=3), n, 0)
    tail = s.lowpass(s.noise(n, ctx.rng, "brown"), 900, sr) * s.perc(n, sr, attack=0.03, curve=3 + 6 * (1 - p["tail"]))
    x = (1 - p["distance"] * 0.7) * norm(crack) * 1.2 + norm(body) + 0.3 * p["tail"] * norm(tail)
    if p["distance"] > 0:
        x = s.lowpass(x, 9000 - 7000 * p["distance"], sr)
    return fade_tail(s.soft_clip(x, 0.2 + 0.3 * p["power"]), sr)


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
    """Magazine out (click + slide), magazine in (thunk + latch), then the bolt racked back and snapping forward."""
    n, sr = ctx.n, ctx.sr
    heavy = p["weight"]
    out = np.zeros(n)

    def click(at: float, hz: float, level: float = 1.0) -> None:
        i = int(at * n)
        out[:] += level * (strike(ctx, n, at=i, ms=0.6, tone_hz=7000) * 0.6
                           + 0.4 * modal(ctx, n, hz * (1.3 - 0.5 * heavy), STEEL_BAR[:3], decay_s=0.025, at=i))  # fmt: skip

    def slide(start: float, length: float, hz: float, level: float) -> None:
        i, k = int(start * n), int(length * n)
        k = min(k, n - i)
        if k <= 8:
            return
        friction = np.clip(dsp.drift(k, sr, 60, ctx.rng), 0, 1) ** 2
        scrape = dsp.filt(s.noise(k, ctx.rng, "pink"), "bandpass", hz, sr, q=2.5) * friction * np.hanning(k)
        out[i : i + k] += level * rms_norm(scrape) * 0.12

    click(0.04, 2600, 0.7)                     # release button
    slide(0.07, 0.16, 1800, 0.8)               # magazine slides out
    out[:] += 0.8 * modal(ctx, n, 420 * (1.2 - 0.4 * heavy), (1, 2.3, 3.9), decay_s=0.05, at=int(0.42 * n))  # mag in: thunk
    click(0.43, 2200, 1.0)
    for k in range(int(p["clicks"]) - 2):
        click(0.5 + 0.03 * k, 3000, 0.4)
    slide(0.62, 0.1, 2400, 1.0)                # bolt back
    click(0.71, 1900, 1.0)
    slide(0.74, 0.06, 2800, 0.7)               # forward
    click(0.8, 1600, 1.2)                      # snaps home
    return fade_tail(out, sr)


@recipe(
    "sword_swing",
    "Blade swish through the air",
    duration=0.4,
    duration_range=(0.12, 1.2),
    params=(
        num("speed", 0.6, 0, 1, "Faster swings are higher and sharper"),
        num("ring", 0.5, 0, 1, "Steel blade singing (0 = pure air swish)"),
    ),
    tags=("sword", "swing", "slash", "swish", "blade", "melee", "attack", "whoosh", "weapon", "steel"),
)
def sword_swing(ctx: RenderContext, p: dict) -> np.ndarray:
    """Air whoosh shaped by the blade's speed, plus the steel itself singing as it cuts through."""
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    speed = np.exp(-((x - 0.45) / 0.18) ** 2)  # fastest mid-swing
    centre = (500 + 1800 * p["speed"]) * ctx.pitch * (0.5 + speed)
    air = dsp.sweep_filter(s.noise(n, ctx.rng, "pink"), "bandpass", centre, sr, q=1.6, block=64) * speed ** (1.5 - p["speed"])
    steel = modal(ctx, n, 1250, STEEL_BAR[:4], decay_s=0.5, brightness=0.55, at=int(0.3 * n)) * np.clip(speed * 1.6, 0, 1)
    shing = dsp.filt(s.noise(n, ctx.rng, "pink"), "bandpass", 4200, sr, q=4) * speed ** 3
    out = rms_norm(air) + p["ring"] * 3.0 * rms_norm(steel) * 0.5 + p["ring"] * 0.6 * rms_norm(shing)
    return fade_tail(out * s.adsr(n, sr, attack=0.02, decay=0.01, sustain=1, release=n / sr * 0.3), sr)


@recipe(
    "blade_hit",
    "Blade striking metal or armour: a bright steel clash with a short ring",
    duration=0.8,
    duration_range=(0.15, 2.5),
    params=(
        num("hardness", 0.7, 0, 1, "Brighter, harder impact"),
        num("ring", 0.6, 0, 1, "How long the metal rings"),
    ),
    tags=("clang", "blade", "sword", "hit", "metal", "parry", "armor", "armour", "clash", "weapon", "impact"),
    version=2,
)
def blade_hit(ctx: RenderContext, p: dict) -> np.ndarray:
    """Steel on steel, measured on real clashes: mostly a 2-10 kHz grinding burst (~40 ms) over dense, high blade
    modes (3-8 kHz) that die within ~0.2 s, plus a faint long ring. Not a low bell-like clang."""
    n, sr = ctx.n, ctx.sr
    h, r = p["hardness"], p["ring"]
    base = 3200 * (0.8 + 0.3 * h) * ctx.rng.uniform(0.94, 1.06)
    ratios = tuple(q * ctx.rng.uniform(0.97, 1.03) for q in BLADE)
    clang = modal(ctx, n, base, ratios, decay_s=0.05 + 0.12 * r, damping=0.6, detune=0.003, brightness=1.0)
    tail = modal(ctx, n, base, ratios[:4], decay_s=0.5 + 0.8 * r, damping=0.3, detune=0.002, brightness=0.8)
    grind = dsp.filt(s.noise(n, ctx.rng), "bandpass", 5500, sr, q=0.6) * exp_env(n, sr, 0.03 + 0.02 * h, attack=0.003)
    hit = strike(ctx, n, ms=0.4, tone_hz=9000)
    knock = dsp.filt(s.noise(n, ctx.rng, "pink"), "lowpass", 400, sr) * exp_env(n, sr, 0.05, attack=0.002)  # arm and hilt
    out = (2.0 * rms_norm(grind) + 0.6 * rms_norm(clang) + 0.06 * r * rms_norm(tail) + 0.8 * norm(hit)
           + 0.1 * rms_norm(knock))  # fmt: skip
    return fade_tail(out, sr)


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
    """The string snaps forward (a low, quickly damped twang), the limbs thump, and the arrow hisses away."""
    n, sr = ctx.n, ctx.sr
    f = (85 + 90 * p["tension"]) * ctx.pitch
    k = min(n, int(0.35 * sr))
    twang = dsp.karplus(f, k, sr, ctx.rng, decay=0.985, brightness=0.45) * np.exp(-np.arange(k) / (0.09 * sr))
    thump = s.osc("sine", s.sweep(f * 1.6, f * 0.8, k), k, sr) * s.perc(k, sr, attack=0.001, curve=7)
    x = np.linspace(0, 1, n)
    flight = np.exp(-((x - 0.3) / 0.16) ** 2)
    whoosh = dsp.sweep_filter(s.noise(n, ctx.rng, "pink"), "bandpass", 1200 + 1800 * flight, sr, q=1.8, block=64) * flight
    out = s.place(norm(twang) + 0.6 * norm(thump), n, 0) + p["whoosh"] * 0.5 * norm(whoosh)
    return fade_tail(out, sr)


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
