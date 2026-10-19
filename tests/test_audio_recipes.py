from __future__ import annotations

import numpy as np
import pytest

from assetforge.audio.effects import resolve_effects
from assetforge.audio.generate import render_variant
from assetforge.audio.recipes import PRESETS, RECIPES, get_recipe, resolve_params, vary_params
from assetforge.audio.variations import plan_variants
from assetforge.errors import InvalidInputError

SR = 22050


def test_registry_has_the_planned_coverage():
    assert len(RECIPES) >= 20
    for needed in ("ui_click", "jump", "land", "cartoon_hit", "crunch", "coin", "power_up", "explosion",
                   "victory", "failure", "whoosh"):  # fmt: skip
        assert needed in RECIPES
    for recipe in RECIPES.values():
        assert recipe.tags and recipe.description
        lo, hi = recipe.duration_range
        assert lo <= recipe.duration <= hi


@pytest.mark.parametrize("name", sorted(RECIPES))
@pytest.mark.parametrize("preset", [None, *sorted(PRESETS)])
def test_every_recipe_and_preset_renders_usable_audio(name, preset):
    recipe = RECIPES[name]
    params, effects = resolve_params(recipe, preset=preset)
    pcm, qc = render_variant(recipe, params, resolve_effects(effects), 1234, SR, -1.0)
    assert qc["ok"], qc
    assert qc["peak_dbfs"] == pytest.approx(-1.0, abs=0.05)
    assert 5 <= qc["duration_ms"] <= params["duration"] * 1000 + 3200  # + effect tails


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_rendering_is_deterministic_and_seed_sensitive(name):
    recipe = RECIPES[name]
    params, _ = resolve_params(recipe)
    a, _ = render_variant(recipe, params, [], 7, SR, -1.0)
    b, _ = render_variant(recipe, params, [], 7, SR, -1.0)
    assert np.array_equal(a, b)


def test_noisy_recipes_change_with_the_seed():
    recipe = RECIPES["explosion"]
    params, _ = resolve_params(recipe)
    a, _ = render_variant(recipe, params, [], 1, SR, -1.0)
    b, _ = render_variant(recipe, params, [], 2, SR, -1.0)
    assert not np.array_equal(a, b)


def test_param_resolution_layers_and_validation():
    coin = get_recipe("coin")
    params, effects = resolve_params(coin, preset="retro", overrides={"note": 90, "duration": 0.5})
    assert (params["note"], params["duration"], params["wave"]) == (90, 0.5, "square")
    assert [e["name"] for e in effects] == ["bitcrush", "resample"]
    soft, _ = resolve_params(coin, preset="soft")
    assert soft["wave"] == "triangle" and soft["brightness"] < 0

    with pytest.raises(InvalidInputError):
        resolve_params(coin, overrides={"nope": 1})
    with pytest.raises(InvalidInputError):
        resolve_params(coin, overrides={"note": 200})
    with pytest.raises(InvalidInputError):
        resolve_params(coin, overrides={"wave": "kazoo"})
    with pytest.raises(InvalidInputError):
        resolve_params(coin, overrides={"note": 80.5})
    with pytest.raises(InvalidInputError):
        resolve_params(coin, preset="glittery")
    with pytest.raises(InvalidInputError):
        get_recipe("theremin")
    assert get_recipe("cartoon-hit").name == "cartoon_hit"


def test_variation_stays_in_range_and_is_seeded():
    recipe = get_recipe("jump")
    base, _ = resolve_params(recipe)
    plans = plan_variants(recipe, base, seed=5, indices=range(1, 9), variation=1.0)
    assert plans[0].params == base
    assert len({p.render_seed for p in plans}) == 8
    for plan in plans[1:]:
        assert plan.params != base
        for p in recipe.all_params():
            if p.kind != "choice":
                assert p.min <= plan.params[p.name] <= p.max
    again = plan_variants(recipe, base, seed=5, indices=range(1, 9), variation=1.0)
    assert [p.params for p in again] == [p.params for p in plans]
    assert vary_params(recipe, base, 0.0, np.random.default_rng(0)) == base
