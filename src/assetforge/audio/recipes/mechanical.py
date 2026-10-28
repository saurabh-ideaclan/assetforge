"""Machines and devices: doors, levers, gears, engines, servos, alarms, beeps, typing, switches."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import WAVES, RenderContext, choice, integer, num, recipe
from assetforge.audio.recipes.music import norm
from assetforge.audio.recipes.physical import PLATE, WOOD_PLANK, fade_tail, modal, rms_norm, strike
from assetforge.audio.recipes.weapons import ring


def click(ctx: RenderContext, n: int, start: int, freq: float, length: float = 0.02) -> np.ndarray:
    start = max(0, start)
    cn = min(n - start, int(length * ctx.sr))
    if cn <= 0:
        return np.zeros(n)
    c = norm(s.bandpass(s.noise(cn, ctx.rng), freq * 0.6, freq * 1.5, ctx.sr)) * s.perc(cn, ctx.sr, attack=0.0003, curve=8)
    return s.place(c, n, start)


@recipe(
    "door",
    "Door opening or closing: creak and latch or slam",
    duration=1.2,
    duration_range=(0.3, 4.0),
    params=(
        choice("action", "open", ("open", "close", "slam"), "What the door does"),
        choice("material", "wood", ("wood", "metal", "stone"), "Door material"),
        num("creak", 0.5, 0, 1, "Hinge creak"),
    ),
    tags=("door", "gate", "open", "close", "slam", "creak", "hinge", "latch", "house", "dungeon"),
)
def door(ctx: RenderContext, p: dict) -> np.ndarray:
    """Hinge creak while it swings, then the door's panel ringing as it hits the frame, and the latch."""
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    out = np.zeros(n)
    if p["creak"] > 0 and p["action"] != "slam":
        f = 160 * ctx.pitch * (1 + 0.25 * dsp.drift(n, sr, 3, ctx.rng))
        stick = dsp.one_pole_lowpass((ctx.rng.uniform(0, 1, n // 160 + 1) < 0.55).repeat(160)[:n].astype(float), 60, sr)
        creak = dsp.filt(dsp.filt(s.osc("saw", f, n, sr) * stick, "bandpass", 800, sr, q=2.5), "lowpass", 3000, sr)
        span = np.clip(x / (0.8 if p["action"] == "open" else 0.5), 0, 1)
        out += p["creak"] * rms_norm(creak) * 0.25 * np.sin(np.pi * span) * (span < 1)
    hit = int(n * {"open": 0.82, "close": 0.55, "slam": 0.03}[p["action"]])
    force = 1.0 if p["action"] == "slam" else 0.45
    material = p["material"]
    base, decay, ratios = {"wood": (150, 0.12, WOOD_PLANK), "metal": (260, 0.6, PLATE), "stone": (90, 0.18, (1, 2.2, 3.4))}[material]
    panel = modal(ctx, n, base, ratios, decay_s=decay * (1.3 if p["action"] == "slam" else 1.0), detune=0.003,
                  brightness=0.75 if material == "metal" else 0.5, at=hit)  # fmt: skip
    thud = np.zeros(n)
    k = n - hit
    if k > 0:
        thud[hit:] = s.osc("sine", s.sweep(150, 80, k), k, sr) * s.perc(k, sr, attack=0.002, curve=12)
    contact = strike(ctx, n, at=hit, ms=1.5, tone_hz=2500 if material != "metal" else 4500)
    latch = (strike(ctx, n, at=hit + int(0.045 * sr), ms=0.5, tone_hz=6000)
             + 0.5 * modal(ctx, n, 2300, (1, 2.7), decay_s=0.03, at=hit + int(0.045 * sr)))  # fmt: skip
    rattle = np.zeros(n)
    if material == "metal" and p["action"] == "slam":
        rattle = modal(ctx, n, 610, PLATE[3:], decay_s=0.25, detune=0.01, brightness=0.8, at=hit + int(0.01 * sr))
    impact = force * (rms_norm(panel) * 0.9 + rms_norm(thud) * 0.35 + 2.5 * contact / (np.abs(contact).max() + 1e-9)
                      + 0.3 * rms_norm(rattle))  # fmt: skip
    mix = out + impact + 0.6 * latch / (np.abs(latch).max() + 1e-9)
    return fade_tail(dsp.filt(mix, "highpass", 45, sr), sr)


@recipe(
    "lever",
    "Lever or switch pulled: a ratchety pull and heavy clunk",
    duration=0.6,
    duration_range=(0.15, 2.0),
    params=(num("weight", 0.6, 0, 1, "Heavier is lower and slower"),),
    tags=("lever", "pull", "switch", "mechanism", "clunk", "puzzle", "dungeon", "activate"),
)
def lever(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = sum(click(ctx, n, int(n * k * 0.1), 1800 - 900 * p["weight"]) * 0.4 for k in range(5))
    hit = int(n * 0.55)
    hn = n - hit
    clunk = ring(ctx, (90, 230, 470), hn, 20) + norm(s.lowpass(s.noise(hn, ctx.rng), 700, sr)) * s.perc(hn, sr, curve=9)
    return out + s.place(norm(clunk), n, hit)


@recipe(
    "gear_ratchet",
    "Gears or ratchet turning: regular mechanical ticks",
    duration=1.0,
    duration_range=(0.2, 5.0),
    params=(num("rate", 14, 2, 60, "Ticks per second"), num("size", 0.5, 0, 1, "Bigger gears are lower")),
    tags=("gear", "gears", "ratchet", "clockwork", "crank", "winch", "mechanism", "machine", "tick"),
)
def gear_ratchet(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    step = sr / p["rate"]
    for k in range(int(n / step)):
        out += click(ctx, n, int(k * step + ctx.rng.uniform(-0.05, 0.05) * step), (2600 - 1800 * p["size"]) * ctx.pitch, 0.015)
    return out


@recipe(
    "engine_rev",
    "Motor or engine revving up and settling",
    duration=1.6,
    duration_range=(0.4, 6.0),
    params=(
        num("idle_hz", 35, 15, 120, "Firing rate at idle"),
        num("rev", 1.0, 0, 2, "How far it revs, in octaves"),
        choice("engine", "car", ("car", "motorbike", "truck", "drone", "spaceship"), "Engine type"),
    ),
    tags=("engine", "motor", "rev", "car", "vehicle", "motorbike", "truck", "drone", "drive", "race"),
)
def engine_rev(ctx: RenderContext, p: dict) -> np.ndarray:
    """Combustion pulses (one per cylinder firing, each slightly different) through an exhaust pipe's
    resonance: an engine's sound is a pulse train shaped by its pipe, not filtered noise."""
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    curve = np.sin(np.pi * np.clip(x / 0.75, 0, 1)) ** 0.8
    f = p["idle_hz"] * ctx.pitch * 2 ** (p["rev"] * curve)
    kind = p["engine"]
    if kind in ("drone", "spaceship"):
        tone = s.osc("saw", f * 4, n, sr) + s.osc("saw", f * 4.02, n, sr) + 0.5 * s.osc("sine", f * 2, n, sr)
        out = dsp.sweep_filter(tone, "lowpass", 600 + 3000 * curve, sr, q=1.5)
        return fade_tail(out * s.adsr(n, sr, attack=0.05, decay=0.01, sustain=1, release=0.25), sr)
    phase = dsp.phase_osc(f, n, sr)
    cycle = phase.astype(int)
    frac = phase % 1.0
    variance = ctx.rng.normal(1.0, 0.12, cycle.max() + 2)[cycle]  # every firing a little different
    pulse = np.exp(-frac * {"car": 14, "motorbike": 20, "truck": 9}[kind]) * np.sin(2 * np.pi * frac * 3) * variance
    pipe_hz = {"car": 140, "motorbike": 260, "truck": 85}[kind]
    period = max(2, int(sr / pipe_hz))
    a = np.zeros(period + 1)
    a[0], a[period] = 1.0, -0.55
    from scipy.signal import lfilter

    exhaust = lfilter([1.0], a, pulse)  # the pipe: a resonant comb
    body = dsp.sweep_filter(exhaust, "lowpass", (1200 + 3000 * curve) * (1.4 if kind == "motorbike" else 1.0), sr, q=1.1)
    body = dsp.filt(body, "peak", 2.5 * pipe_hz, sr, q=1.2, gain_db=5)  # the pipe's bark
    rumble = dsp.filt(exhaust, "lowpass", 160, sr)
    out = dsp.filt(rms_norm(dsp.filt(body, "highpass", 90, sr)) + 0.2 * rms_norm(rumble), "highpass", 40, sr)
    env = s.adsr(n, sr, attack=0.04, decay=0.01, sustain=1, release=0.3)
    return fade_tail(s.soft_clip(out * env * 0.45, 0.25), sr)


@recipe(
    "servo",
    "Robot servo or small motor whirr",
    duration=0.5,
    duration_range=(0.1, 3.0),
    params=(num("whine_hz", 900, 200, 4000, "Motor whine pitch"), num("travel", 0.5, 0, 1, "Pitch rise while moving")),
    tags=("servo", "robot", "motor", "whirr", "mechanical", "mech", "android", "move", "scifi", "machine"),
)
def servo(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = p["whine_hz"] * ctx.pitch * s.semitones(p["travel"] * 7 * np.sin(np.pi * x))
    tone = s.osc("square", f, n, sr, duty=0.3) * 0.5 + s.osc("saw", f * 0.25, n, sr) * 0.3
    return dsp.filt(tone + 0.1 * s.noise(n, ctx.rng), "lowpass", 4000, sr) * np.sin(np.pi * x) ** 0.4


@recipe(
    "alarm",
    "Alarm or siren: alternating or sweeping warning tone",
    duration=2.0,
    duration_range=(0.5, 8.0),
    params=(
        choice("pattern", "two_tone", ("two_tone", "sweep", "beep", "klaxon"), "Alarm style"),
        num("rate", 2.0, 0.3, 12, "Cycles per second"),
        num("tone_hz", 880, 200, 3000, "Main pitch"),
    ),
    tags=("alarm", "siren", "warning", "alert", "danger", "klaxon", "emergency", "beep", "scifi"),
)
def alarm(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    t = np.arange(n) / sr
    phase = (t * p["rate"]) % 1.0
    f0 = p["tone_hz"] * ctx.pitch
    if p["pattern"] == "two_tone":
        return s.osc("square", np.where(phase < 0.5, f0, f0 * 0.75), n, sr, duty=0.4) * 0.6
    if p["pattern"] == "sweep":
        return s.osc("saw", f0 * 2 ** (np.sin(2 * np.pi * p["rate"] * t) * 0.6), n, sr) * 0.5
    if p["pattern"] == "beep":
        return s.osc("sine", f0, n, sr) * (phase < 0.35)
    return s.lowpass(s.osc("saw", f0 * 0.4, n, sr) + s.osc("saw", f0 * 0.4 * 1.26, n, sr), 2000, sr) * (phase < 0.6)


@recipe(
    "beep_sequence",
    "Computer or terminal beeps: random blips in a sequence",
    duration=0.6,
    duration_range=(0.1, 4.0),
    params=(
        integer("beeps", 5, 1, 24, "Number of beeps"),
        num("height", 1200, 200, 4000, "Average pitch"),
        choice("wave", "square", WAVES, "Tone"),
    ),
    tags=("beep", "computer", "terminal", "data", "robot", "scan", "hack", "blip", "scifi", "ui", "processing"),
)
def beep_sequence(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    slot = n // p["beeps"]
    for k in range(p["beeps"]):
        bn = int(slot * ctx.rng.uniform(0.4, 0.8))
        f = p["height"] * ctx.pitch * s.semitones(int(ctx.rng.integers(-7, 8)))
        out[k * slot : k * slot + bn] = s.osc(p["wave"], f, bn, sr, duty=0.5) * s.adsr(bn, sr, attack=0.002, decay=0.01,
                                                                                       sustain=0.8, release=0.005)  # fmt: skip
    return s.lowpass(out, 7000, sr) * 0.7


@recipe(
    "typing",
    "Keyboard typing or typewriter keys",
    duration=1.2,
    duration_range=(0.2, 6.0),
    params=(num("speed", 8, 2, 20, "Keys per second"), choice("keyboard", "mechanical", ("mechanical", "laptop", "typewriter"), "Keyboard")),
    tags=("typing", "keyboard", "keys", "type", "typewriter", "text", "computer", "hack", "dialogue"),
)
def typing(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    t = 0.0
    freq = {"mechanical": 3200, "laptop": 4500, "typewriter": 2200}[p["keyboard"]] * ctx.pitch
    while (start := int(t * sr)) < n:
        out += click(ctx, n, start, freq * ctx.rng.uniform(0.85, 1.15), 0.015) * ctx.rng.uniform(0.5, 1)
        if p["keyboard"] == "typewriter":
            out += 0.5 * s.place(ring(ctx, (1900, 3300), int(0.04 * sr), 60), n, start)
        t += ctx.rng.exponential(1 / p["speed"])
    return out


@recipe(
    "switch",
    "Light switch, button or toggle click",
    duration=0.12,
    duration_range=(0.03, 0.5),
    params=(num("height", 2500, 600, 6000, "Click pitch"), num("double", 0.4, 0, 1, "Second contact click")),
    tags=("switch", "click", "toggle", "button", "press", "flip", "light", "mechanical"),
)
def switch(ctx: RenderContext, p: dict) -> np.ndarray:
    n = ctx.n
    out = click(ctx, n, 0, p["height"] * ctx.pitch)
    if p["double"] > 0:
        out += p["double"] * click(ctx, n, int(n * 0.3), p["height"] * 1.3 * ctx.pitch)
    return out
