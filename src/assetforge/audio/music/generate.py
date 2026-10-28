"""Music requests: compose from a brief, render an agent-written score or a MIDI file, edit and vary.

The project's sound profile fills what the request leaves out (genre, mood, key, tempo range,
instruments, space). Each take stores its score (as JSON in the take's info and as a `.mid` variant),
so any piece can be edited and re-rendered later.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from assetforge.audio import pipeline
from assetforge.audio.music import composer, render
from assetforge.audio.music.score import Score
from assetforge.errors import InvalidInputError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

Backend = Literal["auto", "synth", "soundfont"]


class MusicRequest(pipeline.Common):
    genre: str | None = Field(None, description="Music genre (see `music genres`); default from mood or profile")
    mood: str | None = None
    tempo: float | None = Field(None, ge=40, le=240)
    key: str | None = None
    meter: str | None = None
    bars: int | None = Field(None, ge=1, le=256)
    seconds: float | None = Field(None, gt=0, le=600)
    form: composer.Form = "loop"
    stinger: Literal["victory", "defeat", "level_up", "game_over", "discovery", "danger", "collect"] | None = None
    energy: float = Field(0.5, ge=0, le=1)
    instruments: dict[str, str] = Field(default_factory=dict, description="role → instrument")
    progression: str | None = None
    swing: float | None = Field(None, ge=0, le=1)
    space: str | None = None
    score: dict[str, Any] | None = Field(None, description="Render this Score instead of composing")
    midi: str | None = Field(None, description="Render this MIDI file instead of composing")
    loop: bool | None = Field(None, description="For score/MIDI: render as a seamless loop")
    stems: bool = Field(False, description="Also store drums/bass/harmony/melody stems")
    intensity_layers: list[float] = Field(default_factory=list, max_length=4,
                                          description="Matched versions at these energies (same tune, more/less going on)")  # fmt: skip
    backend: Backend = Field("auto", description="auto: SoundFont when installed, else synth")


def roles_from_list(names: list[str]) -> dict[str, str]:
    """A profile's instrument list → roles (bass-family to bass, kits to drums, pads/strings to harmony)."""
    from assetforge.audio.music.drums import KITS
    from assetforge.audio.music.instruments import INSTRUMENTS, resolve_instrument

    roles: dict[str, str] = {}
    for raw in names:
        try:
            name = resolve_instrument(raw)
        except InvalidInputError:
            continue
        family = "drums" if name in KITS else INSTRUMENTS[name].family
        role = {"drums": "drums", "bass": "bass", "strings": "harmony", "keys": "harmony", "voice": "harmony"}.get(family)
        if family == "synth" and "pad" in name:
            role = "harmony"
        role = role or ("melody" if "melody" not in roles else "counter" if "counter" not in roles else "harmony")
        roles.setdefault(role, name)
    return roles


def resolve_backend(lib: Library, backend: Backend) -> dict[str, Any]:
    """`auto` becomes what this machine can do now, so the stored spec re-renders the same way."""
    if backend == "synth":
        return {"backend": "synth"}
    from assetforge.audio.music import soundfont

    state = soundfont.status(lib.paths.home)
    if backend == "soundfont" and not state["ready"]:
        raise InvalidInputError("The SoundFont backend is not ready: " + "; ".join(state["missing"]),
                                hint="See `assetforge audio soundfont status`")  # fmt: skip
    if not state["ready"]:
        return {"backend": "synth"}
    return {"backend": "soundfont", "soundfont": soundfont.spec_entry(lib.paths.home)}


def build_brief(lib: Library, req: MusicRequest) -> tuple[composer.Brief, dict[str, Any]]:
    from assetforge import sound

    direction, layers = sound.resolve(lib, req.project, use_profile=req.use_profile)
    genre = req.genre or direction.genre
    mood = req.mood or direction.mood
    if not genre and mood:
        genre = next((g for word, g in composer.MOOD_GENRE.items() if word in mood.lower()), None)
    if req.stinger and not genre:
        genre = "orchestral_adventure"
    tempo = req.tempo
    if tempo is None and direction.tempo:
        lo, hi = direction.tempo[0], direction.tempo[-1]
        tempo = round((lo + hi) / 2 + (req.energy - 0.5) * (hi - lo))
    instruments = {**roles_from_list(direction.instruments), **req.instruments}
    brief = composer.Brief(
        genre=genre or "puzzle_casual", mood=mood, tempo=tempo, key=req.key or direction.key, meter=req.meter,
        bars=req.bars, seconds=req.seconds, form="stinger" if req.stinger else req.form, stinger=req.stinger,
        energy=req.energy, instruments=instruments, progression=req.progression, swing=req.swing,
        space=req.space or (direction.space if direction.space not in (None, "dry") else None),
    )  # fmt: skip
    return brief, {"profile_layers": layers, "direction": direction}


def _score_from_request(req: MusicRequest) -> Score | None:
    if req.score is not None and req.midi:
        raise InvalidInputError("Give a score or a MIDI file, not both")
    if req.score is not None:
        data = dict(req.score)
        if req.loop is not None:
            data["loop"] = req.loop
        try:
            return Score(**data)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid score: {exc}") from exc
    if req.midi:
        from assetforge.audio.music.midi import read_midi

        path = Path(req.midi).expanduser()
        if not path.is_file():
            raise InvalidInputError(f"MIDI file not found: {req.midi}")
        return read_midi(path, loop=bool(req.loop))
    return None


def generate_music(lib: Library, req: MusicRequest, *, source_id: str | None = None) -> dict[str, Any]:
    from assetforge import sound
    from assetforge.core.models import AssetType

    seed = req.seed if req.seed is not None else secrets.randbelow(2**31)
    score = _score_from_request(req)
    direction, _ = sound.resolve(lib, req.project, use_profile=req.use_profile)
    spec: dict[str, Any] = {"stems": req.stems, **resolve_backend(lib, req.backend)}
    if score is not None:
        if req.space and not score.space:
            score = score.model_copy(update={"space": req.space})
        spec["score"] = score.model_dump(mode="json", exclude_defaults=True)
        brief = None
        first = score
        name = req.name or (Path(req.midi).stem if req.midi else "score")
        description = req.description or f"Music: {len(score.tracks)} tracks at {score.tempo:g} bpm"
        tags = ["score"]
        kind = "music"
    else:
        brief, _ = build_brief(lib, req)
        spec["compose"] = brief.model_dump(mode="json", exclude_defaults=True)
        if req.intensity_layers:
            from assetforge.audio.ambience.engine import intensity_label

            values = sorted(set(req.intensity_layers))
            spec["layers"] = {intensity_label(v, values): {"compose": {**spec["compose"], "energy": v}}
                              for v in values if abs(v - brief.energy) > 1e-9}  # fmt: skip
        first = composer.compose(brief, pipeline.take_seed(seed, 1))
        genre = composer.GENRES[brief.genre]
        if brief.form == "stinger":
            name = req.name or f"{brief.stinger or 'victory'}_stinger"
            kind = "stinger"
        else:
            name = req.name or "_".join(x for x in (brief.genre, brief.mood and brief.mood.split()[0], brief.form) if x)
            kind = "music"
        description = req.description or " ".join(x for x in (genre.description, f"({brief.mood})" if brief.mood else "") if x)
        tags = [brief.genre, *genre.tags, brief.form, *([brief.mood] if brief.mood else []), *([brief.stinger] if brief.stinger else [])]
    meta = render.audio_meta(first)
    tags += ["music", *(meta["instruments"]), (first.key or "").split(" ")[-1] if first.key else ""]
    out = pipeline.Output(
        engine=render.ENGINE_REF, spec=spec, kind=kind, asset_type=AssetType.MUSIC, name=name, description=description,
        target_lufs=sound.loudness_target(direction, kind), loop=first.loop,
        sample_rate=req.sample_rate or direction.sample_rate or 44100, format=req.format or direction.format or "ogg",
        tags=[t for t in [*tags, *req.tags] if t], audio_meta=meta, **(req.catalog() | {"genre": None}),
    )  # fmt: skip
    return pipeline.produce(lib, out, variants=req.variants, seed=seed, new_take=req.new_take, source_id=source_id,
                            seeded=req.seed is not None)  # fmt: skip


# ---------------------------------------------------------------------- reading back, editing, varying


def take_score(lib: Library, ref: str, take: int = 1) -> tuple[Score, dict[str, Any], str]:
    """The score of one take of a music asset (as rendered)."""
    asset = lib.get_asset(ref)
    gen = asset.metadata.get("generator") or {}
    if gen.get("engine") != render.ENGINE:
        raise InvalidInputError(f"{asset.id} is not music made by AssetForge")
    master = next((v for v in asset.variants if v.label == f"v{take}"), None)
    if master is None:
        raise InvalidInputError(f"{asset.id} has no take {take}")
    spec = gen["spec"]
    data = master.params.get("info", {}).get("score") or spec.get("score")
    if data is None:
        score, _ = render.score_for(spec, master.params["seed"])
    else:
        score = Score(**data)
    return score, master.params, asset.id


def edit_score(score: Score, *, tempo: float | None = None, transpose: int = 0, key: str | None = None,
               instruments: dict[str, str] | None = None, mute: list[str] | None = None,
               loop: bool | None = None, space: str | None = None) -> Score:  # fmt: skip
    from assetforge.audio.music.drums import KITS
    from assetforge.audio.music.theory import note_name, note_number, parse_key

    data = score.model_dump(mode="json")
    if key:
        target = parse_key(key)
        current = parse_key(score.key) if score.key else target
        shift = (target.tonic - current.tonic) % 12
        transpose += shift - 12 if shift > 6 else shift
        data["key"] = target.name if target.scale == current.scale else f"{target.name.split()[0]} {current.scale}"
    elif transpose and score.key:
        k = parse_key(score.key)
        data["key"] = f"{note_name(60 + (k.tonic + transpose) % 12)[:-1]} {k.scale}"
    muted = {m.lower() for m in (mute or [])}
    tracks = []
    for t in data["tracks"]:
        if t["name"].lower() in muted or t["role"] in muted:
            continue
        if instruments:
            t["instrument"] = instruments.get(t["name"]) or instruments.get(t["role"]) or t["instrument"]
        if transpose and t["instrument"] not in KITS:
            t["notes"] = [n | {"pitch": note_number(n["pitch"]) + transpose} for n in t["notes"]]
        tracks.append(t)
    if not tracks:
        raise InvalidInputError("Every track was muted")
    data["tracks"] = tracks
    if tempo:
        data["tempo"] = tempo
    if loop is not None:
        data["loop"] = loop
    if space:
        data["space"] = space
    return Score(**data)


def edit_music(lib: Library, ref: str, req: MusicRequest, *, take: int = 1, **changes: Any) -> dict[str, Any]:
    """Re-render one take's score with changes as a new asset linked `derived_from` the original."""
    score, _, asset_id = take_score(lib, ref, take)
    edited = edit_score(score, **changes)
    original = lib.get_asset(asset_id)
    data = req.model_dump(exclude_unset=True) | {"score": edited.model_dump(mode="json", exclude_defaults=True),
                                                  "name": req.name or f"{original.name}_edit",
                                                  "project": req.project or original.project}  # fmt: skip
    return generate_music(lib, MusicRequest(**data), source_id=asset_id)


def vary_music(lib: Library, ref: str, *, keep: Literal["nothing", "chords", "melody"] = "nothing",
               variants: int = 1, seed: int | None = None, take: int = 1) -> dict[str, Any]:  # fmt: skip
    """New takes. keep=nothing adds takes to the asset; chords/melody make a derived asset that keeps them."""
    if keep == "nothing":
        return pipeline.vary(lib, ref, variants=variants, seed=seed)
    asset = lib.get_asset(ref)
    gen = asset.metadata.get("generator") or {}
    if gen.get("engine") != render.ENGINE or "compose" not in gen.get("spec", {}):
        raise InvalidInputError("Only composed music can keep chords or melody; use `music edit` for scores")
    master = next(v for v in asset.variants if v.label == f"v{take}")
    seeds = master.params.get("info", {}).get("seeds") or {}
    pinned = {"chords": ("structure",), "melody": ("structure", "melody")}[keep]
    compose = dict(gen["spec"]["compose"])
    compose["fixed"] = {k: seeds[k] for k in pinned if k in seeds}
    req = MusicRequest(**{k: v for k, v in compose.items() if k in MusicRequest.model_fields and k not in ("fixed",)},
                       name=f"{asset.name}_{'reharm' if keep == 'chords' else 'arr'}", project=asset.project,
                       variants=variants, seed=seed, backend=gen["spec"].get("backend", "synth"),
                       stems=gen["spec"].get("stems", False))  # fmt: skip
    brief, _ = build_brief(lib, req)
    spec_compose = brief.model_dump(mode="json", exclude_defaults=True) | {"fixed": compose["fixed"]}
    return _generate_with_brief(lib, req, composer.Brief(**spec_compose), source_id=asset.id)


def _generate_with_brief(lib: Library, req: MusicRequest, brief: composer.Brief, *, source_id: str) -> dict[str, Any]:
    from assetforge import sound
    from assetforge.core.models import AssetType

    seed = req.seed if req.seed is not None else secrets.randbelow(2**31)
    direction, _ = sound.resolve(lib, req.project, use_profile=req.use_profile)
    spec = {"stems": req.stems, **resolve_backend(lib, req.backend),
            "compose": brief.model_dump(mode="json", exclude_defaults=True)}  # fmt: skip
    first = composer.compose(brief, pipeline.take_seed(seed, 1))
    meta = render.audio_meta(first)
    out = pipeline.Output(
        engine=render.ENGINE_REF, spec=spec, kind="music", asset_type=AssetType.MUSIC, name=req.name or "variation",
        description=composer.GENRES[brief.genre].description, target_lufs=sound.loudness_target(direction, "music"),
        loop=first.loop, format=req.format or direction.format or "ogg", tags=[brief.genre, "music", "variation"],
        audio_meta=meta, project=req.project, scope=req.scope, status=req.status,
    )  # fmt: skip
    return pipeline.produce(lib, out, variants=req.variants, seed=seed, source_id=source_id, seeded=req.seed is not None)
