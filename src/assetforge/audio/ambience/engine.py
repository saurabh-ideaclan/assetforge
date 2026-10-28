"""Soundscapes: beds + scheduled events + an acoustic space, rendered as seamless stereo loops.

A **scene** (`ambience/scenes/*.yaml`, or `$ASSETFORGE_HOME/scenes/*.yaml` to add or override) lists
beds and events; a request picks a scene and/or adds its own layers, then sets length, intensity,
time of day, distance and width. The resolved layer list is what gets stored, so an asset re-renders
the same even if a scene file changes later.

- Beds render past the loop length and crossfade into the start (`looping.crossfade_loop`).
- Events are scattered by a seeded Poisson process (rate per minute × intensity) and their tails
  fold back onto the start (`looping.fold_loop`).
- The space's reverb runs circularly (over two copies of the loop), so its tail wraps too.
- `stems` stores every layer as its own variant; `intensities` stores matched calm/intense loops.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from assetforge.audio import dsp, pipeline
from assetforge.audio.ambience.beds import BEDS
from assetforge.audio.ambience.events import EVENTS
from assetforge.audio.effects import resolve_effects
from assetforge.audio.looping import circular_effects, crossfade_loop, fold_loop
from assetforge.errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

ENGINE = "assetforge-ambience"
ENGINE_VERSION = 1
DAY_ONLY = {"bird_chirp", "songbird", "crow"}
NIGHT_ONLY = {"owl", "cricket_chirp", "distant_howl", "bat_squeak", "insects_night"}
MAX_EVENT_SECONDS = 12


class BedLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bed: str
    level_db: float = Field(0.0, ge=-48, le=12)
    params: dict[str, float] = Field(default_factory=dict)

    @field_validator("bed")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in BEDS:
            raise ValueError(f"unknown bed {value!r}; available: {', '.join(sorted(BEDS))}")
        return value


class EventLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    rate: float = Field(4.0, gt=0, le=600, description="Average times per minute at intensity 0.5")
    gain_db: float = Field(0.0, ge=-48, le=12)
    pan: float = Field(0.8, ge=0, le=1, description="How far left/right events may land")
    distance: float = Field(0.3, ge=0, le=1, description="0 close, 1 far (duller and quieter)")
    pitch_jitter: float = Field(1.0, ge=0, le=12, description="Random pitch change in semitones")

    @field_validator("event")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in EVENTS:
            raise ValueError(f"unknown event {value!r}; available: {', '.join(sorted(EVENTS))}")
        return value


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    beds: list[BedLayer] = Field(default_factory=list)
    events: list[EventLayer] = Field(default_factory=list)
    space: str = "outdoor"
    width: float = 1.0
    source: str = "built-in"


class AmbienceSpec(BaseModel):
    """The fully resolved soundscape; stored with the asset and fingerprinted."""

    model_config = ConfigDict(extra="forbid")

    scene: str | None = None
    beds: list[BedLayer] = Field(default_factory=list)
    events: list[EventLayer] = Field(default_factory=list)
    length: float = Field(60.0, ge=4, le=600)
    intensity: float = Field(0.5, ge=0, le=1)
    space: str = "dry"
    width: float = Field(1.0, ge=0, le=2)
    distance: float = Field(0.0, ge=0, le=1)
    stems: bool = False
    intensities: list[float] = Field(default_factory=list, max_length=4)


# ---------------------------------------------------------------------- scenes


def _load_scene(text: str, source: str, fallback: str) -> Scene:
    try:
        data = yaml.safe_load(text) or {}
        data.setdefault("name", fallback)
        return Scene(**data, source=source)
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        raise InvalidInputError(f"Bad ambience scene {source}: {exc}") from exc


@lru_cache(maxsize=1)
def builtin_scenes() -> dict[str, Scene]:
    found = {}
    for entry in sorted(resources.files("assetforge.audio.ambience.scenes").iterdir(), key=lambda e: e.name):
        if entry.name.endswith((".yaml", ".yml")):
            scene = _load_scene(entry.read_text(), "built-in", entry.name.rsplit(".", 1)[0])
            found[scene.name] = scene
    return found


def scenes(lib: Library | None = None) -> dict[str, Scene]:
    result = dict(builtin_scenes())
    if lib is not None and (folder := lib.paths.home / "scenes").is_dir():
        for path in sorted(folder.glob("*.y*ml")):
            scene = _load_scene(path.read_text(), str(path), path.stem)
            result[scene.name] = scene
    return result


def get_scene(lib: Library | None, name: str) -> Scene:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    found = scenes(lib).get(key)
    if found is None:
        raise NotFoundError(f"No ambience scene {name!r}", available=sorted(scenes(lib)))
    return found


# ---------------------------------------------------------------------- rendering


def _event_instance(layer: EventLayer, rng: np.random.Generator, sr: int) -> np.ndarray:
    x = EVENTS[layer.event].render(rng, sr)[: MAX_EVENT_SECONDS * sr]
    if layer.pitch_jitter:
        ratio = 2 ** (rng.uniform(-layer.pitch_jitter, layer.pitch_jitter) / 12)
        x = np.interp(np.arange(0, len(x) - 1, ratio), np.arange(len(x)), x)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    x = x / peak if peak > 0 else x
    if layer.distance > 0:
        x = dsp.filt(x, "lowpass", 800 + 15000 * (1 - layer.distance) ** 2, sr) * dsp.db(-14 * layer.distance)
    return x * rng.uniform(0.6, 1.0)


def render_layers(spec: AmbienceSpec, intensity: float, seed: int, sr: int) -> dict[str, np.ndarray]:
    """Each bed/event layer as a seamless (L, 2) loop, before the space and width."""
    length = int(spec.length * sr)
    fade = int(min(2.0, spec.length / 4) * sr)
    layers: dict[str, np.ndarray] = {}
    for i, layer in enumerate(spec.beds):
        rng = np.random.default_rng([seed, 1, i])
        bed = BEDS[layer.bed]
        params = {**bed.params, **layer.params}
        # Beds normalize their own level; intensity sets it (±6 dB around 0.5) on top of their character.
        x = bed.render(length + fade, sr, rng, intensity, params, length) * dsp.db(layer.level_db + 12 * (intensity - 0.5))
        layers[_unique(layers, layer.bed)] = crossfade_loop(x, length, fade)
    factor = 0.3 + 1.4 * intensity
    for i, layer in enumerate(spec.events):
        rng = np.random.default_rng([seed, 2, i])
        buffer = np.zeros((length + MAX_EVENT_SECONDS * sr, 2))
        count = rng.poisson(layer.rate / 60 * spec.length * factor)
        edge = int(0.12 * sr)  # no onsets right at the seam (they read as a click when the loop restarts)
        for start in np.sort(rng.uniform(edge, length - edge, count)).astype(int):
            x = _event_instance(layer, rng, sr) * dsp.db(layer.gain_db)
            dsp.add_at(buffer, dsp.pan(x, rng.uniform(-layer.pan, layer.pan)), int(start))
        layers[_unique(layers, layer.event)] = fold_loop(buffer, length)
    return layers


def _unique(existing: dict[str, Any], name: str) -> str:
    label, k = name, 2
    while label in existing:
        label, k = f"{name}_{k}", k + 1
    return label


def _finish_bus(x: np.ndarray, spec: AmbienceSpec, effects: list[dict[str, Any]], sr: int) -> np.ndarray:
    if spec.distance > 0:
        x = circular_effects(x, resolve_effects([{"name": "lowpass", "params": {"cutoff_hz": 800 + 12000 * (1 - spec.distance) ** 2}}]), sr)
    return dsp.width(circular_effects(x, effects, sr), spec.width)


def intensity_label(value: float, values: list[float]) -> str:
    ordered = sorted(values)
    names = {2: ("calm", "intense"), 3: ("calm", "mid", "intense")}.get(len(ordered))
    return names[ordered.index(value)] if names else f"intensity_{round(value * 100)}"


def render(spec_data: dict[str, Any], index: int, seed: int, sr: int) -> pipeline.Take:
    from assetforge.sound import SPACES

    spec = AmbienceSpec(**spec_data)
    space = resolve_effects(SPACES.get(spec.space, []))
    layers = render_layers(spec, spec.intensity, seed, sr)
    extras: dict[str, np.ndarray] = {}
    if spec.stems:
        extras |= {f"stem_{name}": _finish_bus(x, spec, space, sr) for name, x in layers.items()}
    mix = _finish_bus(sum(layers.values()), spec, space, sr)
    for value in spec.intensities:
        if abs(value - spec.intensity) < 1e-9:
            continue
        extras[intensity_label(value, spec.intensities)] = _finish_bus(sum(render_layers(spec, value, seed, sr).values()),
                                                                       spec, space, sr)  # fmt: skip
    return pipeline.Take(mix, extras, info={"layers": list(layers)})


ENGINE_REF = pipeline.register_engine(ENGINE, ENGINE_VERSION, render, job_kind="audio_ambience")


# ---------------------------------------------------------------------- requests


class AmbienceRequest(pipeline.Common):
    scene: str | None = Field(None, description="A scene (see `ambience scenes`); optional with your own layers")
    length: float = Field(60.0, ge=4, le=600, description="Loop length in seconds")
    intensity: float = Field(0.5, ge=0, le=1, description="0 sparse and calm … 1 dense and stormy")
    time_of_day: Literal["day", "night"] | None = None
    distance: float = Field(0.0, ge=0, le=1, description="Hear the whole scene from further away")
    width: float | None = Field(None, ge=0, le=2)
    space: str | None = Field(None, description="Override the scene's acoustic space")
    beds: list[BedLayer] = Field(default_factory=list, description="Extra beds")
    events: list[EventLayer] = Field(default_factory=list, description="Extra events")
    mute: list[str] = Field(default_factory=list, description="Bed/event names to leave out")
    levels: dict[str, float] = Field(default_factory=dict, description="dB offsets per bed/event name")
    stems: bool = Field(False, description="Store each layer as its own variant")
    intensities: list[float] = Field(default_factory=list, max_length=4, description="Extra matched loops, e.g. [0.2, 1]")


def resolve_spec(lib: Library, req: AmbienceRequest, direction_space: str | None = None) -> tuple[AmbienceSpec, Scene | None]:
    scene = get_scene(lib, req.scene) if req.scene else None
    beds = [b.model_copy() for b in (scene.beds if scene else [])] + list(req.beds)
    events = [e.model_copy() for e in (scene.events if scene else [])] + list(req.events)
    outdoor = bool(scene and "outdoor" in scene.tags)
    if req.time_of_day == "night":
        events = [e for e in events if e.event not in DAY_ONLY]
        if outdoor and not any(b.bed == "insects_night" for b in beds):
            beds.append(BedLayer(bed="insects_night", level_db=-8))
    elif req.time_of_day == "day":
        events = [e for e in events if e.event not in NIGHT_ONLY]
        beds = [b for b in beds if b.bed not in NIGHT_ONLY]
    muted = {m.strip().lower() for m in req.mute}
    beds = [b for b in beds if b.bed not in muted]
    events = [e for e in events if e.event not in muted]
    for name, offset in req.levels.items():
        for b in beds:
            if b.bed == name:
                b.level_db = float(np.clip(b.level_db + offset, -48, 12))
        for e in events:
            if e.event == name:
                e.gain_db = float(np.clip(e.gain_db + offset, -48, 12))
    if not beds and not events:
        raise InvalidInputError("This ambience has no layers: pick a scene or add beds/events",
                                hint="See `assetforge ambience scenes`")  # fmt: skip
    from assetforge.sound import SPACES

    space = req.space or (scene.space if scene else None) or direction_space or "dry"
    if space not in SPACES:
        raise InvalidInputError(f"Unknown space {space!r}", available=sorted(SPACES))
    spec = AmbienceSpec(scene=scene.name if scene else None, beds=beds, events=events, length=req.length,
                        intensity=req.intensity, space=space, width=req.width if req.width is not None else
                        (scene.width if scene else 1.0), distance=req.distance, stems=req.stems,
                        intensities=sorted(set(req.intensities)))  # fmt: skip
    return spec, scene


def generate_ambience(lib: Library, req: AmbienceRequest) -> dict[str, Any]:
    from assetforge import sound
    from assetforge.core.models import AssetType

    direction, _ = sound.resolve(lib, req.project, use_profile=req.use_profile)
    spec, scene = resolve_spec(lib, req, direction.space)
    name = req.name or "_".join(x for x in (scene.name if scene else "ambience", req.time_of_day) if x)
    layer_names = [b.bed for b in spec.beds] + [e.event for e in spec.events]
    tags = [*(scene.tags if scene else []), *layer_names, "ambience", "loop", *req.tags]
    if req.time_of_day:
        tags.append(req.time_of_day)
    out = pipeline.Output(
        engine=ENGINE_REF, spec=spec.model_dump(mode="json"), kind="ambience", asset_type=AssetType.AMBIENCE,
        name=name, description=req.description or (scene.description if scene else "Ambience: " + ", ".join(layer_names)),
        target_lufs=sound.loudness_target(direction, "ambience"), loop=True,
        sample_rate=req.sample_rate or direction.sample_rate or 44100, format=req.format or direction.format or "ogg",
        tags=tags, audio_meta={"length_s": spec.length, "intensity": spec.intensity, "scene": spec.scene,
                               "layers": layer_names, "loop_start": 0.0, "loop_end": spec.length},
        **req.catalog(),
    )  # fmt: skip
    return pipeline.produce(lib, out, variants=req.variants, seed=req.seed, new_take=req.new_take)


def catalog() -> dict[str, Any]:
    """Beds and events with their parameters, for agents and the app."""
    return {"beds": [{"name": b.name, "description": b.description, "params": b.params, "tags": list(b.tags)}
                     for b in BEDS.values()],
            "events": [{"name": e.name, "description": e.description, "tags": list(e.tags)} for e in EVENTS.values()]}  # fmt: skip


def scene_dict(scene: Scene) -> dict[str, Any]:
    return scene.model_dump(mode="json", exclude_defaults=False) | {"layers": [b.bed for b in scene.beds] +
                                                                   [e.event for e in scene.events]}  # fmt: skip


def read_layers_file(path: str | Path) -> dict[str, Any]:
    import json

    file = Path(path).expanduser()
    if not file.is_file():
        raise InvalidInputError(f"Layers file not found: {path}")
    text = file.read_text()
    data = json.loads(text) if file.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise InvalidInputError("A layers file is a mapping with `beds` and/or `events`")
    return data
