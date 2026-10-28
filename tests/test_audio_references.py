"""Reference-matched sounds stay close to measurements of real recordings (no shipped audio, numbers only)."""

from __future__ import annotations

import pytest

from assetforge.audio import references
from assetforge.audio.ambience import engine
from assetforge.audio.export import finish
from assetforge.audio.recipes import RECIPES, render_recipe, resolve_params

SR = 44100


def render(key: str, opts: dict):
    kind, name = key.split(":")
    if kind == "recipe":
        recipe = RECIPES[name]
        params, _ = resolve_params(recipe, overrides=opts)
        return finish(render_recipe(recipe, params, SR, 3), SR)
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
