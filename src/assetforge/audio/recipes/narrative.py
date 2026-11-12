"""Story-driven foley the visual novels kept asking for and the catalog lacked: knocks, a great bell, phones,
chains, pages, hooves and armour. Built from the same modal/strike/noise helpers as the other recipes, so they
reproduce bit for bit and take the common `pitch`/`brightness`/`duration` params and every preset
(`distant`, `muffled`, `huge` give the material-and-distance variants a scene needs)."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, integer, num, recipe
from assetforge.audio.recipes.foley import grains
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import WOOD_PLANK, exp_env, fade_tail, modal, rms_norm, strike


@recipe(
    "knock",
    "Knocking on a door: two to four knuckle raps on wood (or metal), with the panel's thump",
    duration=1.4,
    duration_range=(0.4, 4.0),
    params=(
        integer("knocks", 3, 1, 6, "How many raps"),
        choice("material", "wood", ("wood", "metal", "stone"), "What is knocked on"),
        num("urgency", 0.4, 0, 1, "Faster, harder knocking"),
    ),
    tags=("knock", "knocking", "door", "visitor", "rap", "foley", "house", "arrival"),
)
def knock(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    gap = 0.16 + 0.22 * (1 - p["urgency"])
    out = np.zeros(n)
    base, ratios, decay = {"wood": (210, WOOD_PLANK, 0.09), "metal": (520, (1, 2.4, 3.9, 5.6), 0.25),
                           "stone": (120, (1, 2.1, 3.3), 0.12)}[p["material"]]  # fmt: skip
    for i in range(p["knocks"]):
        at = int((0.01 + i * gap * (1 + 0.04 * ctx.rng.standard_normal())) * sr)
        if at >= n:
            break
        force = 0.8 + 0.4 * p["urgency"] + 0.1 * ctx.rng.standard_normal()
        panel = modal(ctx, n, base * ctx.pitch, ratios, decay_s=decay, detune=0.004, brightness=0.45, at=at)
        tap = strike(ctx, n, at=at, ms=1.2, tone_hz=2800 if p["material"] == "wood" else 4500)
        k = n - at
        thump = np.zeros(n)
        thump[at:] = s.osc("sine", s.sweep(140, 70, k), k, sr) * s.perc(k, sr, attack=0.002, curve=14)
        out += force * (rms_norm(panel) * 0.7 + 0.3 * rms_norm(thump) + 2.0 * tap / (np.abs(tap).max() + 1e-9))
    return fade_tail(dsp.filt(out, "highpass", 50, sr), sr)


@recipe(
    "bell",
    "A great bell tolling: a bronze bell struck once or more, with a long harmonic decay (cathedral, tower, ceremony)",
    duration=4.0,
    duration_range=(1.0, 12.0),
    params=(
        integer("strikes", 1, 1, 6, "Number of tolls"),
        num("size", 0.7, 0, 1, "Bigger bells are lower and ring longer"),
        num("cracked", 0.0, 0, 1, "A cracked bell: detuned partials and a dull, shorter ring"),
    ),
    tags=("bell", "toll", "tolling", "cathedral", "tower", "bronze", "ceremony", "medieval", "church", "alarm"),
)
def bell(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    hz = (420 - 300 * p["size"]) * ctx.pitch
    # bronze bell partials (hum, prime, tierce, quint, nominal) with the minor-third tierce that makes a bell a bell
    ratios = (0.5, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0)
    decay = 1.2 + 3.0 * p["size"]
    detune = 0.002 + 0.03 * p["cracked"]
    gap = max(0.6, (n / sr) / (p["strikes"] + 0.5))
    out = np.zeros(n)
    for i in range(p["strikes"]):
        at = int(i * gap * sr)
        if at >= n:
            break
        ring = modal(ctx, n, hz, ratios, decay_s=decay * (1 - 0.5 * p["cracked"]), detune=detune, brightness=0.35, at=at)
        clapper = strike(ctx, n, at=at, ms=3.0, tone_hz=1800)
        out += rms_norm(ring) + 0.8 * clapper / (np.abs(clapper).max() + 1e-9)
    if p["cracked"] > 0:
        out = dsp.filt(out, "lowpass", 2200 - 900 * p["cracked"], sr)
    return fade_tail(dsp.filt(out, "highpass", 40, sr), sr)


@recipe(
    "phone_ring",
    "A phone ringing: classic bell ring, electronic ringtone or a vibration buzz on a table, repeating until it stops",
    duration=3.0,
    duration_range=(0.5, 12.0),
    params=(
        choice("kind", "electronic", ("electronic", "classic", "vibrate"), "Ringer type"),
        integer("rings", 3, 1, 8, "Ring bursts"),
    ),
    tags=("phone", "ring", "ringing", "ringtone", "call", "mobile", "vibrate", "buzz", "notification", "modern"),
)
def phone_ring(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    period = (n / sr) / p["rings"]
    for i in range(p["rings"]):
        at = int(i * period * sr)
        burst = int(min(n - at, (0.9 if p["kind"] != "vibrate" else 0.7) * sr))
        if burst <= 0:
            break
        t = np.arange(burst) / sr
        if p["kind"] == "electronic":
            melody = s.steps([880, 1100, 880, 1320], burst) * ctx.pitch
            tone = s.osc("square", melody, burst, sr, duty=0.3) * (np.sin(2 * np.pi * 24 * t) > 0)
            seg = dsp.filt(tone, "lowpass", 4500, sr) * 0.5
        elif p["kind"] == "classic":
            seg = (modal(ctx, burst, 1400 * ctx.pitch, (1, 1.5, 2.7), decay_s=0.08, detune=0.01, brightness=0.3, at=0)
                   * 0.0)
            hammer = np.zeros(burst)
            for k in range(int(burst / sr * 20)):
                hammer += modal(ctx, burst, 1400 * ctx.pitch, (1, 1.5, 2.7), decay_s=0.08, detune=0.01, brightness=0.3,
                                at=int(k * sr / 20))  # fmt: skip
            seg = rms_norm(hammer) * 0.6
        else:
            buzz = s.osc("saw", 170 * ctx.pitch, burst, sr) * (np.sin(2 * np.pi * 85 * t) > 0)
            seg = dsp.filt(dsp.filt(buzz, "lowpass", 900, sr), "highpass", 60, sr) * 0.6
        env = np.ones(burst)
        env[-int(0.02 * sr):] = np.linspace(1, 0, int(0.02 * sr))
        env[: int(0.01 * sr)] = np.linspace(0, 1, int(0.01 * sr))
        out[at:at + burst] += seg * env
    return out


@recipe(
    "chain",
    "Chains: links dragging, rattling or pulling taut (dungeons, gates, shackles, drawbridges)",
    duration=1.5,
    duration_range=(0.3, 6.0),
    params=(
        choice("action", "rattle", ("rattle", "drag", "taut"), "What the chain does"),
        num("weight", 0.5, 0, 1, "Heavier links ring lower"),
    ),
    tags=("chain", "chains", "shackle", "links", "rattle", "drag", "dungeon", "gate", "drawbridge", "prison", "medieval"),
)
def chain(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    hz = (900 - 500 * p["weight"]) * ctx.pitch
    density = {"rattle": 28, "drag": 40, "taut": 6}[p["action"]]
    out = np.zeros(n)
    count = int(density * n / sr)
    for i in range(count):
        at = int(ctx.rng.uniform(0, 0.95) * n) if p["action"] != "taut" else int(min(0.9, i * 0.08) * n)
        link = modal(ctx, n, hz * ctx.rng.uniform(0.85, 1.2), (1, 1.6, 2.3, 3.1), decay_s=0.05 + 0.08 * p["weight"],
                     detune=0.01, brightness=0.6, at=at)  # fmt: skip
        out += rms_norm(link) * ctx.rng.uniform(0.3, 1.0)
    if p["action"] == "taut":
        k = int(0.3 * n)
        jerk = modal(ctx, n, hz * 0.6, (1, 1.4, 2.0), decay_s=0.25, detune=0.02, brightness=0.4, at=n - k)
        out += 1.5 * rms_norm(jerk)
    shape = np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5 if p["action"] == "drag" else np.ones(n)
    return fade_tail(dsp.filt(out * shape, "highpass", 120, sr), sr)


@recipe(
    "page_turn",
    "A page turning: paper lifted, flexed and laid down (books, letters, scrolls)",
    duration=0.7,
    duration_range=(0.2, 2.0),
    params=(choice("paper", "book", ("book", "letter", "parchment"), "Paper weight"),),
    tags=("page", "paper", "book", "letter", "scroll", "parchment", "turn", "read", "foley", "library"),
)
def page_turn(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    stiff = {"book": 0.4, "letter": 0.3, "parchment": 0.7}[p["paper"]]
    lift = grains(ctx, n, int(180 + 300 * stiff), 1500 + 2500 * stiff, 9000, 0.004) * np.exp(-6 * x)
    flex = s.bandpass(s.noise(n, ctx.rng), 2000 + 2000 * stiff, 9000, sr) * np.sin(np.pi * x) ** 3 * 0.5
    lay = grains(ctx, n, 60, 800, 4000, 0.008) * np.clip((x - 0.7) / 0.3, 0, 1) * (x > 0.7)
    return fade_tail(norm(lift + flex + lay) * 0.8, sr)


@recipe(
    "hooves",
    "Horse hooves: a horse walking, trotting or galloping past, on earth or stone",
    duration=3.0,
    duration_range=(0.8, 12.0),
    params=(
        choice("gait", "trot", ("walk", "trot", "gallop"), "Gait"),
        choice("ground", "earth", ("earth", "stone", "wood"), "Ground"),
        integer("horses", 1, 1, 6, "How many horses"),
    ),
    tags=("horse", "hooves", "hoof", "gallop", "trot", "rider", "cavalry", "road", "medieval", "approach"),
)
def hooves(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    rate = {"walk": 1.8, "trot": 3.2, "gallop": 4.4}[p["gait"]]
    base, ratios, decay = {"earth": (110, (1, 1.8), 0.05), "stone": (260, (1, 2.3, 3.5), 0.07), "wood": (170, WOOD_PLANK, 0.06)}[p["ground"]]
    out = np.zeros(n)
    for h in range(p["horses"]):
        offset = ctx.rng.uniform(0, 0.3)
        beats = int(rate * n / sr * (4 if p["gait"] != "gallop" else 3))
        for i in range(beats):
            at = int(((i / beats) + offset / beats + 0.01 * ctx.rng.standard_normal()) * n)
            if not 0 <= at < n:
                continue
            hit = modal(ctx, n, base * ctx.pitch * ctx.rng.uniform(0.9, 1.1), ratios, decay_s=decay, detune=0.01, brightness=0.3, at=at)
            k = n - at
            thud = np.zeros(n)
            thud[at:] = s.osc("sine", s.sweep(90, 50, k), k, sr) * s.perc(k, sr, attack=0.003, curve=18)
            out += rms_norm(hit) * 0.5 + rms_norm(thud) * 0.5
    return fade_tail(dsp.filt(out, "highpass", 40, sr), sr)


@recipe(
    "armor",
    "Armour and gear moving: mail rustle, plate clinks and leather creak as someone shifts, walks or draws up",
    duration=1.0,
    duration_range=(0.2, 4.0),
    params=(
        choice("kind", "mail", ("mail", "plate", "leather"), "Armour type"),
        num("movement", 0.5, 0, 1, "Small shift … striding"),
    ),
    tags=("armor", "armour", "mail", "plate", "knight", "guard", "soldier", "gear", "clink", "medieval", "movement"),
)
def armor(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    out = np.zeros(n)
    if p["kind"] == "mail":
        out += grains(ctx, n, int(400 + 600 * p["movement"]), 3000, 9000, 0.003) * np.sin(np.pi * x) ** 0.7
    elif p["kind"] == "plate":
        for _ in range(int(3 + 6 * p["movement"])):
            at = int(ctx.rng.uniform(0.05, 0.9) * n)
            out += rms_norm(modal(ctx, n, 1800 * ctx.pitch * ctx.rng.uniform(0.8, 1.3), (1, 1.5, 2.6), decay_s=0.12, detune=0.01, brightness=0.6, at=at)) * 0.5
        out += grains(ctx, n, 150, 2500, 8000, 0.003) * 0.3 * np.sin(np.pi * x)
    else:
        creak = dsp.filt(s.osc("saw", 220 * ctx.pitch * (1 + 0.3 * dsp.drift(n, sr, 2, ctx.rng)), n, sr), "bandpass", 700, sr, q=3)
        out += rms_norm(creak) * 0.3 * np.sin(np.pi * x) ** 1.5 + grains(ctx, n, 80, 600, 3000, 0.006) * 0.2
    return fade_tail(norm(out) * 0.8, sr)


@recipe(
    "steam_whistle",
    "A steam train whistle: one to four blasts of a breathy three-note chime whistle, the signal heard along a train "
    "(departure, warning, a count of blasts that means something)",
    duration=2.4,
    duration_range=(0.8, 8.0),
    params=(
        integer("blasts", 1, 1, 4, "How many blasts"),
        num("breath", 0.5, 0, 1, "Steam hiss in the tone"),
        num("distance", 0.4, 0, 1, "Farther away: duller, slower onset"),
    ),
    tags=("whistle", "steam", "train", "locomotive", "blast", "blasts", "signal", "departure", "railway", "engine", "halt"),
    version=2,
    presets={"one": {"params": {"blasts": 1}}, "single": {"params": {"blasts": 1}},
             "two": {"params": {"blasts": 2, "duration": 3.2}}, "double": {"params": {"blasts": 2, "duration": 3.2}},
             "three": {"params": {"blasts": 3, "duration": 4.0}}, "triple": {"params": {"blasts": 3, "duration": 4.0}}},  # fmt: skip
)
def steam_whistle(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    k = int(p["blasts"])
    gap = 0.22
    length = max(0.35, (n / sr - gap * (k - 1) - 0.12) / k)
    base = 370.0 * ctx.pitch
    out = np.zeros(n)
    for i in range(k):
        at = int(i * (length + gap) * sr)
        m = int(min(n - at, length * sr))
        if m <= 8:
            break
        t = np.arange(m) / sr
        dur = m / sr
        # the pitch scoops up as steam pressure builds and sags as the cord is let go; a slow waver keeps it alive
        scoop = 1 - 0.07 * np.exp(-t / 0.08)
        sag = 1 - 0.04 * np.clip((t - (dur - 0.25)) / 0.25, 0, 1)
        waver = 1 + 0.004 * np.sin(2 * np.pi * (4.5 + ctx.rng.uniform(-0.5, 0.5)) * t + ctx.rng.uniform(0, 2 * np.pi))
        seg = np.zeros(m)
        for ratio in (1.0, 1.26, 1.5):  # a major-triad chime: three pipes on one valve
            f = base * ratio * (1 + 0.003 * ctx.rng.standard_normal()) * scoop * sag * waver
            seg += 0.5 * s.osc("triangle", f, m, sr) + 0.18 * s.osc("sine", 2 * f, m, sr) + 0.07 * s.osc("sine", 3 * f, m, sr)
        hiss = dsp.filt(dsp.filt(s.noise(m, ctx.rng, "white"), "highpass", 1800, sr), "lowpass", 7000, sr)
        air = dsp.filt(s.noise(m, ctx.rng, "pink"), "bandpass", base * 1.5, sr, q=1.2)
        seg = seg / 3 + p["breath"] * (0.22 * rms_norm(hiss) * 0.2 + 0.35 * rms_norm(air) * 0.2)
        attack = 0.05 + 0.12 * p["distance"]
        env = np.clip(t / attack, 0, 1) * np.clip((dur - t) / 0.22, 0, 1)
        out[at:at + m] += seg * env
    return dsp.filt(out, "lowpass", 6500 - 4500 * p["distance"], sr) * 0.8
