"""Thumbnails, contact sheets and HTML galleries for human and vision-model review.

Thumbnails are cached under previews/thumbs/ by content hash (they are regenerable and never
cataloged). A contact sheet is one PNG grid with an ID, status, size or duration and QC issues
under each item. A vision-capable agent can read it in one look. The HTML gallery adds full-size
images, audio players and ready-to-copy review commands.
"""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from assetforge.core.catalog import Library
from assetforge.core.models import Asset, FileKind, Variant

THUMB_SIZE = 256
LABEL_HEIGHT = 64
CHECKER = (204, 204, 204)
STATUS_COLOURS = {"approved": (34, 139, 34), "candidate": (180, 120, 0), "rejected": (190, 30, 30),
                  "archived": (110, 110, 110)}  # fmt: skip


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def checkerboard(size: tuple[int, int], square: int = 12) -> Image.Image:
    board = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], square):
        for x in range(0, size[0], square):
            if (x // square + y // square) % 2:
                draw.rectangle((x, y, x + square - 1, y + square - 1), fill=CHECKER + (255,))
    return board


def waveform(path: Path, size: tuple[int, int]) -> Image.Image:
    from assetforge.audio.codecs import read_audio

    data, sr = read_audio(path, dtype="float32")
    mono = data.mean(axis=1)
    w, h = size
    image = Image.new("RGBA", size, (24, 28, 40, 255))
    draw = ImageDraw.Draw(image)
    if len(mono):
        columns = np.array_split(mono, w) if len(mono) >= w else [mono[i : i + 1] for i in range(len(mono))]
        mid = h // 2
        for x, chunk in enumerate(columns):
            if chunk.size:
                top, bottom = float(chunk.max()), float(chunk.min())
                draw.line((x, mid - top * (mid - 6), x, mid - bottom * (mid - 6)), fill=(110, 200, 255, 255))
    draw.text((6, h - 20), f"{len(mono) / sr:.2f}s · {sr // 1000}kHz", font=_font(13), fill=(230, 230, 230, 255))
    return image


def _placeholder(text: str, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGBA", size, (60, 60, 70, 255))
    ImageDraw.Draw(image).text((10, size[1] // 2 - 8), text, font=_font(16), fill=(240, 240, 240, 255))
    return image


def thumbnail(lib: Library, variant: Variant, size: int = THUMB_SIZE) -> Path:
    cache = lib.paths.previews / "thumbs" / variant.file.sha256[:2] / f"{variant.file.sha256}_{size}.png"
    if cache.is_file():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    path = Path(variant.file.path)
    try:
        if variant.file.kind == FileKind.IMAGE:
            with Image.open(path) as opened:
                image = opened.convert("RGBA")
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
        elif variant.file.kind == FileKind.AUDIO:
            image = waveform(path, (size, size // 2))
        else:
            image = _placeholder(variant.file.ext.lstrip(".").upper() or "FILE", (size, size // 2))
    except Exception:
        image = _placeholder("unreadable", (size, size // 2))
    tmp = cache.with_suffix(".tmp.png")
    image.save(tmp)
    tmp.replace(cache)
    return cache


def _facts(asset: Asset) -> str:
    v = asset.primary_variant
    if v is None:
        return "no file"
    f = v.file
    parts = [f.ext.lstrip(".")]
    if f.width and f.height:
        parts.append(f"{f.width}x{f.height}")
    if f.duration_ms is not None:
        parts.append(f"{f.duration_ms / 1000:.2f}s")
    if f.has_alpha:
        parts.append("alpha")
    return " ".join(parts)


def _qc_issues(asset: Asset) -> tuple[list[str], list[str]]:
    qc = asset.metadata.get("qc") or {}
    if asset.primary_variant and qc.get("variant_id") not in (None, asset.primary_variant.id):
        return [], []  # stale QC for a different variant
    return qc.get("errors", []), qc.get("warnings", [])


def contact_sheet(
    lib: Library, assets: list[Asset], out: Path, *, columns: int = 4, cell: int = THUMB_SIZE,
    title: str | None = None,
) -> Path:  # fmt: skip
    columns = max(1, min(columns, len(assets) or 1))
    rows = max(1, -(-len(assets) // columns))
    header = 36 if title else 0
    pad = 10
    sheet = Image.new("RGB", (columns * (cell + pad) + pad, header + rows * (cell + LABEL_HEIGHT + pad) + pad),
                      (245, 245, 247))  # fmt: skip
    draw = ImageDraw.Draw(sheet)
    if title:
        draw.text((pad, 8), title, font=_font(20), fill=(20, 20, 20))
    small, tiny = _font(14), _font(12)
    for i, asset in enumerate(assets):
        x = pad + (i % columns) * (cell + pad)
        y = header + pad + (i // columns) * (cell + LABEL_HEIGHT + pad)
        frame = checkerboard((cell, cell))
        if asset.primary_variant is not None:
            with Image.open(thumbnail(lib, asset.primary_variant, cell)) as thumb:
                thumb = thumb.convert("RGBA")
                frame.alpha_composite(thumb, ((cell - thumb.width) // 2, (cell - thumb.height) // 2))
        sheet.paste(frame.convert("RGB"), (x, y))
        status = asset.status.value
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=STATUS_COLOURS.get(status, (0, 0, 0)), width=3)
        ly = y + cell + 4
        draw.text((x, ly), f"{asset.id}  {status}", font=small, fill=STATUS_COLOURS.get(status, (0, 0, 0)))
        draw.text((x, ly + 17), asset.name[:34], font=tiny, fill=(30, 30, 30))
        errors, warnings = _qc_issues(asset)
        issue_text = ", ".join(errors + warnings)
        draw.text((x, ly + 32), (_facts(asset) + ("  ! " + issue_text if issue_text else ""))[:44], font=tiny,
                  fill=(190, 30, 30) if errors else (150, 95, 0) if warnings else (90, 90, 90))  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)
    return out


def gallery_html(lib: Library, assets: list[Asset], out: Path, *, title: str = "AssetForge review") -> Path:
    cards = []
    for asset in assets:
        v = asset.primary_variant
        if v is None:
            media = "<div class='missing'>no file</div>"
        elif v.file.kind == FileKind.AUDIO:
            media = (f"<img class='wave' src='{Path(thumbnail(lib, v)).as_uri()}' alt=''>"
                     f"<audio controls preload='none' src='{Path(v.file.path).as_uri()}'></audio>")  # fmt: skip
        elif v.file.kind == FileKind.IMAGE:
            media = f"<a href='{Path(v.file.path).as_uri()}'><img src='{Path(v.file.path).as_uri()}' alt=''></a>"
        else:
            media = f"<div class='missing'>{html.escape(v.file.ext)}</div>"
        errors, warnings = _qc_issues(asset)
        qc = "".join(f"<span class='err'>{html.escape(i)}</span>" for i in errors)
        qc += "".join(f"<span class='warn'>{html.escape(i)}</span>" for i in warnings)
        prompt = asset.metadata.get("generator", {}).get("prompt") or asset.description
        cards.append(f"""
<div class="card {asset.status.value}">
  <div class="media">{media}</div>
  <div class="meta">
    <b>{asset.id}</b> <span class="status">{asset.status.value}</span><br>
    {html.escape(asset.name)} · {asset.type.value} · {html.escape(_facts(asset))}<br>
    <small>{html.escape(prompt or "")[:180]}</small><br>{qc}
    <code>assetforge review approve {asset.id}</code>
  </div>
</div>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f4f4f6;margin:16px;color:#222}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
.card{{background:#fff;border-radius:8px;overflow:hidden;border:3px solid #ddd}}
.card.approved{{border-color:#2a8b2a}}.card.candidate{{border-color:#b47800}}.card.rejected{{border-color:#be1e1e}}
.media{{background:repeating-conic-gradient(#ccc 0 25%,#fff 0 50%) 50%/24px 24px;display:flex;
justify-content:center;align-items:center;min-height:200px}}
.media img{{max-width:100%;max-height:300px}}.media img.wave{{width:100%}}audio{{width:100%}}
.meta{{padding:8px;font-size:13px;line-height:1.5}}code{{display:block;margin-top:6px;background:#f0f0f0;
padding:3px 5px;border-radius:4px;font-size:11px}}
.err,.warn{{display:inline-block;margin:2px 4px 0 0;padding:0 6px;border-radius:8px;font-size:11px}}
.err{{background:#fde2e2;color:#a00}}.warn{{background:#fff1d6;color:#8a5a00}}.missing{{padding:40px}}
</style></head><body><h2>{html.escape(title)}</h2><div class="grid">{''.join(cards)}</div></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def default_sheet_path(lib: Library, asset_ids: list[str], label: str) -> Path:
    digest = hashlib.sha256(",".join(asset_ids).encode()).hexdigest()[:10]
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40] or "sheet"
    return lib.paths.previews / "sheets" / f"{safe}-{digest}.png"


def describe(assets: list[Asset]) -> list[dict[str, Any]]:
    out = []
    for a in assets:
        errors, warnings = _qc_issues(a)
        out.append({"asset_id": a.id, "name": a.name, "status": a.status.value, "type": a.type.value,
                    "path": a.path, "qc_errors": errors, "qc_warnings": warnings})  # fmt: skip
    return out
