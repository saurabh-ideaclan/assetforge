"""Read an agent's plain description and pick the engine settings for it (used by `request`).

- sound effects: a recipe when one fits, otherwise a synth patch built from the descriptive words
  (low/high, rising/falling, metallic, noisy, soft, short/long…), so any description makes something;
- ambience: the closest scene, plus extra beds/events the words ask for ("rainy forest at night" →
  forest_night + rain), intensity and time of day;
- music: genre, mood, tempo ("140 bpm"), key ("in D minor"), length ("60s", "16 bars"), form
  (loop / linear / stinger) and stinger kind ("victory jingle").
"""

from __future__ import annotations

import re
from typing import Any

from assetforge.core.classify import tokens


def _words(text: str) -> list[str]:
    return [w for w in tokens(text) if len(w) > 1]


def _hit(word: str, vocabulary: set[str]) -> bool:
    return word in vocabulary or (len(word) >= 4 and any(len(v) >= 4 and (v.startswith(word) or word.startswith(v))
                                                         for v in vocabulary))  # fmt: skip


def seconds_in(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?)\b", text.lower())
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return value / 1000 if unit == "ms" else value * 60 if unit.startswith("m") and unit != "ms" else value


# ---------------------------------------------------------------------- sound effects → patch


def patch_from_words(description: str) -> dict[str, Any]:
    words = set(_words(description))

    def has(*options: str) -> bool:
        return any(_hit(w, set(options)) for w in words)

    freq = 70.0 if has("low", "deep", "bass", "heavy", "huge", "rumble", "big") else 1600.0 if has(
        "high", "tiny", "small", "squeak", "chirp", "sparkle") else 440.0  # fmt: skip
    duration = seconds_in(description) or (1.6 if has("long", "slow", "swell", "drone", "hum") else
                                           0.15 if has("short", "quick", "click", "blip", "tick") else 0.5)  # fmt: skip
    if has("metal", "metallic", "bell", "clang", "ring", "chime"):
        osc = [{"wave": "fm", "freq": freq * 2, "fm_ratio": 3.5, "fm_index": 4}]
    elif has("noise", "whoosh", "wind", "air", "hiss", "static", "steam", "swoosh", "rush"):
        osc = [{"wave": "noise_pink"}]
    elif has("beep", "blip", "digital", "computer", "retro", "8bit", "chip", "arcade"):
        osc = [{"wave": "pulse", "freq": freq, "duty": 0.25}]
    elif has("buzz", "zap", "laser", "electric", "saw", "harsh", "synth"):
        osc = [{"wave": "saw", "freq": freq, "unison": 3}]
    elif has("pluck", "string", "twang", "harp"):
        osc = [{"wave": "pluck", "freq": freq}]
    else:
        osc = [{"wave": "triangle" if has("soft", "warm", "gentle", "round") else "sine", "freq": freq}]
    if has("noisy", "gritty", "rough", "crunchy") and osc[0]["wave"] != "noise_pink":
        osc.append({"wave": "noise_white", "level": 0.3})
    patch: dict[str, Any] = {"duration": round(duration, 3), "oscillators": osc}
    if has("rise", "rising", "up", "charge", "power", "grow", "ascend"):
        patch["pitch"] = [[0, 0], [duration, 12]]
    elif has("fall", "falling", "down", "drop", "descend", "die", "fail", "sink"):
        patch["pitch"] = [[0, 0], [duration, -18]]
    elif has("zap", "laser", "pew", "shoot"):
        patch["pitch"] = [[0, 12], [duration * 0.6, -12]]
    if has("hit", "impact", "click", "knock", "tap", "pluck", "thud", "punch"):
        patch["amp"] = {"points": [[0, 0], [0.003, 1], [duration, 0]]}
    elif has("swell", "rise", "charge", "fade"):
        patch["amp"] = {"attack": duration * 0.7, "decay": 0.05, "sustain": 0.9, "release": duration * 0.2}
    else:
        patch["amp"] = {"attack": 0.005, "decay": duration * 0.3, "sustain": 0.5, "release": duration * 0.4}
    if osc[0]["wave"] == "noise_pink":
        lo, hi = (300, 3000) if not has("high", "bright", "hiss") else (2000, 8000)
        patch["filter"] = {"type": "bandpass", "cutoff": lo, "resonance": 1.2,
                           "envelope": [[0, lo], [duration / 2, hi], [duration, lo]]}  # fmt: skip
    elif has("dark", "muffled", "dull", "underwater", "distant"):
        patch["filter"] = {"type": "lowpass", "cutoff": 900}
    elif has("bright", "crisp", "thin"):
        patch["filter"] = {"type": "highpass", "cutoff": 800}
    if has("wobble", "wobbly", "warble", "vibrato", "alien"):
        patch["vibrato"] = {"rate": 7, "depth": 1.0}
    if has("distorted", "dirty", "crunchy", "angry", "aggressive"):
        patch["drive"] = 0.6
    if has("8bit", "retro", "lofi", "crushed"):
        patch["bits"] = 6
    return patch


# ---------------------------------------------------------------------- ambience


def ambience_from_words(description: str, lib: Any = None) -> dict[str, Any]:
    from assetforge.audio.ambience.beds import BEDS
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.ambience.events import EVENTS

    words = _words(description)
    best, best_score = None, 0
    for scene in scenes(lib).values():
        vocab = set(tokens(scene.name)) | {t for tag in scene.tags for t in tokens(tag)}
        score = sum(2 * _hit(w, set(tokens(scene.name))) + _hit(w, vocab) for w in words)
        if score > best_score:
            best, best_score = scene, score
    out: dict[str, Any] = {}
    covered: set[str] = set()
    if best is not None:
        out["scene"] = best.name
        covered = {t for t in tokens(best.name)} | {t for tag in best.tags for t in tokens(tag)}
        covered |= {b.bed for b in best.beds} | {e.event for e in best.events}
    extra_beds, extra_events = [], []
    for w in words:
        if _hit(w, covered) or w in ("night", "day", "ambience", "ambient", "loop", "sound", "background"):
            continue
        bed = next((b for b in BEDS.values() if _hit(w, set(b.tags) | {b.name})), None)
        if bed is not None and bed.name not in {x["bed"] for x in extra_beds}:
            extra_beds.append({"bed": bed.name, "level_db": -4})
            covered |= set(bed.tags)
            continue
        event = next((e for e in EVENTS.values() if _hit(w, set(e.tags) | {e.name})), None)
        if event is not None and event.name not in {x["event"] for x in extra_events}:
            extra_events.append({"event": event.name, "rate": 3})
            covered |= set(event.tags)
    if extra_beds:
        out["beds"] = extra_beds
    if extra_events:
        out["events"] = extra_events
    lowered = set(words)
    if lowered & {"night", "midnight", "nighttime", "evening", "dark"}:
        out["time_of_day"] = "night"
    elif lowered & {"day", "daytime", "morning", "sunny", "noon"}:
        out["time_of_day"] = "day"
    if lowered & {"light", "gentle", "calm", "quiet", "soft", "distant", "sparse", "peaceful"}:
        out["intensity"] = 0.25
    elif lowered & {"heavy", "storm", "stormy", "intense", "busy", "loud", "crowded", "raging", "violent"}:
        out["intensity"] = 0.85
    if (seconds := seconds_in(description)) and 4 <= seconds <= 600:
        out["length"] = seconds
    if not out.get("scene") and not extra_beds and not extra_events:
        out["beds"] = [{"bed": "forest_air"}]
    return out


# ---------------------------------------------------------------------- music

STINGER_WORDS = {"victory": ("victory", "win", "won", "success", "triumph", "complete", "clear", "fanfare"),
                 "defeat": ("defeat", "lose", "lost", "fail", "failure"),
                 "game_over": ("gameover", "over", "death", "died"),
                 "level_up": ("levelup", "level", "upgrade", "rank"),
                 "discovery": ("discovery", "discover", "secret", "found", "treasure", "reveal"),
                 "danger": ("danger", "alert", "warning", "threat", "ambush"),
                 "collect": ("collect", "coin", "pickup", "reward")}  # fmt: skip


def music_from_words(description: str) -> dict[str, Any]:
    from assetforge.audio.music import composer
    from assetforge.errors import InvalidInputError

    text = description.lower()
    words = _words(description)
    out: dict[str, Any] = {}
    best, best_score = None, 0
    for g in composer.GENRES.values():
        vocab = set(g.name.split("_")) | set(g.tags)
        score = sum(2 * _hit(w, set(g.name.split("_"))) + _hit(w, vocab) for w in words)
        if score > best_score:
            best, best_score = g, score
    if best:
        out["genre"] = best.name
    moods = [w for w in words if w in composer.MOOD_GENRE or w in composer.MINOR_MOODS]
    if moods:
        out["mood"] = " ".join(dict.fromkeys(moods))
        if "genre" not in out:
            out["genre"] = composer.MOOD_GENRE.get(moods[0], "puzzle_casual")
    if m := re.search(r"(\d{2,3})\s*bpm", text):
        out["tempo"] = float(m.group(1))
    if m := re.search(r"\b(?:in\s+)?([a-g][#b]?)\s*(major|minor|dorian|phrygian|lydian|mixolydian|harmonic minor)\b", text):
        out["key"] = f"{m.group(1).upper()}{m.group(1)[1:]} {m.group(2)}"
    if m := re.search(r"(\d+)\s*bars?\b", text):
        out["bars"] = int(m.group(1))
    elif (seconds := seconds_in(description)) and seconds >= 2:
        out["seconds"] = seconds
    is_stinger = bool(set(words) & {"stinger", "sting", "jingle", "fanfare", "cue"}) or "game over" in text or "level up" in text
    if is_stinger:
        compact = text.replace(" ", "")
        kind = next((k for k, vocab in STINGER_WORDS.items() if any(v in compact for v in vocab)), "victory")
        out["stinger"] = kind
        out["form"] = "stinger"
    elif set(words) & {"intro", "outro", "credits", "cutscene", "linear", "ending", "opening"}:
        out["form"] = "linear"
    lowered = set(words)
    if lowered & {"calm", "soft", "gentle", "quiet", "sparse", "relaxed", "minimal", "explore", "exploration"}:
        out["energy"] = 0.3
    elif lowered & {"intense", "fast", "action", "battle", "epic", "boss", "chase", "combat", "driving", "climax"}:
        out["energy"] = 0.8
    if "genre" not in out:
        try:
            out["genre"] = composer.resolve_genre(description)
        except InvalidInputError:
            out["genre"] = "puzzle_casual"
    return out
