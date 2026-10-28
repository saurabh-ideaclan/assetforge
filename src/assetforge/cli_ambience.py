"""`assetforge ambience …`: seamless environment loops built from beds, events and scenes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.audio.ambience import engine
from assetforge.cli_audio import common_fields, read_json_file, render_engine_result
from assetforge.cli_common import JsonOpt, parse_pairs, session
from assetforge.core.models import Status
from assetforge.errors import InvalidInputError

ambience_app = typer.Typer(help="Environment and ambient loops: rain, forests, caves, cities, ships…", no_args_is_help=True)


@ambience_app.command("scenes")
def scenes_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List ambience scenes (built-in plus $ASSETFORGE_HOME/scenes/*.yaml)."""
    with session(ctx, json_) as s:
        items = list(engine.scenes(s.lib).values())

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Scene", "Space", "Layers", "Description"):
                table.add_column(column, overflow="fold")
            for sc in items:
                layers = [b.bed for b in sc.beds] + [e.event for e in sc.events]
                table.add_row(sc.name, sc.space, ", ".join(layers), escape(sc.description))
            c.print(table)
            c.print("[dim]Add your own: drop NAME.yaml (beds/events/space) into "
                    f"{s.lib.paths.home / 'scenes'}. `ambience layers` lists beds and events.[/dim]")  # fmt: skip

        s.out.emit({"count": len(items), "scenes": [engine.scene_dict(sc) for sc in items],
                    "custom_dir": str(s.lib.paths.home / "scenes")}, human)  # fmt: skip


@ambience_app.command("scene")
def scene_(ctx: typer.Context, name: Annotated[str, typer.Argument(help="Scene name.")], json_: JsonOpt = False) -> None:
    """Show one scene's beds and events."""
    with session(ctx, json_) as s:
        sc = engine.get_scene(s.lib, name)
        s.out.emit({"scene": engine.scene_dict(sc)}, lambda c: c.print(sc.model_dump(exclude={"source"})))


@ambience_app.command("layers")
def layers_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Beds (continuous textures) and events (one-shots) available to scenes and custom ambiences."""
    with session(ctx, json_) as s:
        data = engine.catalog()

        def human(c: Console) -> None:
            c.print("[bold]Beds[/bold]")
            for b in data["beds"]:
                params = ", ".join(f"{k}={v:g}" for k, v in b["params"].items())
                c.print(f"  {b['name']:<16} {escape(b['description'])} [dim]{params}[/dim]")
            c.print("[bold]Events[/bold]")
            for e in data["events"]:
                c.print(f"  {e['name']:<16} {escape(e['description'])}")

        s.out.emit(data, human)


@ambience_app.command("generate")
def generate(
    ctx: typer.Context,
    scene: Annotated[str | None, typer.Argument(help="Scene (see `ambience scenes`); optional with --layers-file.")] = None,
    length: Annotated[float | None, typer.Option(help="Loop length in seconds (default 60).")] = None,
    intensity: Annotated[float | None, typer.Option(help="0 calm and sparse … 1 dense and stormy (default 0.5).")] = None,
    time_of_day: Annotated[str | None, typer.Option("--time", help="day or night (swaps birds for insects/owls).")] = None,
    distance: Annotated[float | None, typer.Option(help="Hear the scene from further away, 0-1.")] = None,
    width: Annotated[float | None, typer.Option(help="Stereo width 0-2.")] = None,
    space: Annotated[str | None, typer.Option(help="Acoustic space override (see `sound spaces`).")] = None,
    layers_file: Annotated[Path | None, typer.Option("--layers-file", help="JSON/YAML with extra `beds` and `events`.")] = None,
    mute: Annotated[list[str] | None, typer.Option("--mute", help="Leave out a bed/event (repeatable).")] = None,
    level: Annotated[list[str] | None, typer.Option("--level", help="Adjust a layer: name=dB (repeatable).")] = None,
    stems: Annotated[bool, typer.Option("--stems", help="Also store each layer as its own loop.")] = False,
    intensity_level: Annotated[list[float] | None, typer.Option("--intensity-level", help="Also make a matched loop at this intensity (repeatable).")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request; flags override its fields.")] = None,
    name: Annotated[str | None, typer.Option(help="Asset name.")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project (its sound profile sets defaults).")] = None,
    variants: Annotated[int | None, typer.Option("--variants", "-n", help="Takes (1-16).")] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    format_: Annotated[str | None, typer.Option("--format", help="ogg (default), mp3 or wav. Prefer ogg for loops.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    key: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    collection: Annotated[str | None, typer.Option()] = None,
    new_take: Annotated[bool, typer.Option("--new-take")] = False,
    json_: JsonOpt = False,
) -> None:
    """Render a seamless stereo ambience loop (plus optional stems and intensity versions). No API calls."""
    with session(ctx, json_) as s:
        data: dict[str, Any] = read_json_file(file)
        if layers_file is not None:
            extra = engine.read_layers_file(layers_file)
            data["beds"] = [*data.get("beds", []), *extra.get("beds", [])]
            data["events"] = [*data.get("events", []), *extra.get("events", [])]
        flags = {"scene": scene, "length": length, "intensity": intensity, "time_of_day": time_of_day,
                 "distance": distance, "width": width, "space": space}  # fmt: skip
        data |= {k: v for k, v in flags.items() if v is not None}
        if mute:
            data["mute"] = list(mute)
        if level:
            try:
                data["levels"] = {k: float(v) for k, v in parse_pairs(level, "--level").items()}
            except ValueError as exc:
                raise InvalidInputError("--level values are decibels, e.g. rain=-6") from exc
        if stems:
            data["stems"] = True
        if intensity_level:
            data["intensities"] = list(intensity_level)
        data |= common_fields(name=name, project=project, variants=variants, seed=seed, format_=format_, tag=tag,
                              key=key, description=description, status=status, collection=collection,
                              new_take=new_take)  # fmt: skip
        try:
            req = engine.AmbienceRequest(**data)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid ambience request: {exc}") from exc
        res = engine.generate_ambience(s.lib, req)
        s.out.emit(res, lambda c: render_engine_result(c, res, "Rendered"))
