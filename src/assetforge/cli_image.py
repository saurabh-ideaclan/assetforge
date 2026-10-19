"""`assetforge image …` (AI generation/editing) and `assetforge style …` (art-direction profiles)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.cli_common import JsonOpt, dump, session
from assetforge.core import styles
from assetforge.core.models import AssetType, Scope, Status
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.image.edit import EditRequest, edit_image
from assetforge.image.engine import ImageResult, settings_for
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.providers import FACTORIES, get_provider

image_app = typer.Typer(help="Generate and edit images with OpenAI or Gemini.", no_args_is_help=True)
style_app = typer.Typer(help="Art-direction profiles used by image generation.", no_args_is_help=True)


def _load_request_file(file: Path | None) -> dict[str, Any]:
    if file is None:
        return {}
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError as exc:
        raise NotFoundError(f"Request file not found: {file}", path=str(file)) from exc
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"Request file is not valid JSON: {exc}", path=str(file)) from exc
    if not isinstance(data, dict):
        raise InvalidInputError("Request file must contain a JSON object", path=str(file))
    return data


def _merge(data: dict[str, Any], flags: dict[str, Any], lists: dict[str, list[str] | None]) -> dict[str, Any]:
    data = data | {k: v for k, v in flags.items() if v is not None}
    for key, values in lists.items():
        if values:
            data[key] = [*data.get(key, []), *values]
    return data


def render_image_result(c: Console, result: ImageResult) -> None:
    if result.dry_run:
        c.print(f"[bold]Dry run[/bold]: {result.provider} / {result.model}, transparency: {result.transparency}")
        c.print(f"[dim]prompt:[/dim] {escape(result.prompt)}")
        if result.estimated_cost_usd is not None:
            c.print(f"[dim]estimated cost:[/dim] ~${result.estimated_cost_usd:.3f}")
        return
    cost = f", ~${result.cost_usd:.3f}" if result.cost_usd else ""
    verb = "[yellow]Reused[/yellow] earlier job" if result.reused else "Generated with"
    c.print(f"{verb} {result.provider} / {result.model} [dim]{result.job_id}{cost}[/dim]")
    for attempt in result.attempts:
        c.print(f"  [dim]retried after {attempt['provider']} {attempt['code']}: {escape(attempt['message'][:100])}[/dim]")
    table = Table(header_style="bold")
    for column in ("Asset", "Variant", "Size", "Alpha", "QC", "Path"):
        table.add_column(column, overflow="fold" if column == "Path" else "ellipsis")
    for a in result.assets:
        issues = ", ".join(a.qc.get("issues", [])) or "ok"
        table.add_row(a.asset_id, a.variant_id, f"{a.width}x{a.height}", "yes" if a.has_alpha else "no", issues, a.path)
    c.print(table)


# ---------------------------------------------------------------------- image


@image_app.command("providers")
def providers(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Show image providers, whether their API keys are configured, and default models."""
    with session(ctx, json_) as s:
        settings = settings_for(s.lib)
        items = [get_provider(n, settings).describe() for n in FACTORIES if n != "fake" or settings.fake_provider_enabled]
        default = settings.get("ASSETFORGE_IMAGE_PROVIDER") or next((p["name"] for p in items if p["configured"]), None)

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Provider", "Configured", "Generate model", "Edit model", "Transparency", "Max refs"):
                table.add_column(column)
            for p in items:
                table.add_row(
                    p["name"] + (" *" if p["name"] == default else ""),
                    "yes" if p["configured"] else f"no (set {p['key_env']})",
                    p["default_models"]["generate"], p["default_models"]["edit"],
                    "native" if p["native_transparency"] else "keyed", str(p["max_references"]),
                )  # fmt: skip
            c.print(table)

        s.out.emit({"default": default, "providers": items}, human)


@image_app.command("generate")
def generate(
    ctx: typer.Context,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="What to draw.")] = None,
    type_: Annotated[AssetType | None, typer.Option("--type", help="character, sprite, background, icon, tile, ui, effect, image…")] = None,
    style: Annotated[str | None, typer.Option(help="Art direction / style profile name, e.g. cute-jungle.")] = None,
    provider: Annotated[str | None, typer.Option(help="openai, gemini or auto.")] = None,
    model: Annotated[str | None, typer.Option(help="Provider model override.")] = None,
    n: Annotated[int | None, typer.Option("-n", help="Number of candidates (each its own asset).")] = None,
    aspect: Annotated[str | None, typer.Option(help="1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 21:9.")] = None,
    size: Annotated[str | None, typer.Option(help="Exact size like 1536x864 (provider permitting).")] = None,
    quality: Annotated[str | None, typer.Option(help="auto, low, medium, high, max.")] = None,
    transparent: Annotated[bool | None, typer.Option("--transparent/--opaque", help="Transparent background (default by type).")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="png or webp.")] = None,
    reference: Annotated[list[str] | None, typer.Option("--reference", "-r", help="Reference image: asset ID or path (repeatable).")] = None,
    negative: Annotated[str | None, typer.Option(help="Things to avoid.")] = None,
    raw_prompt: Annotated[bool, typer.Option("--raw-prompt", help="Send the prompt exactly as written (no templates).")] = False,
    seed: Annotated[int | None, typer.Option(help="Seed, where the provider supports one.")] = None,
    retries: Annotated[int | None, typer.Option(help="Retries per provider on rate limits/outages (default 2).")] = None,
    no_fallback: Annotated[bool, typer.Option("--no-fallback", help="Don't try other providers on failure.")] = False,
    max_cost: Annotated[float | None, typer.Option(help="Refuse calls estimated above this many USD.")] = None,
    new_variant: Annotated[bool, typer.Option("--new-variant", help="Generate again even if this exact request already ran.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show provider, model and final prompt without calling the API.")] = False,
    name: Annotated[str | None, typer.Option(help="Asset name (default from the prompt).")] = None,
    key: Annotated[str | None, typer.Option(help="Logical key (single candidate only).")] = None,
    description: Annotated[str | None, typer.Option(help="Searchable description (default: the prompt).")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Extra tag (repeatable).")] = None,
    scope: Annotated[Scope | None, typer.Option(help="Reuse scope.")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project (game).")] = None,
    status: Annotated[Status | None, typer.Option(help="Review status (default candidate).")] = None,
    collection: Annotated[str | None, typer.Option(help="Add to this collection (created if missing).")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request; flags override its fields.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Generate new images. Each candidate is stored as its own asset with full provenance."""
    with session(ctx, json_) as s:
        flags = {
            "prompt": prompt, "type": type_, "style": style, "provider": provider, "model": model, "n": n,
            "aspect": aspect, "size": size, "quality": quality, "transparent": transparent, "format": format_,
            "negative": negative, "seed": seed, "retries": retries, "name": name, "key": key,
            "description": description, "scope": scope, "project": project, "status": status, "collection": collection,
            "raw_prompt": raw_prompt or None, "new_variant": new_variant or None, "dry_run": dry_run or None,
            "fallback": False if no_fallback else None, "max_cost": max_cost,
        }  # fmt: skip
        data = _merge(_load_request_file(file), flags, {"references": reference, "tags": tag})
        if "prompt" not in data:
            raise InvalidInputError("A prompt is required (--prompt or the request file)")
        result = generate_image(s.lib, ImageRequest(**data))
        s.out.emit(result.model_dump(mode="json"), lambda c: render_image_result(c, result))


@image_app.command("edit")
def edit(
    ctx: typer.Context,
    source: Annotated[str | None, typer.Argument(help="Asset ID, variant ID, key or image path to edit.")] = None,
    prompt: Annotated[str | None, typer.Option("--prompt", "-p", help="What to change.")] = None,
    reference: Annotated[list[str] | None, typer.Option("--reference", "-r", help="Extra reference image (repeatable).")] = None,
    mask: Annotated[str | None, typer.Option(help="PNG mask: transparent pixels mark the area to change.")] = None,
    provider: Annotated[str | None, typer.Option(help="openai, gemini or auto.")] = None,
    model: Annotated[str | None, typer.Option(help="Provider model override.")] = None,
    n: Annotated[int | None, typer.Option("-n", help="Number of candidates.")] = None,
    aspect: Annotated[str | None, typer.Option(help="Output aspect (default: the source's).")] = None,
    size: Annotated[str | None, typer.Option(help="Exact output size.")] = None,
    quality: Annotated[str | None, typer.Option(help="auto, low, medium, high, max.")] = None,
    transparent: Annotated[bool | None, typer.Option("--transparent/--opaque", help="Default: keep the source's transparency.")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="png or webp.")] = None,
    negative: Annotated[str | None, typer.Option(help="Things to avoid.")] = None,
    raw_prompt: Annotated[bool, typer.Option("--raw-prompt", help="Send the prompt exactly as written.")] = False,
    seed: Annotated[int | None, typer.Option(help="Seed, where supported.")] = None,
    retries: Annotated[int | None, typer.Option(help="Retries per provider (default 2).")] = None,
    no_fallback: Annotated[bool, typer.Option("--no-fallback", help="Don't try other providers on failure.")] = False,
    max_cost: Annotated[float | None, typer.Option(help="Refuse calls estimated above this many USD.")] = None,
    new_variant: Annotated[bool, typer.Option("--new-variant", help="Edit again even if this exact edit already ran.")] = False,
    as_variant: Annotated[bool, typer.Option("--as-variant", help="Store as a variant of the source instead of a new asset.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the plan without calling the API.")] = False,
    name: Annotated[str | None, typer.Option(help="Name for the new asset (default: <source>_edit).")] = None,
    description: Annotated[str | None, typer.Option(help="Searchable description.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Extra tag (repeatable).")] = None,
    status: Annotated[Status | None, typer.Option(help="Review status (default candidate).")] = None,
    collection: Annotated[str | None, typer.Option(help="Add to this collection.")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request; flags override its fields.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Revise an existing image. The original is never modified; the edit links back to it."""
    with session(ctx, json_) as s:
        flags = {
            "source": source, "prompt": prompt, "mask": mask, "provider": provider, "model": model, "n": n,
            "aspect": aspect, "size": size, "quality": quality, "transparent": transparent, "format": format_,
            "negative": negative, "seed": seed, "retries": retries, "name": name, "description": description,
            "status": status, "collection": collection, "raw_prompt": raw_prompt or None,
            "new_variant": new_variant or None, "as_variant": as_variant or None, "dry_run": dry_run or None,
            "fallback": False if no_fallback else None, "max_cost": max_cost,
        }  # fmt: skip
        data = _merge(_load_request_file(file), flags, {"references": reference, "tags": tag})
        for required in ("source", "prompt"):
            if required not in data:
                raise InvalidInputError(f"A {required} is required")
        result = edit_image(s.lib, EditRequest(**data))
        s.out.emit(result.model_dump(mode="json"), lambda c: render_image_result(c, result))


# ---------------------------------------------------------------------- style


def _render_style(c: Console, style: Any) -> None:
    c.print(f"[bold]{style.name}[/bold] ({style.id})")
    for label, value in (("description", style.description), ("instructions", style.instructions),
                         ("palette", ", ".join(style.palette)), ("references", ", ".join(style.reference_asset_ids))):  # fmt: skip
        if value:
            c.print(f"  [dim]{label:>12}[/dim]  {escape(value)}")


@style_app.command("create")
def style_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    description: Annotated[str, typer.Option(help="One-line art direction, e.g. 'cute chunky cartoon, thick outlines'.")] = "",
    instructions: Annotated[str, typer.Option(help="Extra rules added to every prompt.")] = "",
    palette: Annotated[list[str] | None, typer.Option("--palette", help="Hex colours (repeatable or comma-separated).")] = None,
    reference: Annotated[list[str] | None, typer.Option("--reference", "-r", help="Reference asset (repeatable).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Create a style profile."""
    with session(ctx, json_) as s:
        style = styles.create_style(s.lib, name, description=description, instructions=instructions,
                                    palette=palette, references=reference)  # fmt: skip
        s.out.emit({"style": dump(style)}, lambda c: _render_style(c, style))


@style_app.command("list")
def style_list(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """List style profiles."""
    with session(ctx, json_) as s:
        items = styles.list_styles(s.lib)

        def human(c: Console) -> None:
            if not items:
                c.print("[dim]No style profiles.[/dim]")
            for style in items:
                _render_style(c, style)

        s.out.emit({"count": len(items), "styles": dump(items)}, human)


@style_app.command("show")
def style_show(ctx: typer.Context, ref: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Show one style profile."""
    with session(ctx, json_) as s:
        style = styles.get_style(s.lib, ref)
        s.out.emit({"style": dump(style)}, lambda c: _render_style(c, style))


@style_app.command("update")
def style_update(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument()],
    description: Annotated[str | None, typer.Option()] = None,
    instructions: Annotated[str | None, typer.Option()] = None,
    palette: Annotated[list[str] | None, typer.Option("--palette", help="Replace the palette.")] = None,
    add_reference: Annotated[list[str] | None, typer.Option("--add-reference")] = None,
    remove_reference: Annotated[list[str] | None, typer.Option("--remove-reference")] = None,
    json_: JsonOpt = False,
) -> None:
    """Change a style profile."""
    with session(ctx, json_) as s:
        style = styles.update_style(s.lib, ref, description=description, instructions=instructions, palette=palette,
                                    add_references=add_reference, remove_references=remove_reference)  # fmt: skip
        s.out.emit({"style": dump(style)}, lambda c: _render_style(c, style))


@style_app.command("delete")
def style_delete(ctx: typer.Context, ref: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Delete a style profile (assets keep their style tag)."""
    with session(ctx, json_) as s:
        style = styles.delete_style(s.lib, ref)
        s.out.emit({"style": dump(style), "deleted": True}, lambda c: c.print(f"Deleted style {style.name}"))
