"""Synth instruments: chip, analog-style synths, FM keys and bells, plucked strings, organ, strings,
choir, flute, brass and a modelled piano. NumPy/SciPy only, so they work everywhere.

Each instrument renders one note: `render(midi, seconds, velocity, sr, rng) -> mono` including its
release tail. Every instrument has a General MIDI program, used by MIDI export and by the
SoundFont backend (which plays the real sampled instrument when one is installed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.errors import InvalidInputError

NoteFn = Callable[[float, float, float, int, np.random.Generator], np.ndarray]  # (hz, seconds, velocity, sr, rng)


@dataclass(frozen=True)
class Instrument:
    name: str
    family: str
    description: str
    program: int  # General MIDI program (0-based)
    release: float  # seconds of tail after the note ends
    render_hz: NoteFn
    range: tuple[int, int] = (24, 108)

    def render(self, midi: int, seconds: float, velocity: float, sr: int, rng: np.random.Generator) -> np.ndarray:
        return self.render_hz(float(s.note_hz(midi)), seconds, velocity, sr, rng)


INSTRUMENTS: dict[str, Instrument] = {}
ALIASES: dict[str, str] = {}


def instrument(name: str, family: str, description: str, program: int, release: float = 0.2,
               aliases: tuple[str, ...] = (), range_: tuple[int, int] = (24, 108)):  # fmt: skip
    def decorate(fn: NoteFn) -> NoteFn:
        INSTRUMENTS[name] = Instrument(name, family, description, program, release, fn, range_)
        for alias in aliases:
            ALIASES[alias] = name
        return fn

    return decorate


# Drum-kit aliases (kits live in drums.py)
ALIASES.update({"taiko": "drums_orchestral", "timpani": "drums_orchestral", "orchestral_percussion": "drums_orchestral",
                "percussion": "drums", "kit": "drums", "drum_kit": "drums", "808": "drums_808",
                "drum_machine": "drums_808", "electronic_drums": "drums_808", "chip_drums": "drums_chip",
                "8bit_drums": "drums_chip"})  # fmt: skip


def resolve_instrument(name: str) -> str:
    from assetforge.audio.music.drums import KITS

    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    if key in INSTRUMENTS or key in KITS:
        return key
    if key in ALIASES:
        return ALIASES[key]
    raise InvalidInputError(f"Unknown instrument {name!r}", available=sorted([*INSTRUMENTS, *KITS]))


def get(name: str) -> Instrument:
    return INSTRUMENTS[resolve_instrument(name)]


def _len(seconds: float, release: float, sr: int) -> int:
    return max(16, int((seconds + release) * sr))


def env(n: int, sr: int, seconds: float, *, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    """ADSR whose release starts when the note ends (`seconds`), not at the end of the buffer."""
    gate = min(n, int(seconds * sr))
    held = s.adsr(gate, sr, attack=min(attack, seconds * 0.9), decay=decay, sustain=sustain, release=0.0)
    level = held[-1] if gate else 0.0
    tail = level * np.exp(-np.arange(n - gate) / max(1.0, release * sr / 5))
    return np.concatenate([held, tail])


def vel(velocity: float) -> float:
    return 0.2 + 0.8 * velocity**1.5


# ---------------------------------------------------------------------- chip


def _chip(shape: str, duty: float):
    def render(hz, seconds, velocity, sr, rng):
        n = _len(seconds, 0.03, sr)
        return s.osc(shape, hz, n, sr, duty=duty) * env(n, sr, seconds, attack=0.002, decay=0.05, sustain=0.8,  # type: ignore[arg-type]
                                                        release=0.03) * vel(velocity) * 0.6  # fmt: skip

    return render


instrument("chip_square", "chip", "8-bit square wave (NES/Game Boy lead)", 80, 0.03, ("square", "chiptune", "8bit"))(_chip("square", 0.5))
instrument("chip_pulse", "chip", "8-bit 25% pulse: brighter, nasal lead", 80, 0.03, ("pulse",))(_chip("square", 0.25))
instrument("chip_thin", "chip", "8-bit 12.5% pulse: thin and buzzy", 80, 0.03)(_chip("square", 0.125))
instrument("chip_triangle", "chip", "8-bit triangle: soft chip bass or flute", 80, 0.03, ("triangle",))(_chip("triangle", 0.5))


# ---------------------------------------------------------------------- synths


@instrument("saw_lead", "synth", "Bright detuned saw lead", 81, 0.15, ("lead", "synth_lead"))
def saw_lead(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.15, sr)
    f = s.vibrato(hz, n, sr, rate_hz=5.5, depth_semitones=np.linspace(0, 0.15, n))
    x = s.osc("saw", f * 0.997, n, sr) + s.osc("saw", f * 1.003, n, sr)
    x = dsp.sweep_filter(x, "lowpass", 900 + 5000 * velocity * np.exp(-np.arange(n) / sr / 0.4), sr, q=1.2)
    return x * env(n, sr, seconds, attack=0.01, decay=0.2, sustain=0.7, release=0.15) * vel(velocity) * 0.4


@instrument("square_lead", "synth", "Hollow square lead", 80, 0.12)
def square_lead(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.12, sr)
    x = s.lowpass(s.osc("square", s.vibrato(hz, n, sr, rate_hz=5, depth_semitones=0.08), n, sr), 3500, sr)
    return x * env(n, sr, seconds, attack=0.008, decay=0.1, sustain=0.75, release=0.12) * vel(velocity) * 0.4


@instrument("supersaw_pad", "synth", "Wide supersaw pad (synthwave, trance)", 90, 0.8, ("supersaw", "synth_pad"))
def supersaw_pad(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.8, sr)
    x = sum(s.osc("saw", hz * 2 ** (c / 1200), n, sr, phase=float(rng.uniform())) for c in (-18, -9, -3, 0, 4, 10, 17))
    x = s.lowpass(x / 7, 2800, sr)
    return x * env(n, sr, seconds, attack=0.25, decay=0.3, sustain=0.8, release=0.8) * vel(velocity) * 0.5


@instrument("warm_pad", "synth", "Soft warm pad (sine and triangle)", 89, 1.0, ("pad", "soft_pad"))
def warm_pad(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 1.0, sr)
    x = s.osc("triangle", hz, n, sr) + 0.5 * s.osc("sine", hz * 2.003, n, sr) + 0.3 * s.osc("sine", hz * 0.5, n, sr)
    x = x * (1 + 0.1 * np.sin(2 * np.pi * 0.3 * np.arange(n) / sr))
    return x * env(n, sr, seconds, attack=0.4, decay=0.3, sustain=0.85, release=1.0) * vel(velocity) * 0.35


@instrument("sub_bass", "bass", "Deep sine sub bass", 38, 0.08, ("sub",), (24, 60))
def sub_bass(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.08, sr)
    x = s.osc("sine", hz, n, sr) + 0.2 * s.osc("triangle", hz * 2, n, sr)
    return x * env(n, sr, seconds, attack=0.005, decay=0.1, sustain=0.9, release=0.08) * vel(velocity) * 0.7


@instrument("synth_bass", "bass", "Punchy filtered synth bass", 38, 0.08, ("bass",), (24, 64))
def synth_bass(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.08, sr)
    x = s.osc("saw", hz, n, sr) + s.osc("square", hz * 0.5, n, sr) * 0.5
    x = dsp.sweep_filter(x, "lowpass", 300 + 2200 * velocity * np.exp(-np.arange(n) / sr / 0.12), sr, q=1.5)
    return x * env(n, sr, seconds, attack=0.003, decay=0.15, sustain=0.6, release=0.08) * vel(velocity) * 0.5


@instrument("acid_bass", "bass", "Squelchy resonant acid bass", 87, 0.06, ("acid", "303"), (24, 64))
def acid_bass(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.06, sr)
    x = s.osc("saw", hz, n, sr)
    x = dsp.sweep_filter(x, "lowpass", 250 + 4000 * velocity * np.exp(-np.arange(n) / sr / 0.09), sr, q=7)
    return s.soft_clip(x * env(n, sr, seconds, attack=0.002, decay=0.1, sustain=0.5, release=0.06), 0.4) * vel(velocity) * 0.5


@instrument("pluck", "synth", "Short bright synth pluck (arps)", 84, 0.25, ("synth_pluck", "arp"))
def pluck(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.25, sr)
    x = s.osc("saw", hz, n, sr) + s.osc("square", hz * 1.005, n, sr) * 0.5
    x = dsp.sweep_filter(x, "lowpass", 400 + 6000 * np.exp(-np.arange(n) / sr / 0.07), sr, q=1.5)
    return x * s.perc(n, sr, attack=0.002, curve=3) * vel(velocity) * 0.5


# ---------------------------------------------------------------------- FM keys and bells


@instrument("electric_piano", "keys", "FM electric piano (Rhodes-like)", 4, 0.6, ("epiano", "rhodes"))
def electric_piano(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.6, sr)
    t = np.arange(n) / sr
    x = dsp.fm(hz, 1.0, (0.5 + 2.5 * velocity) * np.exp(-t * 3), n, sr) + 0.3 * dsp.fm(hz, 14.0, 0.5 * np.exp(-t * 20), n, sr)
    return x * env(n, sr, seconds, attack=0.002, decay=1.2, sustain=0.35, release=0.6) * vel(velocity) * 0.4


@instrument("bell", "mallet", "Bright FM bell", 14, 1.5, ("bells", "tubular_bells"))
def bell(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 1.5, sr)
    t = np.arange(n) / sr
    return dsp.fm(hz, 3.5, 3 * np.exp(-t * 2), n, sr) * np.exp(-t * 1.6) * vel(velocity) * 0.4


@instrument("celesta", "mallet", "Celesta / music box", 8, 0.8, ("music_box", "glockenspiel"))
def celesta(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.8, sr)
    t = np.arange(n) / sr
    return dsp.fm(hz, 4.0, 1.2 * np.exp(-t * 8), n, sr) * np.exp(-t * 3) * vel(velocity) * 0.4


@instrument("marimba", "mallet", "Wooden marimba", 12, 0.3, ("xylophone", "vibraphone", "kalimba"))
def marimba(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.3, sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * hz * t) + 0.25 * np.sin(2 * np.pi * hz * 3.93 * t) * np.exp(-t * 30)
    return x * np.exp(-t * (6 + hz / 400)) * vel(velocity) * 0.6


@instrument("fm_bass", "bass", "Rubbery FM bass", 39, 0.08, (), (24, 64))
def fm_bass(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.08, sr)
    t = np.arange(n) / sr
    x = dsp.fm(hz, 1.0, 1.5 * np.exp(-t * 8) + 0.5, n, sr)
    return x * env(n, sr, seconds, attack=0.003, decay=0.2, sustain=0.6, release=0.08) * vel(velocity) * 0.6


# ---------------------------------------------------------------------- plucked strings


def _plucked(decay: float, brightness: float, body_hz: float):
    def render(hz, seconds, velocity, sr, rng):
        n = _len(seconds, 0.4, sr)
        x = dsp.karplus(hz, n, sr, rng, decay=decay, brightness=brightness * (0.6 + 0.4 * velocity))
        x = x + 0.15 * dsp.filt(x, "bandpass", body_hz, sr, q=1.5)
        damp = np.ones(n)
        gate = int(seconds * sr)
        if gate < n:
            damp[gate:] = np.exp(-np.arange(n - gate) / (0.08 * sr))
        return x * damp * vel(velocity) * 0.6

    return render


instrument("guitar", "plucked", "Nylon/steel acoustic guitar (plucked string model)", 24, 0.4,
           ("acoustic_guitar", "nylon_guitar"), (40, 88))(_plucked(0.996, 0.5, 220))  # fmt: skip
instrument("harp", "plucked", "Concert harp", 46, 0.4, (), (24, 103))(_plucked(0.998, 0.65, 400))
instrument("koto", "plucked", "Koto / shamisen-like bright pluck", 107, 0.4, ("shamisen",), (48, 96))(_plucked(0.993, 0.9, 900))
instrument("lute", "plucked", "Lute / mandolin, medieval pluck", 25, 0.4, ("mandolin", "banjo"), (40, 88))(_plucked(0.994, 0.7, 500))


# ---------------------------------------------------------------------- sustained acoustic-style


@instrument("organ", "keys", "Drawbar organ", 16, 0.08, ("church_organ", "hammond"))
def organ(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.08, sr)
    t = np.arange(n) / sr
    x = sum(a * np.sin(2 * np.pi * hz * r * t) for r, a in ((0.5, 0.6), (1, 1), (2, 0.6), (3, 0.3), (4, 0.25), (6, 0.1)))
    x = x * (1 + 0.15 * np.sin(2 * np.pi * 6.5 * t))
    return x * env(n, sr, seconds, attack=0.01, decay=0.01, sustain=1, release=0.08) * vel(velocity) * 0.25


@instrument("strings", "strings", "String ensemble (detuned saws, slow swell)", 48, 0.5,
            ("string_ensemble", "violins", "orchestra", "cello", "violin"))  # fmt: skip
def strings(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.5, sr)
    f = s.vibrato(hz, n, sr, rate_hz=5.2, depth_semitones=0.08)
    x = sum(s.osc("saw", f * 2 ** (c / 1200), n, sr, phase=float(rng.uniform())) for c in (-8, -3, 2, 7))
    x = dsp.filt(dsp.filt(x / 4, "lowpass", 2500 + 2000 * velocity, sr), "peak", 1200, sr, q=1, gain_db=3)
    return x * env(n, sr, seconds, attack=0.18, decay=0.2, sustain=0.85, release=0.5) * vel(velocity) * 0.45


@instrument("pizzicato", "strings", "Plucked orchestral strings", 45, 0.25)
def pizzicato(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.25, sr)
    x = dsp.karplus(hz, n, sr, rng, decay=0.985, brightness=0.4)
    return x * vel(velocity) * 0.7


@instrument("choir", "voice", "Choir 'aah' pad (formant synthesis)", 52, 0.6, ("voices", "aah", "ooh"))
def choir(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.6, sr)
    src = sum(s.osc("saw", s.vibrato(hz * 2 ** (c / 1200), n, sr, rate_hz=5 + c / 10, depth_semitones=0.12), n, sr)
              for c in (-10, 0, 9))  # fmt: skip
    formants = ((800, 1.0), (1150, 0.5), (2900, 0.25))
    x = sum(a * dsp.filt(src, "bandpass", f, sr, q=6) for f, a in formants)
    return x * env(n, sr, seconds, attack=0.25, decay=0.2, sustain=0.85, release=0.6) * vel(velocity) * 0.6


@instrument("flute", "wind", "Flute (sine with breath and vibrato)", 73, 0.12, ("pan_flute", "recorder", "whistle", "ocarina"))
def flute(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.12, sr)
    f = s.vibrato(hz, n, sr, rate_hz=5, depth_semitones=np.clip(np.arange(n) / sr - 0.2, 0, 0.3) * 0.5)
    x = s.osc("sine", f, n, sr) + 0.12 * s.osc("sine", f * 2, n, sr)
    breath = dsp.filt(s.noise(n, rng), "bandpass", hz * 2, sr, q=2) * 0.15
    return (x + breath) * env(n, sr, seconds, attack=0.06, decay=0.1, sustain=0.85, release=0.12) * vel(velocity) * 0.45


@instrument("clarinet", "wind", "Clarinet / reed (odd harmonics)", 71, 0.1, ("oboe", "bassoon", "reed"))
def clarinet(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.1, sr)
    x = dsp.filt(s.osc("square", s.vibrato(hz, n, sr, rate_hz=5, depth_semitones=0.05), n, sr), "lowpass", hz * 6, sr)
    return x * env(n, sr, seconds, attack=0.04, decay=0.1, sustain=0.85, release=0.1) * vel(velocity) * 0.4


@instrument("brass", "brass", "Brass section (filtered saw with a swell)", 61, 0.2, ("horns", "trumpet", "french_horn", "trombone"))
def brass(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.2, sr)
    t = np.arange(n) / sr
    x = s.osc("saw", hz * 0.998, n, sr) + s.osc("saw", hz * 1.002, n, sr)
    cutoff = hz * (2 + 6 * velocity * (1 - np.exp(-t / 0.08)))
    x = dsp.sweep_filter(x, "lowpass", cutoff, sr, q=1.2)
    return x * env(n, sr, seconds, attack=0.05, decay=0.2, sustain=0.8, release=0.2) * vel(velocity) * 0.35


@instrument("piano", "keys", "Piano (modelled: inharmonic decaying partials and a hammer)", 0, 0.5,
            ("acoustic_piano", "grand_piano", "keys"))  # fmt: skip
def piano(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.5, sr)
    t = np.arange(n) / sr
    b = 0.0004
    x = sum(np.sin(2 * np.pi * hz * k * np.sqrt(1 + b * k * k) * t + rng.uniform(0, 0.3)) / k**1.2 *
            np.exp(-t * (0.6 + 0.35 * k) * (hz / 260) ** 0.5) for k in range(1, 9))  # fmt: skip
    hammer = dsp.fit_len(s.lowpass(s.noise(int(0.01 * sr), rng), 3000, sr) * 0.3, n)
    damp = np.ones(n)
    gate = int(seconds * sr)
    if gate < n:
        damp[gate:] = np.exp(-np.arange(n - gate) / (0.1 * sr))
    return (x + hammer) * damp * vel(velocity) * 0.35


@instrument("sitar", "plucked", "Sitar-like buzzing pluck", 104, 0.5, (), (40, 88))
def sitar(hz, seconds, velocity, sr, rng):
    n = _len(seconds, 0.5, sr)
    x = dsp.karplus(hz, n, sr, rng, decay=0.997, brightness=0.95)
    return s.soft_clip(x * 2, 0.4) * vel(velocity) * 0.5


def catalog() -> list[dict[str, object]]:
    from assetforge.audio.music.drums import GM_DRUMS, KITS

    items: list[dict[str, object]] = [{"name": i.name, "family": i.family, "description": i.description,
                                       "gm_program": i.program, "aliases": sorted(a for a, t in ALIASES.items()
                                                                                  if t == i.name)}
                                      for i in INSTRUMENTS.values()]  # fmt: skip
    items += [{"name": k, "family": "drums", "description": {"drums": "Acoustic-style kit", "drums_808": "Electronic 808 kit",
                                                             "drums_chip": "8-bit noise kit",
                                                             "drums_orchestral": "Taiko, timpani and orchestral percussion"}[k],
               "pieces": sorted(GM_DRUMS)} for k in KITS]  # fmt: skip
    return items
