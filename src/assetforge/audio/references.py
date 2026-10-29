"""Reference targets: how real recordings of a sound measure, so synthesized versions can be checked.

`references.json` holds numbers only (third-octave spectrum shape, noisiness, level-flutter profile),
measured from freely licensed recordings (Wikimedia Commons, Freesound CC0); the recordings themselves are not
shipped. Natural sounds (weather, fire, foley, impacts) are matched to field recordings; creature voices to
designed game creature sounds (`growl_monster`, `roar_monster`), which is what players expect from a monster. `REFERENCE_FOR` maps recipes and ambience scenes to a target, `match()` scores a render
against it, and the test suite keeps every mapped sound within tolerance of its real-world counterpart.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

import numpy as np

# recipe, bed or scene ("name/case" when one recipe has several targets) → (target name, render overrides)
REFERENCE_FOR: dict[str, tuple[str, dict[str, Any]]] = {
    "recipe:thunder": ("thunder", {"duration": 9.0}),
    "recipe:roar": ("roar_monster", {"duration": 2.5}),
    "recipe:growl": ("growl_monster", {"duration": 2.0}),
    "recipe:blade_hit": ("sword_clash", {"duration": 0.8}),
    "recipe:footstep/snow": ("footstep_snow", {"surface": "snow", "duration": 0.45}),
    "recipe:footstep/wood": ("footstep_wood", {"surface": "wood", "duration": 0.45}),
    "recipe:fire_burst": ("fire_burst", {"duration": 3.0, "size": 0.8}),
    "recipe:spell_cast/fire": ("fire_burst", {"element": "fire", "duration": 2.5}),
    "recipe:fire_ignite": ("fire_ignite", {"size": 1.0, "duration": 0.7}),
    "recipe:door/slam_metal": ("door_slam_metal", {"action": "slam", "material": "metal", "duration": 1.2}),
    "bed:fire_crackle": ("fire_crackle", {"length": 10}),
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
