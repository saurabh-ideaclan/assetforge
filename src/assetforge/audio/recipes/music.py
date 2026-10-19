"""Note-sequence helpers shared by tonal recipes (blips, arpeggios, jingles)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext

CHORDS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7, 12, 16, 19, 24),
    "minor": (0, 3, 7, 12, 15, 19, 24),
    "fifths": (0, 7, 12, 19, 24, 31, 36),
    "octaves": (0, 12, 24, 36, 48, 60, 72),
}


def norm(x: np.ndarray) -> np.ndarray:
    peak = np.max(np.abs(x)) if len(x) else 0.0
    return x / peak if peak > 0 else x


def note_sequence(
    ctx: RenderContext,
    notes: Sequence[float],
    *,
    wave: str,
    duty: float = 0.5,
    weights: Sequence[float] | None = None,
    curves: Sequence[float] | None = None,
    attack: float = 0.003,
) -> np.ndarray:
    """Play MIDI `notes` back to back; each note's slot is proportional to its weight."""
    weights = list(weights or [1.0] * len(notes))
    curves = list(curves or [1.5] * len(notes))
    total = sum(weights)
    bounds = np.round(np.cumsum([0.0, *weights]) / total * ctx.n).astype(int)
    parts = []
    for i, midi in enumerate(notes):
        length = max(1, int(bounds[i + 1] - bounds[i]))
        tone = s.osc(wave, s.note_hz(midi) * ctx.pitch, length, ctx.sr, duty=duty)  # type: ignore[arg-type]
        parts.append(tone * s.perc(length, ctx.sr, attack=attack, curve=curves[i]))
    return s.fit(np.concatenate(parts), ctx.n)


def chord(ctx: RenderContext, notes: Sequence[float], n: int, *, wave: str, duty: float = 0.5) -> np.ndarray:
    voices = [s.osc(wave, s.note_hz(m) * ctx.pitch, n, ctx.sr, duty=duty) for m in notes]  # type: ignore[arg-type]
    return sum(voices, np.zeros(n)) / len(voices)
