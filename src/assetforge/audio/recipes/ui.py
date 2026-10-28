"""Interface sounds: clicks, hovers, confirms, cancels and errors."""

from __future__ import annotations

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import WAVES, RenderContext, choice, integer, num, recipe
from assetforge.audio.recipes.music import note_sequence


@recipe(
    "ui_click",
    "Short, crisp interface click or tap",
    duration=0.05,
    duration_range=(0.015, 0.2),
    params=(
        num("tone_hz", 2500, 600, 8000, "Pitch of the click's tick"),
        num("noise", 0.5, 0, 1, "Amount of noisy transient"),
        num("body", 0.3, 0, 1, "Low thump under the click"),
    ),
    tags=("ui", "click", "tap", "button", "press", "menu", "interface"),
)
def ui_click(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    tone = p["tone_hz"] * ctx.pitch
    tick = s.osc("sine", tone, n, sr) * s.perc(n, sr, attack=0.0005, curve=8)
    noisy = s.highpass(s.noise(n, ctx.rng), 2000, sr) * s.perc(n, sr, attack=0.0002, curve=14)
    body = s.osc("triangle", tone / 5, n, sr) * s.perc(n, sr, attack=0.001, curve=6)
    return tick * (1 - 0.5 * p["noise"]) + noisy * p["noise"] + body * p["body"]


@recipe(
    "ui_hover",
    "Soft blip for hovering or focusing a menu item",
    duration=0.08,
    duration_range=(0.03, 0.4),
    params=(
        num("tone_hz", 1400, 300, 5000, "Starting pitch"),
        num("glide", 3, -12, 12, "Pitch glide over the blip, in semitones", 0.05),
        choice("wave", "sine", WAVES, "Oscillator waveform"),
    ),
    tags=("ui", "hover", "focus", "blip", "select", "menu", "interface"),
)
def ui_hover(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, d = ctx.n, ctx.sr, ctx.duration
    start = p["tone_hz"] * ctx.pitch
    freq = s.sweep(start, start * s.semitones(p["glide"]), n)
    env = s.adsr(n, sr, attack=0.006, decay=d * 0.3, sustain=0.4, release=d * 0.5)
    return s.osc(p["wave"], freq, n, sr) * env


@recipe(
    "ui_confirm",
    "Positive two-note rising blip for confirm, accept or select",
    duration=0.18,
    duration_range=(0.06, 0.8),
    params=(
        integer("base_note", 84, 48, 108, "First note (MIDI number; 84 = C6)"),
        integer("interval", 7, 1, 12, "Rise to the second note, in semitones"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.25, 0.05, 0.5, "Pulse width for the square wave", 0.1),
    ),
    tags=("ui", "confirm", "accept", "ok", "select", "positive", "menu", "interface"),
)
def ui_confirm(ctx: RenderContext, p: dict) -> np.ndarray:
    notes = [p["base_note"], p["base_note"] + p["interval"]]
    x = note_sequence(ctx, notes, wave=p["wave"], duty=p["duty"], weights=[0.4, 0.6], curves=[0.8, 2.0])
    return s.lowpass(x, 9000, ctx.sr)


@recipe(
    "ui_cancel",
    "Two-note falling blip for cancel, back or close",
    duration=0.18,
    duration_range=(0.06, 0.8),
    params=(
        integer("base_note", 79, 48, 108, "First note (MIDI number)"),
        integer("interval", 5, 1, 12, "Drop to the second note, in semitones"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.25, 0.05, 0.5, "Pulse width for the square wave", 0.1),
    ),
    tags=("ui", "cancel", "back", "close", "negative", "menu", "interface"),
)
def ui_cancel(ctx: RenderContext, p: dict) -> np.ndarray:
    notes = [p["base_note"], p["base_note"] - p["interval"]]
    x = note_sequence(ctx, notes, wave=p["wave"], duty=p["duty"], weights=[0.4, 0.6], curves=[0.8, 2.0])
    return s.lowpass(x, 9000, ctx.sr)


@recipe(
    "ui_error",
    "Buzzy low double beep for invalid actions and errors",
    duration=0.28,
    duration_range=(0.08, 1.0),
    params=(
        num("freq_hz", 160, 60, 600, "Buzz pitch"),
        integer("beeps", 2, 1, 4, "Number of beeps"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("detune", 0.25, 0, 1, "Detuning between the two oscillators, in semitones", 0.1),
        num("gap", 0.3, 0, 0.8, "Silence between beeps, as a share of each beep slot", 0.1),
    ),
    tags=("ui", "error", "invalid", "denied", "wrong", "buzz", "negative", "interface"),
)
def ui_error(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    slot = n // p["beeps"]
    length = max(1, int(slot * (1 - p["gap"])))
    f = p["freq_hz"] * ctx.pitch
    beep = (s.osc(p["wave"], f, length, sr) + s.osc(p["wave"], f * s.semitones(p["detune"]), length, sr)) / 2
    beep *= s.adsr(length, sr, attack=0.003, decay=0.02, sustain=0.8, release=min(0.03, length / sr / 3))
    out = s.mix(*(s.place(beep, n, i * slot) for i in range(p["beeps"])))
    return s.lowpass(out, 4000, sr)


@recipe(
    "notification",
    "Friendly notification chime: two or three soft bell notes",
    duration=0.6,
    duration_range=(0.15, 2.0),
    params=(
        integer("note", 79, 55, 100, "First note (MIDI number)"),
        choice("shape", "up", ("up", "down", "triad"), "Note pattern"),
        num("bell", 0.6, 0, 1, "Bell-like overtones"),
    ),
    tags=("notification", "chime", "alert", "message", "ding", "ping", "inbox", "achievement", "ui", "interface"),
)
def notification(ctx: RenderContext, p: dict) -> np.ndarray:
    from assetforge.audio import dsp

    n, sr = ctx.n, ctx.sr
    steps = {"up": (0, 7), "down": (7, 0), "triad": (0, 4, 7)}[p["shape"]]
    out = np.zeros(n)
    slot = n // (len(steps) + 1)
    for k, step in enumerate(steps):
        start = k * slot
        dn = n - start
        f = s.note_hz(p["note"] + step) * ctx.pitch
        out[start:] += dsp.fm(f, 3.5, 2.5 * p["bell"], dn, sr) * s.perc(dn, sr, attack=0.002, curve=3)
    return out


@recipe(
    "slider_tick",
    "Tiny tick for sliders, scroll wheels and steppers",
    duration=0.03,
    duration_range=(0.01, 0.1),
    params=(num("tone_hz", 3500, 800, 9000, "Tick pitch"),),
    tags=("slider", "tick", "scroll", "step", "notch", "volume", "ui", "interface", "wheel"),
)
def slider_tick(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    return s.osc("sine", p["tone_hz"] * ctx.pitch, n, sr) * s.perc(n, sr, attack=0.0002, curve=10)


@recipe(
    "swipe",
    "Interface swipe or panel slide: a short airy whoosh",
    duration=0.25,
    duration_range=(0.06, 0.8),
    params=(choice("direction", "up", ("up", "down"), "Sweep direction"), num("air", 0.6, 0, 1, "Breathy noise vs tone")),
    tags=("swipe", "slide", "panel", "transition", "page", "swoosh", "ui", "interface", "menu", "open"),
)
def swipe(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    curve = x if p["direction"] == "up" else 1 - x
    centre = (600 + 4000 * curve) * ctx.pitch
    air = s.filter_track(s.noise(n, ctx.rng), "bandpass", centre, sr, bandwidth_octaves=0.7)
    tone = s.osc("sine", centre / 2, n, sr)
    return (p["air"] * air / max(np.max(np.abs(air)), 1e-9) + (1 - p["air"]) * 0.5 * tone) * np.sin(np.pi * x)


@recipe(
    "toggle",
    "Toggle switched on or off: two-step click with a pitch hint",
    duration=0.12,
    duration_range=(0.04, 0.4),
    params=(choice("state", "on", ("on", "off"), "On rises, off falls"), num("tone_hz", 1400, 400, 5000, "Pitch")),
    tags=("toggle", "switch", "on", "off", "checkbox", "enable", "disable", "ui", "interface", "settings"),
)
def toggle(ctx: RenderContext, p: dict) -> np.ndarray:
    notes = (0, 5) if p["state"] == "on" else (5, 0)
    n, sr = ctx.n, ctx.sr
    half = n // 2
    out = np.zeros(n)
    for k, step in enumerate(notes):
        f = p["tone_hz"] * ctx.pitch * s.semitones(step)
        out[k * half : k * half + half] = s.osc("sine", f, half, sr) * s.perc(half, sr, attack=0.0005, curve=6)
    return out


@recipe(
    "typewriter",
    "Dialogue text blips: one short voiced blip per letter (RPG speech)",
    duration=0.8,
    duration_range=(0.1, 5.0),
    params=(
        num("rate", 18, 4, 40, "Letters per second"),
        integer("note", 72, 40, 96, "Voice pitch (MIDI number)"),
        num("spread", 3, 0, 12, "Random pitch spread in semitones"),
        choice("wave", "square", WAVES, "Voice tone"),
    ),
    tags=("typewriter", "dialogue", "text", "speech", "talk", "blip", "letters", "rpg", "voice", "chatter", "ui"),
)
def typewriter(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    step = int(sr / p["rate"])
    bn = int(step * 0.6)
    for start in range(0, n - bn, step):
        f = s.note_hz(p["note"] + ctx.rng.uniform(-p["spread"], p["spread"])) * ctx.pitch
        out[start : start + bn] = s.osc(p["wave"], f, bn, sr, duty=0.5) * s.perc(bn, sr, attack=0.002, curve=2)
    return s.lowpass(out, 6000, sr) * 0.6


@recipe(
    "countdown_tick",
    "Countdown or timer beep (use a higher note for the final tick)",
    duration=0.15,
    duration_range=(0.04, 0.6),
    params=(integer("note", 81, 55, 100, "Pitch (MIDI number)"), choice("wave", "sine", WAVES, "Tone")),
    tags=("countdown", "timer", "tick", "beep", "clock", "seconds", "hurry", "time", "ui", "start"),
)
def countdown_tick(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    return s.osc(p["wave"], s.note_hz(p["note"]) * ctx.pitch, n, sr) * s.adsr(n, sr, attack=0.002, decay=0.02,
                                                                             sustain=0.6, release=n / sr * 0.4)  # fmt: skip


@recipe(
    "level_up",
    "Level-up fanfare: fast rising arpeggio ending on a held chord",
    duration=1.2,
    duration_range=(0.4, 3.0),
    params=(
        integer("root", 60, 40, 84, "Root note (MIDI number)"),
        choice("wave", "square", WAVES, "Tone"),
        num("duty", 0.25, 0.1, 0.5, "Pulse width", 0.1),
    ),
    tags=("level", "levelup", "up", "fanfare", "achievement", "rank", "upgrade", "win", "reward", "progress"),
)
def level_up(ctx: RenderContext, p: dict) -> np.ndarray:
    from assetforge.audio.recipes.music import chord as chord_tone

    n, sr = ctx.n, ctx.sr
    arp_notes = [p["root"] + i for i in (0, 4, 7, 12, 16, 19)]
    arp_n = int(n * 0.45)
    sub = type(ctx)(sr=sr, duration=arp_n / sr, pitch=ctx.pitch, rng=ctx.rng)
    arp = note_sequence(sub, arp_notes, wave=p["wave"], duty=p["duty"], curves=[1.0] * 6)
    held_n = n - arp_n
    held = chord_tone(ctx, [p["root"] + 12, p["root"] + 16, p["root"] + 19, p["root"] + 24], held_n, wave=p["wave"],
                      duty=p["duty"]) * s.adsr(held_n, sr, attack=0.005, decay=0.1, sustain=0.6, release=held_n / sr * 0.5)  # fmt: skip
    return np.concatenate([arp, held])


@recipe(
    "purchase",
    "Shop purchase: register 'ka-ching' with coin shimmer",
    duration=0.7,
    duration_range=(0.2, 2.0),
    params=(num("bell", 0.6, 0, 1, "Bell ring vs coin rattle"),),
    tags=("purchase", "buy", "shop", "store", "kaching", "cash", "register", "money", "sell", "coins", "ui"),
)
def purchase(ctx: RenderContext, p: dict) -> np.ndarray:
    from assetforge.audio import dsp

    n, sr = ctx.n, ctx.sr
    clunk = s.place(s.lowpass(s.noise(int(0.04 * sr), ctx.rng), 1500, sr) * s.perc(int(0.04 * sr), sr, curve=6), n, 0)
    bell_start = int(0.06 * sr)
    bn = n - bell_start
    bell = dsp.fm(1568 * ctx.pitch, 2.0, 1.6, bn, sr) + 0.5 * dsp.fm(2093 * ctx.pitch, 2.0, 1.0, bn, sr)
    bell = s.place(bell * s.perc(bn, sr, attack=0.001, curve=2.5), n, bell_start)
    coins = np.zeros(n)
    for _ in range(12):
        i = int(ctx.rng.uniform(0.02, 0.22) * n)  # coins land with the bell, not as a stray tick at the end
        k = min(n - i, int(0.04 * sr))
        coins[i : i + k] += np.sin(2 * np.pi * ctx.rng.uniform(2200, 3800) * np.arange(k) / sr) * s.perc(k, sr, attack=0.002, curve=4)
    out = 0.5 * clunk + p["bell"] * bell + (1 - p["bell"] * 0.5) * 0.2 * coins
    k = min(n, int(0.15 * sr))
    out[-k:] *= np.cos(np.linspace(0, np.pi / 2, k)) ** 2
    return out
