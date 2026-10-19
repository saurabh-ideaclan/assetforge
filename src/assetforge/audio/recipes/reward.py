"""Rewards and outcomes: coins, pickups, power-ups/downs, victory and failure jingles."""

from __future__ import annotations

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import WAVES, RenderContext, choice, integer, num, recipe
from assetforge.audio.recipes.music import CHORDS, chord, note_sequence


@recipe(
    "coin",
    "Classic two-tone coin collect",
    duration=0.35,
    duration_range=(0.1, 1.2),
    params=(
        integer("note", 83, 60, 100, "First note (MIDI number; 83 = B5)"),
        integer("interval", 5, 1, 12, "Jump to the second note, in semitones"),
        num("split", 0.25, 0.1, 0.6, "Share of the duration taken by the first note"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.5, 0.1, 0.5, "Pulse width for the square wave", 0.1),
    ),
    tags=("coin", "collect", "pickup", "money", "gold", "score", "reward"),
)
def coin(ctx: RenderContext, p: dict) -> np.ndarray:
    notes = [p["note"], p["note"] + p["interval"]]
    x = note_sequence(ctx, notes, wave=p["wave"], duty=p["duty"], weights=[p["split"], 1 - p["split"]],
                      curves=[0.2, 2.2])  # fmt: skip
    return s.lowpass(x, 9000, ctx.sr)


@recipe(
    "pickup",
    "Quick bright arpeggio for collecting items, gems or keys",
    duration=0.25,
    duration_range=(0.08, 1.0),
    params=(
        integer("base_note", 76, 48, 100, "First note (MIDI number)"),
        integer("notes", 3, 2, 6, "Number of notes"),
        choice("chord", "major", tuple(CHORDS), "Interval pattern"),
        choice("wave", "triangle", WAVES, "Oscillator waveform"),
        num("duty", 0.5, 0.1, 0.5, "Pulse width for the square wave", 0.1),
    ),
    tags=("pickup", "collect", "item", "gem", "key", "get", "reward"),
)
def pickup(ctx: RenderContext, p: dict) -> np.ndarray:
    notes = [p["base_note"] + i for i in CHORDS[p["chord"]][: p["notes"]]]
    weights = [1.0] * (len(notes) - 1) + [2.0]
    curves = [1.0] * (len(notes) - 1) + [2.0]
    return note_sequence(ctx, notes, wave=p["wave"], duty=p["duty"], weights=weights, curves=curves)


@recipe(
    "power_up",
    "Rising stepped sweep for power-ups, level-ups and upgrades",
    duration=0.8,
    duration_range=(0.25, 2.5),
    params=(
        integer("start_note", 60, 36, 84, "Starting note (MIDI number)"),
        num("span", 24, 6, 36, "Total rise in semitones"),
        integer("steps", 8, 0, 24, "Pitch steps (0 = smooth sweep)"),
        num("vibrato", 0.3, 0, 1, "Vibrato depth"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.5, 0.1, 0.5, "Pulse width for the square wave", 0.1),
    ),
    tags=("power-up", "powerup", "level-up", "upgrade", "buff", "boost", "reward"),
)
def power_up(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, d = ctx.n, ctx.sr, ctx.duration
    x = np.linspace(0.0, 1.0, n)
    semis = np.floor(x * p["steps"]) * (p["span"] / p["steps"]) if p["steps"] else p["span"] * x
    freq = s.vibrato(s.note_hz(p["start_note"]) * ctx.pitch * s.semitones(semis), n, sr,
                     rate_hz=7, depth_semitones=p["vibrato"] * 0.6)  # fmt: skip
    env = s.adsr(n, sr, attack=0.01, decay=0.05, sustain=0.8, release=d * 0.25)
    return s.lowpass(s.osc(p["wave"], freq, n, sr, duty=p["duty"]) * env, 7000, sr)


@recipe(
    "power_down",
    "Falling, wobbling sweep for losing a power, shrinking or running out",
    duration=0.7,
    duration_range=(0.25, 2.5),
    params=(
        integer("start_note", 84, 48, 100, "Starting note (MIDI number)"),
        num("span", 24, 6, 36, "Total fall in semitones"),
        integer("steps", 0, 0, 24, "Pitch steps (0 = smooth sweep)"),
        num("tremolo", 0.5, 0, 1, "Volume wobble"),
        choice("wave", "saw", WAVES, "Oscillator waveform"),
    ),
    tags=("power-down", "powerdown", "shrink", "debuff", "lose", "deflate", "negative"),
)
def power_down(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr, d = ctx.n, ctx.sr, ctx.duration
    x = np.linspace(0.0, 1.0, n)
    t = np.arange(n) / sr
    semis = np.floor(x * p["steps"]) * (p["span"] / p["steps"]) if p["steps"] else p["span"] * x
    freq = s.note_hz(p["start_note"]) * ctx.pitch / s.semitones(semis)
    tremolo = 1 - p["tremolo"] * 0.5 * (1 + np.sin(2 * np.pi * 11 * t))
    env = s.adsr(n, sr, attack=0.005, decay=0.05, sustain=0.85, release=d * 0.3) * tremolo
    return s.filter_track(s.osc(p["wave"], freq, n, sr) * env, "lowpass", s.sweep(6000, 800, n), sr)


@recipe(
    "victory",
    "Triumphant arpeggio and held chord for winning or level complete",
    duration=1.4,
    duration_range=(0.5, 4.0),
    params=(
        integer("root_note", 72, 48, 84, "Root note (MIDI number; 72 = C5)"),
        choice("wave", "square", WAVES, "Oscillator waveform"),
        num("duty", 0.25, 0.1, 0.5, "Pulse width for the square wave", 0.1),
        num("sparkle", 0.4, 0, 1, "Octave-up shimmer layer"),
        num("arp_share", 0.45, 0.2, 0.7, "Share of the duration spent on the arpeggio"),
    ),
    tags=("victory", "win", "level-complete", "success", "fanfare", "jingle", "stinger", "reward"),
)
def victory(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    root = p["root_note"]
    arp_n = int(n * p["arp_share"])
    arp_ctx = RenderContext(sr=sr, duration=arp_n / sr, pitch=ctx.pitch, rng=ctx.rng)
    arp_notes = [root + i for i in (0, 4, 7, 12)]
    arp = note_sequence(arp_ctx, arp_notes, wave=p["wave"], duty=p["duty"], curves=[1.2] * 4)
    hold_n = n - len(arp)
    held = chord(ctx, [root - 12, root, root + 4, root + 7, root + 12], hold_n, wave=p["wave"], duty=p["duty"])
    held *= s.adsr(hold_n, sr, attack=0.01, decay=0.1, sustain=0.7, release=hold_n / sr * 0.6)
    out = np.concatenate([arp, held])
    if p["sparkle"]:
        shimmer = np.concatenate(
            [note_sequence(arp_ctx, [m + 12 for m in arp_notes], wave="triangle", curves=[2.0] * 4),
             chord(ctx, [root + 24, root + 28], hold_n, wave="triangle") * s.perc(hold_n, sr, curve=1.5)]
        )  # fmt: skip
        out += p["sparkle"] * 0.4 * shimmer
    return s.lowpass(out, 8000, sr)


@recipe(
    "failure",
    "Descending 'wah wah wahhh' for game over or failure",
    duration=1.3,
    duration_range=(0.5, 4.0),
    params=(
        integer("root_note", 62, 40, 80, "First note (MIDI number)"),
        integer("steps", 4, 2, 5, "Number of falling notes"),
        num("wah", 0.6, 0, 1, "Muted-trumpet wah on the notes"),
        choice("wave", "saw", WAVES, "Oscillator waveform"),
    ),
    tags=("failure", "fail", "game-over", "lose", "defeat", "sad", "jingle", "stinger", "negative"),
)
def failure(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    weights = [1.0] * (p["steps"] - 1) + [2.5]
    bounds = np.round(np.cumsum([0.0, *weights]) / sum(weights) * n).astype(int)
    freq = np.empty(n)
    env = np.empty(n)
    for i in range(p["steps"]):
        a, b = bounds[i], bounds[i + 1]
        length = b - a
        freq[a:b] = s.note_hz(p["root_note"] - i) * ctx.pitch
        env[a:b] = s.adsr(length, sr, attack=0.03, decay=0.05, sustain=0.8, release=length / sr * 0.35)
    last = bounds[-2]
    t = np.arange(n - last) / sr
    freq[last:] *= s.semitones(0.4 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(1.0, t * 3))
    tone = s.osc(p["wave"], freq, n, sr) * env
    wah = 1200 + p["wah"] * 1000 * np.sin(2 * np.pi * 3.0 * np.arange(n) / sr - np.pi / 2)
    return s.filter_track(tone, "lowpass", np.maximum(wah, 300.0), sr)
