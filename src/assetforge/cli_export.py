"""`assetforge export`: write game-ready files, an atlas and a PixiJS manifest into a game repo."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import AssetType
from assetforge.exporters.generic import ExportRequest, ExportResult, export


def render_export(c: Console, r: ExportResult) -> None:
    verb = "Would export" if r.dry_run else "Exported"
    n = r.counts
    c.print(f"{verb} {n['assets']} asset(s) to [bold]{escape(r.out)}[/bold] (bundle [cyan]{r.bundle}[/cyan]): "
            f"{n['written']} file(s) written, {n['unchanged']} unchanged, {n['removed']} removed")  # fmt: skip
    for atlas in r.atlases:
        pages = ", ".join(f"{p['width']}x{p['height']}" for p in atlas["pages"])
        c.print(f"  atlas [cyan]{atlas['name']}[/cyan]: {len(atlas['frames'])} frames on {pages}"
                + (f"; animations: {', '.join(atlas['animations'])}" if atlas["animations"] else ""))  # fmt: skip
    for key, value in r.outputs.items():
        if value:
            c.print(f"  {key}: {escape(value)}")
    for s in r.skipped:
        c.print(f"  [yellow]skipped[/yellow] {s['asset_id']}: {escape(s['reason'])}")


def register(app: typer.Typer) -> None:
    @app.command("export")
    def export_command(
        ctx: typer.Context,
        refs: Annotated[list[str] | None, typer.Argument(help="Assets to export (or select with the options).")] = None,
        out: Annotated[Path, typer.Option("--out", "-o", help="Target directory, e.g. ../my-game/public/assets.")] = Path("assets"),
        collection: Annotated[str | None, typer.Option(help="Export a collection.")] = None,
        project: Annotated[str | None, typer.Option(help="Record usage for this project; alone, exports what it uses.")] = None,
        query: Annotated[str | None, typer.Option("--query", "-q", help="Export search results.")] = None,
        type_: Annotated[list[AssetType] | None, typer.Option("--type", help="Only these types (repeatable).")] = None,
        include_candidates: Annotated[bool, typer.Option("--include-candidates", help="Also export unreviewed candidates.")] = False,
        allow_restricted: Annotated[bool, typer.Option("--allow-restricted", help="Export assets not cleared for commercial use.")] = False,
        layout: Annotated[str, typer.Option(help="by-type (characters/beetle.png) or flat (beetle.png); keys always win.")] = "by-type",
        image_format: Annotated[list[str] | None, typer.Option("--image-format", help="webp and/or png (default both).")] = None,
        audio_format: Annotated[list[str] | None, typer.Option("--audio-format", help="ogg, m4a, mp3, wav (default ogg + m4a: M4A is the Safari/iOS fallback).")] = None,
        no_audio_extras: Annotated[bool, typer.Option("--no-audio-extras", help="Skip stems and intensity versions of ambience/music.")] = False,
        audio_takes: Annotated[bool, typer.Option("--audio-takes", help="Also export every take (v2, v3…) for round-robin playback.")] = False,
        audio_sprite: Annotated[str | None, typer.Option("--audio-sprite", help="Also pack short sounds into NAME.ogg + NAME.json (Howler sprite).")] = None,
        max_image_size: Annotated[int | None, typer.Option(help="Downscale larger images to fit this box.")] = None,
        atlas: Annotated[str | None, typer.Option(help="Pack characters/sprites/icons/UI/effects into an atlas with this name.")] = None,
        atlas_max_size: Annotated[int, typer.Option(help="Maximum atlas page size.")] = 2048,
        atlas_padding: Annotated[int, typer.Option(help="Pixels between frames.")] = 2,
        pot: Annotated[bool, typer.Option("--pot", help="Power-of-two atlas pages.")] = False,
        no_pixi: Annotated[bool, typer.Option("--no-pixi", help="Skip the PixiJS manifest.json.")] = False,
        bundle: Annotated[str | None, typer.Option(help="PixiJS bundle name (default: collection/project).")] = None,
        typescript: Annotated[str | None, typer.Option(help="Also write alias constants to this .ts file.")] = None,
        no_record_usage: Annotated[bool, typer.Option("--no-record-usage", help="Don't record project usage.")] = False,
        clean: Annotated[bool, typer.Option("--clean", help="Remove files from earlier exports that are no longer exported.")] = False,
        overwrite: Annotated[bool, typer.Option("--overwrite", help="Replace files AssetForge didn't create.")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be written.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Export assets as named game files, with optional spritesheet atlas and PixiJS manifest."""
        with session(ctx, json_) as s:
            req = ExportRequest(
                out=str(out), refs=refs or [], collection=collection, project=project, query=query, types=type_ or [],
                include_candidates=include_candidates, allow_restricted=allow_restricted, layout=layout,  # type: ignore[arg-type]
                image_formats=image_format or ["webp", "png"], audio_formats=audio_format or ["ogg", "m4a"],
                audio_extras=not no_audio_extras, audio_takes=audio_takes, audio_sprite=audio_sprite, max_image_size=max_image_size, atlas=atlas, atlas_max_size=atlas_max_size, atlas_padding=atlas_padding,
                atlas_power_of_two=pot, pixi=not no_pixi, bundle=bundle, typescript=typescript,
                record_usage=not no_record_usage, clean=clean, overwrite=overwrite, dry_run=dry_run,
            )  # fmt: skip
            result = export(s.lib, req)
            s.out.emit(result.model_dump(mode="json"), lambda c: render_export(c, result))
