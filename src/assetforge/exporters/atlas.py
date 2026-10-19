"""Deterministic texture-atlas packing that writes PixiJS/TexturePacker "hash" spritesheet JSON.

Frames are trimmed to their opaque bounds (the JSON's spriteSourceSize/sourceSize restore the
original placement), sorted by height and shelf-packed. Every candidate width is tried and the
smallest sheet wins. Frames that don't fit in one `max_size` sheet spill into extra pages linked
via `meta.related_multi_packs`, which PixiJS loads automatically. Frame names like `walk_01`,
`walk_02` become an `animations.walk` entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from assetforge.errors import InvalidInputError

_FRAME_NUMBER = re.compile(r"^(?P<base>.+?)[_-]?(?P<n>\d+)$")


@dataclass
class Frame:
    name: str
    image: Image.Image  # trimmed RGBA
    source_size: tuple[int, int]
    offset: tuple[int, int]  # trimmed box position inside the source image

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @property
    def trimmed(self) -> bool:
        return self.size != self.source_size


@dataclass
class Sheet:
    width: int
    height: int
    placements: list[tuple[Frame, int, int]] = field(default_factory=list)


def make_frame(name: str, image: Image.Image, *, trim: bool = True) -> Frame:
    rgba = image.convert("RGBA")
    if trim:
        alpha = np.asarray(rgba.getchannel("A"))
        if alpha.any():
            ys, xs = np.nonzero(alpha)
            box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            return Frame(name, rgba.crop(box), rgba.size, (box[0], box[1]))
    return Frame(name, rgba, rgba.size, (0, 0))


def _shelf(frames: list[Frame], width: int, padding: int, max_height: int | None) -> tuple[Sheet, list[Frame]]:
    """Place frames left to right on shelves; returns the sheet and frames that didn't fit."""
    sheet = Sheet(width, 0)
    x = y = padding
    shelf = 0
    rest: list[Frame] = []
    for frame in frames:
        w, h = frame.size
        if x + w + padding > width:
            x, y, shelf = padding, y + shelf + padding, 0
        if max_height is not None and y + h + padding > max_height:
            rest.append(frame)
            continue
        sheet.placements.append((frame, x, y))
        x += w + padding
        shelf = max(shelf, h)
    sheet.height = y + shelf + padding if sheet.placements else 0
    return sheet, rest


def _next_pot(n: int) -> int:
    return 1 << max(0, (n - 1).bit_length())


def pack(frames: list[Frame], *, max_size: int = 2048, padding: int = 2, power_of_two: bool = False) -> list[Sheet]:
    if not frames:
        return []
    names = [f.name for f in frames]
    if len(set(names)) != len(names):
        raise InvalidInputError("Atlas frame names must be unique")
    for f in frames:
        if f.size[0] + 2 * padding > max_size or f.size[1] + 2 * padding > max_size:
            raise InvalidInputError(f"Frame {f.name} ({f.size[0]}x{f.size[1]}) does not fit a {max_size}px atlas")
    ordered = sorted(frames, key=lambda f: (-f.size[1], -f.size[0], f.name))
    widest = max(f.size[0] for f in frames) + 2 * padding
    if power_of_two:
        widths = [w for w in (1 << k for k in range(4, 15)) if widest <= w <= max_size]
    else:
        widths = sorted({min(max_size, w) for w in range((widest + 15) // 16 * 16, max_size + 16, 16)})

    best: Sheet | None = None
    best_key: tuple[int, int] | None = None
    for width in widths:
        sheet, rest = _shelf(ordered, width, padding, max_size)
        if rest:
            continue
        if power_of_two:
            sheet.height = _next_pot(sheet.height)
        key = (sheet.width * sheet.height, abs(sheet.width - sheet.height))
        if best_key is None or key < best_key:
            best, best_key = sheet, key
    if best is not None:
        return [best]

    sheets = []  # multi-pack: full-width pages until everything is placed
    remaining = ordered
    while remaining:
        sheet, remaining = _shelf(remaining, max_size, padding, max_size)
        if power_of_two:
            sheet.height = _next_pot(sheet.height)
        sheets.append(sheet)
    return sheets


def render(sheet: Sheet) -> Image.Image:
    canvas = Image.new("RGBA", (sheet.width, sheet.height), (0, 0, 0, 0))
    for frame, x, y in sheet.placements:
        canvas.paste(frame.image, (x, y))
    return canvas


def animations(names: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        if m := _FRAME_NUMBER.match(name):
            groups.setdefault(m.group("base"), []).append((int(m.group("n")), name))
    return {base: [n for _, n in sorted(items)] for base, items in sorted(groups.items()) if len(items) >= 2}


def sheet_json(sheet: Sheet, image_name: str, *, related: list[str] | None = None,
               anims: dict[str, list[str]] | None = None) -> dict[str, Any]:  # fmt: skip
    frames = {}
    for frame, x, y in sorted(sheet.placements, key=lambda p: p[0].name):
        w, h = frame.size
        frames[frame.name] = {
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "rotated": False,
            "trimmed": frame.trimmed,
            "spriteSourceSize": {"x": frame.offset[0], "y": frame.offset[1], "w": w, "h": h},
            "sourceSize": {"w": frame.source_size[0], "h": frame.source_size[1]},
        }
    data: dict[str, Any] = {"frames": frames}
    own = {f.name for f, _, _ in sheet.placements}
    sheet_anims = {k: v for k, v in (anims or {}).items() if all(n in own for n in v)}
    if sheet_anims:
        data["animations"] = sheet_anims
    data["meta"] = {
        "app": "assetforge",
        "image": image_name,
        "format": "RGBA8888",
        "size": {"w": sheet.width, "h": sheet.height},
        "scale": "1",
    }
    if related:
        data["meta"]["related_multi_packs"] = related
    return data


@dataclass
class AtlasFile:
    path: Path  # relative to the export root
    data: bytes


def build_atlas(
    name: str,
    frames: list[Frame],
    *,
    formats: list[str],
    max_size: int = 2048,
    padding: int = 2,
    power_of_two: bool = False,
) -> tuple[list[AtlasFile], dict[str, Any]]:
    """Pack frames and return the image/JSON files to write plus a summary. One JSON per image format,
    named `<name>.<fmt>.json`, so the PixiJS resolver can pick a format from `<name>.{webp,png}.json`."""
    import io
    import json

    sheets = pack(frames, max_size=max_size, padding=padding, power_of_two=power_of_two)
    anims = animations([f.name for f in frames])
    files: list[AtlasFile] = []
    stems = [name if len(sheets) == 1 else f"{name}-{i}" for i in range(len(sheets))]
    for fmt in formats:
        for i, (sheet, stem) in enumerate(zip(sheets, stems, strict=True)):
            image_name = f"{stem}.{fmt}"
            buffer = io.BytesIO()
            if fmt == "webp":
                render(sheet).save(buffer, format="WEBP", lossless=True, method=6)
            else:
                render(sheet).save(buffer, format="PNG", optimize=True)
            files.append(AtlasFile(Path(image_name), buffer.getvalue()))
            related = [f"{s}.{fmt}.json" for j, s in enumerate(stems) if j != i] or None
            data = sheet_json(sheet, image_name, related=related, anims=anims)
            files.append(AtlasFile(Path(f"{stem}.{fmt}.json"), json.dumps(data, indent=2).encode()))
    summary = {
        "name": name,
        "pages": [{"stem": s, "width": sh.width, "height": sh.height, "frames": len(sh.placements)}
                  for s, sh in zip(stems, sheets, strict=True)],  # fmt: skip
        "frames": sorted(f.name for f in frames),
        "animations": anims,
        "formats": formats,
    }
    return files, summary
