"""Phase 04 commands: processing, previews, QC, review workflow and lineage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from assetforge.audio import process as audio_proc
from assetforge.cli_audio import audio_app
from assetforge.cli_common import JsonOpt, dump, session
from assetforge.cli_image import image_app
from assetforge.core import collections as coll
from assetforge.core import jobs, lineage as lineage_mod, previews, qc, review as review_mod
from assetforge.core.catalog import Library
from assetforge.core.derive import Derivative
from assetforge.core.models import AssetType, FileKind, LineageRelation, Status
from assetforge.core.search import SearchQuery, search
from assetforge.errors import AssetForgeError, ExitCode, InvalidInputError, NotFoundError
from assetforge.image import process as image_proc
from assetforge.output import render_assets

review_app = typer.Typer(help="Review candidates: queue, approve (QC-gated), reject, pick one of a batch.",
                         no_args_is_help=True)  # fmt: skip


def _ops_file(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise NotFoundError(f"Ops file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"Ops file is not valid JSON: {exc}") from exc
    ops = data.get("ops") if isinstance(data, dict) else data
    if not isinstance(ops, list):
        raise InvalidInputError('Ops file must be a list of ops or {"ops": [...]}')
    return ops


def _assemble(preset_ops: list[dict[str, Any]], flag_ops: list[dict[str, Any]],
              file_ops: list[dict[str, Any]], fmt: dict[str, Any] | None) -> list[dict[str, Any]]:  # fmt: skip
    """preset ops, then file ops, then flag ops; a single trailing format (flags win over presets)."""
    body = [op for op in [*preset_ops, *file_ops, *flag_ops] if op.get("op") != "format"]
    formats = [op for op in [*preset_ops, *file_ops] if op.get("op") == "format"]
    final = fmt or (formats[-1] if formats else None)
    return [*body, *([final] if final else [])]


def _run_batch(ctx: typer.Context, json_: bool, refs: list[str], runner: Any, build: Any = None) -> None:
    """Run `runner(lib, ref, ops)` per ref. `build()` makes the ops inside the session, so bad presets or
    ops files are reported through the normal JSON error contract."""
    with session(ctx, json_) as s:
        ops = build() if build else None
        results: list[dict[str, Any]] = []
        for ref in refs:
            try:
                d: Derivative = runner(s.lib, ref, ops)
                results.append({"ref": ref, "asset_id": d.asset_id, "variant_id": d.variant_id, "label": d.label,
                                "path": d.path, "reused": d.reused, "new_asset": d.new_asset})  # fmt: skip
            except AssetForgeError as exc:
                if len(refs) == 1:
                    raise
                results.append({"ref": ref, "error": exc.to_dict()})
        failed = sum(1 for r in results if "error" in r)
        status = "ok" if not failed else "partial" if failed < len(results) else "error"

        def human(c: Console) -> None:
            for r in results:
                if "error" in r:
                    c.print(f"[red]{r['ref']}[/red]: {escape(r['error']['message'])}")
                else:
                    verb = "exists" if r["reused"] else "new asset" if r["new_asset"] else "new variant"
                    c.print(f"{r['asset_id']} [cyan]{r['label']}[/cyan] {r['variant_id']} ({verb})  {r['path']}")

        s.out.emit({"count": len(results), "results": results}, human, status=status)
        if failed:
            raise typer.Exit(int(ExitCode.PARTIAL if failed < len(results) else ExitCode.ERROR))


# ---------------------------------------------------------------------- image process

ImageRefs = Annotated[list[str], typer.Argument(help="Assets to process (each gets a derivative).")]


@image_app.command("process")
def image_process(
    ctx: typer.Context,
    refs: ImageRefs,
    preset: Annotated[str | None, typer.Option(help=f"One of: {', '.join(image_proc.PRESETS)}.")] = None,
    remove_background: Annotated[bool, typer.Option("--remove-background", help="Key out a flat background.")] = False,
    key: Annotated[str | None, typer.Option(help="Background colour for --remove-background, e.g. #FF00FF.")] = None,
    trim: Annotated[bool, typer.Option("--trim", help="Trim transparent (or flat) padding.")] = False,
    trim_margin: Annotated[int, typer.Option(help="Pixels kept around the subject when trimming.")] = 0,
    crop: Annotated[str | None, typer.Option(help="x,y,width,height")] = None,
    flip: Annotated[str | None, typer.Option(help="horizontal or vertical")] = None,
    rotate: Annotated[int | None, typer.Option(help="90, 180 or 270 (clockwise)")] = None,
    hue: Annotated[float | None, typer.Option(help="Hue shift in degrees.")] = None,
    saturation: Annotated[float | None, typer.Option(help="Saturation multiplier (1 = unchanged).")] = None,
    brightness: Annotated[float | None, typer.Option(help="Brightness multiplier.")] = None,
    contrast: Annotated[float | None, typer.Option(help="Contrast multiplier.")] = None,
    tint: Annotated[str | None, typer.Option(help="Tint colour #RRGGBB (keeps shading).")] = None,
    tint_strength: Annotated[float, typer.Option(help="0..1")] = 0.5,
    grayscale: Annotated[bool, typer.Option("--grayscale")] = False,
    resize: Annotated[str | None, typer.Option(help="WxH, Wx or xH (aspect is always preserved).")] = None,
    mode: Annotated[str, typer.Option(help="contain (fit), cover (fill+crop) or pad (fit+pad to exact size).")] = "contain",
    scale: Annotated[float | None, typer.Option(help="Scale factor, e.g. 0.5 or 2.")] = None,
    nearest: Annotated[bool, typer.Option("--nearest", help="Nearest-neighbour resampling (pixel art).")] = False,
    no_upscale: Annotated[bool, typer.Option("--no-upscale", help="Never enlarge.")] = False,
    pad: Annotated[int | None, typer.Option(help="Add a margin of N pixels.")] = None,
    background: Annotated[str | None, typer.Option(help="Pad/canvas colour (default transparent).")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="png, webp or jpg.")] = None,
    quality: Annotated[int | None, typer.Option(help="webp/jpg quality (webp is lossless without it).")] = None,
    ops_file: Annotated[Path | None, typer.Option("--ops-file", help='JSON list of ops (or {"ops": [...]}).')] = None,
    variant: Annotated[str | None, typer.Option(help="Source variant ID or label (default: primary).")] = None,
    label: Annotated[str | None, typer.Option(help="Label for the new variant (default from the ops).")] = None,
    primary: Annotated[bool, typer.Option("--primary", help="Make the result the asset's primary file.")] = False,
    new_asset: Annotated[bool, typer.Option("--new-asset", help="Create a new linked asset instead of a variant.")] = False,
    name: Annotated[str | None, typer.Option(help="Name for --new-asset.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Create processed image derivatives (resize, trim, recolor, convert…). Originals are never changed."""
    resample = "nearest" if nearest else "lanczos"
    flag_ops: list[dict[str, Any]] = []
    if remove_background:
        flag_ops.append({"op": "remove_background", **({"key": key} if key else {})})
    if trim:
        flag_ops.append({"op": "trim", "margin": trim_margin})
    if crop:
        try:
            x, y, w, h = (int(v) for v in crop.split(","))
        except ValueError as exc:
            raise typer.BadParameter("--crop expects x,y,width,height") from exc
        flag_ops.append({"op": "crop", "x": x, "y": y, "width": w, "height": h})
    if flip:
        flag_ops.append({"op": "flip", "axis": flip})
    if rotate:
        flag_ops.append({"op": "rotate", "degrees": rotate})
    recolor = {k: v for k, v in {"hue": hue, "saturation": saturation, "brightness": brightness,
                                 "contrast": contrast, "tint": tint}.items() if v is not None}  # fmt: skip
    if grayscale:
        recolor["grayscale"] = True
    if recolor:
        if tint:
            recolor["tint_strength"] = tint_strength
        flag_ops.append({"op": "recolor", **recolor})
    if resize:
        w_text, _, h_text = resize.lower().partition("x")
        try:
            dims = {"width": int(w_text) if w_text else None, "height": int(h_text) if h_text else None}
        except ValueError as exc:
            raise typer.BadParameter("--resize expects WxH, Wx or xH") from exc
        op = {"op": "resize", **{k: v for k, v in dims.items() if v}, "mode": mode, "resample": resample}
        if background:
            op["background"] = background
        if no_upscale:
            op["upscale"] = False
        flag_ops.append(op)
    if scale:
        flag_ops.append({"op": "scale", "factor": scale, "resample": resample})
    if pad:
        flag_ops.append({"op": "pad", "margin": pad, **({"background": background} if background else {})})
    fmt = None
    if format_:
        fmt = {"op": "format", "format": format_, **({"quality": quality} if quality else {})}
    _run_batch(
        ctx, json_, refs,
        lambda lib, ref, ops: image_proc.process_image_asset(
            lib, ref, ops, variant=variant, label=label, make_primary=primary, new_asset=new_asset, name=name),
        build=lambda: _assemble(image_proc.expand_preset(preset) if preset else [], flag_ops, _ops_file(ops_file), fmt),
    )  # fmt: skip


# ---------------------------------------------------------------------- audio process


@audio_app.command("process")
def audio_process(
    ctx: typer.Context,
    refs: Annotated[list[str], typer.Argument(help="Audio assets to process.")],
    preset: Annotated[str | None, typer.Option(help=f"One of: {', '.join(audio_proc.PRESETS)}.")] = None,
    trim_silence: Annotated[bool, typer.Option("--trim-silence", help="Remove leading/trailing silence.")] = False,
    threshold_db: Annotated[float, typer.Option(help="Silence threshold for --trim-silence.")] = -50.0,
    cut: Annotated[str | None, typer.Option(help="start_ms,end_ms")] = None,
    gain: Annotated[float | None, typer.Option(help="Gain in dB.")] = None,
    normalize: Annotated[str | None, typer.Option(help="lufs or peak.")] = None,
    target: Annotated[float | None, typer.Option(help="Normalization target (LUFS or dBFS).")] = None,
    ceiling: Annotated[float, typer.Option(help="Peak ceiling in dBFS for normalization.")] = -1.0,
    fade_in: Annotated[float | None, typer.Option(help="Fade-in in ms.")] = None,
    fade_out: Annotated[float | None, typer.Option(help="Fade-out in ms.")] = None,
    mono: Annotated[bool | None, typer.Option("--mono/--stereo", help="Mix down to mono or up to stereo.")] = None,
    sample_rate: Annotated[int | None, typer.Option(help="Resample to this rate.")] = None,
    loop_crossfade: Annotated[float | None, typer.Option(help="Make a seamless loop with this crossfade (ms).")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="wav, ogg or mp3.")] = None,
    quality: Annotated[float | None, typer.Option(help="ogg/mp3 quality 0..1.")] = None,
    ops_file: Annotated[Path | None, typer.Option("--ops-file", help="JSON list of ops.")] = None,
    variant: Annotated[str | None, typer.Option(help="Source variant ID or label (default: primary).")] = None,
    label: Annotated[str | None, typer.Option(help="Label for the new variant.")] = None,
    primary: Annotated[bool, typer.Option("--primary", help="Make the result the primary file.")] = False,
    new_asset: Annotated[bool, typer.Option("--new-asset", help="Create a new linked asset.")] = False,
    name: Annotated[str | None, typer.Option(help="Name for --new-asset.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Finish audio: trim silence, normalize loudness, fade, mono/stereo, resample, loop, convert."""
    flag_ops: list[dict[str, Any]] = []
    if trim_silence:
        flag_ops.append({"op": "trim_silence", "threshold_db": threshold_db})
    if cut:
        try:
            start, end = (float(v) for v in cut.split(","))
        except ValueError as exc:
            raise typer.BadParameter("--cut expects start_ms,end_ms") from exc
        flag_ops.append({"op": "cut", "start_ms": start, "end_ms": end})
    if mono is not None:
        flag_ops.append({"op": "channels", "count": 1 if mono else 2})
    if sample_rate:
        flag_ops.append({"op": "resample", "rate": sample_rate})
    if gain is not None:
        flag_ops.append({"op": "gain", "db": gain})
    if normalize or target is not None:
        mode = normalize or "lufs"
        flag_ops.append({"op": "normalize", "mode": mode, "target": target if target is not None else
                         (-16.0 if mode == "lufs" else -1.0), "ceiling_db": ceiling})  # fmt: skip
    if loop_crossfade:
        flag_ops.append({"op": "loop", "crossfade_ms": loop_crossfade})
    if fade_in or fade_out:
        flag_ops.append({"op": "fade", "in_ms": fade_in or 0, "out_ms": fade_out or 0})
    fmt = None
    if format_:
        fmt = {"op": "format", "format": format_, **({"quality": quality} if quality is not None else {})}
    _run_batch(
        ctx, json_, refs,
        lambda lib, ref, ops: audio_proc.process_audio_asset(
            lib, ref, ops, variant=variant, label=label, make_primary=primary, new_asset=new_asset, name=name),
        build=lambda: _assemble(audio_proc.expand_preset(preset) if preset else [], flag_ops, _ops_file(ops_file), fmt),
    )  # fmt: skip


def register(app: typer.Typer) -> None:
    app.add_typer(review_app, name="review")

    @app.command("process")
    def process(
        ctx: typer.Context,
        refs: Annotated[list[str], typer.Argument(help="Image or audio assets.")],
        preset: Annotated[str | None, typer.Option(help="Any image or audio preset name.")] = None,
        ops_file: Annotated[Path | None, typer.Option("--ops-file", help="JSON list of ops.")] = None,
        variant: Annotated[str | None, typer.Option(help="Source variant (default: primary).")] = None,
        label: Annotated[str | None, typer.Option(help="Label for the new variant.")] = None,
        primary: Annotated[bool, typer.Option("--primary")] = False,
        new_asset: Annotated[bool, typer.Option("--new-asset")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Create a processed derivative of any asset; image vs audio is detected per asset."""
        if not preset and not ops_file:
            raise typer.BadParameter("give --preset or --ops-file (or use `image process` / `audio process`)")

        def run(lib: Library, ref: str, file_ops: list[dict[str, Any]]) -> Derivative:
            asset = lib.get_asset(ref)
            kind = (asset.primary_variant.file.kind if asset.primary_variant else None)
            module = image_proc if kind == FileKind.IMAGE else audio_proc if kind == FileKind.AUDIO else None
            if module is None:
                raise InvalidInputError(f"{asset.id} is neither a raster image nor audio")
            ops = _assemble(module.expand_preset(preset) if preset else [], [], file_ops, None)
            fn = module.process_image_asset if module is image_proc else module.process_audio_asset  # type: ignore[attr-defined]
            return fn(lib, ref, ops, variant=variant, label=label, make_primary=primary, new_asset=new_asset)

        _run_batch(ctx, json_, refs, run, build=lambda: _ops_file(ops_file))

    @app.command("preview")
    def preview(
        ctx: typer.Context,
        refs: Annotated[list[str] | None, typer.Argument(help="Assets to show (or select with the options).")] = None,
        collection: Annotated[str | None, typer.Option(help="Show a collection.")] = None,
        job: Annotated[str | None, typer.Option(help="Show the assets a generation job produced.")] = None,
        query: Annotated[str | None, typer.Option("--query", "-q", help="Show search results.")] = None,
        type_: Annotated[list[AssetType] | None, typer.Option("--type")] = None,
        status: Annotated[list[Status] | None, typer.Option("--status")] = None,
        project: Annotated[str | None, typer.Option()] = None,
        style: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(help="Maximum assets.")] = 24,
        columns: Annotated[int, typer.Option(help="Grid columns.")] = 4,
        cell: Annotated[int, typer.Option(help="Thumbnail size in pixels.")] = 256,
        out: Annotated[Path | None, typer.Option(help="Output PNG (default: library previews/sheets/).")] = None,
        html: Annotated[bool, typer.Option("--html", help="Also write an HTML gallery with audio players.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Render a contact sheet (PNG) and optional HTML gallery for review by people or vision models."""
        with session(ctx, json_) as s:
            lib = s.lib
            if refs:
                ids, title = [lib.resolve_asset_id(r) for r in refs], "Selected assets"
            elif collection:
                c, items = coll.collection_assets(lib, collection)
                ids, title = [a.id for a in items], f"Collection {c.name}"
            elif job:
                j = jobs.get_job(lib, job)
                ids = j.result.get("asset_ids") or ([j.asset_id] if j.asset_id else [])
                title = f"Job {j.id}: {j.request.get('request', j.request).get('prompt', j.kind)}"[:90]
            else:
                q = SearchQuery(text=query or "", types=type_ or [], statuses=status or [], project=project,
                                style=style, limit=limit)  # fmt: skip
                ids, title = [h.id for h in search(lib, q).results], f"Search: {query or 'latest'}"
            ids = ids[:limit]
            if not ids:
                raise NotFoundError("No assets to preview")
            assets = [lib.get_asset(i) for i in ids]
            sheet = out or previews.default_sheet_path(lib, ids, title)
            previews.contact_sheet(lib, assets, sheet, columns=columns, cell=cell, title=title)
            gallery = previews.gallery_html(lib, assets, sheet.with_suffix(".html"), title=title) if html else None
            s.out.emit(
                {"sheet": str(sheet), "html": str(gallery) if gallery else None, "count": len(assets),
                 "assets": previews.describe(assets)},  # fmt: skip
                lambda c: c.print(f"Contact sheet with {len(assets)} asset(s): {sheet}" + (f"\nGallery: {gallery}" if gallery else "")),
            )

    @app.command("qc")
    def qc_command(
        ctx: typer.Context,
        refs: Annotated[list[str] | None, typer.Argument(help="Assets to check.")] = None,
        collection: Annotated[str | None, typer.Option(help="Check a whole collection.")] = None,
        status: Annotated[list[Status] | None, typer.Option("--status", help="Check all assets with this status.")] = None,
        variant: Annotated[str | None, typer.Option(help="Variant ID or label to check (default: primary).")] = None,
        strict: Annotated[bool, typer.Option("--strict", help="Exit non-zero if any asset has QC errors.")] = False,
        json_: JsonOpt = False,
    ) -> None:
        """Run quality checks (transparency, padding, blank images, clipping, silence, loudness, loop seams)."""
        with session(ctx, json_) as s:
            lib = s.lib
            if refs:
                ids = [lib.resolve_asset_id(r) for r in refs]
            elif collection:
                ids = [a.id for a in coll.collection_assets(lib, collection)[1]]
            elif status:
                marks = ",".join("?" * len(status))
                ids = [r[0] for r in lib.conn.execute(f"SELECT id FROM assets WHERE status IN ({marks}) ORDER BY id",
                                                      [st.value for st in status])]  # fmt: skip
            else:
                raise InvalidInputError("Name assets, or pass --collection or --status")
            reports = [qc.check_asset(lib, i, variant=variant) for i in ids]
            failing = [r for r in reports if not r["ok"]]

            def human(c: Console) -> None:
                table = Table(header_style="bold")
                for column in ("Asset", "Name", "Variant", "Result", "Errors", "Warnings"):
                    table.add_column(column)
                for r in reports:
                    table.add_row(r["asset_id"], escape(r["name"]), r["label"] or "-",
                                  "[green]ok[/green]" if r["ok"] else "[red]FAIL[/red]",
                                  ", ".join(r["errors"]), ", ".join(r["warnings"]))  # fmt: skip
                c.print(table)

            s.out.emit({"ok": not failing, "count": len(reports), "failing": len(failing), "reports": reports}, human)
            if strict and failing:
                raise typer.Exit(int(ExitCode.ERROR))

    @app.command("lineage")
    def lineage_command(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument()],
        depth: Annotated[int, typer.Option(help="How many generations to follow.")] = 10,
        json_: JsonOpt = False,
    ) -> None:
        """Show where an asset came from, what was derived from it, and its variant tree."""
        with session(ctx, json_) as s:
            graph = lineage_mod.lineage(s.lib, ref, depth=depth)

            def human(c: Console) -> None:
                root = Tree(f"[bold]{graph['asset_id']}[/bold] {escape(graph['name'])} ({graph['origin']}, {graph['status']})")
                if graph["ancestors"]:
                    _tree(root.add("[dim]came from[/dim]"), graph["ancestors"], "parents")
                if graph["descendants"]:
                    _tree(root.add("[dim]used to make[/dim]"), graph["descendants"], "children")
                variants = root.add("[dim]variants[/dim]")
                _variant_tree(variants, graph["variants"])
                c.print(root)

            s.out.emit({"lineage": graph}, human)

    @app.command("link")
    def link(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="The newer asset.")],
        to: Annotated[str, typer.Option("--to", help="The asset it relates to.")],
        relation: Annotated[LineageRelation, typer.Option(help="How REF relates to --to.")] = LineageRelation.DERIVED_FROM,
        json_: JsonOpt = False,
    ) -> None:
        """Record a lineage relation by hand, e.g. an imported edit of an older asset."""
        with session(ctx, json_) as s:
            s.lib.add_lineage(ref, to, relation)
            a, b = s.lib.resolve_asset_id(ref), s.lib.resolve_asset_id(to)
            s.out.emit({"link": {"asset_id": a, "relation": relation.value, "related_asset_id": b}},
                       lambda c: c.print(f"{a} {relation.value} {b}"))  # fmt: skip


def _tree(node: Tree, items: list[dict[str, Any]], key: str) -> None:
    for item in items:
        child = node.add(f"{item['relation']}: {item['asset_id']} {escape(item['name'])} ({item['status']})")
        _tree(child, item.get(key, []), key)


def _variant_tree(node: Tree, items: list[dict[str, Any]]) -> None:
    for v in items:
        star = " [green]*primary[/green]" if v["primary"] else ""
        ops = f" ← {', '.join(v['ops'])}" if v["ops"] else ""
        _variant_tree(node.add(f"{v['label']} {v['variant_id']} {v['file']}{ops}{star}"), v["children"])


# ---------------------------------------------------------------------- review


def _review_simple(status: Status, verb: str) -> Any:
    def command(
        ctx: typer.Context,
        refs: Annotated[list[str], typer.Argument(help="Assets.")],
        note: Annotated[str | None, typer.Option(help="Reason, kept in the asset's history.")] = None,
        json_: JsonOpt = False,
    ) -> None:
        with session(ctx, json_) as s:
            ids = s.lib.set_status(refs, status, note=note)
            s.out.emit({"review": {"status": status.value, "asset_ids": ids}},
                       lambda c: c.print(f"Marked {len(ids)} asset(s) {status.value}: {', '.join(ids)}"))  # fmt: skip

    command.__doc__ = verb
    return command


review_app.command("reject")(_review_simple(Status.REJECTED, "Reject assets (hidden from default search)."))
@review_app.command("archive")
def review_archive(
    ctx: typer.Context,
    refs: Annotated[list[str], typer.Argument(help="Assets to archive.")],
    note: Annotated[str | None, typer.Option(help="Reason, kept in the asset's history.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Archive assets (hidden from views and default search; nothing is deleted). A sheet's frames go with it."""
    with session(ctx, json_) as s:
        result = review_mod.archive(s.lib, refs, note=note)
        s.out.emit({"review": result}, lambda c: c.print(
            f"Archived {', '.join(result['asset_ids'])}" + (f" and {len(result['parts'])} part(s)" if result["parts"] else "")))
review_app.command("candidate")(_review_simple(Status.CANDIDATE, "Send assets back to candidate status."))


@review_app.command("restore")
def review_restore(
    ctx: typer.Context,
    refs: Annotated[list[str], typer.Argument(help="Archived assets to bring back.")],
    note: Annotated[str | None, typer.Option(help="Reason, kept in the asset's history.")] = None,
    json_: JsonOpt = False,
) -> None:
    """Un-archive assets, returning each to the status it had before it was archived."""
    with session(ctx, json_) as s:
        result = review_mod.restore(s.lib, refs, note=note)
        s.out.emit({"review": result}, lambda c: c.print(
            ", ".join(f"{a} → {st}" for a, st in result["restored"].items()) or "Nothing was archived"))


@review_app.command("approve")
def review_approve(
    ctx: typer.Context,
    refs: Annotated[list[str], typer.Argument(help="Assets to approve.")],
    note: Annotated[str | None, typer.Option(help="Reason, kept in the asset's history.")] = None,
    primary: Annotated[str | None, typer.Option(help="Make this variant the primary file first.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Approve even if QC finds errors.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Approve assets after QC passes (blank/empty images, silent/clipped audio block approval)."""
    with session(ctx, json_) as s:
        result = review_mod.approve(s.lib, refs, note=note, force=force, primary=primary)
        s.out.emit({"review": result}, lambda c: c.print(f"Approved {', '.join(result['asset_ids'])}"))


@review_app.command("pick")
def review_pick(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="The candidate to keep.")],
    note: Annotated[str | None, typer.Option()] = None,
    primary: Annotated[str | None, typer.Option(help="Make this variant the primary file first.")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    json_: JsonOpt = False,
) -> None:
    """Approve one candidate and reject the other candidates from the same generation job."""
    with session(ctx, json_) as s:
        result = review_mod.pick(s.lib, ref, note=note, force=force, primary=primary)
        s.out.emit({"review": result}, lambda c: c.print(
            f"Approved {result['asset_ids'][0]}; rejected {', '.join(result['rejected']) or 'nothing'}"))


@review_app.command("queue")
def review_queue(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option()] = None,
    type_: Annotated[list[AssetType] | None, typer.Option("--type")] = None,
    style: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option()] = 50,
    json_: JsonOpt = False,
) -> None:
    """Candidates waiting for review, oldest first, with their last QC result."""
    with session(ctx, json_) as s:
        items = review_mod.queue(s.lib, project=project, types=type_, style=style, limit=limit)
        s.out.emit(
            {"count": len(items), "queue": [{**dump(a), "qc": q} for a, q in items]},
            lambda c: render_assets(c, [a for a, _ in items], title="Review queue"),
        )



