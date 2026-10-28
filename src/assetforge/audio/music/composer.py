"""A seeded, rule-based composer: a short brief (genre, mood, tempo, key, length, energy) → a `Score`.

It writes game music, not hit songs: loops that repeat well, stingers, and simple linear cues.
Per genre a template sets the tempo range, scale, progressions, instruments per role, drum groove,
bass and accompaniment styles and melody rules. Then, all from the seed:

- **structure**: key, tempo, progression per section (A, B…; intro/outro for linear cues);
- **melody**: a one-bar rhythmic/pitch motif developed over 4-bar phrases (repeat, sequence,
  vary, cadence), chord tones on strong beats, stepwise motion with occasional leaps;
- **accompaniment**: pads, block chords, arpeggios, strums, ostinatos or Alberti patterns;
- **bass**: roots, driving eighths, octaves, walking lines or pedals;
- **drums**: a genre groove with phrase-end fills and crashes.

Each aspect has its own random stream, so `energy` (how many layers play and how busy they are)
can change without changing the tune; that is how matched calm/combat versions are made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assetforge.audio.music.score import Score
from assetforge.audio.music.theory import Chord, Key, chord, parse_key, parse_meter, voice
from assetforge.errors import InvalidInputError

COMPOSER_VERSION = 1
Form = Literal["loop", "linear", "stinger"]
STINGERS = ("victory", "defeat", "level_up", "game_over", "discovery", "danger", "collect")
ASPECTS = ("structure", "melody", "harmony", "bass", "drums")


@dataclass(frozen=True)
class Genre:
    name: str
    description: str
    tempo: tuple[int, int]
    scales: tuple[str, ...]  # first is the default; minor-leaning moods pick a minor one when present
    progressions: tuple[str, ...]
    instruments: dict[str, str]  # role → instrument
    drums: str  # groove name
    bass: str  # root | eighths | octave | walking | pedal
    comp: str  # pad | block | arp | strum | ostinato | alberti
    melody_range: tuple[int, int] = (67, 86)
    density: float = 0.55  # melody busyness 0..1
    leap: float = 0.2
    meter: str = "4/4"
    swing: float = 0.0
    space: str = "room"
    chord_bars: int = 1  # bars per chord
    extra: dict[str, str] = field(default_factory=dict)  # optional roles added at higher energy (arp, counter…)
    tags: tuple[str, ...] = ()


MAJOR_POOL = ("I V vi IV", "I IV V I", "vi IV I V", "I vi IV V", "I IV vi V", "I iii IV V")
MINOR_POOL = ("i VI III VII", "i iv v i", "i VI iv V", "i VII VI VII", "i iv VI V", "i III VII iv")

GENRES: dict[str, Genre] = {g.name: g for g in (
    Genre("chiptune", "8-bit console music: square leads, triangle bass, noise drums", (128, 160), ("major", "minor"),
          MAJOR_POOL, {"melody": "chip_pulse", "harmony": "chip_square", "bass": "chip_triangle", "drums": "drums_chip"},
          "chip", "octave", "arp", (72, 91), 0.7, 0.3, space="dry", tags=("retro", "8bit", "arcade", "pixel", "nes")),
    Genre("synthwave", "80s retro synths: supersaw pads, arps, gated drums", (90, 118), ("minor", "dorian"),
          MINOR_POOL, {"melody": "saw_lead", "harmony": "supersaw_pad", "bass": "synth_bass", "drums": "drums_808"},
          "synthwave", "eighths", "pad", (64, 84), 0.45, 0.2, extra={"arp": "pluck"}, space="hall",
          tags=("retro", "80s", "neon", "outrun", "cyberpunk", "racing")),
    Genre("orchestral_adventure", "Heroic orchestral: strings, brass melody, timpani", (100, 128), ("major", "mixolydian"),
          ("I V vi IV", "I IV I V", "I bVII IV I", "vi IV I V"), {"melody": "brass", "harmony": "strings",
          "bass": "pizzicato", "drums": "drums_orchestral"}, "march", "root", "ostinato", (62, 81), 0.5, 0.25,
          extra={"counter": "flute"}, space="hall", tags=("orchestral", "heroic", "adventure", "epic", "fantasy", "rpg")),
    Genre("epic_battle", "Battle music: driving string ostinato, taiko, brass and choir", (130, 160),
          ("minor", "harmonic_minor"), ("i VI III VII", "i VII VI VII", "i iv VI V", "VI VII i i"),
          {"melody": "brass", "harmony": "strings", "bass": "strings", "drums": "drums_orchestral"}, "epic", "eighths",
          "ostinato", (60, 79), 0.5, 0.3, extra={"counter": "choir"}, space="hall",
          tags=("battle", "war", "combat", "fight", "epic", "action")),
    Genre("boss_fight", "Boss battle: aggressive, dark and fast", (140, 172), ("harmonic_minor", "phrygian", "minor"),
          ("i bII i bII", "i bVI bVII i", "i iv bII V", "i VI bII V"), {"melody": "saw_lead", "harmony": "strings",
          "bass": "acid_bass", "drums": "drums"}, "driving", "eighths", "ostinato", (62, 84), 0.65, 0.35,
          extra={"counter": "brass", "arp": "pluck"}, space="room", tags=("boss", "fight", "danger", "intense", "action")),
    Genre("action", "Fast action/chase: synth bass, driving drums, strings", (138, 162), ("minor", "dorian"),
          MINOR_POOL, {"melody": "square_lead", "harmony": "strings", "bass": "synth_bass", "drums": "drums"},
          "driving", "eighths", "ostinato", (64, 84), 0.6, 0.3, extra={"arp": "pluck"},
          tags=("action", "chase", "run", "racing", "fast", "platformer")),
    Genre("fantasy_village", "Cosy village: lute and harp, flute melody, light percussion", (84, 108),
          ("major", "mixolydian"), ("I IV I V", "I vi IV V", "I V IV I", "I IV vi V"), {"melody": "flute",
          "harmony": "lute", "bass": "pizzicato", "drums": "drums"}, "folk", "root", "arp", (67, 86), 0.5, 0.2,
          meter="3/4", extra={"counter": "harp"}, space="room",
          tags=("village", "town", "medieval", "folk", "cozy", "peaceful", "rpg", "tavern")),
    Genre("ambient_drone", "Ambient: slow pads, sparse bells, no drums", (60, 80), ("lydian", "minor", "major"),
          ("I IV", "i VI", "I ii", "i iv", "I V vi IV"), {"melody": "bell", "harmony": "warm_pad", "bass": "sub_bass"},
          "none", "pedal", "pad", (72, 91), 0.2, 0.3, chord_bars=2, space="space",
          tags=("ambient", "calm", "relax", "space", "dream", "meditation", "menu")),
    Genre("lofi", "Lo-fi hip hop: dusty electric piano 7ths, lazy swung beat", (70, 90), ("major", "dorian"),
          ("ii7 V7 Imaj7 vi7", "Imaj7 vi7 ii7 V7", "IVmaj7 iii7 ii7 Imaj7", "vi7 ii7 V7 Imaj7"),
          {"melody": "electric_piano", "harmony": "electric_piano", "bass": "fm_bass", "drums": "drums"}, "lofi",
          "root", "block", (67, 84), 0.4, 0.2, swing=0.55, space="room", tags=("lofi", "chill", "study", "relaxed", "cozy")),
    Genre("puzzle_casual", "Light and bouncy casual/puzzle music", (100, 126), ("major",),
          ("I vi IV V", "I IV V I", "I V vi IV", "IV V iii vi"), {"melody": "marimba", "harmony": "pluck",
          "bass": "fm_bass", "drums": "drums"}, "light", "octave", "block", (72, 88), 0.6, 0.25,
          extra={"counter": "celesta"}, tags=("puzzle", "casual", "cute", "happy", "mobile", "match3", "bouncy")),
    Genre("jazz_lounge", "Lounge jazz: swung ride, walking bass, piano 7ths", (96, 132), ("major", "dorian"),
          ("ii7 V7 Imaj7 VImaj7", "Imaj7 vi7 ii7 V7", "iii7 vi7 ii7 V7"), {"melody": "clarinet", "harmony": "piano",
          "bass": "pizzicato", "drums": "drums"}, "jazz", "walking", "block", (62, 82), 0.5, 0.3, swing=0.6,
          tags=("jazz", "lounge", "noir", "bar", "elevator", "shop")),
    Genre("rock", "Rock: overdriven leads and power chords, rock beat", (110, 150), ("minor", "mixolydian"),
          ("i VI III VII", "I bVII IV I", "i VII VI V", "I V vi IV"), {"melody": "saw_lead", "harmony": "saw_lead",
          "bass": "synth_bass", "drums": "drums"}, "rock", "eighths", "strum", (62, 81), 0.55, 0.25,
          tags=("rock", "guitar", "energetic", "sports", "racing")),
    Genre("horror", "Horror: dissonant low strings, music box, sparse hits", (56, 76), ("phrygian", "harmonic_minor"),
          ("i bII", "i bii i bII", "i iv bII i"), {"melody": "celesta", "harmony": "strings", "bass": "strings",
          "drums": "drums_orchestral"}, "sparse", "pedal", "pad", (72, 88), 0.2, 0.4, chord_bars=2, space="cave",
          tags=("horror", "scary", "creepy", "dark", "haunted", "suspense", "spooky")),
    Genre("mystery", "Mystery/stealth: pizzicato ostinato, clarinet, soft pads", (78, 100),
          ("harmonic_minor", "dorian"), ("i iv i V", "i VI iv V", "i bVI bVII i"), {"melody": "clarinet",
          "harmony": "warm_pad", "bass": "pizzicato", "drums": "drums"}, "sparse", "walking", "ostinato", (62, 81),
          0.4, 0.25, tags=("mystery", "stealth", "detective", "sneak", "investigation", "puzzle")),
    Genre("sci_fi", "Sci-fi: shimmering arps, wide pads, bell lead", (90, 122), ("lydian", "minor"),
          ("I II", "i VI VII i", "I V/1 IV", "i bVI bVII"), {"melody": "bell", "harmony": "supersaw_pad",
          "bass": "sub_bass", "drums": "drums_808"}, "half_time", "pedal", "arp", (67, 88), 0.4, 0.3,
          extra={"arp": "pluck"}, space="hall", tags=("scifi", "space", "future", "tech", "cyber", "station")),
    Genre("menu_theme", "Title/menu: warm piano and strings, gentle melody", (84, 104), ("major",),
          ("I V vi IV", "IV I V vi", "I iii IV V"), {"melody": "celesta", "harmony": "piano", "bass": "sub_bass",
          "drums": "drums"}, "light", "root", "alberti", (72, 88), 0.4, 0.2, extra={"counter": "strings"},
          tags=("menu", "title", "main", "start", "theme", "intro")),
    Genre("credits", "End credits: broad, warm and conclusive", (76, 96), ("major",),
          ("I V vi iii IV I IV V", "I IV vi V"), {"melody": "flute", "harmony": "strings", "bass": "pizzicato",
          "drums": "drums_orchestral"}, "march", "root", "pad", (65, 84), 0.4, 0.25, extra={"counter": "piano"},
          space="hall", tags=("credits", "ending", "finale", "outro", "emotional")),
)}  # fmt: skip

MINOR_MOODS = {"sad", "dark", "tense", "scary", "melancholy", "mysterious", "ominous", "sinister", "danger", "angry",
               "epic", "dramatic", "gloomy", "eerie", "serious", "suspense"}  # fmt: skip
MOOD_GENRE = {"calm": "ambient_drone", "peaceful": "fantasy_village", "happy": "puzzle_casual", "playful": "puzzle_casual",
              "cute": "puzzle_casual", "heroic": "orchestral_adventure", "epic": "epic_battle", "tense": "mystery",
              "scary": "horror", "spooky": "horror", "intense": "boss_fight", "retro": "chiptune", "chill": "lofi",
              "relaxed": "lofi", "mysterious": "mystery", "dark": "horror", "sad": "ambient_drone",
              "energetic": "action", "triumphant": "orchestral_adventure"}  # fmt: skip


class Brief(BaseModel):
    """What to compose. Everything is optional except the genre (or a mood that implies one)."""

    model_config = ConfigDict(extra="forbid")

    genre: str = Field("puzzle_casual", description="See `music genres`")
    mood: str | None = Field(None, description="e.g. heroic, tense, calm, sad, playful (leans scale and tempo)")
    tempo: float | None = Field(None, ge=40, le=240)
    key: str | None = Field(None, description="e.g. 'D minor', 'F# dorian'")
    meter: str | None = None
    bars: int | None = Field(None, ge=1, le=256)
    seconds: float | None = Field(None, gt=0, le=600, description="Target length; rounded to whole phrases")
    form: Form = "loop"
    stinger: Literal["victory", "defeat", "level_up", "game_over", "discovery", "danger", "collect"] | None = None
    energy: float = Field(0.5, ge=0, le=1, description="0 sparse and quiet … 1 full and busy")
    instruments: dict[str, str] = Field(default_factory=dict, description="role → instrument overrides")
    progression: str | None = Field(None, description="Roman numerals, e.g. 'i VI III VII'")
    swing: float | None = Field(None, ge=0, le=1)
    space: str | None = None
    fixed: dict[str, int] = Field(default_factory=dict, description="Pin aspect seeds: structure, melody, harmony, bass, drums")

    @field_validator("genre")
    @classmethod
    def _genre(cls, value: str) -> str:
        return resolve_genre(value)

    @field_validator("instruments")
    @classmethod
    def _instruments(cls, value: dict[str, str]) -> dict[str, str]:
        from assetforge.audio.music.instruments import resolve_instrument

        return {role: resolve_instrument(name) for role, name in value.items()}


def resolve_genre(text: str) -> str:
    key = text.strip().lower().replace("-", "_").replace(" ", "_")
    if key in GENRES:
        return key
    aliases = {"8bit": "chiptune", "chip": "chiptune", "retro": "chiptune", "orchestral": "orchestral_adventure",
               "adventure": "orchestral_adventure", "battle": "epic_battle", "epic": "epic_battle", "boss": "boss_fight",
               "village": "fantasy_village", "fantasy": "fantasy_village", "ambient": "ambient_drone",
               "drone": "ambient_drone", "lo_fi": "lofi", "puzzle": "puzzle_casual", "casual": "puzzle_casual",
               "jazz": "jazz_lounge", "scifi": "sci_fi", "space": "sci_fi", "menu": "menu_theme", "title": "menu_theme",
               "ending": "credits", "chase": "action", "stealth": "mystery", "scary": "horror"}  # fmt: skip
    if key in aliases:
        return aliases[key]
    words = set(re.split(r"[_\s]+", key))
    best = max(GENRES.values(), key=lambda g: len(words & set(g.tags)) + len(words & set(g.name.split("_"))))
    if words & (set(best.tags) | set(best.name.split("_"))):
        return best.name
    raise InvalidInputError(f"Unknown music genre {text!r}", available=sorted(GENRES))


# ---------------------------------------------------------------------- helpers


@dataclass
class Plan:
    genre: Genre
    key: Key
    tempo: float
    meter: str
    beats_per_bar: float
    bars: int
    chords: list[Chord]  # one per bar
    sections: list[tuple[str, int, int]]  # (name, first bar, bar count)
    seeds: dict[str, int]
    form: Form


def _rng(seeds: dict[str, int], aspect: str) -> np.random.Generator:
    return np.random.default_rng(seeds[aspect])


def _seeds(brief: Brief, seed: int) -> dict[str, int]:
    derived = {a: int(np.random.SeedSequence([seed, i]).generate_state(1)[0]) for i, a in enumerate(ASPECTS)}
    return derived | {k: v for k, v in brief.fixed.items() if k in ASPECTS}


def _pick_scale(genre: Genre, mood: str | None, rng: np.random.Generator) -> str:
    if mood and set(re.split(r"\W+", mood.lower())) & MINOR_MOODS:
        minor = [s for s in genre.scales if s in ("minor", "harmonic_minor", "dorian", "phrygian")]
        if minor:
            return minor[0]
    return genre.scales[0] if rng.uniform() < 0.7 else genre.scales[int(rng.integers(len(genre.scales)))]


def _is_minor_scale(scale: str) -> bool:
    return scale in ("minor", "harmonic_minor", "dorian", "phrygian", "melodic_minor", "minor_pentatonic", "blues",
                     "locrian", "hirajoshi")  # fmt: skip


def _progression_pool(genre: Genre, key: Key) -> list[str]:
    minor = _is_minor_scale(key.scale)
    own = [p for p in genre.progressions if (p.split()[0][0].islower() if p.split()[0][0] not in "b#" else
                                             p.split()[0][1].islower()) == minor]  # fmt: skip
    return own or list(MINOR_POOL if minor else MAJOR_POOL)


def plan_structure(brief: Brief, seed: int) -> Plan:
    genre = GENRES[brief.genre]
    seeds = _seeds(brief, seed)
    rng = _rng(seeds, "structure")
    if brief.key:
        key = parse_key(brief.key)
    else:
        key = Key(int(rng.integers(12)), _pick_scale(genre, brief.mood, rng))
    lo, hi = genre.tempo
    tempo = brief.tempo or round(lo + (hi - lo) * float(np.clip(0.25 + 0.5 * brief.energy + rng.uniform(-0.2, 0.2), 0, 1)))
    meter = brief.meter or genre.meter
    bpb = parse_meter(meter).beats_per_bar
    if brief.form == "stinger":
        bars = brief.bars or {"collect": 1, "level_up": 2, "victory": 3, "discovery": 2, "danger": 2}.get(
            brief.stinger or "victory", 2)  # fmt: skip
    elif brief.bars:
        bars = brief.bars
    else:
        seconds = brief.seconds or (45.0 if brief.form == "loop" else 75.0)
        bars = max(4, int(round(seconds / (bpb * 60 / tempo) / 4)) * 4)
    progression_text = brief.progression or ""
    pool = _progression_pool(genre, key)
    a_prog = progression_text or pool[int(rng.integers(len(pool)))]
    b_prog = progression_text or pool[int(rng.integers(len(pool)))]
    a_chords = [chord(sym, key) for sym in a_prog.split()]
    b_chords = [chord(sym, key) for sym in b_prog.split()]
    sections: list[tuple[str, int, int]] = []
    if brief.form == "linear" and bars >= 12:
        intro = 2 if bars < 24 else 4
        outro = 2
        body = bars - intro - outro
        sections = [("intro", 0, intro)]
        half = body // 2
        sections += [("A", intro, half), ("B", intro + half, body - half), ("outro", bars - outro, outro)]
    elif bars >= 16:
        half = bars // 2
        sections = [("A", 0, half), ("B", half, bars - half)]
    else:
        sections = [("A", 0, bars)]
    chords: list[Chord] = []
    for name, start, count in sections:
        prog = b_chords if name == "B" else a_chords
        for bar in range(count):
            chords.append(prog[(bar // genre.chord_bars) % len(prog)])
    if brief.form == "linear":  # end on the tonic
        tonic = chord("i" if _is_minor_scale(key.scale) else "I", key)
        chords[-1] = tonic
        if len(chords) > 1 and sections[-1][0] == "outro":
            chords[-2] = chord("iv" if _is_minor_scale(key.scale) else "IV", key)
    return Plan(genre, key, float(tempo), meter, bpb, bars, chords, sections, seeds, brief.form)


# ---------------------------------------------------------------------- parts


def _note(pitch: int, start: float, beats: float, velocity: float) -> dict[str, Any]:
    return {"pitch": int(pitch), "start": round(start, 4), "beats": round(max(beats, 0.05), 4),
            "velocity": round(float(np.clip(velocity, 0.05, 1.0)), 3)}  # fmt: skip


RHYTHMS = {  # one bar of 4 beats; scaled for other meters
    "sparse": [[4], [2, 2], [3, 1], [1.5, 2.5], [2, 1, 1]],
    "medium": [[1, 1, 2], [1, 0.5, 0.5, 2], [0.5, 0.5, 1, 2], [1.5, 0.5, 1, 1], [1, 1, 1, 1], [2, 0.5, 0.5, 1]],
    "busy": [[0.5] * 8, [0.5, 0.5, 1, 0.5, 0.5, 1], [0.25, 0.25, 0.5, 1, 0.5, 0.5, 1], [1, 0.5, 0.5, 0.5, 0.5, 1],
             [0.75, 0.25, 0.5, 0.5, 1, 1]],
}  # fmt: skip


def _rhythm(rng: np.random.Generator, density: float, bpb: float) -> list[float]:
    pool = RHYTHMS["sparse" if density < 0.35 else "busy" if density > 0.62 else "medium"]
    pattern = pool[int(rng.integers(len(pool)))]
    scale = bpb / 4
    return [d * scale for d in pattern]


def _chord_tone_near(target: int, ch: Chord, key: Key, lo: int, hi: int) -> int:
    candidates = [p for p in range(lo, hi + 1) if p % 12 in ch.pitch_classes]
    return min(candidates, key=lambda p: (abs(p - target), p)) if candidates else key.snap(target)


def _scale_step(pitch: int, steps: int, key: Key) -> int:
    pcs = sorted(key.pitch_classes)
    p = pitch
    direction = 1 if steps > 0 else -1
    for _ in range(abs(steps)):
        p += direction
        while p % 12 not in pcs:
            p += direction
    return p


def melody(plan: Plan, rng: np.random.Generator, density: float) -> list[dict[str, Any]]:
    g, key, bpb = plan.genre, plan.key, plan.beats_per_bar
    lo, hi = g.melody_range
    notes: list[dict[str, Any]] = []
    motifs: dict[str, tuple[list[float], list[int]]] = {}
    for name, first, count in plan.sections:
        if name in ("intro", "outro"):
            continue
        if name not in motifs:
            rhythm = _rhythm(rng, density, bpb)
            contour = [0] + [int(rng.choice([-2, -1, -1, 1, 1, 2, 3, -3] if rng.uniform() < g.leap else [-1, 1, -1, 1, 0]))
                             for _ in rhythm[1:]]  # fmt: skip
            motifs[name] = (rhythm, contour)
        rhythm, contour = motifs[name]
        previous = (lo + hi) // 2
        for bar in range(count):
            absolute = first + bar
            ch = plan.chords[absolute]
            phrase_pos = bar % 4
            if phrase_pos == 3 or bar == count - 1:  # cadence: a long chord tone (the tonic at the end)
                target = key.degree(0, (lo + hi) // 24 - 1) if bar == count - 1 else previous
                tone = _chord_tone_near(target, ch, key, lo, hi)
                lead_in = rhythm[0] if rhythm[0] < bpb else bpb / 2
                if lead_in < bpb:
                    step = _scale_step(tone, int(rng.choice([1, -1])), key)
                    notes.append(_note(step, absolute * bpb, lead_in, 0.7))
                notes.append(_note(tone, absolute * bpb + (lead_in if lead_in < bpb else 0),
                                   bpb - (lead_in if lead_in < bpb else 0), 0.75))  # fmt: skip
                previous = tone
                continue
            bar_rhythm = rhythm if phrase_pos != 2 else _rhythm(rng, density, bpb)
            bar_contour = contour if phrase_pos != 2 else [0] + [int(rng.choice([-1, 1, 2, -2])) for _ in bar_rhythm[1:]]
            start = _chord_tone_near(previous + (2 if phrase_pos == 1 else 0), ch, key, lo, hi)
            pitch, t = start, 0.0
            for i, (dur, step) in enumerate(zip(bar_rhythm, bar_contour, strict=False)):
                if i:
                    pitch = _scale_step(pitch, step, key) if step else pitch
                    if pitch > hi or pitch < lo:
                        pitch = _scale_step(pitch, -2 * int(np.sign(step)) or -1, key)
                strong = abs(t % (bpb / 2)) < 1e-6
                if strong:
                    pitch = _chord_tone_near(pitch, ch, key, lo, hi)
                if density < 0.5 and not strong and rng.uniform() < 0.15:
                    t += dur  # a breath
                    continue
                accent = 0.82 if strong else 0.68
                notes.append(_note(pitch, absolute * bpb + t, dur * 0.95, accent + rng.uniform(-0.05, 0.05)))
                t += dur
            previous = pitch
    return notes


def counter_line(plan: Plan, rng: np.random.Generator) -> list[dict[str, Any]]:
    """Long notes on 3rds/7ths in the middle register: a second voice under the melody."""
    notes, previous = [], 64
    for bar, ch in enumerate(plan.chords):
        tones = sorted(ch.pitch_classes, key=lambda pc: (pc - ch.root) % 12)
        pc = tones[1] if len(tones) > 1 else tones[0]
        pitch = min((p for p in range(55, 76) if p % 12 == pc), key=lambda p: abs(p - previous))
        half = plan.beats_per_bar / 2
        notes.append(_note(pitch, bar * plan.beats_per_bar, half, 0.55))
        second = _chord_tone_near(pitch + int(rng.choice([-2, 2, 0])), ch, plan.key, 55, 76)
        notes.append(_note(second, bar * plan.beats_per_bar + half, half, 0.5))
        previous = second
    return notes


def accompaniment(plan: Plan, style: str, rng: np.random.Generator, energy: float) -> list[dict[str, Any]]:
    bpb = plan.beats_per_bar
    notes: list[dict[str, Any]] = []
    previous: list[int] | None = None
    center = 60 if style in ("pad", "block", "strum") else 64
    for bar, ch in enumerate(plan.chords):
        v = voice(ch, center=center, previous=previous, size=3 if style in ("arp", "alberti") else 4)
        previous = v
        t0 = bar * bpb
        section = next((name for name, first, count in plan.sections if first <= bar < first + count), "A")
        if style == "pad" or section in ("intro", "outro"):
            for p in v:
                notes.append(_note(p, t0, bpb, 0.55 + 0.2 * energy))
        elif style == "block":
            hits = [0.0, bpb / 2] if energy < 0.5 else [0.0, 1.5 * bpb / 4, bpb / 2, 3.5 * bpb / 4]
            for h in hits:
                for p in v:
                    notes.append(_note(p, t0 + h, bpb / 4 if energy > 0.5 else bpb / 2 * 0.9, 0.55))
        elif style == "arp":
            step = 0.25 if energy > 0.6 else 0.5
            shape = [*v, v[-1] + 12 - 12 * (v[-1] + 12 > 96), *reversed(v[1:-1])]
            for i in range(int(bpb / step)):
                notes.append(_note(shape[i % len(shape)], t0 + i * step, step * 0.9, 0.5 + 0.1 * (i % 2 == 0)))
        elif style == "strum":
            for h in ([0.0, bpb / 2] if energy < 0.6 else [0.0, 1.0, 1.5, 2.5, 3.0][: int(bpb + 1)]):
                for k, p in enumerate(v):
                    notes.append(_note(p, t0 + h + k * 0.015, bpb / 4, 0.6))
        elif style == "ostinato":
            step = 0.25 if energy > 0.65 else 0.5
            root = min(v)
            pattern = [root, root + 7, root + 12, root + 7]
            for i in range(int(bpb / step)):
                notes.append(_note(pattern[i % 4], t0 + i * step, step * 0.85, 0.55 + 0.15 * (i % 4 == 0)))
        elif style == "alberti":
            low, high, mid = v[0], v[-1], v[len(v) // 2]
            for i in range(int(bpb / 0.5)):
                notes.append(_note([low, high, mid, high][i % 4], t0 + i * 0.5, 0.45, 0.5))
    return notes


def bassline(plan: Plan, style: str, rng: np.random.Generator, energy: float) -> list[dict[str, Any]]:
    bpb = plan.beats_per_bar
    notes = []
    for bar, ch in enumerate(plan.chords):
        root = 36 + (ch.bass if ch.bass is not None else ch.root) % 12
        root = root + 12 if root < 33 else root
        t0 = bar * bpb
        next_root = 36 + plan.chords[(bar + 1) % len(plan.chords)].root % 12
        if style == "pedal":
            notes.append(_note(36 + plan.key.tonic, t0, bpb, 0.7))
        elif style == "root":
            notes += [_note(root, t0, bpb / 2 * 0.95, 0.8), _note(root + (7 if rng.uniform() < 0.5 else 0), t0 + bpb / 2, bpb / 2 * 0.9, 0.7)]
        elif style == "eighths":
            for i in range(int(bpb * 2)):
                notes.append(_note(root, t0 + i * 0.5, 0.45, 0.75 + 0.1 * (i % 2 == 0)))
        elif style == "octave":
            for i in range(int(bpb * 2)):
                notes.append(_note(root + 12 * (i % 2), t0 + i * 0.5, 0.45, 0.75))
        elif style == "walking":
            tones = sorted({root, root + (ch.intervals[1] if len(ch.intervals) > 1 else 4), root + 7})
            line = [root] + [int(rng.choice(tones)) for _ in range(int(bpb) - 2)]
            approach = next_root + int(rng.choice([-1, 1, 2]))
            for i, p in enumerate([*line, approach][: int(bpb)]):
                notes.append(_note(p, t0 + i, 0.95, 0.7))
    return notes


GROOVES: dict[str, dict[str, list[tuple[float, float]]]] = {  # piece → [(beat, velocity)] for one 4/4 bar
    "chip": {"kick": [(0, 0.9), (2, 0.9)], "snare": [(1, 0.8), (3, 0.8)], "hat_closed": [(b / 2, 0.5) for b in range(8)]},
    "synthwave": {"kick": [(0, 1), (2, 1)], "snare": [(1, 0.9), (3, 0.9)], "hat_closed": [(b / 2, 0.45) for b in range(8)]},
    "rock": {"kick": [(0, 1), (2, 0.9), (2.5, 0.7)], "snare": [(1, 0.95), (3, 0.95)], "hat_closed": [(b / 2, 0.55) for b in range(8)]},
    "driving": {"kick": [(0, 1), (1, 0.8), (2, 1), (3, 0.8)], "snare": [(1, 0.9), (3, 0.9)],
                "hat_closed": [(b / 4, 0.4 + 0.15 * (b % 2 == 0)) for b in range(16)]},
    "four_on_floor": {"kick": [(b, 1) for b in range(4)], "clap": [(1, 0.8), (3, 0.8)], "hat_open": [(b + 0.5, 0.5) for b in range(4)]},
    "lofi": {"kick": [(0, 0.8), (1.75, 0.6), (2.5, 0.7)], "snare": [(1, 0.6), (3, 0.6)], "hat_closed": [(b / 2, 0.3) for b in range(8)]},
    "jazz": {"ride": [(0, 0.6), (1, 0.55), (1.5, 0.45), (2, 0.6), (3, 0.55), (3.5, 0.45)], "hat_pedal": [(1, 0.5), (3, 0.5)],
             "kick": [(0, 0.35)]},
    "march": {"kick": [(0, 0.9), (2, 0.8)], "snare": [(1, 0.5), (3, 0.5), (3.5, 0.4)], "crash": []},
    "epic": {"kick": [(0, 1), (0.75, 0.7), (1.5, 0.8), (2, 1), (2.75, 0.7), (3.5, 0.8)], "snare": [(1, 0.7), (3, 0.7)],
             "tom_low": [(3.75, 0.6)]},
    "folk": {"tambourine": [(1, 0.5), (3, 0.5)], "shaker": [(b / 2, 0.35) for b in range(8)], "kick": [(0, 0.5)]},
    "light": {"kick": [(0, 0.7), (2, 0.6)], "rim": [(1, 0.6), (3, 0.6)], "shaker": [(b / 2, 0.3) for b in range(8)]},
    "half_time": {"kick": [(0, 0.9), (2.5, 0.6)], "snare": [(2, 0.85)], "hat_closed": [(b / 2, 0.35) for b in range(8)]},
    "sparse": {"kick": [(0, 0.6)], "tom_low": [(2.5, 0.4)]},
    "none": {},
}  # fmt: skip


def drum_part(plan: Plan, groove: str, rng: np.random.Generator, energy: float) -> list[dict[str, Any]]:
    bpb = plan.beats_per_bar
    pattern = GROOVES.get(groove, {})
    if not pattern or energy < 0.15:
        return []
    scale = bpb / 4
    notes = []
    for bar in range(plan.bars):
        section = next((name for name, first, count in plan.sections if first <= bar < first + count), "A")
        if section in ("intro",) and energy < 0.8:
            continue
        t0 = bar * bpb
        if bar % 4 == 0 and energy > 0.55 and groove not in ("sparse", "jazz", "folk"):
            notes.append({"pitch": "crash", "start": t0, "beats": 1.0, "velocity": 0.6})
        fill = bar % 4 == 3 and energy > 0.4 and groove not in ("sparse", "jazz", "none")
        for piece, hits in pattern.items():
            for beat, velocity in hits:
                b = beat * scale
                if b >= bpb or (fill and b >= bpb - 1):
                    continue
                if piece.startswith("hat") and energy < 0.3:
                    continue
                notes.append({"pitch": piece, "start": round(t0 + b, 4), "beats": 0.25,
                              "velocity": round(float(np.clip(velocity * (0.7 + 0.4 * energy) + rng.uniform(-0.05, 0.05), 0.05, 1)), 3)})  # fmt: skip
        if fill:
            pieces = ["snare", "tom_high", "tom_mid", "tom_low"] if groove not in ("epic", "march") else ["tom_low", "kick"]
            for i in range(4):
                notes.append({"pitch": pieces[i % len(pieces)], "start": round(t0 + bpb - 1 + i * 0.25, 4), "beats": 0.25,
                              "velocity": round(0.6 + 0.1 * i, 3)})  # fmt: skip
    return notes


# ---------------------------------------------------------------------- stingers


def stinger(brief: Brief, plan: Plan, rng: np.random.Generator) -> list[dict[str, Any]]:
    kind = brief.stinger or "victory"
    key, bpb = plan.key, plan.beats_per_bar
    lead = brief.instruments.get("melody") or plan.genre.instruments["melody"]
    harm = brief.instruments.get("harmony") or plan.genre.instruments["harmony"]
    kit = plan.genre.instruments.get("drums", "drums")
    total = plan.bars * bpb
    tonic = key.degree(0, 5)
    tracks: list[dict[str, Any]] = []

    def run(degrees: list[int], start: float, step: float, octave: int = 5, last: float | None = None, vel: float = 0.8):
        out = [_note(key.degree(d, octave), start + i * step, step * 0.9, vel) for i, d in enumerate(degrees)]
        if last:
            out[-1]["beats"] = last
        return out

    if kind in ("victory", "level_up", "collect"):
        step = {"victory": 0.5, "level_up": 0.25, "collect": 0.25}[kind]
        degrees = {"victory": [0, 2, 4, 7, 4, 7], "level_up": [0, 2, 4, 7, 9, 11, 14], "collect": [4, 7]}[kind]
        mel = run(degrees, 0, step, last=total - step * (len(degrees) - 1))
        chord_start = step * (len(degrees) - 1)
        final = chord("I" if not _is_minor_scale(key.scale) else "i", key)
        tracks.append({"name": "fanfare", "instrument": lead, "role": "melody", "notes": mel})
        tracks.append({"name": "chord", "instrument": harm, "role": "harmony",
                       "notes": [_note(p, chord_start, total - chord_start, 0.7) for p in voice(final, center=64)]})  # fmt: skip
        if kind == "victory":
            tracks.append({"name": "hits", "instrument": kit, "role": "drums",
                           "notes": [{"pitch": "snare", "start": i * 0.25, "beats": 0.25, "velocity": 0.5 + 0.05 * i}
                                     for i in range(int(chord_start / 0.25))] +
                                    [{"pitch": "crash", "start": chord_start, "beats": 1, "velocity": 0.9},
                                     {"pitch": "kick", "start": chord_start, "beats": 1, "velocity": 1}]})  # fmt: skip
    elif kind in ("defeat", "game_over"):
        degrees = [4, 3, 2, 1, 0] if kind == "defeat" else [7, 6, 4, 2, 0]
        step = total / (len(degrees) + 2)
        mel = run(degrees, 0, step, octave=4, last=step * 3, vel=0.65)
        tracks.append({"name": "fall", "instrument": lead, "role": "melody", "notes": mel})
        final = chord("i", key) if _is_minor_scale(key.scale) else chord("iv", key)
        tracks.append({"name": "chord", "instrument": harm, "role": "harmony",
                       "notes": [_note(p, 0, total, 0.5) for p in voice(final, center=55)]})  # fmt: skip
    elif kind == "discovery":
        lyd = Key(key.tonic, "lydian")
        mel = [_note(lyd.degree(d, 5), i * 0.25, 0.3, 0.6) for i, d in enumerate([0, 2, 3, 4, 6, 7, 9, 11])]
        tracks.append({"name": "shimmer", "instrument": "celesta", "role": "melody", "notes": mel})
        tracks.append({"name": "chord", "instrument": harm, "role": "pad",
                       "notes": [_note(p, 0, total, 0.5) for p in (tonic - 12, tonic - 5, tonic + 4, tonic + 6)]})  # fmt: skip
    else:  # danger
        stabs = [(0, 0), (1.5, 1), (2, 0), (3, 1)]
        notes = []
        for start, flat2 in stabs:
            base = 12 * 4 + key.tonic + flat2
            notes += [_note(base, start, 0.4, 0.9), _note(base + 7, start, 0.4, 0.8)]
        tracks.append({"name": "stabs", "instrument": "brass" if harm != "chip_square" else harm, "role": "harmony", "notes": notes})
        tracks.append({"name": "hits", "instrument": "drums_orchestral", "role": "drums",
                       "notes": [{"pitch": "kick", "start": s, "beats": 1, "velocity": 0.9} for s, _ in stabs]})  # fmt: skip
    return tracks


# ---------------------------------------------------------------------- compose


def compose(brief: Brief, seed: int) -> Score:
    plan = plan_structure(brief, seed)
    g = plan.genre
    roles = {**g.instruments, **brief.instruments}
    energy = brief.energy
    tracks: list[dict[str, Any]] = []
    if brief.form == "stinger":
        tracks = stinger(brief, plan, _rng(plan.seeds, "melody"))
    else:
        if "melody" in roles and energy >= 0.1:
            tracks.append({"name": "melody", "instrument": roles["melody"], "role": "melody",
                           "notes": melody(plan, _rng(plan.seeds, "melody"), g.density), "gain_db": 0.0})  # fmt: skip
        if "harmony" in roles:
            tracks.append({"name": "harmony", "instrument": roles["harmony"], "role": "harmony",
                           "notes": accompaniment(plan, g.comp, _rng(plan.seeds, "harmony"), energy), "gain_db": -4.0})  # fmt: skip
        if "bass" in roles and energy >= 0.2:
            tracks.append({"name": "bass", "instrument": roles["bass"], "role": "bass",
                           "notes": bassline(plan, g.bass, _rng(plan.seeds, "bass"), energy), "gain_db": -2.0})  # fmt: skip
        if "drums" in roles and energy >= 0.25:
            drum_notes = drum_part(plan, g.drums, _rng(plan.seeds, "drums"), energy)
            if drum_notes:
                tracks.append({"name": "drums", "instrument": roles["drums"], "role": "drums", "notes": drum_notes,
                               "gain_db": -3.0})  # fmt: skip
        extra = {**g.extra, **{r: i for r, i in brief.instruments.items() if r in ("arp", "counter")}}
        if "arp" in extra and energy >= 0.45:
            tracks.append({"name": "arp", "instrument": extra["arp"], "role": "arp",
                           "notes": accompaniment(plan, "arp", _rng(plan.seeds, "harmony"), max(energy, 0.7)),
                           "gain_db": -9.0, "pan": 0.3})  # fmt: skip
        if "counter" in extra and energy >= 0.65:
            tracks.append({"name": "counter", "instrument": extra["counter"], "role": "counter",
                           "notes": counter_line(plan, _rng(plan.seeds, "melody")), "gain_db": -7.0, "pan": -0.3})  # fmt: skip
    if g.instruments.get("harmony") == "saw_lead" and g.name == "rock":
        for t in tracks:
            if t["role"] == "harmony":
                t["effects"] = [{"name": "distortion", "params": {"drive_db": 18}}, {"name": "lowpass", "params": {"cutoff_hz": 3500}}]
    tracks = [t for t in tracks if t["notes"]]
    if not tracks:
        raise InvalidInputError("Nothing to play at this energy; raise `energy`")
    sections = [{"name": name, "start": first * plan.beats_per_bar, "beats": count * plan.beats_per_bar}
                for name, first, count in plan.sections]  # fmt: skip
    swing = brief.swing if brief.swing is not None else g.swing
    return Score(tempo=plan.tempo, meter=plan.meter, key=plan.key.name, bars=plan.bars, loop=brief.form == "loop",
                 swing=swing, space=brief.space or g.space, tracks=tracks, sections=sections if brief.form != "stinger" else [])  # fmt: skip


def seeds_for(brief: Brief, seed: int) -> dict[str, int]:
    return _seeds(brief, seed)


def catalog() -> list[dict[str, Any]]:
    return [{"name": g.name, "description": g.description, "tempo": list(g.tempo), "scales": list(g.scales),
             "instruments": g.instruments, "extra": g.extra, "meter": g.meter, "tags": list(g.tags)}
            for g in GENRES.values()]  # fmt: skip
