"""Rasterize SVG (agent-written icons and UI) with resvg: a Rust renderer shipped as a wheel, no system libraries."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from assetforge.errors import InvalidInputError


def render_svg(path: Path, *, size: int | None = None, width: int | None = None, height: int | None = None
               ) -> Image.Image:  # fmt: skip
    """Render to RGBA. `size` fits the longest side; `width`/`height` set exact pixels (aspect kept if one)."""
    import resvg_py

    try:
        if size and not (width or height):
            native = _render(resvg_py, path, {})
            zoom = size / max(native.size)
            return native if abs(zoom - 1) < 1e-6 else _render(resvg_py, path, {"zoom": zoom})
        options = {k: v for k, v in (("width", width), ("height", height)) if v}
        return _render(resvg_py, path, options)
    except ValueError as exc:
        raise InvalidInputError(f"Cannot render SVG {path.name}: {exc}", path=str(path)) from exc


def _render(resvg_py, path: Path, options: dict) -> Image.Image:  # type: ignore[no-untyped-def]
    data = resvg_py.svg_to_bytes(svg_path=str(path), **options)
    image = Image.open(io.BytesIO(bytes(data)))
    image.load()
    return image.convert("RGBA")
