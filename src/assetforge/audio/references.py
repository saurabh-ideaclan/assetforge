"""Reference targets: how real recordings of a sound measure, so synthesized versions can be checked.

`references.json` holds numbers only (third-octave spectrum shape, noisiness, level-flutter profile),
measured from freely licensed field recordings (Wikimedia Commons, Freesound CC0); the recordings themselves
are not shipped. `REFERENCE_FOR` maps recipes and ambience scenes to a target, `match()` scores a render
against it, and the test suite keeps every mapped sound within tolerance of its real-world counterpart.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

import numpy as np

# recipe or scene → (target name, render overrides used when checking)
REFERENCE_FOR: dict[str, tuple[str, dict[str, Any]]] = {
    "recipe:thunder": ("thunder", {"duration": 9.0}),
    "recipe:roar": ("roar", {"duration": 4.0}),
    "recipe:growl": ("growl", {"duration": 3.0}),
    "recipe:engine_rev": ("engine", {"duration": 4.0}),
    "recipe:blast": ("explosion", {}),
    "recipe:lightning_zap": ("electric_spark", {"duration": 1.5}),
    "scene:rain_light": ("rain_light", {"length": 10}),
    "scene:rain_storm": ("rain_heavy", {"length": 10}),
    "scene:forest_night": ("night_forest", {"length": 10}),
}
BAND_RANGE = (100, 6300)


@lru_cache(maxsize=1)
def targets() -> dict[str, dict[str, Any]]:
    try:
        text = resources.files("assetforge.audio").joinpath("references.json").read_text()
    except FileNotFoundError:
        return {}
    return json.loads(text)


def spectrum_shape(x: np.ndarray, sr: int, bands_hz: list[float]) -> np.ndarray:
    """Third-octave levels in dB relative to the loudest band."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    levels = np.array([X[(f >= b / 2 ** (1 / 6)) & (f < b * 2 ** (1 / 6))].sum() for b in bands_hz]) + 1e-20
    db = 10 * np.log10(levels / levels.sum())
    return db - db.max()


def match(x: np.ndarray, sr: int, target: str) -> dict[str, Any]:
    """How far a render's spectrum shape is from the real recordings (mean |dB| over 100 Hz-6.3 kHz)."""
    t = targets()[target]
    bands = t["bands_hz"]
    ours = spectrum_shape(x, sr, bands)
    ref = np.asarray(t["level_db"])
    keep = [(BAND_RANGE[0] <= b <= BAND_RANGE[1]) for b in bands]
    diff = (ours - ref)[keep]
    diff -= np.median(diff)  # compare shape, not overall level
    return {"target": target, "mean_abs_db": round(float(np.mean(np.abs(diff))), 2),
            "worst_db": round(float(np.max(np.abs(diff))), 1),
            "per_band": {int(b): round(float(d), 1) for b, d in zip(np.asarray(bands)[keep], diff, strict=True)},
            "sources": t.get("sources", [])}  # fmt: skip
