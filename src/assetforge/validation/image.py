"""Image QC: dimensions, transparency coverage, blank images and subjects cut off at the frame edge."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from assetforge.core.models import ISOLATED_TYPES as MODEL_ISOLATED_TYPES
from assetforge.core.models import AssetType

ISOLATED_TYPES = MODEL_ISOLATED_TYPES
ERROR_ISSUES = {"blank", "empty"}


def analyze_image(image: Image.Image, *, asset_type: AssetType | None = None,
                  expect_transparency: bool | None = None) -> dict[str, Any]:  # fmt: skip
    rgba = np.asarray(image.convert("RGBA"))
    h, w = rgba.shape[:2]
    alpha = rgba[..., 3]
    opaque = alpha > 16
    transparent_fraction = float(1.0 - opaque.mean())
    has_alpha = bool((alpha < 255).any())
    visible = rgba[..., :3][opaque]
    colour_std = float(visible.std(axis=0).max()) if visible.size else 0.0  # per channel: a flat colour is 0

    bbox = None
    touches_edge = False
    if opaque.any():
        ys, xs = np.nonzero(opaque)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        touches_edge = bool(opaque[0].any() or opaque[-1].any() or opaque[:, 0].any() or opaque[:, -1].any())

    issues: list[str] = []
    if not opaque.any() or transparent_fraction > 0.995:
        issues.append("empty")
    elif colour_std < 2.0 and not (has_alpha and transparent_fraction > 0.01):
        issues.append("blank")  # one flat colour; a flat silhouette on transparency (vector icon) is fine
    if expect_transparency and not has_alpha:
        issues.append("no_transparency")
    if expect_transparency and has_alpha and touches_edge and asset_type in ISOLATED_TYPES:
        issues.append("touches_edge")
    if expect_transparency and transparent_fraction < 0.05 and asset_type in ISOLATED_TYPES:
        issues.append("background_not_removed")
    if bbox and has_alpha and asset_type in ISOLATED_TYPES:
        subject_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if subject_area < 0.5 * w * h:
            issues.append("excess_padding")  # trim it (e.g. `image process --preset game-sprite`)
    if max(w, h) > 4096:
        issues.append("too_large")
    return {
        "ok": not (set(issues) & ERROR_ISSUES),
        "issues": issues,
        "width": w,
        "height": h,
        "has_alpha": has_alpha,
        "transparent_fraction": round(transparent_fraction, 4),
        "subject_bbox": bbox,
        "colour_std": round(colour_std, 2),
        "power_of_two": bool(w & (w - 1) == 0 and h & (h - 1) == 0),
    }
