"""`assetforge art …`: art-style presets and per-game art profiles (an LLM's art-direction toolkit)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge import art
from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import AssetType
from assetforge.errors import InvalidInputError

art_app = typer.Typer(help="Art-style presets and per-game art profiles.", no_args_is_help=True)
profile_app = typer.Typer(help="A game's visual identity, applied to every image generated for that project.",
                          no_args_is_help=True)  # fmt: skip
art_app.add_typer(profile_app, name="profile")


def collect_art(
    art_file: Path | None = None, *, style: str | None = None, description: str | None = None,
    mood: str | None = None, lighting: str | None = None, palette: list[str] | None = None,
    perspective: str | None = None, composition: str | None = None, detail: str | None = None,
    avoid: list[str] | None = None, genre: str | None = None, theme: str | None = None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any] | None:  # fmt: skip
    """Art-direction fields from a file plus flag overrides (None when nothing was given)."""
    data: dict[str, Any] = dict(base or {})
    if art_file is not None:
        data |= art.read_direction_file(art_file).model_dump(exclude_defaults=True)
    flags = {"style": style, "description": description, "mood": mood, "lighting": lighting, "perspective": perspective,
             "composition": composition, "detail": detail, "genre": genre, "theme": theme}  # fmt: skip
    data |= {k: v for k, v in flags.items() if v is not None}
    if palette:
        data["palette"] = palette
    if avoid:
        data["avoid"] = [*data.get("avoid", []), *avoid]
    return data or None


def _preset_row(p: art.Preset) -> dict[str, Any]:
    return p.model_dump(mode="json", exclude_defaults=True) | {"name": p.name, "source": p.source}


@art_app.command("presets")
def presets_(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List art-style presets (built-in plus $ASSETFORGE_HOME/presets/*.yaml). Any other style text works too."""
    with session(ctx, json_) as s:
        items = list(art.presets(s.lib).values())

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Preset", "Summary", "Best for", "Source"):
                table.add_column(column, overflow="fold")
            for p in items:
                table.add_row(p.name, escape(p.summary), ", ".join(p.best_for), "built-in" if p.source == "built-in"
                              else "library")  # fmt: skip
            c.print(table)
            c.print("[dim]Show one: assetforge art preset NAME. Add your own: drop a YAML file in "
                    f"{s.lib.paths.home / 'presets'}[/dim]")  # fmt: skip

        s.out.emit({"count": len(items), "presets": [_preset_row(p) for p in items],
                    "custom_dir": str(s.lib.paths.home / "presets")}, human)  # fmt: skip


@art_app.command("preset")
def preset_(ctx: typer.Context, name: Annotated[str, typer.Argument(help="Preset name.")], json_: JsonOpt = False) -> None:
    """Show one preset's full visual description."""
    with session(ctx, json_) as s:
        p = art.get_preset(s.lib, name)
        s.out.emit({"preset": _preset_row(p), "prompt_text": art.render(p.direction(), preset=p)},
                   lambda c: c.print(art.profile_yaml(art.ArtProfile(project=p.name, direction=p.direction(),
                                                                     created_at="", updated_at=""))))  # fmt: skip


@art_app.command("preview")
def preview(
    ctx: typer.Context,
    prompt: Annotated[str, typer.Option("--prompt", "-p", help="What to draw.")],
    type_: Annotated[AssetType, typer.Option("--type", help="Asset type.")] = AssetType.IMAGE,
    project: Annotated[str | None, typer.Option(help="Apply this project's art profile.")] = None,
    style: Annotated[str | None, typer.Option(help="Preset or style override.")] = None,
    art_file: Annotated[Path | None, typer.Option("--art-file", help="YAML/JSON art direction overrides.")] = None,
    provider: Annotated[str | None, typer.Option(help="Provider whose prompt to show (affects transparency).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Show the merged art direction and the exact prompt a request would send. Never calls an API."""
    from assetforge.image.generate import ImageRequest, generate_image

    with session(ctx, json_) as s:
        direction = collect_art(art_file)
        req = ImageRequest(prompt=prompt, type=type_, project=project, style=style, provider=provider or "auto",
                           art=direction, dry_run=True)  # fmt: skip
        result = generate_image(s.lib, req)

        def human(c: Console) -> None:
            c.print(f"[bold]Layers:[/bold] {', '.join(result.art_layers) or 'none'}")
            c.print(f"[bold]Prompt[/bold] ({result.provider}/{result.model}, transparency {result.transparency}):")
            c.print(escape(result.prompt))

        s.out.emit({"art": result.art, "layers": result.art_layers, "prompt": result.prompt,
                    "provider": result.provider, "model": result.model, "transparency": result.transparency,
                    "estimated_cost_usd": result.estimated_cost_usd}, human)  # fmt: skip


# ---------------------------------------------------------------------- profiles


def _profile_dict(p: art.ArtProfile) -> dict[str, Any]:
    return {"project": p.project, "art": p.direction.model_dump(mode="json", exclude_defaults=True),
            "reference_asset_ids": p.reference_asset_ids, "created_at": p.created_at, "updated_at": p.updated_at}  # fmt: skip


@profile_app.command("set")
def profile_set(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Argument(help="Game/project (or `game:` in the file).")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="YAML/JSON profile, e.g. art.yaml.")] = None,
    style: Annotated[str | None, typer.Option(help="Preset name or style label.")] = None,
    description: Annotated[str | None, typer.Option(help="The look in a sentence or two.")] = None,
    genre: Annotated[str | None, typer.Option(help="Game genre.")] = None,
    theme: Annotated[str | None, typer.Option(help="Setting/theme.")] = None,
    mood: Annotated[str | None, typer.Option()] = None,
    lighting: Annotated[str | None, typer.Option()] = None,
    perspective: Annotated[str | None, typer.Option(help="side view, top-down, isometric…")] = None,
    composition: Annotated[str | None, typer.Option()] = None,
    detail: Annotated[str | None, typer.Option(help="low, medium or high.")] = None,
    palette: Annotated[list[str] | None, typer.Option("--palette", help="Hex colour (repeatable or comma-separated).")] = None,
    avoid: Annotated[list[str] | None, typer.Option("--avoid", help="Thing to keep out (repeatable).")] = None,
    reference: Annotated[list[str] | None, typer.Option("--reference", "-r", help="Style reference asset (repeatable).")] = None,
    replace: Annotated[bool, typer.Option("--replace", help="Replace the whole profile instead of merging fields.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Create or update a game's art profile. Fields merge into the existing profile unless --replace."""
    with session(ctx, json_) as s:
        file_project = None
        base: dict[str, Any] = {}
        if file is not None:
            if not file.is_file():
                raise InvalidInputError(f"Profile file not found: {file}")
            file_project, direction = art.parse_profile_yaml(file.read_text())
            base = direction.model_dump(exclude_defaults=True)
        project = project or file_project
        if not project:
            raise InvalidInputError("Name the project: `art profile set PROJECT` or `game:` in the file")
        data = collect_art(None, style=style, description=description, mood=mood, lighting=lighting, palette=palette,
                           perspective=perspective, composition=composition, detail=detail, avoid=avoid,
                           genre=genre, theme=theme, base=base) or {}  # fmt: skip
        if reference:
            data["references"] = [*data.get("references", []), *reference]
        profile = art.set_profile(s.lib, project, art.ArtDirection(**data), replace=replace)
        out: dict[str, Any] = {"profile": _profile_dict(profile)}
        if file is not None and "sound" in (text := file.read_text()):
            from assetforge import sound

            _, sound_direction = sound.parse_profile_yaml(text)
            if sound_direction is not None and "sound:" in text:
                saved = sound.set_profile(s.lib, profile.project, sound_direction, replace=replace)
                out["sound_profile"] = saved.direction.model_dump(mode="json", exclude_defaults=True)
        s.out.emit(out, lambda c: c.print(art.profile_yaml(profile)))


@profile_app.command("show")
def profile_show(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Game/project.")],
    yaml_: Annotated[bool, typer.Option("--yaml", help="Print the profile as YAML (to edit and `set --file`).")] = False,
    json_: JsonOpt = False,
) -> None:
    """Show a game's art profile and the prompt text it adds."""
    with session(ctx, json_) as s:
        profile = art.get_profile(s.lib, project)
        if yaml_ and not json_:
            typer.echo(art.profile_yaml(profile), nl=False)
            return
        resolved = art.resolve(s.lib, project=profile.project, style=None, art=None)
        s.out.emit({"profile": _profile_dict(profile), "layers": resolved.layers, "prompt_text": resolved.text(),
                    "yaml": art.profile_yaml(profile)}, lambda c: c.print(art.profile_yaml(profile)))  # fmt: skip


@profile_app.command("list")
def profile_list(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List games that have an art profile."""
    with session(ctx, json_) as s:
        items = art.list_profiles(s.lib)

        def human(c: Console) -> None:
            if not items:
                c.print("[dim]No art profiles yet.[/dim]")
            for p in items:
                c.print(f"[bold]{p.project}[/bold]  {p.direction.style or '-'}  [dim]{escape(p.direction.mood or '')}[/dim]")

        s.out.emit({"count": len(items), "profiles": [_profile_dict(p) for p in items]}, human)


@profile_app.command("delete")
def profile_delete(ctx: typer.Context, project: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Remove a game's art profile (assets are not touched)."""
    with session(ctx, json_) as s:
        art.delete_profile(s.lib, project)
        s.out.emit({"deleted": project}, lambda c: c.print(f"Deleted the art profile of {project}"))
