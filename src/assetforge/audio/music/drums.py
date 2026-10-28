"""Synthesized drum kits: acoustic-style, 808/electronic, chip (8-bit) and orchestral percussion.

A drum note's pitch is a piece name (`kick`, `snare`…) or its General MIDI number, so MIDI files
and the SoundFont backend line up with the synth kits.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.errors import InvalidInputError

# General MIDI percussion numbers (channel 10)
GM_DRUMS: dict[str, int] = {
    "kick": 36, "rim": 37, "snare": 38, "clap": 39, "tom_low": 41, "hat_closed": 42, "tom_mid": 45, "hat_pedal": 44,
    "hat_open": 46, "tom_high": 48, "crash": 49, "ride": 51, "tambourine": 54, "cowbell": 56, "shaker": 70,
    "conga": 63, "bongo": 60, "woodblock": 76, "triangle": 81, "taiko": 35, "timpani": 47,
}  # fmt: skip
GM_TO_PIECE = {number: name for name, number in GM_DRUMS.items()}
GM_TO_PIECE |= {35: "kick", 40: "snare", 43: "tom_low", 47: "tom_mid", 50: "tom_high", 57: "crash", 59: "ride",
                52: "crash", 55: "crash", 53: "ride", 69: "shaker", 62: "conga", 61: "bongo", 64: "conga"}  # fmt: skip
KITS = ("drums", "drums_808", "drums_chip", "drums_orchestral")

Piece = Callable[[np.random.Generator, int, str], np.ndarray]
BRIGHT = {"hat_closed", "hat_pedal", "hat_open", "ride", "crash", "shaker", "tambourine", "triangle"}


def drum_piece(pitch: int | str) -> str:
    if isinstance(pitch, int) or (isinstance(pitch, str) and pitch.strip().isdigit()):
        number = int(pitch)
        if number not in GM_TO_PIECE:
            raise InvalidInputError(f"No drum piece for MIDI note {number}", available=sorted(GM_DRUMS))
        return GM_TO_PIECE[number]
    name = str(pitch).strip().lower().replace(" ", "_").replace("-", "_")
    if name not in GM_DRUMS:
        raise InvalidInputError(f"Unknown drum piece {pitch!r}", available=sorted(GM_DRUMS))
    return name


def _noise_hit(rng: np.random.Generator, sr: int, seconds: float, lo: float, hi: float, curve: float) -> np.ndarray:
    n = int(seconds * sr)
    return s.bandpass(s.noise(n, rng), lo, hi, sr) * s.perc(n, sr, attack=0.0005, curve=curve)


def kick(rng, sr, kit):
    if kit == "drums_808":
        n = int(0.9 * sr)
        return s.osc("sine", s.sweep(120, 48, n) * (1 + 0 * rng.uniform()), n, sr) * s.perc(n, sr, attack=0.001, curve=2.5)
    if kit == "drums_chip":
        n = int(0.15 * sr)
        return s.osc("triangle", s.sweep(180, 50, n), n, sr) * s.perc(n, sr, attack=0.0005, curve=2)
    n = int(0.45 * sr)
    body = s.osc("sine", s.sweep(150, 52, n, curve="exp"), n, sr) * s.perc(n, sr, attack=0.001, curve=5)
    click = _noise_hit(rng, sr, 0.01, 1500, 6000, 3)
    return body + 0.3 * dsp.fit_len(click, n)


def snare(rng, sr, kit):
    if kit == "drums_chip":  # console style: a short triangle drop under a burst of mid noise
        n = int(0.1 * sr)
        body = s.osc("triangle", s.sweep(330, 140, n), n, sr) * s.perc(n, sr, attack=0.0005, curve=4)
        return 0.7 * body + dsp.fit_len(_noise_hit(rng, sr, 0.09, 700, 5000, 4), n)
    n = int(0.3 * sr)
    tone = s.osc("triangle", s.sweep(240, 180, n), n, sr) * s.perc(n, sr, attack=0.0005, curve=8)
    rattle = _noise_hit(rng, sr, 0.3, 1200, 9000 if kit != "drums_808" else 6000, 4)
    return 0.6 * tone + rattle


def clap(rng, sr, kit):
    n = int(0.3 * sr)
    out = np.zeros(n)
    for k, offset in enumerate((0.0, 0.011, 0.022, 0.034)):
        burst = _noise_hit(rng, sr, 0.3 if k == 3 else 0.02, 900, 5000, 6 if k == 3 else 2)
        dsp.add_at(out, burst, int(offset * sr))
    return out


def hat(rng, sr, kit, seconds=0.06):
    if kit == "drums_chip":
        return _noise_hit(rng, sr, seconds * 0.6, 5000, 9000, 6)
    n = int(seconds * sr)
    metal = sum(s.osc("square", f, n, sr) for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0))
    return s.highpass(metal / 6 + s.noise(n, rng) * 0.6, 7000, sr) * s.perc(n, sr, attack=0.0005, curve=6 if seconds < 0.1 else 2.5)


def tom(freq):
    def piece(rng, sr, kit):
        n = int(0.4 * sr)
        if kit == "drums_chip":
            return s.osc("triangle", s.sweep(freq * 1.5, freq * 0.8, n), n, sr) * s.perc(n, sr, curve=3)
        body = s.osc("sine", s.sweep(freq * 1.4, freq, n), n, sr) * s.perc(n, sr, attack=0.001, curve=4)
        return body + 0.2 * dsp.fit_len(_noise_hit(rng, sr, 0.05, 300, 3000, 4), n)

    return piece


def cymbal(seconds):
    def piece(rng, sr, kit):
        n = int(seconds * sr)
        metal = sum(s.osc("square", f * rng.uniform(0.98, 1.02), n, sr) for f in (296.0, 461.0, 593.0, 822.0, 1031.0))
        wash = s.highpass(metal / 5 * 0.5 + s.noise(n, rng), 4000, sr)
        return wash * s.perc(n, sr, attack=0.002, curve=2 if seconds > 1 else 3)

    return piece


def rim(rng, sr, kit):
    n = int(0.05 * sr)
    return s.osc("sine", 1700, n, sr) * s.perc(n, sr, curve=6) + 0.5 * _noise_hit(rng, sr, 0.05, 2000, 6000, 8)


def shaker(rng, sr, kit):
    n = int(0.1 * sr)
    x = np.linspace(0, 1, n)
    return s.bandpass(s.noise(n, rng), 5000, 11000, sr) * np.sin(np.pi * x) ** 2


def cowbell(rng, sr, kit):
    n = int(0.3 * sr)
    tone = s.osc("square", 587, n, sr) + s.osc("square", 845, n, sr)
    return s.bandpass(tone, 500, 2500, sr) * s.perc(n, sr, attack=0.001, curve=4) * 0.5


def hand_drum(freq):
    def piece(rng, sr, kit):
        n = int(0.3 * sr)
        return s.osc("sine", s.sweep(freq * 1.3, freq, n), n, sr) * s.perc(n, sr, attack=0.001, curve=6) + 0.3 * dsp.fit_len(
            _noise_hit(rng, sr, 0.02, 1000, 5000, 4), n)  # fmt: skip

    return piece


def taiko(rng, sr, kit):
    n = int(1.2 * sr)
    body = s.osc("sine", s.sweep(110, 62, n), n, sr) * s.perc(n, sr, attack=0.002, curve=3)
    skin = s.lowpass(s.noise(n, rng), 600, sr) * s.perc(n, sr, attack=0.001, curve=10)
    return body + 0.6 * skin


def timpani(rng, sr, kit):
    n = int(1.6 * sr)
    f = 98.0
    t = np.arange(n) / sr
    tone = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t * (1.5 + r)) for r, a in ((1, 1), (1.5, 0.5), (1.98, 0.35)))
    return tone + 0.3 * s.lowpass(s.noise(n, rng), 400, sr) * s.perc(n, sr, curve=12)


def triangle_bell(rng, sr, kit):
    n = int(1.2 * sr)
    return dsp.fm(3520, 1.41, 0.8, n, sr) * s.perc(n, sr, attack=0.001, curve=2) * 0.5


PIECES: dict[str, Piece] = {
    "kick": kick, "snare": snare, "clap": clap, "rim": rim, "hat_closed": hat,
    "hat_pedal": lambda r, sr, k: hat(r, sr, k, 0.04), "hat_open": lambda r, sr, k: hat(r, sr, k, 0.35),
    "tom_low": tom(90), "tom_mid": tom(130), "tom_high": tom(180), "crash": cymbal(1.8), "ride": cymbal(0.9),
    "tambourine": lambda r, sr, k: shaker(r, sr, k) * 1.2, "shaker": shaker, "cowbell": cowbell,
    "conga": hand_drum(200), "bongo": hand_drum(330), "woodblock": lambda r, sr, k: rim(r, sr, k) * 0.8,
    "triangle": triangle_bell, "taiko": taiko, "timpani": timpani,
}  # fmt: skip


def render_hit(pitch: int | str, kit: str, velocity: float, rng: np.random.Generator, sr: int) -> np.ndarray:
    piece = drum_piece(pitch)
    if kit == "drums_orchestral" and piece in ("kick", "tom_low"):
        piece = "taiko" if piece == "kick" else "timpani"
    x = PIECES[piece](rng, sr, kit)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    # Metal and shakers sit under the kick and snare; at full level they read as constant hiss.
    level = 0.45 if piece in BRIGHT else 1.0
    return (x / peak if peak > 0 else x) * (0.25 + 0.75 * velocity) * level
