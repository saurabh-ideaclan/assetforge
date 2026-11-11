"""Enclosed pools of the key colour (arm gaps, a keyed panel inside a transparent frame) are removed too."""

from __future__ import annotations

import numpy as np
from PIL import Image

from assetforge.image.transparency import remove_background


def test_enclosed_key_pool_is_removed_but_small_details_stay() -> None:
    w = h = 400
    rgb = np.full((h, w, 3), 255, dtype=np.uint8)  # magenta frame all round
    rgb[:, :] = (255, 0, 255)
    rgb[40:360, 40:360] = (40, 60, 90)  # the figure
    rgb[150:230, 160:220] = (255, 0, 255)  # a key pool enclosed by the figure (4,800 px): background
    rgb[300:320, 300:320] = (255, 0, 255)  # a 400 px magenta detail: kept
    out = np.asarray(remove_background(Image.fromarray(rgb, "RGB"), "#FF00FF").image)
    assert out[190, 190, 3] == 0 and out[100, 100, 3] == 255 and out[310, 310, 3] == 255 and out[5, 5, 3] == 0
