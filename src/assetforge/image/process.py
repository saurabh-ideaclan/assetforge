"""Deterministic image processing: an ordered list of ops applied to a source variant.

Ops are plain data (`{"op": "resize", "width": 512, "height": 512, "mode": "contain"}`) stored on
the resulting variant, so every game-ready file records exactly how it was made from which source.
Resizing never stretches: `contain` fits inside the box, `cover` fills it and crops the overflow,
`pad` fits and then pads to the exact box. Alpha is premultiplied while resampling, so
transparent edges don't pick up dark fringes.
"""

from __future__ import annotations

import inspect
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance

from assetforge.core.catalog import Library
from assetforge.core.derive import Derivative, register_derivative, source_variant
from assetforge.core.models import FileKind
from assetforge.errors import InvalidInputError
from assetforge.image.transparency import remove_background

PROCESSOR_VERSION = 1
RESAMPLE = {"lanczos": Image.Resampling.LANCZOS, "bicubic": Image.Resampling.BICUBIC,
            "bilinear": Image.Resampling.BILINEAR, "nearest": Image.Resampling.NEAREST}  # fmt: skip
FORMATS = {"png": ".png", "webp": ".webp", "jpg": ".jpg"}
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")

PRESETS: dict[str, list[dict[str, Any]]] = {
    "game-sprite": [{"op": "trim", "margin": 2}, {"op": "format", "format": "png"}],
    "web-sprite": [{"op": "trim", "margin": 2}, {"op": "format", "format": "webp", "lossless": True}],
    "web-background": [{"op": "format", "format": "webp", "quality": 88}],
    "pixel-art-2x": [{"op": "scale", "factor": 2, "resample": "nearest"}, {"op": "format", "format": "png"}],
    # Fit the trimmed subject into 116 px, then centre it on a 128 px canvas: a real 6 px margin at final scale.
    "icon-128": [{"op": "trim"}, {"op": "resize", "width": 116, "height": 116, "mode": "contain"},
                 {"op": "pad", "width": 128, "height": 128}, {"op": "format", "format": "png"}],  # fmt: skip
}


def parse_colour(text: str | None) -> tuple[int, int, int, int]:
    if text is None or text.lower() in ("transparent", "none"):
        return (0, 0, 0, 0)
    match = _HEX.match(text.strip())
    if not match:
        raise InvalidInputError(f"Colours must be hex like #RRGGBB or #RRGGBBAA, got {text!r}")
    rgb = match.group(1)
    alpha = int(match.group(2), 16) if match.group(2) else 255
    return (int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), alpha)


def _rgba(image: Image.Image) -> Image.Image:
    return image if image.mode == "RGBA" else image.convert("RGBA")


def _resize(image: Image.Image, size: tuple[int, int], resample: str) -> Image.Image:
    if image.size == size:
        return image
    method = RESAMPLE[resample]
    if method == Image.Resampling.NEAREST:
        return image.resize(size, method)
    return _rgba(image).convert("RGBa").resize(size, method).convert("RGBA")


# ---------------------------------------------------------------------- ops


def op_trim(image: Image.Image, threshold: int = 8, margin: int = 0) -> Image.Image:
    rgba = _rgba(image)
    alpha = np.asarray(rgba.getchannel("A"))
    if (alpha < 255).any():
        mask = alpha > threshold
    else:  # opaque: trim a uniform border colour instead
        rgb = np.asarray(rgba.convert("RGB"), dtype=int)
        mask = np.abs(rgb - rgb[0, 0]).sum(axis=2) > threshold * 3
    if not mask.any():
        raise InvalidInputError("Nothing to keep: the image is empty after trimming")
    ys, xs = np.nonzero(mask)
    box = (int(xs.min()) - margin, int(ys.min()) - margin, int(xs.max()) + 1 + margin, int(ys.max()) + 1 + margin)
    return rgba.crop(box)  # Pillow pads out-of-bounds areas with transparent pixels


def op_crop(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image.width or y + height > image.height:
        raise InvalidInputError(f"Crop {width}x{height}+{x}+{y} is outside the {image.width}x{image.height} image")
    return image.crop((x, y, x + width, y + height))


def op_resize(
    image: Image.Image,
    width: int | None = None,
    height: int | None = None,
    mode: str = "contain",
    resample: str = "lanczos",
    background: str | None = None,
    upscale: bool = True,
) -> Image.Image:
    if not width and not height:
        raise InvalidInputError("resize needs a width, a height or both")
    w, h = image.size
    if mode not in ("contain", "cover", "pad"):
        raise InvalidInputError(f"Unknown resize mode {mode!r}; use contain, cover or pad")
    if not width or not height:  # one side given: keep the aspect ratio
        scale = (width / w) if width else (height / h)  # type: ignore[operator]
        if not upscale:
            scale = min(scale, 1.0)
        return _resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), resample)
    if mode == "cover":
        scale = max(width / w, height / h)
        scaled = _resize(image, (max(width, round(w * scale)), max(height, round(h * scale))), resample)
        left, top = (scaled.width - width) // 2, (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))
    scale = min(width / w, height / h)
    if not upscale:
        scale = min(scale, 1.0)
    fitted = _resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), resample)
    if mode == "contain":
        return fitted
    canvas = Image.new("RGBA", (width, height), parse_colour(background))
    canvas.alpha_composite(_rgba(fitted), ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas


def op_scale(image: Image.Image, factor: float, resample: str = "lanczos") -> Image.Image:
    if factor <= 0:
        raise InvalidInputError("scale factor must be positive")
    return _resize(image, (max(1, round(image.width * factor)), max(1, round(image.height * factor))), resample)


def op_pad(
    image: Image.Image, margin: int = 0, width: int | None = None, height: int | None = None,
    background: str | None = None,
) -> Image.Image:  # fmt: skip
    w = width or image.width + 2 * margin
    h = height or image.height + 2 * margin
    if w < image.width or h < image.height:
        raise InvalidInputError(f"pad target {w}x{h} is smaller than the {image.width}x{image.height} image")
    canvas = Image.new("RGBA", (w, h), parse_colour(background))
    canvas.alpha_composite(_rgba(image), ((w - image.width) // 2, (h - image.height) // 2))
    return canvas


def op_recolor(
    image: Image.Image,
    hue: float = 0.0,
    saturation: float = 1.0,
    brightness: float = 1.0,
    contrast: float = 1.0,
    tint: str | None = None,
    tint_strength: float = 0.5,
    grayscale: bool = False,
) -> Image.Image:
    rgba = _rgba(image)
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    if grayscale:
        rgb = rgb.convert("L").convert("RGB")
    if hue % 360:
        hsv = np.asarray(rgb.convert("HSV"), dtype=np.int16)
        hsv[..., 0] = (hsv[..., 0] + round(hue / 360 * 255)) % 256
        rgb = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if tint:
        colour = parse_colour(tint)[:3]
        # Multiply-style tint that keeps shading: blend each pixel toward colour * luminance.
        arr = np.asarray(rgb, dtype=float)
        lum = arr.mean(axis=2, keepdims=True) / 255.0
        target = lum * np.array(colour, dtype=float)
        arr = arr * (1 - tint_strength) + target * tint_strength
        rgb = Image.fromarray(np.clip(arr, 0, 255).round().astype(np.uint8), "RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def op_flip(image: Image.Image, axis: str = "horizontal") -> Image.Image:
    if axis not in ("horizontal", "vertical"):
        raise InvalidInputError("flip axis must be horizontal or vertical")
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT if axis == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM)


def op_rotate(image: Image.Image, degrees: int = 90) -> Image.Image:
    turns = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
    if degrees not in turns:
        raise InvalidInputError("rotate supports 90, 180 and 270 degrees (clockwise)")
    return image.transpose(turns[degrees])


def op_remove_background(image: Image.Image, key: str | None = None) -> Image.Image:
    return remove_background(image, key).image


OPS: dict[str, Callable[..., Image.Image]] = {
    "trim": op_trim,
    "crop": op_crop,
    "resize": op_resize,
    "scale": op_scale,
    "pad": op_pad,
    "recolor": op_recolor,
    "flip": op_flip,
    "rotate": op_rotate,
    "remove_background": op_remove_background,
}


def validate_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check op names and arguments, and make sure `format` (if any) comes last."""
    if not ops:
        raise InvalidInputError("No processing operations given")
    clean = []
    for i, raw in enumerate(ops):
        if not isinstance(raw, dict) or "op" not in raw:
            raise InvalidInputError(f"Operation #{i + 1} must be an object with an 'op' field")
        name, args = str(raw["op"]).replace("-", "_"), {k: v for k, v in raw.items() if k != "op"}
        if name == "format":
            if i != len(ops) - 1:
                raise InvalidInputError("'format' must be the last operation")
            if args.get("format", "png") not in FORMATS:
                raise InvalidInputError(f"Unknown image format {args.get('format')!r}", allowed=list(FORMATS))
            unknown = set(args) - {"format", "quality", "lossless", "background"}
        elif name in OPS:
            unknown = set(args) - (set(inspect.signature(OPS[name]).parameters) - {"image"})
        else:
            raise InvalidInputError(f"Unknown image operation {name!r}", allowed=[*OPS, "format"])
        if unknown:
            raise InvalidInputError(f"Unknown argument(s) for {name}: {', '.join(sorted(unknown))}")
        clean.append({"op": name, **args})
    return clean


def apply_ops(image: Image.Image, ops: list[dict[str, Any]]) -> tuple[Image.Image, dict[str, Any]]:
    """Run ops in order. Returns the image and its encoding settings (from a trailing `format` op)."""
    encoding: dict[str, Any] = {"format": "png"}
    for op in ops:
        args = {k: v for k, v in op.items() if k != "op"}
        if op["op"] == "format":
            encoding = {"format": "png", **args}
            continue
        try:
            image = OPS[op["op"]](image, **args)
        except TypeError as exc:
            raise InvalidInputError(f"Bad arguments for {op['op']}: {exc}") from exc
    return image, encoding


def encode(image: Image.Image, path: Path, encoding: dict[str, Any]) -> Path:
    fmt = encoding.get("format", "png")
    target = path.with_suffix(FORMATS[fmt])
    if fmt == "png":
        image.save(target, format="PNG", optimize=True)
    elif fmt == "webp":
        lossless = encoding.get("lossless", "quality" not in encoding)
        image.save(target, format="WEBP", lossless=lossless, quality=int(encoding.get("quality", 90)), method=6)
    else:  # jpg has no alpha: flatten onto the background colour (white by default)
        background = parse_colour(encoding.get("background") or "#FFFFFF")
        flat = Image.new("RGBA", image.size, background[:3] + (255,))
        flat.alpha_composite(_rgba(image))
        flat.convert("RGB").save(target, format="JPEG", quality=int(encoding.get("quality", 90)), optimize=True)
    return target


def ops_label(ops: list[dict[str, Any]]) -> str:
    parts = []
    for op in ops:
        name = op["op"]
        if name == "resize":
            parts.append(f"{op.get('width') or 'w'}x{op.get('height') or 'h'}")
        elif name == "scale":
            parts.append(f"{op['factor']:g}x")
        elif name == "format":
            parts.append(op.get("format", "png"))
        elif name == "remove_background":
            parts.append("cutout")
        else:
            parts.append(name)
    return "_".join(parts)


def expand_preset(preset: str) -> list[dict[str, Any]]:
    if preset not in PRESETS:
        raise InvalidInputError(f"Unknown image preset {preset!r}", allowed=sorted(PRESETS))
    return [dict(op) for op in PRESETS[preset]]


def process_image_asset(
    lib: Library,
    ref: str,
    ops: list[dict[str, Any]],
    *,
    variant: str | None = None,
    label: str | None = None,
    make_primary: bool = False,
    new_asset: bool = False,
    name: str | None = None,
) -> Derivative:
    ops = validate_ops(ops)
    asset = lib.get_asset(ref)
    source = source_variant(asset, variant)
    if source.file.kind != FileKind.IMAGE:
        raise InvalidInputError(f"{asset.id} ({source.label}) is not a raster image")
    with Image.open(source.file.path) as opened:
        image, encoding = apply_ops(opened.copy(), ops)
    tmp = Path(tempfile.mkdtemp(dir=lib.paths.tmp, prefix="process-"))
    try:
        output = encode(image, tmp / "derivative", encoding)
        params = {"ops": ops, "processor": "assetforge-image", "processor_version": PROCESSOR_VERSION}
        return register_derivative(lib, asset, source, output, label=label or ops_label(ops), params=params,
                                   make_primary=make_primary, new_asset=new_asset, name=name)  # fmt: skip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

