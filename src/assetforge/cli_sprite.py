"""`assetforge sprite …`: turn sheets into aligned, game-ready frames and animations."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from assetforge.cli_common import JsonOpt, session
from assetforge.core.models import AssetType
from assetforge.errors import InvalidInputError
from assetforge.image.sprites import SliceOutcome, assemble_assets, parse_animation, slice_asset

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
    for f in o.frames:
        c.print(f"  {f['asset_id']}  {f['name']:<28} {escape(f['path'] or '')}")


@sprite_app.command("slice")
def slice_(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Sheet asset (ID or key).")],
    grid: Annotated[str, typer.Option(help="Columns x rows, e.g. 4x2.")],
    names: Annotated[str | None, typer.Option(help="Comma-separated frame names, row by row.")] = None,
    animation: Annotated[list[str] | None, typer.Option("--animation", "-a", help="NAME=START-END[@FPS][:once], cells 0-based (repeatable).")] = None,
    cell: Annotated[int | None, typer.Option(help="Square cell size in px (default: fit the largest frame).")] = None,
    anchor: Annotated[str, typer.Option(help="bottom (feet on one baseline) or center.")] = "bottom",
    margin: Annotated[int, typer.Option(help="Empty pixels kept around the subject in its cell.")] = 4,
    base: Annotated[str | None, typer.Option(help="Frame name prefix (default: the sheet name).")] = None,
    frame_type: Annotated[AssetType | None, typer.Option("--frame-type", help="Type of the frame assets (default animation/sprite).")] = None,
    json_: JsonOpt = False,
) -> None:
    """Cut a grid sheet into frames scaled by one shared factor and aligned on a common anchor.

    Frames become assets named <base>_<animation>_<nn> (atlas export turns them into animations), the sheet
    gets an aligned variant, and `metadata.animation` records cell size, anchor, fps and loop.
    """
    with session(ctx, json_) as s:
        if anchor not in ("bottom", "center"):
            raise InvalidInputError("--anchor must be bottom or center")
        columns, rows = parse_grid(grid)
        animations = dict(parse_animation(a) for a in animation or [])
        outcome = slice_asset(s.lib, ref, columns=columns, rows=rows, names=[n.strip() for n in names.split(",")]
                              if names else None, animations=animations, cell=cell, anchor=anchor,  # type: ignore[arg-type]
                              margin=margin, base=base, frame_type=frame_type)  # fmt: skip
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
