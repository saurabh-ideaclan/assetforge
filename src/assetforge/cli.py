"""The `assetforge` command line: AssetForge's public, agent-facing API.

Contract: with --json (or ASSETFORGE_JSON=1) every command prints exactly one JSON
document on stdout with a top-level "status" of "ok", "partial" or "error". Errors carry
a stable `error.code`, diagnostics go to stderr, exit codes are listed in
`assetforge.errors.ExitCode`, and no command ever prompts for input.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from assetforge import __version__
from assetforge.cli_art import art_app
from assetforge.cli_sprite import sprite_app
from assetforge.cli_audio import audio_app
from assetforge.cli_image import image_app, style_app
from assetforge.cli_export import register as register_export_command
from assetforge.cli_jobs import jobs_app
from assetforge.cli_ops import register as register_ops_commands
from assetforge.cli_report import register as register_report_commands
from assetforge.cli_ui import register as register_ui_commands
from assetforge.cli_discover import register as register_discover_commands
from assetforge.cli_create import register as register_create_commands
from assetforge.cli_process import register as register_process_commands
from assetforge.cli_common import JsonOpt, parse_pairs, session
from assetforge.cli_common import dump as _dump
from assetforge.core import collections as coll
from assetforge.core.models import (
    AssetPatch,
    AssetType,
    CommercialUse,
    FileKind,
    ImportOptions,
    Origin,
    Scope,
    Status,
)
from assetforge.core.search import SearchQuery, search as run_search
from assetforge.errors import ExitCode, InvalidInputError
from assetforge.output import render_asset, render_assets, render_collections, render_import

app = typer.Typer(
    name="assetforge",
    help="Local-first game asset catalog and generator for AI agents.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
collection_app = typer.Typer(help="Organize assets into named sets.", no_args_is_help=True)
app.add_typer(collection_app, name="collection")
app.add_typer(audio_app, name="audio")
app.add_typer(image_app, name="image")
app.add_typer(style_app, name="style")
app.add_typer(jobs_app, name="jobs")
app.add_typer(art_app, name="art")
app.add_typer(sprite_app, name="sprite")

def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    home: Annotated[
        Path | None,
        typer.Option("--home", envvar="ASSETFORGE_HOME", help="Library directory (default ~/.assetforge)."),
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    ctx.obj = {"home": home}


# ---------------------------------------------------------------------- library


@app.command()
def init(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Create the library (idempotent) and show where it lives."""
    with session(ctx, json_) as s:
        info = s.lib.info()
        s.out.emit(
            {"library": info},
            lambda c: c.print(
                f"AssetForge library ready at [bold]{info['home']}[/bold] "
                f"(schema v{info['schema_version']}, {info['counts']['assets']} assets)"
            ),
        )


@app.command()
def info(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show library location and statistics."""
    with session(ctx, json_) as s:
        data = s.lib.info()

        def human(c: Any) -> None:
            c.print(f"[bold]Library[/bold] {data['home']}  (schema v{data['schema_version']})")
            c.print("  " + ", ".join(f"{v} {k}" for k, v in data["counts"].items()))
            c.print(f"  stored: {data['stored_bytes'] / 1_048_576:.1f} MiB")
            if data["by_type"]:
                c.print("  by type: " + ", ".join(f"{k}={v}" for k, v in data["by_type"].items()))
            if data["by_status"]:
                c.print("  by status: " + ", ".join(f"{k}={v}" for k, v in data["by_status"].items()))

        s.out.emit({"library": data}, human)


@app.command("import")
def import_(
    ctx: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Files and/or directories to import.")],
    type_: Annotated[AssetType | None, typer.Option("--type", help="Asset type (inferred when omitted).")] = None,
    name: Annotated[str | None, typer.Option(help="Asset name (single file only).")] = None,
    key: Annotated[str | None, typer.Option(help="Logical key such as enemy/beetle/hit (single file only).")] = None,
    description: Annotated[str, typer.Option(help="Searchable description.")] = "",
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Tag to add (repeatable).")] = None,
    scope: Annotated[Scope, typer.Option(help="Reuse scope.")] = Scope.GLOBAL,
    project: Annotated[str | None, typer.Option(help="Owning project (game).")] = None,
    style: Annotated[str | None, typer.Option(help="Art-direction / style profile.")] = None,
    category: Annotated[str | None, typer.Option(help="Category, e.g. environment, weapon, creature.")] = None,
    subcategory: Annotated[str | None, typer.Option(help="Subcategory, e.g. forest, sword, undead.")] = None,
    theme: Annotated[str | None, typer.Option(help="Theme, e.g. dark-fantasy, space, underwater.")] = None,
    genre: Annotated[str | None, typer.Option(help="Game genre, e.g. platformer, puzzle, survival.")] = None,
    status: Annotated[Status, typer.Option(help="Review status for new assets.")] = Status.APPROVED,
    license_: Annotated[str | None, typer.Option("--license", help="License name, e.g. CC0-1.0.")] = None,
    commercial_use: Annotated[CommercialUse, typer.Option(help="Commercial-use permission.")] = CommercialUse.UNKNOWN,
    attribution: Annotated[str | None, typer.Option(help="Required credit line, if any.")] = None,
    collection: Annotated[str | None, typer.Option(help="Add imported assets to this collection (created if missing).")] = None,
    auto_tags: Annotated[bool, typer.Option("--auto-tags/--no-auto-tags", help="Tag assets with their folder names.")] = True,
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive", help="Descend into subfolders.")] = True,
    all_files: Annotated[bool, typer.Option("--all-files", help="Also import files of unknown type.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report what would happen without writing.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Import existing files. Identical content is stored once and reported as a duplicate."""
    with session(ctx, json_) as s:
        try:
            options = ImportOptions(
                type=type_, name=name, key=key, description=description, tags=tag or [], scope=scope,
                project=project, style=style, category=category, subcategory=subcategory, theme=theme, genre=genre,
                status=status, license=license_, commercial_use=commercial_use,
                attribution=attribution, collection=collection, auto_tags=auto_tags, recursive=recursive,
                all_files=all_files, dry_run=dry_run,
            )  # fmt: skip
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        report = s.lib.import_paths(paths, options)
        s.out.emit(report.model_dump(mode="json"), lambda c: render_import(c, report), status=report.status)
        if report.status == "partial":
            raise typer.Exit(int(ExitCode.PARTIAL))
        if report.status == "error":
            raise typer.Exit(int(ExitCode.ERROR))


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Free-text description of the asset you need.")] = "",
    type_: Annotated[list[AssetType] | None, typer.Option("--type", help="Restrict to type (repeatable).")] = None,
    kind: Annotated[FileKind | None, typer.Option(help="Restrict to file kind.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Require tag (repeatable).")] = None,
    style: Annotated[str | None, typer.Option(help="Prefer assets of this style.")] = None,
    strict_style: Annotated[bool, typer.Option("--strict-style", help="Only return assets of --style.")] = False,
    project: Annotated[str | None, typer.Option(help="Search as this project: global + its own + matching-style assets.")] = None,
    scope: Annotated[list[Scope] | None, typer.Option("--scope", help="Restrict to scope (repeatable).")] = None,
    status: Annotated[list[Status] | None, typer.Option("--status", help="Statuses to include (default: approved and candidate).")] = None,
    collection: Annotated[str | None, typer.Option(help="Restrict to a collection.")] = None,
    include_restricted: Annotated[bool, typer.Option("--include-restricted", help="Include assets not cleared for commercial use.")] = False,
    transparent: Annotated[bool | None, typer.Option("--transparent/--opaque", help="Require (or exclude) transparency.")] = None,
    min_duration_ms: Annotated[int | None, typer.Option(help="Minimum audio duration.")] = None,
    max_duration_ms: Annotated[int | None, typer.Option(help="Maximum audio duration.")] = None,
    min_width: Annotated[int | None, typer.Option(help="Minimum image width.")] = None,
    min_height: Annotated[int | None, typer.Option(help="Minimum image height.")] = None,
    max_width: Annotated[int | None, typer.Option(help="Maximum image width.")] = None,
    max_height: Annotated[int | None, typer.Option(help="Maximum image height.")] = None,
    category: Annotated[str | None, typer.Option(help="Exact category.")] = None,
    subcategory: Annotated[str | None, typer.Option(help="Exact subcategory.")] = None,
    theme: Annotated[str | None, typer.Option(help="Exact theme.")] = None,
    genre: Annotated[str | None, typer.Option(help="Exact game genre.")] = None,
    origin: Annotated[list[Origin] | None, typer.Option("--origin", help="ai, procedural, import, derived (repeatable).")] = None,
    color: Annotated[list[str] | None, typer.Option("--color", help="Dominant colour name or #hex (repeatable, all required).")] = None,
    orientation: Annotated[str | None, typer.Option(help="landscape, portrait or square.")] = None,
    aspect: Annotated[str | None, typer.Option(help="Aspect ratio W:H, e.g. 16:9 (±5%).")] = None,
    tone: Annotated[str | None, typer.Option(help="Overall brightness: dark, mid or light.")] = None,
    format_: Annotated[list[str] | None, typer.Option("--format", help="Primary file format: png, webp, svg… (repeatable).")] = None,
    similar: Annotated[str | None, typer.Option(help="Rank by visual similarity to this asset (ignores the text query).")] = None,
    all_terms: Annotated[bool, typer.Option("--all-terms", help="Every query word must match.")] = False,
    min_coverage: Annotated[float, typer.Option(help="Drop hits matching less than this share of query words.")] = 0.0,
    limit: Annotated[int, typer.Option(help="Maximum results.")] = 20,
    json_: JsonOpt = False,
) -> None:
    """Find existing assets before generating new ones.

    Plain words match names, tags, descriptions, type, style, category, theme, genre, colour names and
    technical words (transparent/opaque, landscape/portrait/square, dark/light, png/svg), e.g.
    `search "transparent dark enemy sprite"`. Filters narrow further; `--similar ast_00012` ranks by look.
    """
    with session(ctx, json_) as s:
        try:
            q = SearchQuery(
                text=query, types=type_ or [], kind=kind, tags=tag or [], style=style, strict_style=strict_style,
                project=project, scopes=scope or [], statuses=status or [], collection=collection,
                include_restricted=include_restricted, transparent=transparent, min_duration_ms=min_duration_ms,
                max_duration_ms=max_duration_ms, min_width=min_width, min_height=min_height, max_width=max_width,
                max_height=max_height, category=category, subcategory=subcategory, theme=theme, genre=genre,
                origins=origin or [], colors=color or [], orientation=orientation, aspect=aspect, tone=tone,
                formats=format_ or [], similar=similar, all_terms=all_terms, min_coverage=min_coverage, limit=limit,
            )  # fmt: skip
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        result = run_search(s.lib, q)
        s.out.emit(result.model_dump(mode="json"), lambda c: render_assets(c, result.results))


@app.command()
def get(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Asset ID, variant ID, key, or project:key.")],
    project: Annotated[str | None, typer.Option(help="Project to resolve a key in.")] = None,
    path: Annotated[bool, typer.Option("--path", help="Print only the primary file path.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Show an asset's metadata, variants and file paths."""
    with session(ctx, json_) as s:
        asset = s.lib.get_asset(ref, project=project)
        if path and not json_:
            typer.echo(asset.path or "")
            return
        s.out.emit({"asset": _dump(asset)}, lambda c: render_asset(c, asset))


@app.command()
def update(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Asset ID, variant ID, key, or project:key.")],
    name: Annotated[str | None, typer.Option()] = None,
    key: Annotated[str | None, typer.Option(help="New key ('' clears it).")] = None,
    type_: Annotated[AssetType | None, typer.Option("--type")] = None,
    description: Annotated[str | None, typer.Option()] = None,
    scope: Annotated[Scope | None, typer.Option(help="Change reuse scope, e.g. promote project -> global.")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project ('' clears it).")] = None,
    style: Annotated[str | None, typer.Option(help="Style ('' clears it).")] = None,
    category: Annotated[str | None, typer.Option(help="('' clears) Category, e.g. environment, weapon, creature.")] = None,
    subcategory: Annotated[str | None, typer.Option(help="('' clears) Subcategory, e.g. forest, sword, undead.")] = None,
    theme: Annotated[str | None, typer.Option(help="('' clears) Theme, e.g. dark-fantasy, space, underwater.")] = None,
    genre: Annotated[str | None, typer.Option(help="('' clears) Game genre, e.g. platformer, puzzle, survival.")] = None,
    license_: Annotated[str | None, typer.Option("--license")] = None,
    commercial_use: Annotated[CommercialUse | None, typer.Option()] = None,
    attribution: Annotated[str | None, typer.Option()] = None,
    add_tag: Annotated[list[str] | None, typer.Option("--add-tag", help="Repeatable.")] = None,
    remove_tag: Annotated[list[str] | None, typer.Option("--remove-tag", help="Repeatable.")] = None,
    meta: Annotated[list[str] | None, typer.Option("--meta", help="key=value (JSON values allowed); repeatable.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Edit an asset's descriptive metadata. Files are never modified."""
    with session(ctx, json_) as s:
        given = {
            "name": name, "key": key, "type": type_, "description": description, "scope": scope,
            "project": project, "style": style, "license": license_, "commercial_use": commercial_use,
            "attribution": attribution, "category": category, "subcategory": subcategory, "theme": theme,
            "genre": genre,
        }  # fmt: skip
        patch = AssetPatch(
            **{k: v for k, v in given.items() if v is not None},
            add_tags=add_tag or [],
            remove_tags=remove_tag or [],
            metadata=parse_pairs(meta or [], "--meta"),
        )
        asset = s.lib.update_asset(ref, patch)
        s.out.emit({"asset": _dump(asset)}, lambda c: render_asset(c, asset))


@app.command()
def use(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Asset or variant to record.")],
    project: Annotated[str, typer.Option(help="Game/project that uses the asset.")],
    variant: Annotated[str | None, typer.Option(help="Variant ID (default: primary).")] = None,
    as_: Annotated[str | None, typer.Option("--as", help="How the project refers to it, e.g. sfx/coin.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Record that a project uses an asset (shared files are never duplicated)."""
    with session(ctx, json_) as s:
        usage = s.lib.record_usage(ref, project, variant_id=variant, usage_key=as_)
        s.out.emit(
            {"usage": _dump(usage)},
            lambda c: c.print(f"{usage.project} now uses {usage.asset_id} ({usage.variant_id})"),
        )


@app.command()
def usage(
    ctx: typer.Context,
    project: Annotated[str, typer.Option(help="Project to list.")],
    json_: JsonOpt = False,
) -> None:
    """List the assets a project uses."""
    with session(ctx, json_) as s:
        rows = s.lib.project_usage(project)
        s.out.emit(
            {"project": project, "count": len(rows), "usage": [{**_dump(u), "asset": _dump(a)} for u, a in rows]},
            lambda c: render_assets(c, [a for _, a in rows], title=f"Used by {project}"),
        )


@app.command()
def verify(
    ctx: typer.Context,
    rehash: Annotated[bool, typer.Option("--rehash", help="Re-hash every file (slower, detects corruption).")] = False,
    json_: JsonOpt = False,
) -> None:
    """Check that every cataloged file exists and is intact."""
    with session(ctx, json_) as s:
        report = s.lib.verify(rehash=rehash)

        def human(c: Any) -> None:
            state = "[green]OK[/green]" if report["ok"] else "[red]PROBLEMS FOUND[/red]"
            c.print(f"{state}: checked {report['checked']} file(s), {len(report['orphan_objects'])} orphan object(s)")
            for issue in report["issues"]:
                c.print(f"  {issue['problem']}: {issue['path']}")

        s.out.emit({"verify": report}, human, status="ok" if report["ok"] else "error")
        if not report["ok"]:
            raise typer.Exit(int(ExitCode.STORAGE))


# ---------------------------------------------------------------------- collections


@collection_app.command("create")
def collection_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    kind: Annotated[str, typer.Option(help="game, character, style, type, purpose or custom.")] = "custom",
    description: Annotated[str, typer.Option()] = "",
    project: Annotated[str | None, typer.Option()] = None,
    json_: JsonOpt = False,
) -> None:
    """Create a collection."""
    with session(ctx, json_) as s:
        c = coll.create_collection(s.lib, name, kind=kind, description=description, project=project)
        s.out.emit({"collection": _dump(c)}, lambda con: con.print(f"Created collection {c.name} ({c.id})"))


@collection_app.command("list")
def collection_list(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List collections."""
    with session(ctx, json_) as s:
        items = coll.list_collections(s.lib)
        s.out.emit({"count": len(items), "collections": _dump(items)}, lambda c: render_collections(c, items))


@collection_app.command("show")
def collection_show(ctx: typer.Context, ref: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Show a collection and its assets."""
    with session(ctx, json_) as s:
        c, assets = coll.collection_assets(s.lib, ref)
        s.out.emit(
            {"collection": _dump(c), "assets": _dump(assets)},
            lambda con: render_assets(con, assets, title=f"{c.name} ({c.id}, {c.kind})"),
        )


@collection_app.command("add")
def collection_add(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Collection name or ID.")],
    assets: Annotated[list[str], typer.Argument(help="Assets to add.")],
    json_: JsonOpt = False,
) -> None:
    """Add assets to a collection (idempotent)."""
    with session(ctx, json_) as s:
        c, added = coll.add_assets(s.lib, ref, assets)
        s.out.emit(
            {"collection": _dump(c), "added": added},
            lambda con: con.print(f"Added {added} asset(s) to {c.name}; it now has {c.asset_count}"),
        )


@collection_app.command("remove")
def collection_remove(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Collection name or ID.")],
    assets: Annotated[list[str], typer.Argument(help="Assets to remove.")],
    json_: JsonOpt = False,
) -> None:
    """Remove assets from a collection (the assets are kept)."""
    with session(ctx, json_) as s:
        c, removed = coll.remove_assets(s.lib, ref, assets)
        s.out.emit(
            {"collection": _dump(c), "removed": removed},
            lambda con: con.print(f"Removed {removed} asset(s) from {c.name}; it now has {c.asset_count}"),
        )


@collection_app.command("delete")
def collection_delete(ctx: typer.Context, ref: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Delete a collection. Its assets stay in the library."""
    with session(ctx, json_) as s:
        c = coll.delete_collection(s.lib, ref)
        s.out.emit({"collection": _dump(c), "deleted": True}, lambda con: con.print(f"Deleted collection {c.name}"))


register_process_commands(app)
register_export_command(app)
register_ops_commands(app)
register_report_commands(app)
register_ui_commands(app)
register_discover_commands(app)
register_create_commands(app)


# ---------------------------------------------------------------------- entry point


def _json_requested(args: list[str]) -> bool:
    return "--json" in args or os.environ.get("ASSETFORGE_JSON", "").lower() in ("1", "true", "yes")


def _is_usage_error(exc: Exception) -> bool:
    # Typer bundles its own click; usage errors are ClickExceptions, recognized by their interface.
    return callable(getattr(exc, "format_message", None)) and callable(getattr(exc, "show", None))


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Turns usage errors into JSON errors when --json is given."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        result = app(args=args, prog_name="assetforge", standalone_mode=False)
    except typer.Abort:
        return 130
    except Exception as exc:
        if not _is_usage_error(exc):
            raise
        if type(exc).__name__ == "NoArgsIsHelpError":
            sys.stdout.write(exc.format_message() + "\n")
            return int(ExitCode.OK)
        if _json_requested(args):
            error = {"code": "USAGE", "message": exc.format_message()}
            sys.stdout.write(json.dumps({"status": "error", "error": error}, indent=2) + "\n")
        exc.show()
        return int(ExitCode.USAGE)
    return result if isinstance(result, int) else int(ExitCode.OK)
