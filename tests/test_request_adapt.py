"""`request` reuse that adapts close matches, the dry-run decision step, and ranking by discovery metadata."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from assetforge import art
from assetforge.config import Settings
from assetforge.core.models import AssetType, ImportOptions, Status
from assetforge.image import engine, providers
from assetforge.image.providers.fake import FakeProvider
from assetforge.request import AssetRequest, handle

from conftest import run_cli


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    provider = FakeProvider(Settings({}), name="gemini", native_transparency=False, cost_per_image=0.04)
    providers.OVERRIDES["gemini"] = provider
    providers.OVERRIDES["openai"] = provider
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    return provider


def subject(path, size=(200, 300), colour=(60, 40, 90, 255)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((20, 20, size[0] - 20, size[1] - 20), fill=colour)
    ImageDraw.Draw(image).rectangle((60, 60, 90, 90), fill=(250, 250, 250, 255))
    image.save(path)
    return path


@pytest.fixture
def ghoul(lib, tmp_path):
    return lib.import_paths([subject(tmp_path / "ghoul.png")], ImportOptions(
        type=AssetType.ENEMY, description="hunched graveyard ghoul enemy", style="dark-fantasy", genre="roguelike",
        category="creature")).items[0].asset_id  # fmt: skip


def test_reuse_adapts_size_and_format_without_touching_the_original(lib, ghoul):
    original = lib.get_asset(ghoul).primary_variant
    result = handle(lib, AssetRequest(type="enemy", description="graveyard ghoul", project="crypt-run", size="64x96",
                                      format="webp"))  # fmt: skip
    assert result.reused and result.reuse_reason == "search" and result.asset_id == ghoul
    assert result.adapted["ops"] == [{"op": "resize", "width": 64, "height": 96, "mode": "pad"},
                                     {"op": "format", "format": "webp"}]  # fmt: skip
    [variant] = result.variants
    assert (variant["width"], variant["height"], variant["format"]) == (64, 96, "webp")
    asset = lib.get_asset(ghoul)
    assert asset.primary_variant.id == original.id and asset.primary_variant.file.sha256 == original.file.sha256
    assert [(u.project, u.variant_id) for u in asset.usage] == [("crypt-run", variant["variant_id"])]
    # asking again reuses the same derivative instead of making another
    again = handle(lib, AssetRequest(type="enemy", description="graveyard ghoul", project="crypt-run", size="64x96",
                                     format="webp"))  # fmt: skip
    assert again.variants[0]["variant_id"] == variant["variant_id"] and again.adapted["reused_existing"]
    assert len(lib.get_asset(ghoul).variants) == 2


def test_adapt_as_new_asset_is_owned_by_the_project(lib, ghoul):
    result = handle(lib, AssetRequest(type="enemy", description="graveyard ghoul", project="crypt-run",
                                      size="128x128", adapt_as_new_asset=True))  # fmt: skip
    assert result.asset_id != ghoul and result.adapted["new_asset"]
    derived = lib.get_asset(result.asset_id)
    assert derived.project == "crypt-run" and any(l.asset_id == ghoul for l in derived.lineage)


def test_tiny_images_are_not_upscaled_for_reuse(lib, ghoul, fake):
    result = handle(lib, AssetRequest(type="enemy", description="graveyard ghoul", size="1024x1024", provider="gemini"))
    assert not result.reused and result.generated["kind"] == "image"
    assert result.alternatives and result.alternatives[0]["asset_id"] == ghoul  # the near miss is reported


def test_dry_run_reports_reuse_or_generation_and_spends_nothing(lib, ghoul, fake):
    reuse = handle(lib, AssetRequest(type="enemy", description="graveyard ghoul", size="32x48", dry_run=True))
    assert reuse.state == "dry_run" and reuse.would == "reuse" and reuse.asset_id == ghoul
    assert reuse.plan["adapt_ops"][0]["op"] == "resize" and len(lib.get_asset(ghoul).variants) == 1
    art.set_profile(lib, "crypt-run", art.ArtDirection(style="horror", mood="cold dread"))
    gen = handle(lib, AssetRequest(type="background", description="flooded crypt corridor", project="crypt-run",
                                   provider="gemini", dry_run=True))  # fmt: skip
    assert gen.would == "generate" and gen.plan["method"] == "ai_image" and gen.plan["estimated_cost_usd"] > 0
    assert gen.plan["art_layers"] == ["preset:horror", "profile:crypt-run"] and "cold dread" in gen.plan["prompt"]
    assert fake.calls == [] and lib.info()["counts"]["assets"] == 1
    audio = handle(lib, AssetRequest(type="sfx", description="coin pickup", dry_run=True))
    assert audio.plan["method"] == "procedural_audio" and audio.plan["recipe"] == "coin"


def test_discovery_metadata_breaks_ties(lib, tmp_path):
    a = lib.import_paths([subject(tmp_path / "a.png", colour=(90, 20, 20, 255))], ImportOptions(
        type=AssetType.OBJECT, description="wooden treasure chest", genre="platformer")).items[0].asset_id  # fmt: skip
    b = lib.import_paths([subject(tmp_path / "b.png", colour=(20, 90, 20, 255))], ImportOptions(
        type=AssetType.OBJECT, description="wooden treasure chest", genre="roguelike")).items[0].asset_id  # fmt: skip
    pick = handle(lib, AssetRequest(type="object", description="wooden treasure chest", genre="roguelike"))
    assert pick.asset_id == b and pick.alternatives[0]["asset_id"] == a


def test_generated_assets_get_profile_metadata_and_spritesheets_are_requestable(lib):
    art.set_profile(lib, "crypt-run", art.ArtDirection(style="dark-fantasy", genre="roguelike", theme="crypt"))
    result = handle(lib, AssetRequest(type="spritesheet", description="skeleton walk cycle", project="crypt-run",
                                      provider="gemini", sheet={"columns": 4, "rows": 1}))  # fmt: skip
    asset = lib.get_asset(result.asset_id)
    assert asset.type == AssetType.SPRITESHEET and asset.style == "dark-fantasy"
    assert (asset.genre, asset.theme, asset.status) == ("roguelike", "crypt", Status.CANDIDATE)
    assert "4 columns by 1 rows" in asset.metadata["generator"]["final_prompt"]


def test_cli_request_dry_run(capsys, home, lib, ghoul):
    code, out, _ = run_cli(capsys, "request", "--type", "enemy", "-d", "graveyard ghoul", "--size", "64x64",
                           "--dry-run", "--json")  # fmt: skip
    assert code == 0 and out["would"] == "reuse" and out["plan"]["adapt_ops"][0]["width"] == 64
