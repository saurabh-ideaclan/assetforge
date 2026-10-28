"""`assetforge music …`: compose game music and stingers, render scores and MIDI, edit and vary pieces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.audio.music import composer, instruments
from assetforge.audio.music.generate import MusicRequest, edit_music, generate_music, take_score, vary_music
from assetforge.cli_audio import common_fields, read_json_file, render_engine_result
from assetforge.cli_common import JsonOpt, parse_pairs, session
from assetforge.core.models import Status
from assetforge.errors import InvalidInputError

music_app = typer.Typer(help="Compose music and stingers, render scores/MIDI, edit and vary. No API calls.",
                        no_args_is_help=True)  # fmt: skip

Name = Annotated[str | None, typer.Option(help="Asset name.")]
Project = Annotated[str | None, typer.Option(help="Owning project (its sound profile fills in genre, key, tempo…).")]
Variants = Annotated[int | None, typer.Option("--variants", "-n", help="Takes (1-16); each is a different piece.")]
Seed = Annotated[int | None, typer.Option(help="Seed for reproducible output.")]
Format = Annotated[str | None, typer.Option("--format", help="ogg (default), mp3 or wav. Prefer ogg for loops.")]
Tags = Annotated[list[str] | None, typer.Option("--tag", "-t")]
Stems = Annotated[bool, typer.Option("--stems", help="Also store drums/bass/harmony/melody stems.")]
Backend = Annotated[str | None, typer.Option(help="auto (SoundFont if installed), synth or soundfont.")]


def _roles(values: list[str] | None) -> dict[str, str]:
    return {k: str(v) for k, v in parse_pairs(values or [], "--instrument").items()}


@music_app.command("genres")
def genres(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Genres the composer knows (with default instruments, tempo range and scales)."""
    with session(ctx, json_) as s:
        items = composer.catalog()

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Genre", "Tempo", "Instruments", "Description"):
                table.add_column(column, overflow="fold")
            for g in items:
                table.add_row(g["name"], f"{g['tempo'][0]}-{g['tempo'][1]}", ", ".join(f"{r}={i}" for r, i in g["instruments"].items()),
                              escape(g["description"]))  # fmt: skip
            c.print(table)
            c.print(f"[dim]Stingers: {', '.join(composer.STINGERS)}[/dim]")

        s.out.emit({"count": len(items), "genres": items, "stingers": list(composer.STINGERS),
                    "forms": ["loop", "linear", "stinger"]}, human)  # fmt: skip


@music_app.command("instruments")
def instruments_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Synth instruments and drum kits (each maps to a General MIDI program for SoundFonts and MIDI)."""
    with session(ctx, json_) as s:
        items = instruments.catalog()

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Instrument", "Family", "Description"):
                table.add_column(column, overflow="fold")
            for i in items:
                table.add_row(str(i["name"]), str(i["family"]), escape(str(i["description"])))
            c.print(table)

        s.out.emit({"count": len(items), "instruments": items}, human)


@music_app.command("compose")
def compose(
    ctx: typer.Context,
    genre: Annotated[str | None, typer.Option(help="Genre (see `music genres`).")] = None,
    mood: Annotated[str | None, typer.Option(help="heroic, tense, calm, sad, playful, dark…")] = None,
    tempo: Annotated[float | None, typer.Option(help="BPM.")] = None,
    key: Annotated[str | None, typer.Option(help="e.g. 'D minor', 'F# dorian'.")] = None,
    meter: Annotated[str | None, typer.Option(help="4/4, 3/4, 6/8…")] = None,
    bars: Annotated[int | None, typer.Option(help="Length in bars.")] = None,
    seconds: Annotated[float | None, typer.Option(help="Target length (rounded to whole phrases).")] = None,
    form: Annotated[str, typer.Option(help="loop (seamless), linear (intro → outro) or stinger.")] = "loop",
    stinger: Annotated[str | None, typer.Option(help=f"Short cue: {', '.join(composer.STINGERS)}.")] = None,
    energy: Annotated[float | None, typer.Option(help="0 sparse … 1 full and busy (default 0.5).")] = None,
    instrument: Annotated[list[str] | None, typer.Option("--instrument", "-i", help="role=instrument (melody, harmony, bass, drums, arp, counter).")] = None,
    progression: Annotated[str | None, typer.Option(help="Roman numerals, e.g. 'i VI III VII'.")] = None,
    swing: Annotated[float | None, typer.Option(help="0 straight … 1 full swing.")] = None,
    space: Annotated[str | None, typer.Option(help="Acoustic space (see `sound spaces`).")] = None,
    intensity_layer: Annotated[list[float] | None, typer.Option("--intensity-layer", help="Also render a matched version at this energy (repeatable).")] = None,
    stems: Stems = False,
    backend: Backend = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request; flags override its fields.")] = None,
    name: Name = None, project: Project = None, variants: Variants = None, seed: Seed = None, format_: Format = None,
    tag: Tags = None,
    key_: Annotated[str | None, typer.Option("--asset-key", help="Logical key, e.g. music/level1.")] = None,
    description: Annotated[str | None, typer.Option()] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    collection: Annotated[str | None, typer.Option()] = None,
    new_take: Annotated[bool, typer.Option("--new-take")] = False,
    json_: JsonOpt = False,
) -> None:
    """Compose and render original music: loops, linear cues or stingers, with optional stems and matched
    intensity versions. The score is saved too (as JSON and MIDI) so it can be edited later."""
    with session(ctx, json_) as s:
        data: dict[str, Any] = read_json_file(file)
        flags = {"genre": genre, "mood": mood, "tempo": tempo, "key": key, "meter": meter, "bars": bars,
                 "seconds": seconds, "stinger": stinger, "energy": energy, "progression": progression, "swing": swing,
                 "space": space, "backend": backend}  # fmt: skip
        data |= {k: v for k, v in flags.items() if v is not None}
        if form != "loop" or "form" not in data:
            data["form"] = form
        if instrument:
            data["instruments"] = {**data.get("instruments", {}), **_roles(instrument)}
        if intensity_layer:
            data["intensity_layers"] = list(intensity_layer)
        if stems:
            data["stems"] = True
        data |= common_fields(name=name, project=project, variants=variants, seed=seed, format_=format_, tag=tag,
                              key=key_, description=description, status=status, collection=collection,
                              new_take=new_take)  # fmt: skip
        try:
            req = MusicRequest(**data)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid music request: {exc}") from exc
        res = generate_music(s.lib, req)
        s.out.emit(res, lambda c: render_engine_result(c, res, "Composed"))


@music_app.command("render")
def render_(
    ctx: typer.Context,
    score: Annotated[Path | None, typer.Option("--score", help="Score JSON (tempo, meter, key, tracks of notes…).")] = None,
    midi: Annotated[Path | None, typer.Option("--midi", help="A Standard MIDI File (.mid).")] = None,
    loop: Annotated[bool | None, typer.Option("--loop/--no-loop", help="Render as a seamless loop.")] = None,
    instrument: Annotated[list[str] | None, typer.Option("--instrument", "-i", help="Override: track-or-role=instrument.")] = None,
    space: Annotated[str | None, typer.Option()] = None,
    stems: Stems = False,
    backend: Backend = None,
    name: Name = None, project: Project = None, seed: Seed = None, format_: Format = None, tag: Tags = None,
    status: Annotated[Status | None, typer.Option()] = None,
    json_: JsonOpt = False,
) -> None:
    """Render a score written by you/an agent, or any MIDI file, with the synth instruments (or SoundFont)."""
    with session(ctx, json_) as s:
        if (score is None) == (midi is None):
            raise InvalidInputError("Give --score FILE or --midi FILE")
        data: dict[str, Any] = {"stems": stems, "space": space, "loop": loop}
        if score is not None:
            data["score"] = read_json_file(score)
        else:
            data["midi"] = str(midi)
        if instrument:
            from assetforge.audio.music.generate import _score_from_request, edit_score

            base = _score_from_request(MusicRequest(**{k: v for k, v in data.items() if k in ("score", "midi", "loop")}))
            assert base is not None
            data = {"stems": stems, "score": edit_score(base, instruments=_roles(instrument), space=space).model_dump(mode="json")}
            data["name"] = name or (midi.stem if midi else "score")
        data |= common_fields(name=name, project=project, variants=None, seed=seed, format_=format_, tag=tag, key=None,
                              description=None, status=status, collection=None, new_take=False)  # fmt: skip
        if backend:
            data["backend"] = backend
        res = generate_music(s.lib, MusicRequest(**{k: v for k, v in data.items() if v is not None}))
        s.out.emit(res, lambda c: render_engine_result(c, res, "Rendered"))


@music_app.command("score")
def score_(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="A music asset.")],
    take: Annotated[int, typer.Option(help="Which take (v1, v2…).")] = 1,
    midi: Annotated[Path | None, typer.Option("--midi", help="Also write the score as a MIDI file here.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Print a piece's score (JSON you can edit and `music render --score`), optionally as MIDI."""
    with session(ctx, json_) as s:
        score, _, asset_id = take_score(s.lib, ref, take)
        data = {"asset_id": asset_id, "take": take, "summary": score.summary(),
                "score": score.model_dump(mode="json", exclude_defaults=True)}  # fmt: skip
        if midi is not None:
            from assetforge.audio.music.midi import write_midi

            data["midi"] = str(write_midi(score, midi.expanduser()))
        s.out.emit(data, lambda c: c.print(json.dumps(data["score"], indent=2)))


@music_app.command("edit")
def edit(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="A music asset.")],
    take: Annotated[int, typer.Option(help="Which take to start from.")] = 1,
    tempo: Annotated[float | None, typer.Option()] = None,
    key: Annotated[str | None, typer.Option(help="New key (transposes melodic tracks).")] = None,
    transpose: Annotated[int, typer.Option(help="Semitones.")] = 0,
    instrument: Annotated[list[str] | None, typer.Option("--instrument", "-i", help="track-or-role=instrument.")] = None,
    mute: Annotated[list[str] | None, typer.Option("--mute", help="Drop a track or role (repeatable).")] = None,
    loop: Annotated[bool | None, typer.Option("--loop/--no-loop")] = None,
    space: Annotated[str | None, typer.Option()] = None,
    stems: Stems = False,
    name: Name = None, project: Project = None, seed: Seed = None, format_: Format = None,
    json_: JsonOpt = False,
) -> None:
    """Re-render a piece with changes (tempo, key, instruments, muted parts) as a new linked asset."""
    with session(ctx, json_) as s:
        req = MusicRequest(**{k: v for k, v in {"name": name, "project": project, "seed": seed, "format": format_,
                                                "stems": stems or None}.items() if v is not None})  # fmt: skip
        res = edit_music(s.lib, ref, req, take=take, tempo=tempo, key=key, transpose=transpose,
                         instruments=_roles(instrument), mute=mute, loop=loop, space=space)  # fmt: skip
        s.out.emit(res, lambda c: render_engine_result(c, res, "Edited into"))


@music_app.command("vary")
def vary(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="A composed music asset.")],
    keep: Annotated[str, typer.Option(help="nothing (new pieces as takes), chords (new melody) or melody (new arrangement).")] = "nothing",
    variants: Annotated[int, typer.Option("--variants", "-n")] = 1,
    seed: Seed = None,
    json_: JsonOpt = False,
) -> None:
    """More versions of a piece: brand-new takes, a new melody over the same chords, or a new arrangement."""
    if keep not in ("nothing", "chords", "melody"):
        raise typer.BadParameter("keep is nothing, chords or melody")
    with session(ctx, json_) as s:
        res = vary_music(s.lib, ref, keep=keep, variants=variants, seed=seed)  # type: ignore[arg-type]
        s.out.emit(res, lambda c: render_engine_result(c, res, "Varied"))
