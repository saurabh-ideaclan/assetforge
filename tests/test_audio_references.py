"""Reference-matched sounds stay close to measurements of real recordings (no shipped audio, numbers only)."""

from __future__ import annotations

import numpy as np
import pytest

from assetforge.audio import references
from assetforge.audio.ambience import engine
from assetforge.audio.ambience.beds import BEDS
from assetforge.audio.export import finish
from assetforge.audio.recipes import RECIPES, render_recipe, resolve_params

SR = 44100


def render(key: str, opts: dict):
    kind, name = key.split(":")
    name = name.split("/")[0]  # "footstep/snow": one recipe, several targets
    if kind == "recipe":
        recipe = RECIPES[name]
        params, _ = resolve_params(recipe, overrides=opts)
        return finish(render_recipe(recipe, params, SR, 3), SR)
    if kind == "bed":
        bed = BEDS[name]
        n = int(opts["length"] * SR)
        return bed.render(n, SR, np.random.default_rng(3), 0.5, {**bed.params, **opts}, n)
    scene = engine.builtin_scenes()[name]
    spec = engine.AmbienceSpec(scene=name, beds=scene.beds, events=scene.events, space=scene.space, length=opts["length"])
    return engine.render(spec.model_dump(mode="json"), 1, 3, SR).mix


def test_every_mapping_has_a_target():
    assert set(t for t, _ in references.REFERENCE_FOR.values()) <= set(references.targets())


@pytest.mark.parametrize("key", sorted(references.REFERENCE_FOR))
def test_sound_matches_real_recordings(key):
    target, opts = references.REFERENCE_FOR[key]
    report = references.match(render(key, opts), SR, target)
    assert report["mean_abs_db"] <= 4.5, report


def _envelope_db(x, ms):
    x = x.mean(axis=1) if x.ndim == 2 else x
    k = max(1, int(ms / 1000 * SR))
    return 20 * np.log10(np.sqrt(np.convolve(x**2, np.ones(k) / k, "same")) + 1e-9)


def _fall_20db(x) -> float:
    env = _envelope_db(x, 6)
    peak = int(np.argmax(env))
    return float(np.nonzero(env[peak:] < env[peak] - 20)[0][0] / SR)


# Real impacts die fast (seconds to fall 20 dB, measured on the reference recordings, with some slack).
DECAYS = {"recipe:blade_hit": (0.05, 0.2), "recipe:door/slam_metal": (0.08, 0.3), "recipe:footstep/wood": (0.01, 0.15)}


@pytest.mark.parametrize("key", sorted(DECAYS))
def test_impacts_decay_like_real_ones(key):
    lo, hi = DECAYS[key]
    assert lo <= _fall_20db(render(key, references.REFERENCE_FOR[key][1])) <= hi


@pytest.mark.parametrize("key", ["recipe:growl", "recipe:roar"])
def test_voices_are_not_chopped_into_bursts(key):
    """Real growls and roars dip 11-29 dB between pulses; separate bursts with silence between sound synthetic."""
    env = _envelope_db(render(key, references.REFERENCE_FOR[key][1]), 2)
    k = int(0.06 * SR)
    mid = env[len(env) // 6 : -len(env) // 6]
    dips = [w.max() - w.min() for w in np.split(mid[: len(mid) // k * k], len(mid) // k)]
    assert np.median(dips) <= 30
