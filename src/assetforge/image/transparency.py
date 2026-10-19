"""Background removal for providers without native transparency (key-colour / flat backgrounds).

Only pixels that are close to the background colour *and* connected to the image border are
removed, so key-coloured details inside the subject survive. Edges get a soft alpha ramp, and
the background colour is un-mixed from semi-transparent pixels to avoid coloured fringes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy import ndimage

HARD_THRESHOLD = 48.0  # RGB distance considered background
SOFT_THRESHOLD = 110.0  # distance above which an edge pixel is fully opaque


@dataclass(frozen=True)
class KeyResult:
    image: Image.Image
    background_rgb: tuple[int, int, int]
    removed_fraction: float
    border_uniformity: float  # std-dev of border colour distance; high means the backdrop wasn't flat


def _hex_to_rgb(hexcode: str) -> np.ndarray:
    h = hexcode.lstrip("#")
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=float)


def remove_background(image: Image.Image, key_hex: str | None = None) -> KeyResult:
    rgb = np.asarray(image.convert("RGB"), dtype=float)
    h, w, _ = rgb.shape
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    # The model rarely paints the exact key colour; trust the border when it is close to it.
    bg = np.median(border, axis=0)
    if key_hex is not None:
        key = _hex_to_rgb(key_hex)
        if np.linalg.norm(bg - key) > 120:
            bg = key
    dist = np.linalg.norm(rgb - bg, axis=2)
    border_dist = np.linalg.norm(border - bg, axis=1)

    near = dist < HARD_THRESHOLD
    labels, _ = ndimage.label(near)
    edge_labels = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    background = np.isin(labels, edge_labels[edge_labels > 0])

    alpha = np.full((h, w), 255.0)
    alpha[background] = 0.0
    fringe = ndimage.binary_dilation(background, iterations=2) & ~background
    ramp = np.clip((dist - HARD_THRESHOLD) / (SOFT_THRESHOLD - HARD_THRESHOLD), 0.0, 1.0)
    alpha[fringe] = ramp[fringe] * 255.0

    # Un-mix the backdrop colour from partially transparent pixels: c = (p - (1 - a) * bg) / a.
    a = (alpha / 255.0)[..., None]
    with np.errstate(divide="ignore", invalid="ignore"):
        unmixed = np.where(a > 0, (rgb - (1 - a) * bg) / np.maximum(a, 1e-6), 0.0)
    out_rgb = np.where(fringe[..., None], np.clip(unmixed, 0, 255), rgb)
    out_rgb[background] = 0.0

    rgba = np.dstack([out_rgb, alpha]).round().astype(np.uint8)
    return KeyResult(
        image=Image.fromarray(rgba, "RGBA"),
        background_rgb=tuple(int(v) for v in bg),  # type: ignore[arg-type]
        removed_fraction=float(background.mean()),
        border_uniformity=float(border_dist.std()),
    )
