"""The story's sound plan: the writer picks, from AssetForge's real catalog, what every location sounds like and how
every music mood is played, so a "creaking floorboards, ticking clock, rain on the roof" family home doesn't get the
word-matched "haunted house" (wind howl, crows, a wolf) and the music follows the story's music direction.

One structured call per story (free on a subscription), validated against the catalog and saved as `sound.json`.
`story/assets.py` turns it into explicit `sound` options for each ambience and music request.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from assetforge.story.models import Id, Mood, Script, Strict, World, walk

PROMPT_VERSION = 2


class BedPick(Strict):
    bed: str
    level_db: float = Field(-12.0, description="-6 (prominent) … -26 (barely there)")
    inside: float | None = Field(None, description="rain only: 0 outdoors … 1 heard from deep inside a building")


class EventPick(Strict):
    event: str
    rate: float = Field(1.0, description="Times per minute (0.2 rare … 6 frequent)")
    gain_db: float = Field(-10.0, description="-4 … -20")
    distance: float = Field(0.5, description="0 close … 1 far away")


class LocationSound(Strict):
    location: Id
    scene: str | None = Field(None, description="A catalog scene to start from, or null to build from beds/events")
    beds: list[BedPick] = Field(default_factory=list)
    events: list[EventPick] = Field(default_factory=list)
    intensity: float = Field(0.4, description="0 sparse and calm … 1 dense")
    space: Literal["dry", "room", "hall", "cave", "outdoor"] | None = None


class RolePick(Strict):
    role: Literal["melody", "harmony", "bass", "drums", "arp", "counter"]
    instrument: str


class MusicCue(Strict):
    mood: Mood
    genre: str
    # a list, not a role→instrument map: strict JSON-schema modes (OpenAI/codex) can't express free-form maps
    instruments: list[RolePick] = Field(default_factory=list, description="Which instrument plays each role")
    energy: float = Field(0.4, description="0 sparse … 1 full")
    tempo: float | None = Field(None, description="BPM, optional")


class SoundPlan(Strict):
    locations: list[LocationSound]
    music: list[MusicCue]


def catalog_text(lib: Any = None) -> str:
    from assetforge.audio.ambience.beds import BEDS
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.ambience.events import EVENTS
    from assetforge.audio.music import composer, instruments

    data = {
        "scenes": {s.name: s.description for s in scenes(lib).values()},
        "beds": {b.name: b.description for b in BEDS.values()},
        "events": {e.name: e.description for e in EVENTS.values()},
        "music_genres": {g["name"]: f"{g['description']} (default: {g['instruments']})" for g in composer.catalog()},
        "instruments": [i["name"] for i in instruments.catalog()],
        "music_roles": ["melody", "harmony", "bass", "drums", "arp", "counter"],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def prompt(script: Script, world: World | None, lib: Any = None) -> str:
    used = {}
    for scene in script.scenes:
        for _, where, when, _ in walk(scene):
            used.setdefault(where, set()).add(when)
    locations = [{"id": loc.id, "name": loc.name, "description": loc.description, "sounds_like": loc.ambience,
                  "times": sorted(used.get(loc.id, []))} for loc in script.locations if loc.id in used]  # fmt: skip
    moods = sorted({st.music for s in script.scenes for st in walk(s)})
    return "\n\n".join([
        f"SOUND CATALOG (use only these names):\n{catalog_text(lib)}",
        f"STORY: {script.title}. Music direction: {world.music_direction if world else ''}",
        "LOCATIONS:\n" + json.dumps(locations, ensure_ascii=False),
        f"MUSIC MOODS USED: {moods}",
        "You are the sound designer of a visual novel. Players listen for a long time while reading, so every "
        "ambience must be cinematic but easy on the ear: a quiet, warm bed with a few characterful details, never "
        "a wall of noise. Rules: (1) For each location, start from the closest catalog scene ONLY if it fits the "
        "mood of the place (never a horror or haunted scene for a normal home), else null and build it from beds "
        "and events. (2) Use at most 3 beds and 4 events. Noise-like beds (rain, wind, room_tone, traffic, "
        "crowd_murmur) belong at -12 dB or quieter; room_tone at -20 or quieter. Rain heard from inside a "
        "building sets inside 0.6-0.9. (3) Events are details: 0.2-3 per minute, gain -8 to -16. (4) Every "
        "location in the list gets an entry. (5) For each music mood pick the genre that best matches the music "
        "direction and that mood, and choose instruments per role so the direction's instruments are heard "
        "(e.g. 'acoustic guitar' → guitar, 'synth pads' → warm_pad); list them as {role, instrument} pairs. A "
        "scene already includes its own layers: only add beds or events it lacks. Calm and sad moods use energy 0.2-0.4.",
    ])  # fmt: skip


def clean(plan: SoundPlan, script: Script, lib: Any = None) -> tuple[SoundPlan, list[str]]:
    """Drop anything not in the catalog and clamp levels, so a model slip can't make a harsh or broken sound."""
    from assetforge.audio.ambience.beds import BEDS
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.ambience.events import EVENTS
    from assetforge.audio.music import composer, instruments

    notes: list[str] = []
    scene_names = set(scenes(lib))
    genres = {g["name"] for g in composer.catalog()}
    instrument_names = {i["name"] for i in instruments.catalog()}
    roles = {"melody", "harmony", "bass", "drums", "arp", "counter"}
    noisy = {"rain", "wind", "room_tone", "traffic", "crowd_murmur", "blizzard", "desert_wind", "electrical_buzz"}
    locations = []
    for loc in plan.locations:
        if loc.scene and loc.scene not in scene_names:
            notes.append(f"{loc.location}: unknown scene {loc.scene!r} dropped")
            loc = loc.model_copy(update={"scene": None})
        in_scene: set[str] = set()
        if loc.scene:  # layers the scene already has would play twice (two clocks out of step)
            chosen = scenes(lib)[loc.scene]
            in_scene = {b.bed for b in chosen.beds} | {e.event for e in chosen.events}
            if dropped := [x for x in [*(b.bed for b in loc.beds), *(e.event for e in loc.events)] if x in in_scene]:
                notes.append(f"{loc.location}: {dropped} already in scene {loc.scene!r}")
        beds = []
        for b in [b for b in loc.beds if b.bed not in in_scene][:3]:
            if b.bed not in BEDS:
                notes.append(f"{loc.location}: unknown bed {b.bed!r} dropped")
                continue
            ceiling = -20.0 if b.bed == "room_tone" else -12.0 if b.bed in noisy else -6.0
            beds.append(b.model_copy(update={"level_db": max(-30.0, min(ceiling, b.level_db)),
                                             "inside": None if b.bed != "rain" else b.inside}))  # fmt: skip
        events = [e.model_copy(update={"rate": max(0.1, min(6.0, e.rate)), "gain_db": max(-24.0, min(-4.0, e.gain_db)),
                                       "distance": max(0.0, min(1.0, e.distance))})
                  for e in [e for e in loc.events if e.event not in in_scene][:4] if e.event in EVENTS]  # fmt: skip
        locations.append(loc.model_copy(update={"beds": beds, "events": events,
                                                "intensity": max(0.0, min(1.0, loc.intensity))}))  # fmt: skip
    known = {loc.location for loc in locations}
    locations += [LocationSound(location=loc.id) for loc in script.locations if loc.id not in known]
    music = []
    for cue in plan.music:
        if cue.genre not in genres:
            notes.append(f"music {cue.mood}: unknown genre {cue.genre!r} → ambient_drone")
            cue = cue.model_copy(update={"genre": "ambient_drone"})
        music.append(cue.model_copy(update={
            "instruments": [p for p in cue.instruments if p.role in roles and p.instrument in instrument_names],
            "energy": max(0.0, min(1.0, cue.energy)),
            "tempo": None if cue.tempo is None else max(50.0, min(180.0, cue.tempo))}))  # fmt: skip
    return plan.model_copy(update={"locations": locations, "music": music}), notes


def offline_builder(script: Script) -> Any:
    def build(_: Any) -> dict[str, Any]:
        return {"locations": [{"location": loc.id, "scene": "home_interior" if "house" in loc.id or "home" in loc.id
                               else None, "beds": [], "events": [], "intensity": 0.4} for loc in script.locations],
                "music": [{"mood": m, "genre": "lofi", "instruments": [{"role": "melody", "instrument": "guitar"},
                                                                        {"role": "harmony", "instrument": "warm_pad"}],
                           "energy": 0.3} for m in sorted({st.music for s in script.scenes for st in walk(s)})]}  # fmt: skip
    return build


def ambience_options(loc: LocationSound) -> dict[str, Any]:
    """`sound` options for an ambience request (explicit: no word-guessing on top)."""
    def params(b: BedPick) -> dict[str, Any]:
        if b.bed == "room_tone":  # the air of a room, never a hum or a rumble
            return {"params": {"hum": 0.0, "low_cut": 160.0}}
        return {"params": {"inside": b.inside}} if b.inside is not None else {}

    opts: dict[str, Any] = {"scene": loc.scene, "intensity": loc.intensity, "length": 60.0,
                            "beds": [{"bed": b.bed, "level_db": b.level_db, **params(b)} for b in loc.beds],
                            "events": [{"event": e.event, "rate": e.rate, "gain_db": e.gain_db, "distance": e.distance}
                                       for e in loc.events]}  # fmt: skip
    if loc.space:
        opts["space"] = loc.space
    return opts


def music_options(cue: MusicCue) -> dict[str, Any]:
    opts: dict[str, Any] = {"mood": cue.mood, "genre": cue.genre, "energy": cue.energy, "form": "loop"}
    if cue.instruments:
        opts["instruments"] = {p.role: p.instrument for p in cue.instruments}
    if cue.tempo:
        opts["tempo"] = cue.tempo
    return opts
