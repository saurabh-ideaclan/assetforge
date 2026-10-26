"""Automatic QC against what was asked for, subject-based naming, and the capped `regenerate` review loop."""

from __future__ import annotations

import pytest

from assetforge.config import Settings
from assetforge.core import qc, settings_store
from assetforge.core.models import Status
from assetforge.errors import InvalidInputError, RetryLimitError
from assetforge.image import engine, providers
from assetforge.image.engine import subject_name
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.providers.fake import FakeProvider
from assetforge.regenerate import regenerate

from conftest import make_png, run_cli

SETTINGS = Settings({})


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    provider = FakeProvider(SETTINGS, name="gemini", native_transparency=False, cost_per_image=0.03)
    providers.OVERRIDES["gemini"] = provider
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    return provider


def gen(lib, **kwargs):
    return generate_image(lib, ImageRequest(provider="gemini", **kwargs), SETTINGS)


def test_subject_names_skip_style_words():
    assert subject_name("A cute chunky cartoon beetle enemy, front view, thick outline") == "beetle_enemy"
    assert subject_name("Derelict space station corridor with flickering lights") == "derelict_space_station_corridor"
    assert subject_name("cute 2d game sprite") == "image"


def test_qc_checks_requested_size_and_near_duplicates(lib, tmp_path):
    result = gen(lib, prompt="iron shield", type="object", size="300x200")
    [candidate] = result.assets
    asset = lib.get_asset(candidate.asset_id)
    assert asset.metadata["expect"]["size"] == "300x200"
    if (candidate.width, candidate.height) != (300, 200):  # the fake honours aspect, not exact pixels
        assert "size_mismatch" in candidate.qc["issues"] and "size_mismatch" in asset.metadata["qc"]["warnings"]
    # the same picture at another size, imported later, is flagged as a near-duplicate of the generated one
    from PIL import Image

    with Image.open(candidate.path) as image:
        image.resize((image.width // 2, image.height // 2)).save(tmp_path / "shield_small.png")
    twin = lib.import_paths([tmp_path / "shield_small.png"]).items[0].asset_id
    report = qc.check_asset(lib, twin)
    assert "near_duplicate" in report["warnings"] and report["near_duplicates"][0]["asset_id"] == candidate.asset_id


def test_qc_flags_bad_sheets(lib, tmp_path):
    asset_id = lib.import_paths([make_png(tmp_path / "s.png", (64, 32))]).items[0].asset_id
    lib.merge_metadata(asset_id, {"animation": {"slice_report": {"expected": 8, "found": 6, "empty_cells": [3, 7],
                                                                 "issues": ["missing_frames"]}}})  # fmt: skip
    report = qc.check_asset(lib, asset_id)
    assert "sheet_frames" in report["warnings"] and report["metrics"]["sheet"]["found"] == 6


def test_regenerate_applies_feedback_links_and_is_capped(lib):
    first = gen(lib, prompt="graveyard ghoul", type="enemy", style="cartoon")
    original = first.assets[0].asset_id
    plan = regenerate(lib, original, feedback="less cartoonish, realistic proportions", dry_run=True,
                      settings=SETTINGS)  # fmt: skip
    assert plan["attempt"] == 1 and "Changes after review: less cartoonish" in plan["prompt"]
    out = regenerate(lib, original, feedback="less cartoonish, realistic proportions", style="dark-fantasy",
                     reject_previous=True, settings=SETTINGS)  # fmt: skip
    new_id = out["result"]["assets"][0]["asset_id"]
    new = lib.get_asset(new_id)
    assert new.style == "dark-fantasy" and new.name.endswith("_r1")
    assert new.metadata["regeneration"] == {"of": original, "attempt": 1, "previous": original,
                                            "feedback": "less cartoonish, realistic proportions"}  # fmt: skip
    assert any(l.asset_id == original and l.relation == "variant_of" for l in new.lineage)
    old = lib.get_asset(original)
    assert old.status == Status.REJECTED and old.metadata["review"]["regenerated_as"] == [new_id]
    # a regeneration of a regeneration counts against the same original
    settings_store.set_value(lib, "generation.max_regenerations", 2)
    regenerate(lib, new_id, feedback="darker lighting", settings=SETTINGS)
    with pytest.raises(RetryLimitError):
        regenerate(lib, new_id, feedback="even darker", settings=SETTINGS)


def test_regenerate_refuses_non_ai_assets(lib, tmp_path):
    asset_id = lib.import_paths([make_png(tmp_path / "x.png")]).items[0].asset_id
    with pytest.raises(InvalidInputError):
        regenerate(lib, asset_id, feedback="make it red")


def test_cli_regenerate(capsys, home, lib):
    original = gen(lib, prompt="crystal golem", type="enemy").assets[0].asset_id
    code, out, _ = run_cli(capsys, "regenerate", original, "-m", "sharper crystal facets", "--avoid", "cute faces",
                           "--json")  # fmt: skip
    assert code == 0 and out["regenerate"]["attempt"] == 1 and out["regenerate"]["remaining"] == 2
    code, out, _ = run_cli(capsys, "config", "set", "generation.max_regenerations", "1", "--json")
    code, out, _ = run_cli(capsys, "regenerate", original, "-m", "again", "--json")
    assert code == 7 and out["error"]["code"] == "RETRY_LIMIT"
