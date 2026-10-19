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
