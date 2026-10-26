"""`assetforge audio …`: procedural sound effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.audio.effects import EFFECTS
from assetforge.audio.generate import (
    AudioRequest,
    AudioResult,
    generate_audio,
    reproduce_audio,
    vary_audio,
)
from assetforge.audio.recipes import PRESETS, RECIPES, get_recipe
from assetforge.cli_common import JsonOpt, parse_pairs, session
from assetforge.core.models import Scope, Status
from assetforge.errors import ExitCode, InvalidInputError, NotFoundError
from assetforge.validation.audio import analyze_file

audio_app = typer.Typer(help="Procedurally generate, vary, reproduce and analyze sound effects.", no_args_is_help=True)


def _presets() -> list[dict[str, Any]]:
    return [{"name": name, **preset} for name, preset in sorted(PRESETS.items())]


# ---------------------------------------------------------------------- discovery


@audio_app.command("recipes")
def recipes(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Argument(help="Show one recipe's parameters.")] = None,
    json_: JsonOpt = False,
) -> None:
    """List SFX recipes, presets and effects (everything `audio generate` accepts)."""
    with session(ctx, json_) as s:
        if name:
            recipe = get_recipe(name).to_dict()

            def human_one(c: Console) -> None:
                c.print(f"[bold]{recipe['name']}[/bold] ({recipe['type']}, v{recipe['version']}): {recipe['description']}")
                table = Table(header_style="bold")
                for column in ("Param", "Default", "Range / choices", "Description"):
                    table.add_column(column)
                for p in recipe["params"]:
                    allowed = ", ".join(p["choices"]) if p["kind"] == "choice" else f"{p['min']:g} .. {p['max']:g}"
                    table.add_row(p["name"], str(p["default"]), allowed, escape(p["description"]))
                c.print(table)
                c.print(f"presets: {', '.join(recipe['presets'])}")

            s.out.emit({"recipe": recipe}, human_one)
            return

        items = [r.to_dict() for r in sorted(RECIPES.values(), key=lambda r: r.name)]
        effects = [e.to_dict() for e in EFFECTS.values()]

        def human(c: Console) -> None:
            table = Table(header_style="bold")
            for column in ("Recipe", "Type", "Default length", "Description"):
                table.add_column(column)
            for r in items:
                table.add_row(r["name"], r["type"], f"{r['default_duration']:g}s", escape(r["description"]))
            c.print(table)
            c.print("[bold]presets[/bold]: " + ", ".join(f"{p['name']} ({p['description']})" for p in _presets()))
            c.print("[bold]effects[/bold]: " + ", ".join(e["name"] for e in effects))
            c.print("[dim]`assetforge audio recipes NAME` shows a recipe's parameters.[/dim]")

        s.out.emit({"count": len(items), "recipes": items, "presets": _presets(), "effects": effects}, human)


# ---------------------------------------------------------------------- generation


def _render_result(c: Console, result: AudioResult, verb: str) -> None:
    if result.reused:
        why = "same request as" if result.reuse_reason == "fingerprint" else "identical sound already in"
        c.print(f"[yellow]Reused[/yellow] {result.asset_id} {escape(result.asset.name)} ({why} the library)")
    else:
        c.print(
            f"{verb} [bold cyan]{result.asset_id}[/bold cyan] {escape(result.asset.name)}: "
            f"{len(result.variants)} variant(s) as {result.format}  [dim]{result.job_id or ''}[/dim]"
        )
    table = Table(header_style="bold")
    for column in ("Label", "Variant", "Length", "Peak", "RMS", "Path"):
        table.add_column(column, overflow="fold" if column == "Path" else "ellipsis")
    for v in result.variants:
        length = f"{v.duration_ms / 1000:.2f}s" if v.duration_ms is not None else "-"
        table.add_row(v.label, v.variant_id, length, f"{v.qc['peak_dbfs']}", f"{v.qc['rms_dbfs']}", v.path)
    c.print(table)


@audio_app.command("generate")
def generate(
    ctx: typer.Context,
    recipe: Annotated[str | None, typer.Option("--recipe", "-r", help="Recipe name (see `audio recipes`).")] = None,
    preset: Annotated[str | None, typer.Option(help="Sound character: soft, punchy, retro, cartoon, heavy, …")] = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="Recipe parameter name=value (repeatable).")] = None,
    duration: Annotated[float | None, typer.Option(help="Length in seconds.")] = None,
    effect: Annotated[list[str] | None, typer.Option("--effect", "-e", help="Effect, e.g. reverb:room_size=0.5 (repeatable; replaces preset effects).")] = None,
    variants: Annotated[int | None, typer.Option("--variants", "-n", help="Number of variations (1-32).")] = None,
    variation: Annotated[float | None, typer.Option(help="How different variants are, 0-1 (default 0.3).")] = None,
    seed: Annotated[int | None, typer.Option(help="Seed for reproducible output (random when omitted).")] = None,
    format_: Annotated[str | None, typer.Option("--format", "-f", help="wav, ogg or mp3 (WAV masters are always kept).")] = None,
    sample_rate: Annotated[int | None, typer.Option(help="22050, 32000, 44100 or 48000.")] = None,
    peak_db: Annotated[float | None, typer.Option(help="Normalization peak in dBFS (default -1).")] = None,
    name: Annotated[str | None, typer.Option(help="Asset name (default: recipe[_preset]).")] = None,
    key: Annotated[str | None, typer.Option(help="Logical key, e.g. enemy/beetle/hit.")] = None,
    description: Annotated[str | None, typer.Option(help="Searchable description.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t", help="Extra tag (repeatable).")] = None,
    scope: Annotated[Scope | None, typer.Option(help="Reuse scope (default global).")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project (game).")] = None,
    style: Annotated[str | None, typer.Option(help="Art-direction style, e.g. dark-fantasy (not the sound preset).")] = None,
    status: Annotated[Status | None, typer.Option(help="Review status (default candidate).")] = None,
    collection: Annotated[str | None, typer.Option(help="Add to this collection (created if missing).")] = None,
    new_variant: Annotated[bool, typer.Option("--new-variant", help="Force a fresh take instead of reusing an identical sound.")] = False,
    from_: Annotated[str | None, typer.Option("--from", help="Start from an existing procedural sound's settings.")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="JSON request file; flags override its fields.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Generate a new procedural sound effect with seeded variations. No API calls."""
    with session(ctx, json_) as s:
        data: dict[str, Any] = {}
        if file is not None:
            try:
                data = json.loads(file.read_text())
            except FileNotFoundError as exc:
                raise NotFoundError(f"Request file not found: {file}", path=str(file)) from exc
            except json.JSONDecodeError as exc:
                raise InvalidInputError(f"Request file is not valid JSON: {exc}", path=str(file)) from exc
            if not isinstance(data, dict):
                raise InvalidInputError("Request file must contain a JSON object", path=str(file))
        flags = {
            "recipe": recipe, "preset": preset, "duration": duration, "variants": variants, "variation": variation,
            "seed": seed, "format": format_, "sample_rate": sample_rate, "peak_db": peak_db, "name": name,
            "key": key, "description": description, "scope": scope, "project": project, "style": style,
            "status": status, "collection": collection,
        }  # fmt: skip
        data |= {k: v for k, v in flags.items() if v is not None}
        if new_variant:
            data["new_variant"] = True
        if param:
            data["params"] = {**data.get("params", {}), **parse_pairs(param, "--param")}
        if effect:
            data["effects"] = list(effect)
        if tag:
            data["tags"] = [*data.get("tags", []), *tag]
        result = generate_audio(s.lib, AudioRequest(**data), from_ref=from_)
        s.out.emit(result.model_dump(mode="json"), lambda c: _render_result(c, result, "Generated"))


@audio_app.command("vary")
def vary(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="A procedurally generated asset.")],
    variants: Annotated[int, typer.Option("--variants", "-n", help="How many new variants (1-32).")] = 1,
    variation: Annotated[float | None, typer.Option(help="How different, 0-1 (default: the asset's setting).")] = None,
    seed: Annotated[int | None, typer.Option(help="Seed for these variants.")] = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="Shift a base parameter name=value.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Add more variations to an existing procedural sound (same asset, new variants)."""
    with session(ctx, json_) as s:
        result = vary_audio(
            s.lib, ref, variants=variants, variation=variation, seed=seed, params=parse_pairs(param or [], "--param")
        )
        s.out.emit(result.model_dump(mode="json"), lambda c: _render_result(c, result, "Added to"))


@audio_app.command("reproduce")
def reproduce(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Asset or variant to re-render.")],
    json_: JsonOpt = False,
) -> None:
    """Re-render a procedural sound from its stored recipe and confirm it is bit-identical."""
    with session(ctx, json_) as s:
        report = reproduce_audio(s.lib, ref)

        def human(c: Console) -> None:
            for v in report["variants"]:
                mark = "[green]match[/green]" if v["match"] else "[red]DIFFERENT[/red]"
                note = " (recipe version changed)" if v["recipe_version_changed"] else ""
                c.print(f"{v['label']:>6} {v['variant_id']}  {mark}{note}")

        s.out.emit({"reproduce": report}, human, status="ok" if report["ok"] else "error")
        if not report["ok"]:
            raise typer.Exit(int(ExitCode.ERROR))


@audio_app.command("analyze")
def analyze(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Audio file path, asset ID, variant ID or key.")],
    json_: JsonOpt = False,
) -> None:
    """Measure peak/RMS level, clipping, DC offset and silence of a sound."""
    with session(ctx, json_) as s:
        candidate = Path(target).expanduser()
        if candidate.is_file():
            path = candidate
        else:
            asset = s.lib.get_asset(target)
            variant = next((v for v in asset.variants if v.id == target), asset.primary_variant)
            if variant is None or variant.file.kind != "audio":
                raise InvalidInputError(f"{target} is not an audio asset")
            path = Path(variant.file.path)
        report = analyze_file(path)

        def human(c: Console) -> None:
            state = "[green]OK[/green]" if report["ok"] else "[red]PROBLEM[/red]"
            c.print(f"{state} {escape(str(path))}")
            for k, v in report.items():
                if k not in ("ok",):
                    c.print(f"  [dim]{k:>20}[/dim]  {v}")

        s.out.emit({"path": str(path), "analysis": report}, human)
