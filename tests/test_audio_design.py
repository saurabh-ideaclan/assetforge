"""Sound effects at full range: every recipe renders cleanly, synth patches, layered designs and their CLI."""

from __future__ import annotations

import json

import numpy as np
import pytest

from assetforge.audio import design, pipeline
from assetforge.audio.export import finish
from assetforge.audio.recipes import PRESETS, RECIPES, render_recipe, resolve_params
from assetforge.errors import InvalidInputError
from assetforge.validation.audio import analyze

from conftest import run_cli

SR = 22050


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_every_recipe_renders_clean_audio(name):
    recipe = RECIPES[name]
    params, _ = resolve_params(recipe)
    x = render_recipe(recipe, params, SR, 7)
    assert np.isfinite(x).all()
    qc = analyze(finish(x, SR), SR)
    assert qc["ok"] and "too_quiet" not in qc["issues"], qc


def test_new_categories_and_presets_exist():
    for name in ("gunshot", "spell_cast", "growl", "door", "footstep", "thunder", "notification", "typewriter"):
        assert name in RECIPES
    assert {"magical", "scifi", "horror", "underwater", "distant", "radio", "huge"} <= set(PRESETS)
    surfaces = next(p for p in RECIPES["footstep"].params if p.name == "surface").choices
    assert {"grass", "stone", "wood", "metal", "snow", "water", "sand"} <= set(surfaces)


def test_patch_renders_envelopes_filters_and_is_deterministic():
    spec = design.validate(design.design_from_patch({
        "duration": 0.6,
        "oscillators": [{"wave": "saw", "note": "A2", "unison": 3}, {"wave": "noise_pink", "level": 0.3}],
        "pitch": [[0, 12], [0.3, 0]],
        "amp": {"points": [[0, 0], [0.01, 1], [0.6, 0]]},
        "filter": {"type": "lowpass", "cutoff": 400, "resonance": 4, "envelope": [[0, 6000], [0.5, 300]]},
        "vibrato": {"rate": 6, "depth": 0.3}, "drive": 0.3, "bits": 8,
    }))  # fmt: skip
    a = design.render(spec, 1, 11, SR).mix
    b = design.render(spec, 1, 11, SR).mix
    assert a.shape == (int(0.6 * SR), 2) and np.array_equal(a, b)
    assert not np.array_equal(a, design.render(spec, 2, 12, SR).mix)
    with pytest.raises(InvalidInputError):
        design.validate({"layers": [{"recipe": "laser", "patch": {}}]})
    with pytest.raises(InvalidInputError):
        design.validate({"layers": [{"recipe": "laser", "params": {"nope": 1}}]})


def test_layered_design_catalogs_reuses_varies_and_reproduces(lib):
    spec = {"layers": [{"recipe": "charge_up", "params": {"duration": 0.4}},
                       {"recipe": "explosion", "start": 0.35, "gain_db": -3, "pan": -0.3},
                       {"recipe": "footstep", "params": {"surface": "gravel"}, "repeat": 3, "interval": 0.2,
                        "start": 0.5}], "space": "cave"}  # fmt: skip
    res = design.generate_design(lib, design.DesignRequest(design=spec, variants=2, seed=3, project="crypt"))
    assert not res["reused"] and res["engine"] == design.ENGINE and len(res["variants"]) == 2
    asset = lib.get_asset(res["asset_id"])
    assert asset.primary_variant.file.ext == ".ogg" and asset.primary_variant.file.channels == 2
    assert asset.metadata["generator"]["spec"]["space"] == "cave" and "designed" in asset.tags
    again = design.generate_design(lib, design.DesignRequest(design=spec, variants=2, seed=3, project="crypt"))
    assert again["reused"] and again["asset_id"] == res["asset_id"]
    assert pipeline.reproduce(lib, res["asset_id"])["ok"]
    added = pipeline.vary(lib, res["asset_id"], variants=2, seed=5)
    assert [v["label"] for v in added["variants"]] == ["v3_ogg", "v4_ogg"]


def test_profile_space_applies_to_designs(lib):
    from assetforge import sound

    sound.set_profile(lib, "deep-sea", sound.SoundDirection(space="underwater"))
    res = design.generate_design(lib, design.DesignRequest(patch={"oscillators": [{"wave": "sine", "freq": 300}]},
                                                           project="deep-sea", seed=1))  # fmt: skip
    assert lib.get_asset(res["asset_id"]).metadata["generator"]["spec"]["space"] == "underwater"


def test_cli_design_patch_vary_reproduce(capsys, home, tmp_path):
    design_file = tmp_path / "zap.json"
    design_file.write_text(json.dumps({"layers": [{"recipe": "lightning_zap"}, {"recipe": "thunder", "start": 0.1,
                                                                                "params": {"duration": 1.5}}]}))  # fmt: skip
    code, out, _ = run_cli(capsys, "audio", "design", "-f", str(design_file), "--seed", "2", "--name", "storm_zap", "--json")
    assert code == 0 and out["asset"]["name"] == "storm_zap" and out["variants"][0]["format"] == "ogg"
    asset_id = out["asset_id"]
    patch_file = tmp_path / "blip.json"
    patch_file.write_text(json.dumps({"duration": 0.2, "oscillators": [{"wave": "pulse", "duty": 0.25, "freq": 880}]}))
    code, out, _ = run_cli(capsys, "audio", "patch", "-f", str(patch_file), "--seed", "1", "--json")
    assert code == 0 and out["engine"] == "assetforge-design"
    code, out, _ = run_cli(capsys, "audio", "vary", asset_id, "-n", "1", "--seed", "4", "--json")
    assert code == 0 and out["variants"][0]["label"] == "v2_ogg"
    code, out, _ = run_cli(capsys, "audio", "reproduce", asset_id, "--json")
    assert code == 0 and out["reproduce"]["ok"]
    code, out, _ = run_cli(capsys, "audio", "vary", asset_id, "--param", "x=1", "--json")
    assert code == 4
