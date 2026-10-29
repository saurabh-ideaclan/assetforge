"""`assetforge creature …`: monster and animal voices layered from installed creature recordings."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.audio.creatures import engine, pack
from assetforge.cli_audio import common_fields, read_json_file, render_engine_result
from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import Status
from assetforge.errors import InvalidInputError

creature_app = typer.Typer(help="Creature voices (growl, roar, attack, hurt, death…) layered from CC0 recordings.",
                           no_args_is_help=True)  # fmt: skip


@creature_app.command("status")
def status_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Which creature recordings are installed, by role and family."""
    with session(ctx, json_) as s:
        st = pack.status(s.lib.paths.home)

        def human(c: Console) -> None:
            if not st["ready"]:
                c.print("[yellow]No creature recordings installed.[/yellow] Run `assetforge creature install --yes` (~12 MB, CC0).")
                return
            c.print(f"{st['clips']} clips from {len(st['packs'])} packs in {st['folder']}")
            c.print("Roles: " + ", ".join(f"{k} {v}" for k, v in st["roles"].items()))
            c.print("Families: " + ", ".join(f"{k} {v}" for k, v in st["families"].items()))

        s.out.emit(st, human)


@creature_app.command("install")
def install_(
    ctx: typer.Context,
    pack_name: Annotated[list[str] | None, typer.Option("--pack", help="Only these packs (repeatable; default all five).")] = None,
    source_dir: Annotated[Path | None, typer.Option("--from", help="Folder that already holds the pack downloads.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the download (required; commands never prompt).")] = False,
    json_: JsonOpt = False,
) -> None:
    """Download five CC0 creature packs from OpenGameArt (~12 MB, pinned SHA-256s) and catalog every clip."""
    with session(ctx, json_) as s:
        if not yes and source_dir is None:
            raise InvalidInputError("Downloading the creature packs (~12 MB from opengameart.org) needs --yes",
                                    hint="Re-run with --yes, or --from a folder holding the downloads")  # fmt: skip
        res = pack.install(s.lib.paths.home, list(pack_name) if pack_name else None, source_dir=source_dir)

        def human(c: Console) -> None:
            for name, info in res["installed"].items():
                c.print(f"[green]Installed[/green] {name}: {escape(info['title'])} by {info['author']} ({info['clips']} clips)")
            for name, why in res["skipped"].items():
                c.print(f"[yellow]Skipped[/yellow] {name}: {escape(why)}")

        s.out.emit(res, human)


@creature_app.command("add-folder")
def add_folder_(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(help="Folder of recordings named after what they are (growl, roar, breath…).")],
    name: Annotated[str, typer.Option(help="Pack name (lowercase-with-dashes).")],
    license_: Annotated[str, typer.Option("--license", help="License of these recordings.")],
    title: Annotated[str | None, typer.Option()] = None,
    json_: JsonOpt = False,
) -> None:
    """Add your own creature recordings (e.g. growls you recorded) as a pack."""
    with session(ctx, json_) as s:
        info = pack.add_folder(s.lib.paths.home, name, folder.expanduser(), license_=license_, title=title or "")
        s.out.emit({"pack": name, "info": info}, lambda c: c.print(f"Added {name}: {info['clips']} clips"))


@creature_app.command("list")
def list_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Creature presets, sounds and families."""
    with session(ctx, json_) as s:
        data = engine.catalog()

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Creature", "Size", "Aggression", "Family", "Description"):
                table.add_column(column)
            for k, v in data["creatures"].items():
                table.add_row(k, f"{v['size']:g}", f"{v['aggression']:g}", v["family"], escape(v["description"]))
            c.print(table)
            c.print("Sounds: " + ", ".join(data["sounds"]))

        s.out.emit(data, human)


@creature_app.command("generate")
def generate(
    ctx: typer.Context,
    creature: Annotated[str, typer.Argument(help="A preset (see `creature list`), e.g. wolf, troll, dragon.")] = "beast",
    sound: Annotated[str, typer.Option("--sound", "-s", help="growl, roar, attack, grunt, hurt, death, idle or breath.")] = "growl",
    size: Annotated[float | None, typer.Option(help="0 tiny … 1 colossal (default: the creature's).")] = None,
    aggression: Annotated[float | None, typer.Option(help="0 calm … 1 furious.")] = None,
    duration: Annotated[float | None, typer.Option(help="Approximate length of the main voice in seconds.")] = None,
    family: Annotated[str | None, typer.Option(help="beast, alien, insect, slime or critter.")] = None,
    space: Annotated[str | None, typer.Option(help="Acoustic space (see `sound spaces`).")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request; flags override its fields.")] = None,
    name: Annotated[str | None, typer.Option(help="Asset name.")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project (its sound profile sets defaults).")] = None,
    variants: Annotated[int | None, typer.Option("--variants", "-n", help="Takes (1-16), each a different blend.")] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    format_: Annotated[str | None, typer.Option("--format", help="ogg (default), mp3 or wav.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    key: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    collection: Annotated[str | None, typer.Option()] = None,
    new_take: Annotated[bool, typer.Option("--new-take")] = False,
    json_: JsonOpt = False,
) -> None:
    """Make a creature sound: layered, pitched for size, a different blend per take. No API calls."""
    with session(ctx, json_) as s:
        data: dict[str, Any] = read_json_file(file)
        flags = {"creature": creature, "sound": sound, "size": size, "aggression": aggression, "duration": duration,
                 "family": family, "space": space}  # fmt: skip
        data |= {k: v for k, v in flags.items() if v is not None}
        data |= common_fields(name=name, project=project, variants=variants, seed=seed, format_=format_, tag=tag,
                              key=key, description=description, status=status, collection=collection,
                              new_take=new_take)  # fmt: skip
        try:
            req = engine.CreatureRequest(**data)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid creature request: {exc}") from exc
        res = engine.generate_creature(s.lib, req)
        s.out.emit(res, lambda c: render_engine_result(c, res, "Made"))
