"""Seamless loops: render a little past the loop length, then wrap the overhang onto the start.

Two ways to wrap, depending on what spills past the end:

- `crossfade_loop` for continuous textures (wind, rain, drones): the start fades from the
  overhang into the original start, so the last sample flows into the first.
- `fold_loop` for decays (reverb tails, notes ringing past a bar line): the overhang is
  added onto the start, exactly as it would sound when the loop repeats.
"""

from __future__ import annotations

from typing import Any

import numpy as np

Array = np.ndarray


def crossfade_loop(x: Array, length: int, fade: int, *, equal_power: bool = True) -> Array:
    """Loop of exactly `length` samples from a render at least `length + fade` long."""
    if len(x) < length + fade:
        raise ValueError("render too short for the loop crossfade")
    fade = max(1, fade)
    y = x[:length].copy()
    t = np.linspace(0.0, 1.0, fade, endpoint=False)
    if equal_power:  # uncorrelated noise keeps its level through the fade
        fade_in, fade_out = np.sin(t * np.pi / 2), np.cos(t * np.pi / 2)
    else:  # correlated (tonal) material
        fade_in, fade_out = t, 1.0 - t
    if x.ndim == 2:
        fade_in, fade_out = fade_in[:, None], fade_out[:, None]
    y[:fade] = x[:fade] * fade_in + x[length : length + fade] * fade_out
    return y


def fold_loop(x: Array, length: int) -> Array:
    """Loop of exactly `length` samples; everything after `length` is added back from the start."""
    y = x[:length].copy()
    offset = length
    while offset < len(x):
        chunk = x[offset : offset + length]
        y[: len(chunk)] += chunk
        offset += length
    return y


def circular_effects(x: Array, effects: list[dict[str, Any]], sr: int) -> Array:
    """Apply an effect chain to a loop as if it repeated forever, so reverb and echo tails wrap around."""
    from assetforge.audio.effects import apply_effects

    if not effects:
        return x
    n = len(x)
    # Steady state: the last of K copies carries the tails of every earlier copy (tails die within ~8 s).
    copies = 2 + int(np.ceil(8 * sr / n))
    y = apply_effects(np.concatenate([x] * copies), effects, sr)
    return y[(copies - 1) * n : copies * n]
