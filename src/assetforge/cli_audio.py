"""`assetforge audio …`: procedural sound effects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from assetforge.audio import pipeline
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


def _engine_made(lib: Any, ref: str) -> bool:
    engines = pipeline.load_engines()
    return (lib.get_asset(ref).metadata.get("generator") or {}).get("engine") in engines


def read_json_file(file: Path | None) -> dict[str, Any]:
    if file is None:
        return {}
    try:
        data = json.loads(file.read_text())
    except FileNotFoundError as exc:
        raise NotFoundError(f"File not found: {file}", path=str(file)) from exc
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{file} is not valid JSON: {exc}", path=str(file)) from exc
    if not isinstance(data, dict):
        raise InvalidInputError(f"{file} must contain a JSON object", path=str(file))
    return data


def render_engine_result(c: Console, res: dict[str, Any], verb: str) -> None:
    asset = res["asset"]
    if res["reused"]:
        why = "same request as" if res["reuse_reason"] == "fingerprint" else "identical sound already in"
        c.print(f"[yellow]Reused[/yellow] {res['asset_id']} {escape(asset['name'])} ({why} the library)")
    else:
        c.print(f"{verb} [bold cyan]{res['asset_id']}[/bold cyan] {escape(asset['name'])}: "
                f"{len(res['variants'])} take(s) as {res['format']}  [dim]{res.get('job_id') or ''}[/dim]")  # fmt: skip
    audio = res.get("audio") or {}
    facts = [f"{k} {audio[k]}" for k in ("bpm", "key", "bars", "intensity") if audio.get(k) is not None]
    if audio.get("loop"):
        facts.append("seamless loop")
    if facts:
        c.print("[dim]" + " · ".join(facts) + "[/dim]")
    table = Table(header_style="bold")
    for column in ("Label", "Variant", "Length", "Peak", "Extras", "Path"):
        table.add_column(column, overflow="fold" if column == "Path" else "ellipsis")
    for v in res["variants"]:
        length = f"{v['duration_ms'] / 1000:.2f}s" if v["duration_ms"] is not None else "-"
        table.add_row(v["label"], v["variant_id"], length, str((v.get("qc") or {}).get("peak_dbfs", "-")),
                      ", ".join([*v["extras"], *v["files"]]) or "-", v["path"])  # fmt: skip
    c.print(table)


def common_fields(*, name: str | None, project: str | None, variants: int | None, seed: int | None,
                  format_: str | None, tag: list[str] | None, key: str | None, description: str | None,
                  status: Status | None, collection: str | None, new_take: bool) -> dict[str, Any]:  # fmt: skip
    data: dict[str, Any] = {k: v for k, v in {"name": name, "project": project, "variants": variants, "seed": seed,
                                               "format": format_, "key": key, "description": description,
                                               "status": status, "collection": collection}.items() if v is not None}  # fmt: skip
    if tag:
        data["tags"] = list(tag)
    if new_take:
        data["new_take"] = True
    return data


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


@audio_app.command("design")
def design_(
    ctx: typer.Context,
    file: Annotated[Path, typer.Option("--file", "-f", help="JSON design ({\"layers\": [...]}) or a request with `design`.")],
    name: Annotated[str | None, typer.Option(help="Asset name.")] = None,
    project: Annotated[str | None, typer.Option(help="Owning project (its sound profile adds the acoustic space).")] = None,
    variants: Annotated[int | None, typer.Option("--variants", "-n", help="Takes to render (1-16).")] = None,
    seed: Annotated[int | None, typer.Option(help="Seed for reproducible output.")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="ogg (default), mp3 or wav.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    key: Annotated[str | None, typer.Option(help="Logical key, e.g. weapons/plasma/fire.")] = None,
    description: Annotated[str | None, typer.Option()] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    collection: Annotated[str | None, typer.Option()] = None,
    new_take: Annotated[bool, typer.Option("--new-take", help="Render fresh even if this seeded request exists.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Build a sound from layers: recipes and/or synth patches placed in time, panned, with effects. No API calls.

    Layer fields: recipe|patch, preset, params, start, gain_db, pan, pitch, repeat, interval, effects.
    Design fields: layers, effects (bus), space, width, variation. See `assetforge guide`.
    """
    from assetforge.audio.design import DesignRequest, generate_design

    with session(ctx, json_) as s:
        data = read_json_file(file)
        if "layers" in data:
            data = {"design": data}
        data |= common_fields(name=name, project=project, variants=variants, seed=seed, format_=format_, tag=tag,
                              key=key, description=description, status=status, collection=collection,
                              new_take=new_take)  # fmt: skip
        res = generate_design(s.lib, DesignRequest(**data))
        s.out.emit(res, lambda c: render_engine_result(c, res, "Designed"))


@audio_app.command("patch")
def patch_(
    ctx: typer.Context,
    file: Annotated[Path, typer.Option("--file", "-f", help="JSON synth patch (oscillators, pitch, amp, filter, …).")],
    name: Annotated[str | None, typer.Option(help="Asset name.")] = None,
    project: Annotated[str | None, typer.Option()] = None,
    variants: Annotated[int | None, typer.Option("--variants", "-n")] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    format_: Annotated[str | None, typer.Option("--format")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", "-t")] = None,
    key: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    collection: Annotated[str | None, typer.Option()] = None,
    new_take: Annotated[bool, typer.Option("--new-take")] = False,
    json_: JsonOpt = False,
) -> None:
    """Synthesize one voice from a patch: oscillators (sine/triangle/square/pulse/saw/noise/pluck/fm), pitch
    envelope, ADSR or point envelope, filter with cutoff envelope and resonance, vibrato, tremolo, drive, bits."""
    from assetforge.audio.design import DesignRequest, generate_design

    with session(ctx, json_) as s:
        data = read_json_file(file)
        if "patch" not in data:
            data = {"patch": data}
        data |= common_fields(name=name, project=project, variants=variants, seed=seed, format_=format_, tag=tag,
                              key=key, description=description, status=status, collection=collection,
                              new_take=new_take)  # fmt: skip
        res = generate_design(s.lib, DesignRequest(**data))
        s.out.emit(res, lambda c: render_engine_result(c, res, "Synthesized"))


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
    """Add more variations to an existing procedural sound, design, ambience or track (same asset, new takes)."""
    with session(ctx, json_) as s:
        if _engine_made(s.lib, ref):
            if param or variation is not None:
                raise InvalidInputError("--param/--variation apply to recipe sounds; designs, ambience and music vary by seed")
            res = pipeline.vary(s.lib, ref, variants=variants, seed=seed)
            s.out.emit(res, lambda c: render_engine_result(c, res, "Added to"))
            return
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
    """Re-render a procedural sound, design, ambience or music asset from its stored spec; confirm it is bit-identical."""
    with session(ctx, json_) as s:
        report = pipeline.reproduce(s.lib, ref) if _engine_made(s.lib, ref) else reproduce_audio(s.lib, ref)

        def human(c: Console) -> None:
            for v in report["variants"]:
                mark = "[green]match[/green]" if v["match"] else "[red]DIFFERENT[/red]"
                note = " (recipe version changed)" if v.get("recipe_version_changed") or v.get("engine_version_changed") else ""
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


# ---------------------------------------------------------------------- SoundFonts

soundfont_app = typer.Typer(help="Optional realistic instruments for music: General MIDI SoundFonts via fluidsynth.",
                            no_args_is_help=True)  # fmt: skip
audio_app.add_typer(soundfont_app, name="soundfont")


@soundfont_app.command("status")
def soundfont_status(ctx: typer.Context, json_: JsonOpt = False) -> None:
    """Is the SoundFont backend ready? Shows fluidsynth, the installed SoundFonts and what is missing."""
    from assetforge.audio.music import soundfont

    with session(ctx, json_) as s:
        state = soundfont.status(s.lib.paths.home)

        def human(c: Console) -> None:
            mark = "[green]ready[/green]" if state["ready"] else "[yellow]not ready[/yellow] (music uses synth instruments)"
            c.print(f"SoundFont backend: {mark}")
            c.print(f"  fluidsynth: {state['fluidsynth'] or '-'}")
            for name, font in state["installed"].items():
                c.print(f"  {name}: {escape(font['title'])}  [dim]{font['license']}[/dim]")
            for item in state["missing"]:
                c.print(f"  [yellow]missing[/yellow] {escape(item)}")

        s.out.emit({"soundfont": state}, human)


@soundfont_app.command("install")
def soundfont_install(
    ctx: typer.Context,
    name: Annotated[str, typer.Option(help="generaluser (default, ~30 MB) or fluidr3 (MIT), or your own label.")] = "generaluser",
    url: Annotated[str | None, typer.Option(help="Download from this URL instead.")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="Use a local .sf2/.sf3 instead of downloading.")] = None,
    license_: Annotated[str | None, typer.Option("--license", help="License of a --file/--url SoundFont.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the download (required; commands never prompt).")] = False,
    json_: JsonOpt = False,
) -> None:
    """Install a SoundFont into the library (download needs --yes). It becomes the default for music."""
    from assetforge.audio.music import soundfont

    with session(ctx, json_) as s:
        if file is not None:
            if not file.is_file():
                raise NotFoundError(f"File not found: {file}")
            known = soundfont.KNOWN.get(name, {})
            entry = soundfont.register(s.lib.paths.home, name, file.expanduser(),
                                       license_=license_ or known.get("license", "see source"),
                                       title=known.get("title", ""))  # fmt: skip
        else:
            known = soundfont.KNOWN.get(name, {})
            if not yes:
                raise InvalidInputError(f"Downloading {known.get('title', name)} from {url or known.get('url')} needs --yes",
                                        hint="Re-run with --yes, or use --file with a SoundFont you already have")  # fmt: skip
            entry = soundfont.install(s.lib.paths.home, name, url=url, license_=license_)
        s.out.emit({"installed": entry, "ready": soundfont.status(s.lib.paths.home)["ready"]},
                   lambda c: c.print(f"Installed {entry['name']} ({entry['size_bytes'] // 1_000_000} MB) at {entry['path']}"))  # fmt: skip


@soundfont_app.command("use")
def soundfont_use(ctx: typer.Context, name: Annotated[str, typer.Argument()], json_: JsonOpt = False) -> None:
    """Make an installed SoundFont the default."""
    from assetforge.audio.music import soundfont

    with session(ctx, json_) as s:
        soundfont.set_default(s.lib.paths.home, name)
        s.out.emit({"default": name}, lambda c: c.print(f"Default SoundFont: {name}"))
