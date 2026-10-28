"""`assetforge sprite …`: turn sheets into aligned, game-ready frames and animations."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import AssetType
from assetforge.errors import InvalidInputError
from assetforge.image.sprites import SliceOutcome, assemble_assets, parse_animation, plan_slice, slice_asset

sprite_app = typer.Typer(help="Slice sprite sheets into aligned frames; assemble frames into animations.",
                         no_args_is_help=True)  # fmt: skip


def parse_grid(text: str) -> tuple[int, int]:
    try:
        columns, rows = (int(x) for x in text.lower().split("x"))
    except ValueError:
        raise InvalidInputError(f"Grid must look like 4x2 (columns x rows), got {text!r}") from None
    return columns, rows


def render_outcome(c: Console, o: SliceOutcome) -> None:
    r = o.report
    c.print(f"[bold]{o.sheet_asset_id}[/bold]: {r['found']}/{r['expected']} frames, cell {r['cell'][0]}x{r['cell'][1]}, "
            f"scale {r['scale']:.3f}, collection [cyan]{o.collection}[/cyan]")  # fmt: skip
    if r["issues"]:
        c.print(f"[yellow]issues:[/yellow] {', '.join(r['issues'])}"
                + (f" (empty cells {r['empty_cells']})" if r["empty_cells"] else ""))  # fmt: skip
    for name, anim in o.animations.items():
        c.print(f"  animation [bold]{name}[/bold]: {len(anim['frames'])} frames @ {anim['fps']:g} fps"
                + (" loop" if anim["loop"] else " once"))  # fmt: skip
    if o.archived:
        c.print(f"  archived {len(o.archived)} frame(s) from the earlier slice")
    for f in o.frames:
        c.print(f"  {f['asset_id']}  {f['name']:<28} {escape(f['path'] or '')}")


@sprite_app.command("slice")
def slice_(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Sheet asset (ID or key).")],
    grid: Annotated[str | None, typer.Option(help="Columns x rows, e.g. 4x2 (default: from the request/prompt, or detected).")] = None,
    names: Annotated[str | None, typer.Option(help="Comma-separated frame names, row by row (default: from the prompt).")] = None,
    animation: Annotated[list[str] | None, typer.Option("--animation", "-a", help="NAME=START-END[@FPS][:once], cells 0-based (repeatable).")] = None,
    cell: Annotated[int | None, typer.Option(help="Square cell size in px (default: fit the largest frame).")] = None,
    anchor: Annotated[str, typer.Option(help="bottom (feet on one baseline) or center.")] = "bottom",
    fit: Annotated[str, typer.Option(help="shared (one scale: animations), each (fill each cell: icon/item sets) or auto.")] = "auto",
    margin: Annotated[int, typer.Option(help="Empty pixels kept around the subject in its cell.")] = 4,
    base: Annotated[str | None, typer.Option(help="Frame name prefix (default: the sheet name).")] = None,
    frame_type: Annotated[AssetType | None, typer.Option("--frame-type", help="Type of the frame assets.")] = None,
    allow_crowded: Annotated[bool, typer.Option("--allow-crowded", help="Slice even if a cell holds several subjects.")] = False,
    keep_previous: Annotated[bool, typer.Option("--keep-previous", help="Don't archive frames from an earlier slice.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the grid, subjects, names and issues; write nothing.")] = False,
    json_: JsonOpt = False,
) -> None:
    """Cut a sheet into aligned frames. The grid is read from how it was generated, its prompt, or the image.

    A grid that would glue two subjects into one frame is refused with the grid the sheet really has. Frames
    become assets (atlas export turns <base>_<anim>_<nn> into animations), the sheet gets an aligned variant, and
    re-slicing archives the frames of the earlier slice. `--fit each` suits icon/item sets; `shared` animations.
    """
    with session(ctx, json_) as s:
        if anchor not in ("bottom", "center"):
            raise InvalidInputError("--anchor must be bottom or center")
        if fit not in ("auto", "shared", "each"):
            raise InvalidInputError("--fit must be auto, shared or each")
        columns, rows = parse_grid(grid) if grid else (None, None)
        animations = dict(parse_animation(a) for a in animation or [])
        name_list = [n.strip() for n in names.split(",")] if names else None
        if dry_run:
            plan = plan_slice(s.lib, ref, columns=columns, rows=rows, names=name_list, animations=animations,
                              fit=fit, base=base, cell=cell, anchor=anchor, margin=margin)  # type: ignore[arg-type]  # fmt: skip

            def human(c: Console) -> None:
                src = {"request": "from how it was generated", "prompt": "from its prompt", "detected": "detected",
                       "given": "as given"}[plan.source]  # fmt: skip
                c.print(f"[bold]{plan.sheet_asset_id}[/bold]: {plan.columns}x{plan.rows} grid ({src}), "
                        f"{plan.subjects} subjects, fit {plan.fit}")  # fmt: skip
                if plan.report["issues"]:
                    c.print(f"[yellow]issues:[/yellow] {', '.join(plan.report['issues'])}")
                c.print("names: " + ", ".join(plan.names))

            s.out.emit({"plan": plan.model_dump(mode="json")}, human)
            return
        outcome = slice_asset(s.lib, ref, columns=columns, rows=rows, names=name_list, animations=animations,
                              cell=cell, anchor=anchor, margin=margin, base=base, frame_type=frame_type,  # type: ignore[arg-type]
                              fit=fit, allow_crowded=allow_crowded, replace_previous=not keep_previous)  # type: ignore[arg-type]  # fmt: skip
        s.out.emit({"slice": outcome.model_dump(mode="json")}, lambda c: render_outcome(c, outcome))


@sprite_app.command("assemble")
def assemble(
    ctx: typer.Context,
    refs: Annotated[list[str], typer.Argument(help="Frame assets in playback order.")],
    name: Annotated[str, typer.Option(help="Animation name, e.g. hero_walk.")],
    fps: Annotated[float, typer.Option(help="Playback speed.")] = 10,
    loop: Annotated[bool, typer.Option("--loop/--once", help="Loop or play once.")] = True,
    columns: Annotated[int | None, typer.Option(help="Sheet columns (default: one row).")] = None,
    cell: Annotated[int | None, typer.Option(help="Square cell size in px (default: fit the largest frame).")] = None,
    anchor: Annotated[str, typer.Option(help="bottom or center.")] = "bottom",
    margin: Annotated[int, typer.Option()] = 4,
    project: Annotated[str | None, typer.Option(help="Owning project (default: the first frame's).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Normalize separate frame images (shared scale, common anchor, equal cells) into an animation."""
    with session(ctx, json_) as s:
        if anchor not in ("bottom", "center"):
            raise InvalidInputError("--anchor must be bottom or center")
        outcome = assemble_assets(s.lib, refs, name=name, columns=columns, fps=fps, loop=loop, cell=cell,
                                  anchor=anchor, margin=margin, project=project)  # type: ignore[arg-type]  # fmt: skip
        data: dict[str, Any] = {"assemble": outcome.model_dump(mode="json")}
        s.out.emit(data, lambda c: render_outcome(c, outcome))
