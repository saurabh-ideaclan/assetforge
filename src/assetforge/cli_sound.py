"""`assetforge sound …`: per-game sound profiles (the audio counterpart of `art profile`)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from assetforge import sound
from assetforge.cli_common import JsonOpt, session
from assetforge.errors import InvalidInputError

sound_app = typer.Typer(help="How each game sounds: genre, mood, space, key, tempo, instruments.", no_args_is_help=True)
profile_app = typer.Typer(help="A game's sound direction, applied to every sound, ambience and track made for it.",
                          no_args_is_help=True)  # fmt: skip
sound_app.add_typer(profile_app, name="profile")


def _profile_dict(p: sound.SoundProfile) -> dict[str, Any]:
    return {"project": p.project, "sound": p.direction.model_dump(mode="json", exclude_defaults=True),
            "created_at": p.created_at, "updated_at": p.updated_at}  # fmt: skip


@sound_app.command("spaces")
def spaces(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Acoustic spaces a profile or request can name, and the effects each adds."""
    with session(ctx, json_) as s:
        s.out.emit({"spaces": sound.SPACES},
                   lambda c: [c.print(f"[bold]{k}[/bold]  {', '.join(e['name'] for e in v) or 'no effects'}")
                              for k, v in sound.SPACES.items()])  # fmt: skip


@profile_app.command("set")
def profile_set(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Argument(help="Game/project (or `game:` in the file).")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="YAML sound profile (or an art profile with a `sound:` section).")] = None,
    style: Annotated[str | None, typer.Option(help="Overall sonic style (also the default SFX preset if one matches).")] = None,
    genre: Annotated[str | None, typer.Option(help="Music genre (see `music genres`).")] = None,
    mood: Annotated[str | None, typer.Option()] = None,
    era: Annotated[str | None, typer.Option()] = None,
    space: Annotated[str | None, typer.Option(help=f"Acoustic space: {', '.join(sound.SPACES)}.")] = None,
    key: Annotated[str | None, typer.Option(help="Musical key, e.g. 'D minor'.")] = None,
    tempo: Annotated[list[float] | None, typer.Option("--tempo", help="BPM, or give twice for a range.")] = None,
    instrument: Annotated[list[str] | None, typer.Option("--instrument", help="Preferred instrument (repeatable).")] = None,
    sfx_preset: Annotated[str | None, typer.Option("--sfx-preset", help="Default SFX preset.")] = None,
    avoid: Annotated[list[str] | None, typer.Option("--avoid", help="Thing to keep out (repeatable).")] = None,
    replace: Annotated[bool, typer.Option("--replace", help="Replace the whole profile instead of merging fields.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Create or update a game's sound profile. Fields merge into the existing profile unless --replace."""
    with session(ctx, json_) as s:
        data: dict[str, Any] = {}
        file_project = None
        if file is not None:
            if not file.is_file():
                raise InvalidInputError(f"Profile file not found: {file}")
            file_project, direction = sound.parse_profile_yaml(file.read_text())
            if direction is None:
                raise InvalidInputError(f"{file} has no sound fields (add a `sound:` section)")
            data = direction.model_dump(exclude_defaults=True)
        project = project or file_project
        if not project:
            raise InvalidInputError("Name the project: `sound profile set PROJECT` or `game:` in the file")
        flags = {"style": style, "genre": genre, "mood": mood, "era": era, "space": space, "key": key,
                 "sfx_preset": sfx_preset}  # fmt: skip
        data |= {k: v for k, v in flags.items() if v is not None}
        if tempo:
            data["tempo"] = tempo
        if instrument:
            data["instruments"] = instrument
        if avoid:
            data["avoid"] = [*data.get("avoid", []), *avoid]
        try:
            direction = sound.SoundDirection(**data)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid sound profile: {exc}") from exc
        profile = sound.set_profile(s.lib, project, direction, replace=replace)
        s.out.emit({"profile": _profile_dict(profile)}, lambda c: c.print(sound.profile_yaml(profile)))


@profile_app.command("show")
def profile_show(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Game/project.")],
    yaml_: Annotated[bool, typer.Option("--yaml", help="Print as YAML (to edit and `set --file`).")] = False,
    json_: JsonOpt = False,
) -> None:
    """Show a game's sound profile."""
    with session(ctx, json_) as s:
        profile = sound.get_profile(s.lib, project)
        if yaml_ and not json_:
            typer.echo(sound.profile_yaml(profile), nl=False)
            return
        s.out.emit({"profile": _profile_dict(profile), "yaml": sound.profile_yaml(profile)},
                   lambda c: c.print(sound.profile_yaml(profile)))  # fmt: skip


@profile_app.command("list")
def profile_list(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List games that have a sound profile."""
    with session(ctx, json_) as s:
        items = sound.list_profiles(s.lib)

        def human(c: Console) -> None:
            if not items:
                c.print("[dim]No sound profiles yet.[/dim]")
            for p in items:
                d = p.direction
                c.print(f"[bold]{p.project}[/bold]  {d.genre or d.style or '-'}  [dim]{d.mood or ''} {d.space or ''}[/dim]")

        s.out.emit({"count": len(items), "profiles": [_profile_dict(p) for p in items]}, human)


@profile_app.command("delete")
def profile_delete(ctx: typer.Context, project: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Remove a game's sound profile (assets are not touched)."""
    with session(ctx, json_) as s:
        sound.delete_profile(s.lib, project)
        s.out.emit({"deleted": project}, lambda c: c.print(f"Deleted the sound profile of {project}"))
