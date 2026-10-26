"""`assetforge add` (catalog an agent-made file) and `assetforge methods` (which method suits which asset)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.add import AddRequest, add_file
from assetforge.cli_art import collect_art
from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import AssetType, Scope, Status
from assetforge.methods import METHODS


def register(app: typer.Typer) -> None:
    @app.command("add")
    def add(
        ctx: typer.Context,
        path: Annotated[Path, typer.Argument(help="The file you made (.svg, .png, .webp, .wav…).")],
        method: Annotated[str, typer.Option(help="svg, procedural, file or manual.")] = "file",
        type_: Annotated[AssetType, typer.Option("--type", help="Asset type.")] = AssetType.IMAGE,
        name: Annotated[str | None, typer.Option(help="Asset name (default: the file name).")] = None,
        key: Annotated[str | None, typer.Option(help="Logical key, e.g. ui/icons/heart.")] = None,
        description: Annotated[str, typer.Option("--description", "-d", help="Searchable description.")] = "",
        style: Annotated[str | None, typer.Option(help="Preset or style label.")] = None,
        art_file: Annotated[Path | None, typer.Option("--art-file", help="Art direction it was made with (YAML).")] = None,
        project: Annotated[str | None, typer.Option(help="Owning game/project (its art profile is recorded).")] = None,
        scope: Annotated[Scope | None, typer.Option(help="Reuse scope.")] = None,
        tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Tag (repeatable).")] = None,
        category: Annotated[str | None, typer.Option()] = None,
        subcategory: Annotated[str | None, typer.Option()] = None,
        theme: Annotated[str | None, typer.Option()] = None,
        genre: Annotated[str | None, typer.Option()] = None,
        script: Annotated[Path | None, typer.Option(help="Generator script to keep as provenance.")] = None,
        size: Annotated[str | None, typer.Option(help="SVG render size: WxH or longest side (e.g. 256).")] = None,
        collection: Annotated[str | None, typer.Option(help="Add to this collection (created if missing).")] = None,
        status: Annotated[Status, typer.Option(help="Review status.")] = Status.CANDIDATE,
        json_: JsonOpt = False,
    ) -> None:
        """Catalog a file you made (SVG, script output…) with full metadata, QC and provenance.

        SVG keeps the vector master and gets a PNG render as its primary file. Identical content returns the
        existing asset. Example: `assetforge add heart.svg --method svg --type icon --size 256 -d "red heart"`.
        """
        with session(ctx, json_) as s:
            req = AddRequest(
                path=str(path), method=method, type=type_, name=name, key=key, description=description, style=style,
                art=collect_art(art_file), project=project, scope=scope, tags=tag or [], category=category,
                subcategory=subcategory, theme=theme, genre=genre, script=str(script) if script else None,
                size=size, collection=collection, status=status,
            )  # fmt: skip
            result = add_file(s.lib, req)

            def human(c: Console) -> None:
                verb = "Added" if result.created else "[yellow]Already cataloged[/yellow] as"
                c.print(f"{verb} {result.asset_id} ({result.method}): {escape(result.path)}")
                if result.qc.get("errors") or result.qc.get("warnings"):
                    c.print(f"  QC: {', '.join(result.qc['errors'] + result.qc['warnings'])}")
                for dup in result.near_duplicates:
                    c.print(f"  [yellow]looks like {dup['asset_id']}[/yellow] (distance {dup['distance']})")

            s.out.emit(result.model_dump(mode="json"), human)

    @app.command("regenerate")
    def regenerate_command(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="The AI image that missed the brief.")],
        feedback: Annotated[str, typer.Option("--feedback", "-m", help="What to change, concretely.")],
        n: Annotated[int, typer.Option("-n", help="Candidates to make.")] = 1,
        style: Annotated[str | None, typer.Option(help="Switch to another preset/style.")] = None,
        art_file: Annotated[Path | None, typer.Option("--art-file", help="Art-direction overrides (YAML).")] = None,
        mood: Annotated[str | None, typer.Option()] = None,
        lighting: Annotated[str | None, typer.Option()] = None,
        avoid: Annotated[list[str] | None, typer.Option("--avoid", help="Add to the avoid list (repeatable).")] = None,
        max_cost: Annotated[float | None, typer.Option(help="Refuse calls estimated above this many USD.")] = None,
        reject_previous: Annotated[bool, typer.Option("--reject-previous", help="Mark the reviewed image rejected.")] = False,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the revised prompt and cost only.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Ask for another version of an AI image with review feedback (capped by generation.max_regenerations).

        Example: `assetforge regenerate ast_00042 -m "too cartoonish; grittier, realistic proportions, no outline"`.
        """
        from assetforge.regenerate import regenerate

        with session(ctx, json_) as s:
            overrides = collect_art(art_file, mood=mood, lighting=lighting, avoid=avoid)
            out = regenerate(s.lib, ref, feedback=feedback, n=n, art=overrides, style=style, max_cost=max_cost,
                             reject_previous=reject_previous, dry_run=dry_run)  # fmt: skip

            def human(c: Console) -> None:
                c.print(f"Attempt {out['attempt']} of {out['limit']} for {out['root']}")
                if dry_run:
                    c.print(f"[dim]prompt:[/dim] {escape(out['prompt'])}")
                    return
                for a in out["result"]["assets"]:
                    c.print(f"  new candidate {a['asset_id']}: {escape(a['path'])}")

            s.out.emit({"regenerate": out}, human)

    @app.command("guide")
    def guide(ctx: typer.Context, json_: JsonOpt = False) -> None:
        """Everything a coding agent needs in one document: workflow, commands, presets, methods, formats."""
        from assetforge import art
        from assetforge.guide import guide_document

        with session(ctx, json_) as s:
            doc = guide_document(sorted(art.presets(s.lib)), str(s.lib.paths.home))

            def human(c: Console) -> None:
                c.print("[bold]AssetForge for agents[/bold]")
                for i, step in enumerate(doc["workflow"], start=1):
                    c.print(f" {i}. [bold]{step['step']}[/bold]: {escape(step['do'])}")
                    c.print(f"    [dim]{escape(step['command'])}[/dim]")
                c.print(f"[bold]Presets:[/bold] {', '.join(doc['art']['presets'])}")
                c.print("[dim]Run with --json for the full reference (commands, request schema, profile format).[/dim]")

            s.out.emit({"guide": doc}, human)

    @app.command("methods")
    def methods(ctx: typer.Context, json_: JsonOpt = False) -> None:
        """Generation methods, what each is good for, and its limits. Pick per asset, not per game."""
        with session(ctx, json_) as s:

            def human(c: Console) -> None:
                for m in METHODS:
                    c.print(f"[bold cyan]{m['name']}[/bold cyan]  [dim]{escape(m['command'])}[/dim]")
                    c.print(f"  good for: {', '.join(m['good_for'])}")
                    c.print(f"  [yellow]limits:[/yellow] {'; '.join(m['limits'])}")

            s.out.emit({"methods": METHODS}, human)
