"""Music theory as data: notes, keys and modes, roman-numeral chords, voicing and meter.

Everything is MIDI note numbers (60 = middle C) and beats in quarter notes. Chord symbols are
roman numerals relative to the key's own scale, as composers write them: in A minor `VI` is
F major and `bII` is B-flat major. Case sets the quality (upper major, lower minor) unless a
suffix says otherwise: `7`, `maj7`, `m7`, `o`/`dim`, `ø`/`m7b5`, `+`/`aug`, `sus2`, `sus4`, `add9`, `9`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from assetforge.errors import InvalidInputError

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PITCH_CLASS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}

SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "phrygian_dominant": (0, 1, 4, 5, 7, 8, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "hirajoshi": (0, 2, 3, 7, 8),
    "whole_tone": (0, 2, 4, 6, 8, 10),
    "chromatic": tuple(range(12)),
}
SCALE_ALIASES = {"maj": "major", "ionian": "major", "min": "minor", "m": "minor", "aeolian": "minor",
                 "natural_minor": "minor", "harmonic": "harmonic_minor", "melodic": "melodic_minor",
                 "pentatonic": "major_pentatonic", "minor_pent": "minor_pentatonic", "major_pent": "major_pentatonic",
                 "spanish": "phrygian_dominant", "japanese": "hirajoshi"}  # fmt: skip
# Heptatonic parent used for roman numerals when a key uses a gapped scale.
PARENT_SCALE = {"major_pentatonic": "major", "minor_pentatonic": "minor", "blues": "minor", "hirajoshi": "minor",
                "whole_tone": "lydian", "chromatic": "major"}  # fmt: skip
CHROMATIC_FRIENDLY = {"blues", "chromatic", "whole_tone"}


# ---------------------------------------------------------------------- notes


def note_number(name: str | int | float) -> int:
    """'C4' → 60, 'F#3' → 54, 'Bb5' → 82; numbers pass through."""
    if isinstance(name, int | float):
        return int(name)
    text = name.strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]*)(-?\d+)", text)
    if not m:
        raise InvalidInputError(f"Bad note {name!r}; use names like C4, F#3, Bb5 or MIDI numbers")
    pc = _PITCH_CLASS[m.group(1).lower()] + m.group(2).count("#") + m.group(2).count("♯")
    pc -= m.group(2).count("b") + m.group(2).count("♭")
    return pc + 12 * (int(m.group(3)) + 1)


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def pitch_class(name: str) -> int:
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]*)", name.strip())
    if not m:
        raise InvalidInputError(f"Bad note name {name!r}")
    return (_PITCH_CLASS[m.group(1).lower()] + m.group(2).count("#") + m.group(2).count("♯")
            - m.group(2).count("b") - m.group(2).count("♭")) % 12  # fmt: skip


# ---------------------------------------------------------------------- keys


@dataclass(frozen=True)
class Key:
    tonic: int  # pitch class 0..11
    scale: str

    @property
    def name(self) -> str:
        return f"{NOTE_NAMES[self.tonic]} {self.scale.replace('_', ' ')}"

    @property
    def intervals(self) -> tuple[int, ...]:
        return SCALES[self.scale]

    @property
    def mode(self) -> str:
        """'major' or 'minor' by the third above the tonic (what players and search mean by it)."""
        return "minor" if 3 in self.intervals and 4 not in self.intervals else "major"

    @property
    def pitch_classes(self) -> frozenset[int]:
        return frozenset((self.tonic + i) % 12 for i in self.intervals)

    def degree(self, degree: int, octave: int = 4) -> int:
        """Scale degree (0 = tonic, may be negative or beyond the octave) → MIDI note."""
        steps = self.intervals
        octave_shift, index = divmod(degree, len(steps))
        return 12 * (octave + 1 + octave_shift) + self.tonic + steps[index]

    def snap(self, midi: int) -> int:
        """Nearest note in the key (ties go down)."""
        for offset in (0, -1, 1, -2, 2):
            if (midi + offset) % 12 in self.pitch_classes:
                return midi + offset
        return midi

    def in_key(self, midi: int) -> bool:
        return midi % 12 in self.pitch_classes


@lru_cache(maxsize=256)
def parse_key(text: str) -> Key:
    """'A minor', 'Am', 'F# dorian', 'Bb', 'c harmonic minor' → Key."""
    raw = " ".join(str(text).replace("-", " ").replace("_", " ").split())
    m = re.fullmatch(r"([A-Ga-g][#b♯♭]?)\s*(.*)", raw)
    if not m:
        raise InvalidInputError(f"Bad key {text!r}; use e.g. 'A minor', 'C major', 'F# dorian', 'Em'")
    tonic = pitch_class(m.group(1))
    scale_text = m.group(2).strip().lower().replace(" ", "_") or "major"
    scale = SCALE_ALIASES.get(scale_text, scale_text)
    if scale not in SCALES:
        raise InvalidInputError(f"Unknown scale {m.group(2)!r}", available=sorted(SCALES))
    return Key(tonic, scale)


# ---------------------------------------------------------------------- chords

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}
_SUFFIXES: dict[str, tuple[int, ...] | None] = {
    "": None, "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "7": None, "9": None, "o": (0, 3, 6), "dim": (0, 3, 6),
    "o7": (0, 3, 6, 9), "dim7": (0, 3, 6, 9), "ø": (0, 3, 6, 10), "m7b5": (0, 3, 6, 10), "+": (0, 4, 8),
    "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "add9": None, "6": None, "5": (0, 7),
}  # fmt: skip
_CHORD_RE = re.compile(r"([b#]?)(vii|iii|vi|iv|ii|v|i)(maj7|m7b5|m7|dim7|dim|o7|o|ø|aug|\+|sus2|sus4|add9|7|9|6|5)?"
                       r"(?:/(\d))?$", re.IGNORECASE)  # fmt: skip


@dataclass(frozen=True)
class Chord:
    symbol: str
    root: int  # pitch class
    intervals: tuple[int, ...]  # semitones above the root
    bass: int | None = None  # pitch class for slash chords (inversions)

    @property
    def pitch_classes(self) -> list[int]:
        return [(self.root + i) % 12 for i in self.intervals]

    def notes(self, octave: int = 4) -> list[int]:
        base = 12 * (octave + 1) + self.root
        return [base + i for i in self.intervals]


@lru_cache(maxsize=2048)
def chord(symbol: str, key: Key) -> Chord:
    """Parse one roman-numeral chord in `key`."""
    m = _CHORD_RE.fullmatch(symbol.strip())
    if not m:
        raise InvalidInputError(f"Bad chord {symbol!r}; use roman numerals like I, vi, V7, bVII, iio, IVmaj7")
    accidental, numeral, suffix, inversion = m.group(1), m.group(2), (m.group(3) or ""), m.group(4)
    parent = Key(key.tonic, PARENT_SCALE.get(key.scale, key.scale))
    root = (parent.tonic + parent.intervals[_ROMAN[numeral.lower()] - 1] + {"b": -1, "#": 1, "": 0}[accidental]) % 12
    upper = numeral.isupper()
    suffix_key = suffix.lower() if suffix.lower() in _SUFFIXES else suffix
    fixed = _SUFFIXES.get(suffix_key)
    if fixed is not None:
        intervals = fixed
    else:
        triad = (0, 4, 7) if upper else (0, 3, 7)
        extra = {"7": (10,), "9": (10, 14), "add9": (14,), "6": (9,)}.get(suffix_key, ())
        if suffix_key == "7" and not upper:
            extra = (10,)
        intervals = triad + extra
    bass = None
    if inversion:
        idx = int(inversion) % len(intervals)
        bass = (root + intervals[idx]) % 12
    return Chord(symbol, root, tuple(intervals), bass)


def progression(symbols: str | list[str], key: Key) -> list[Chord]:
    items = symbols.split() if isinstance(symbols, str) else symbols
    return [chord(s, key) for s in items]


def voice(chord_: Chord, *, center: int = 64, previous: list[int] | None = None, size: int = 4) -> list[int]:
    """Close voicing of `size` notes near `center`, or the inversion nearest the previous voicing."""
    pcs = chord_.pitch_classes
    tones = (pcs * ((size + len(pcs) - 1) // len(pcs)))[:size] if len(pcs) < size else pcs[:size]
    candidates = []
    for inversion in range(len(tones)):
        rotated = tones[inversion:] + tones[:inversion]
        notes, last = [], center - 12
        for pc in rotated:
            n = last + ((pc - last) % 12 or 12) if notes else center - 6 + ((pc - (center - 6)) % 12)
            notes.append(n)
            last = n
        candidates.append(notes)
        candidates.append([n - 12 for n in notes])
    target = previous or [center]

    def cost(notes: list[int]) -> float:
        if previous:
            return sum(min(abs(n - p) for p in target) for n in notes)
        return abs(sum(notes) / len(notes) - center)

    return sorted(min(candidates, key=cost))


# ---------------------------------------------------------------------- meter


@dataclass(frozen=True)
class Meter:
    numerator: int
    denominator: int

    @property
    def beats_per_bar(self) -> float:
        """Bar length in quarter notes (6/8 → 3.0)."""
        return self.numerator * 4 / self.denominator

    @property
    def compound(self) -> bool:
        return self.denominator == 8 and self.numerator % 3 == 0

    @property
    def text(self) -> str:
        return f"{self.numerator}/{self.denominator}"


def parse_meter(text: str) -> Meter:
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(2|4|8|16)\s*", str(text))
    if not m:
        raise InvalidInputError(f"Bad time signature {text!r}; use e.g. 4/4, 3/4, 6/8")
    return Meter(int(m.group(1)), int(m.group(2)))


def swing(beat: float, amount: float, unit: float = 0.5) -> float:
    """Delay off-beat subdivisions: amount 0 = straight, 1 = full triplet swing."""
    if amount <= 0:
        return beat
    pos = beat % (unit * 2)
    if abs(pos - unit) < 1e-6:
        return beat + unit * amount / 3
    return beat
