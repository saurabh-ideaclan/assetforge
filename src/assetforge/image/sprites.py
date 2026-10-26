"""Game-ready sprite frames: slice a generated sheet into aligned frames, or assemble frames into a sheet.

Independently generated images rarely animate well, so the practical route is one image call that
draws every pose on a grid (same model pass → same design and lighting), then this module:

1. finds each subject by alpha connected components (dilated, so thin parts such as antennae stay
   attached) and assigns it to its grid cell by centroid; stray specks are dropped, and small pieces
   beside or above the main subject (a sweat drop, a spark) are kept, while anything entirely below
   it (a caption the model added) is not;
2. removes key-colour spill on the edge pixels when the sheet was keyed (magenta or green);
3. scales **every frame by one shared factor**, so a character never changes size between frames;
4. pads each frame into a uniform cell on a common anchor (feet on one baseline, or centred).

The result is frame assets named `<base>_<animation>_<nn>` (the exporter turns those into atlas
animations), an aligned sheet variant, and `metadata.animation` with cell size, anchor, fps and loop.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from scipy import ndimage

from assetforge.core import collections as collections_mod
from assetforge.core.catalog import Library
from assetforge.core.models import AssetType, LineageRelation, NewAsset, Origin
from assetforge.core.util import normalize_name, slugify
from assetforge.errors import InvalidInputError

Anchor = Literal["bottom", "center"]
ALPHA_ON = 24
SPECK_SHARE = 0.004  # blobs smaller than this share of a cell are noise


class AnimationSpec(BaseModel):
    frames: list[int] = Field(description="Cell indexes (0-based, row by row) in playback order")
    fps: float = Field(10, gt=0, le=60)
    loop: bool = True


_ANIM = re.compile(r"^(?P<name>[A-Za-z0-9 _-]+)=(?P<start>\d+)(?:-(?P<end>\d+))?(?:@(?P<fps>[\d.]+))?(?P<once>:once)?$")


def parse_animation(text: str) -> tuple[str, AnimationSpec]:
    """`walk=0-3@8` → frames 0..3 at 8 fps, looping; add `:once` for a one-shot (e.g. `die=8-11@10:once`)."""
    match = _ANIM.match(text.strip())
    if not match:
        raise InvalidInputError(f"Bad animation {text!r}; use NAME=START-END[@FPS][:once], e.g. walk=0-3@8")
    start, end = int(match["start"]), int(match["end"] or match["start"])
    if end < start:
        raise InvalidInputError(f"Animation {text!r} ends before it starts")
    return slugify(match["name"], "_"), AnimationSpec(frames=list(range(start, end + 1)),
                                                      fps=float(match["fps"] or 10), loop=not match["once"])  # fmt: skip


@dataclass
class Blob:
    mask: np.ndarray
    area: int
    box: tuple[int, int, int, int]  # x0, y0, x1, y1
    cx: float
    cy: float


@dataclass
class SliceReport:
    expected: int
    found: int
    empty_cells: list[int] = field(default_factory=list)
    merged_cells: list[int] = field(default_factory=list)
    dropped_pieces: int = 0
    height_spread: float = 0.0
    scale: float = 1.0
    cell: tuple[int, int] = (0, 0)
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"expected": self.expected, "found": self.found, "empty_cells": self.empty_cells,
                "merged_cells": self.merged_cells, "dropped_pieces": self.dropped_pieces,
                "height_spread": round(self.height_spread, 3), "scale": round(self.scale, 4),
                "cell": list(self.cell), "issues": self.issues}  # fmt: skip


def _premul_resize(image: Image.Image, size: tuple[int, int], *, nearest: bool = False) -> Image.Image:
    if image.size == size:
        return image
    if nearest:
        return image.resize(size, Image.Resampling.NEAREST)
    return image.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _blobs(rgba: np.ndarray, min_area: int, dilate: int) -> list[Blob]:
    mask = rgba[..., 3] > ALPHA_ON
    grown = ndimage.binary_dilation(mask, iterations=dilate) if dilate else mask
    labels, count = ndimage.label(grown)
    found = []
    for index, region in enumerate(ndimage.find_objects(labels), start=1):
        if region is None:
            continue
        local = (labels[region] == index) & mask[region]
        area = int(local.sum())
        if area < min_area:
            continue
        ys, xs = np.nonzero(local)
        y0, x0 = region[0].start, region[1].start
        full = np.zeros_like(mask)
        full[region] = local
        found.append(Blob(full, area, (int(x0 + xs.min()), int(y0 + ys.min()), int(x0 + xs.max() + 1),
                                       int(y0 + ys.max() + 1)), float(x0 + xs.mean()), float(y0 + ys.mean())))  # fmt: skip
    return found


def _despill(rgba: np.ndarray, mask: np.ndarray, key: str | None) -> np.ndarray:
    out = rgba.copy()
    out[~mask] = 0
    if key not in ("magenta", "green"):
        return out
    solid = (out[..., 3] > ALPHA_ON) & mask
    edge = solid & ~ndimage.binary_erosion(solid, iterations=4)
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    if key == "magenta":
        spill = edge & (np.minimum(r, b) > g + 20)
        limit = np.maximum(g, g + 0.5 * np.abs(r - b))
        r[spill] = np.minimum(r[spill], limit[spill])
        b[spill] = np.minimum(b[spill], limit[spill])
    else:
        spill = edge & (g > np.maximum(r, b) + 20)
        g[spill] = np.maximum(r[spill], b[spill])
    return out


def key_name(background_rgb: list[int] | tuple[int, ...] | None) -> str | None:
    """'magenta'/'green' for a keyed sheet's background colour, else None."""
    if not background_rgb:
        return None
    r, g, b = background_rgb[:3]
    if r > 150 and b > 150 and g < 110:
        return "magenta"
    if g > 150 and r < 110 and b < 110:
        return "green"
    return None


def _ensure_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] < 255:
        return rgba
    from assetforge.image.transparency import remove_background

    return remove_background(rgba).image  # an opaque sheet on a flat backdrop


def place(frames: list[Image.Image], *, cell: int | tuple[int, int] | None = None, anchor: Anchor = "bottom",
          margin: int = 4, nearest: bool = False) -> tuple[list[Image.Image], float, tuple[int, int]]:  # fmt: skip
    """Scale all frames by one shared factor and pad them into equal cells on a common anchor."""
    max_w = max(f.width for f in frames)
    max_h = max(f.height for f in frames)
    if cell is None:
        cw, ch, scale = max_w + 2 * margin, max_h + 2 * margin, 1.0
    else:
        cw, ch = (cell, cell) if isinstance(cell, int) else cell
        if cw <= 2 * margin or ch <= 2 * margin:
            raise InvalidInputError(f"Cell {cw}x{ch} is too small for a {margin}px margin")
        scale = min((cw - 2 * margin) / max_w, (ch - 2 * margin) / max_h)
    placed = []
    for frame in frames:
        size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
        small = _premul_resize(frame, size, nearest=nearest)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        x = (cw - small.width) // 2
        y = ch - margin - small.height if anchor == "bottom" else (ch - small.height) // 2
        canvas.alpha_composite(small, (x, max(0, y)))
        placed.append(canvas)
    return placed, scale, (cw, ch)


def grid_sheet(frames: list[Image.Image], columns: int, padding: int = 0) -> Image.Image:
    cw, ch = frames[0].size
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cw + (columns + 1) * padding, rows * ch + (rows + 1) * padding), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        sheet.alpha_composite(frame, (padding + (i % columns) * (cw + padding), padding + (i // columns) * (ch + padding)))
    return sheet


def slice_image(image: Image.Image, columns: int, rows: int, *, cell: int | tuple[int, int] | None = None,
                anchor: Anchor = "bottom", margin: int = 4, key: str | None = None, dilate: int = 2,
                nearest: bool = False) -> tuple[list[Image.Image | None], SliceReport]:  # fmt: skip
    """Cut a grid sheet into aligned frames (None for an empty cell) plus a consistency report."""
    if columns < 1 or rows < 1:
        raise InvalidInputError("A grid needs at least 1 column and 1 row")
    rgba = np.asarray(_ensure_alpha(image), dtype=np.float64)
    h, w = rgba.shape[:2]
    cell_w, cell_h = w / columns, h / rows
    blobs = _blobs(rgba, max(12, int(cell_w * cell_h * SPECK_SHARE)), dilate)
    cells: list[list[Blob]] = [[] for _ in range(columns * rows)]
    for blob in blobs:
        cells[min(rows - 1, int(blob.cy / cell_h)) * columns + min(columns - 1, int(blob.cx / cell_w))].append(blob)

    report = SliceReport(expected=columns * rows, found=0)
    crops: list[Image.Image | None] = []
    for index, members in enumerate(cells):
        if not members:
            report.empty_cells.append(index)
            crops.append(None)
            continue
        members.sort(key=lambda b: -b.area)
        main = members[0]
        x0, y0, x1, y1 = main.box
        mask = main.mask.copy()
        for extra in members[1:]:
            ex0, ey0, ex1, ey1 = extra.box
            if ey0 >= y1 - 4 or extra.area < main.area * 0.01:  # below the subject (a caption) or a speck
                report.dropped_pieces += 1
                continue
            mask |= extra.mask
            x0, y0, x1, y1 = min(x0, ex0), min(y0, ey0), max(x1, ex1), max(y1, ey1)
            if index not in report.merged_cells:
                report.merged_cells.append(index)
        clean = _despill(rgba, mask, key)[y0:y1, x0:x1]
        crops.append(Image.fromarray(clean.round().clip(0, 255).astype(np.uint8), "RGBA"))
        report.found += 1

    present = [c for c in crops if c is not None]
    if not present:
        raise InvalidInputError("No subjects found on the sheet (is the background transparent or a flat colour?)")
    heights = [c.height for c in present]
    report.height_spread = (max(heights) - min(heights)) / max(heights)
    placed, report.scale, report.cell = place(present, cell=cell, anchor=anchor, margin=margin, nearest=nearest)
    it = iter(placed)
    frames = [next(it) if c is not None else None for c in crops]
    if report.found != report.expected:
        report.issues.append("missing_frames" if report.found < report.expected else "extra_frames")
    if report.height_spread > 0.25:
        report.issues.append("inconsistent_frame_size")
    if report.dropped_pieces:
        report.issues.append("dropped_pieces")
    return frames, report


# ---------------------------------------------------------------------- catalog


class SliceOutcome(BaseModel):
    sheet_asset_id: str
    collection: str
    frames: list[dict[str, Any]]
    animations: dict[str, dict[str, Any]]
    aligned_variant_id: str | None
    report: dict[str, Any]


def frame_names(base: str, count: int, *, names: list[str] | None = None,
                animations: dict[str, AnimationSpec] | None = None) -> list[str]:  # fmt: skip
    """`<base>_<animation>_<nn>` for cells in an animation, `<base>_<name>` with explicit names, else `<base>_<nn>`."""
    result = [f"{base}_{i + 1:02d}" for i in range(count)]
    if names:
        for i, name in enumerate(names[:count]):
            result[i] = normalize_name(f"{base}_{' '.join(slugify(name).split('-')[:4])}")
    for anim, spec in (animations or {}).items():
        for n, cell in enumerate(spec.frames, start=1):
            if cell >= count:
                raise InvalidInputError(f"Animation {anim} uses cell {cell}, but the sheet has {count} cells")
            result[cell] = normalize_name(f"{base}_{anim}_{n:02d}")
    if len(set(result)) != len(result):
        raise InvalidInputError("Frame names must be unique", names=result)
    return result


def _register_frames(lib: Library, sheet_id: str, frames: list[Image.Image | None], names: list[str], *,
                     frame_type: AssetType, anchor: Anchor, cell: tuple[int, int],
                     animations: dict[str, AnimationSpec], collection: str, extra: dict[str, Any],
                     sources: list[str | None] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    sheet = lib.get_asset(sheet_id)
    collection_id = collections_mod.ensure_collection(lib, collection, project=sheet.project).id
    membership = {cell_index: (anim, spec) for anim, spec in animations.items() for cell_index in spec.frames}
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="frames-"))
    out = []
    try:
        for index, (frame, name) in enumerate(zip(frames, names, strict=True)):
            if frame is None:
                continue
            path = tmp / f"{name}.png"
            frame.save(path, optimize=True)
            anim, spec = membership.get(index, (None, None))
            pivot_y = 0.5 if anchor == "center" else round((cell[1] - extra.get("margin", 4)) / cell[1], 4)
            info = {"sheet": sheet_id, "cell": index, "anchor": anchor, "pivot": [0.5, pivot_y],
                    "cell_size": list(cell), **extra}  # fmt: skip
            if anim and spec:
                info |= {"animation": anim, "index": spec.frames.index(index), "fps": spec.fps, "loop": spec.loop}
            reg = lib.register_file(path, NewAsset(
                name=name, type=frame_type, description=f"Frame {index + 1} of {sheet.name}" + (
                    f" ({anim} {spec.frames.index(index) + 1}/{len(spec.frames)})" if anim and spec else ""),
                scope=sheet.scope, project=sheet.project, style=sheet.style, category=sheet.category,
                subcategory=sheet.subcategory, theme=sheet.theme, genre=sheet.genre, status=sheet.status,
                origin=Origin.DERIVED, license=sheet.license, commercial_use=sheet.commercial_use,
                tags=[*[t for t in sheet.tags if t != "spritesheet"], "frame", *([anim] if anim else [])],
                metadata={"frame": info},
            ), label="original", params={"frame_of": sheet_id, "cell": index}, collection_id=collection_id)  # fmt: skip
            if reg.created:
                lib.add_lineage(reg.asset_id, (sources or [None] * len(frames))[index] or sheet_id,
                                LineageRelation.DERIVED_FROM)  # fmt: skip
            variant = lib.get_asset(reg.asset_id).primary_variant
            out.append({"cell": index, "name": name, "asset_id": reg.asset_id, "variant_id": reg.variant_id,
                        "path": variant.file.path if variant else None, "created": reg.created, "animation": anim})  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _add_sheet_variant(lib: Library, asset_id: str, sheet: Image.Image, label: str, params: dict[str, Any]) -> str:
    asset = lib.get_asset(asset_id)
    if existing := next((v for v in asset.variants if v.label == label), None):
        return existing.id
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="sheet-"))
    try:
        path = tmp / f"{label}.png"
        sheet.save(path, optimize=True)
        source = asset.primary_variant.id if asset.primary_variant else None
        return lib.add_variant(asset_id, path, label=label, parent_variant_id=source, params=params).id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _animation_meta(frames: list[dict[str, Any]], animations: dict[str, AnimationSpec]) -> dict[str, dict[str, Any]]:
    by_cell = {f["cell"]: f for f in frames}
    return {name: {"fps": spec.fps, "loop": spec.loop,
                   "frames": [by_cell[c]["name"] for c in spec.frames if c in by_cell],
                   "asset_ids": [by_cell[c]["asset_id"] for c in spec.frames if c in by_cell]}
            for name, spec in animations.items()}  # fmt: skip


def slice_asset(lib: Library, ref: str, *, columns: int, rows: int, names: list[str] | None = None,
                animations: dict[str, AnimationSpec] | None = None, cell: int | None = None,
                anchor: Anchor = "bottom", margin: int = 4, frame_type: AssetType | None = None,
                base: str | None = None, nearest: bool = False) -> SliceOutcome:  # fmt: skip
    """Slice a cataloged sheet into frame assets, an aligned sheet variant and animation metadata."""
    sheet = lib.get_asset(ref)
    primary = sheet.primary_variant
    if primary is None or primary.file.kind != "image":
        raise InvalidInputError(f"{sheet.id} is not a raster image")
    generator = sheet.metadata.get("generator") or {}
    key = key_name((generator.get("key") or {}).get("background_rgb"))
    nearest = nearest or (sheet.style or "") == "pixel-art"
    with Image.open(primary.file.path) as opened:
        frames, report = slice_image(opened.copy(), columns, rows, cell=cell, anchor=anchor, margin=margin, key=key,
                                     nearest=nearest)  # fmt: skip
    animations = animations or {}
    base = normalize_name(base or sheet.name.removesuffix("_sheet"))
    names_out = frame_names(base, columns * rows, names=names, animations=animations)
    frame_type = frame_type or (AssetType.ANIMATION if animations else AssetType.SPRITE)
    collection = f"{slugify(base)}-frames"
    registered = _register_frames(lib, sheet.id, frames, names_out, frame_type=frame_type, anchor=anchor,
                                  cell=report.cell, animations=animations, collection=collection,
                                  extra={"margin": margin})  # fmt: skip
    aligned = grid_sheet([f if f is not None else Image.new("RGBA", report.cell) for f in frames], columns)
    label = f"aligned_{columns}x{rows}_{report.cell[0]}x{report.cell[1]}_{anchor}"
    variant_id = _add_sheet_variant(lib, sheet.id, aligned, label, {"slice": {"columns": columns, "rows": rows,
                                                                              "anchor": anchor, "margin": margin}})  # fmt: skip
    anim_meta = _animation_meta(registered, animations)
    lib.merge_metadata(sheet.id, {"animation": {
        "columns": columns, "rows": rows, "cell": list(report.cell), "anchor": anchor, "collection": collection,
        "frames": [{"cell": f["cell"], "name": f["name"], "asset_id": f["asset_id"]} for f in registered],
        "animations": anim_meta, "aligned_variant_id": variant_id, "slice_report": report.as_dict(),
    }}, event="sliced")  # fmt: skip
    return SliceOutcome(sheet_asset_id=sheet.id, collection=collection, frames=registered, animations=anim_meta,
                        aligned_variant_id=variant_id, report=report.as_dict())  # fmt: skip


def assemble_assets(lib: Library, refs: list[str], *, name: str, columns: int | None = None, fps: float = 10,
                    loop: bool = True, cell: int | None = None, anchor: Anchor = "bottom", margin: int = 4,
                    project: str | None = None, frame_type: AssetType = AssetType.ANIMATION) -> SliceOutcome:  # fmt: skip
    """Normalize existing frame assets (shared scale, common anchor, equal cells) into an animation sheet."""
    if len(refs) < 2:
        raise InvalidInputError("Assemble needs at least two frames")
    assets = [lib.get_asset(r) for r in refs]
    images = []
    for a in assets:
        if a.primary_variant is None or a.primary_variant.file.kind != "image":
            raise InvalidInputError(f"{a.id} is not a raster image")
        with Image.open(a.primary_variant.file.path) as opened:
            image = _ensure_alpha(opened.copy())
        box = image.getchannel("A").point(lambda v: 255 if v > ALPHA_ON else 0).getbbox()
        images.append(image.crop(box) if box else image)
    nearest = all((a.style or "") == "pixel-art" for a in assets)
    placed, scale, cell_size = place(images, cell=cell, anchor=anchor, margin=margin, nearest=nearest)
    columns = columns or len(placed)
    sheet_image = grid_sheet(placed, columns)
    base = normalize_name(name)
    first = assets[0]
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="assemble-"))
    try:
        path = tmp / f"{base}_sheet.png"
        sheet_image.save(path, optimize=True)
        reg = lib.register_file(path, NewAsset(
            name=f"{base}_sheet", type=AssetType.SPRITESHEET, description=f"{name} animation, {len(placed)} frames",
            project=project or first.project, style=first.style, theme=first.theme, genre=first.genre,
            category=first.category, status=first.status, origin=Origin.DERIVED, license=first.license,
            commercial_use=first.commercial_use, tags=["animation", "assembled"],
        ), label="original", params={"assembled_from": [a.id for a in assets]})  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    spec = AnimationSpec(frames=list(range(len(placed))), fps=fps, loop=loop)
    names = [normalize_name(f"{base}_{i + 1:02d}") for i in range(len(placed))]
    animations = {base: spec}
    registered = _register_frames(lib, reg.asset_id, placed, names, frame_type=frame_type, anchor=anchor, cell=cell_size,
                                  animations=animations, collection=f"{slugify(base)}-frames", extra={"margin": margin},
                                  sources=[a.id for a in assets])  # fmt: skip
    for source in assets:
        lib.add_lineage(reg.asset_id, source.id, LineageRelation.DERIVED_FROM)
    anim_meta = _animation_meta(registered, animations)
    report = SliceReport(expected=len(placed), found=len(placed), scale=scale, cell=cell_size)
    heights = [i.height for i in images]
    report.height_spread = (max(heights) - min(heights)) / max(heights)
    lib.merge_metadata(reg.asset_id, {"animation": {
        "columns": columns, "rows": (len(placed) + columns - 1) // columns, "cell": list(cell_size), "anchor": anchor,
        "collection": f"{slugify(base)}-frames", "animations": anim_meta, "slice_report": report.as_dict(),
        "frames": [{"cell": f["cell"], "name": f["name"], "asset_id": f["asset_id"]} for f in registered],
    }}, event="assembled")  # fmt: skip
    return SliceOutcome(sheet_asset_id=reg.asset_id, collection=f"{slugify(base)}-frames", frames=registered,
                        animations=anim_meta, aligned_variant_id=reg.variant_id, report=report.as_dict())  # fmt: skip
