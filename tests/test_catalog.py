from __future__ import annotations

import os
from pathlib import Path

import pytest

from assetforge.core import collections as coll
from assetforge.core.catalog import Library
from assetforge.core.models import AssetPatch, ImportOptions, LineageRelation, NewAsset, Origin, Status
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError

from conftest import make_png, make_wav


def _import(lib, path, **options):
    return lib.import_paths([path], ImportOptions(**options)).items[0]


def test_library_creates_layout_and_is_reopenable(home):
    with Library(home) as lib:
        assert lib.info()["schema_version"] == 3
    for sub in ("objects/sha256", "previews", "jobs", "styles", "collections", "exports", "tmp"):
        assert (home / sub).is_dir()
    assert (home / "catalog.sqlite3").is_file()
    with Library(home) as lib:
        assert lib.info()["counts"]["assets"] == 0


def test_get_resolves_ids_variants_and_keys(lib, tmp_path):
    item = _import(lib, make_png(tmp_path / "a.png"), key="ui/a")

    assert lib.get_asset(item.asset_id).id == item.asset_id
    assert lib.get_asset(item.variant_id).id == item.asset_id
    assert lib.get_asset("ui/a").id == item.asset_id
    with pytest.raises(NotFoundError):
        lib.get_asset("ast_99999")
    with pytest.raises(NotFoundError):
        lib.get_asset("nope/key")


def test_same_key_in_two_projects_is_ambiguous_without_project(lib, tmp_path):
    a = _import(lib, make_png(tmp_path / "a.png"), key="ui/coin", project="game-a")
    b = _import(lib, make_png(tmp_path / "b.png", color=(9, 9, 9, 255)), key="ui/coin", project="game-b")

    with pytest.raises(ConflictError):
        lib.get_asset("ui/coin")
    assert lib.get_asset("ui/coin", project="game-b").id == b.asset_id
    assert lib.get_asset("game-a:ui/coin").id == a.asset_id


def test_update_validates_and_records_history(lib, tmp_path):
    item = _import(lib, make_png(tmp_path / "a.png"), tags=["old"])

    asset = lib.update_asset(
        item.asset_id,
        AssetPatch(name="Hero Idle", add_tags=["Hero"], remove_tags=["old"], metadata={"frames": 4}),
    )
    assert (asset.name, asset.tags, asset.metadata) == ("hero_idle", ["hero"], {"frames": 4})
    assert asset.history[0].event == "updated"

    with pytest.raises(InvalidInputError):
        lib.update_asset(item.asset_id, AssetPatch(scope="project"))
    asset = lib.update_asset(item.asset_id, AssetPatch(scope="project", project="panda"))
    asset = lib.update_asset(item.asset_id, AssetPatch(scope="global", project=""))
    assert (asset.scope, asset.project) == ("global", None)


def test_review_status_and_notes(lib, tmp_path):
    item = _import(lib, make_png(tmp_path / "a.png"), status="candidate")
    lib.set_status([item.asset_id], Status.APPROVED, note="looks right")

    asset = lib.get_asset(item.asset_id)
    assert asset.status == Status.APPROVED
    assert asset.history[0].data == {"from": "candidate", "to": "approved", "note": "looks right"}


def test_usage_shares_one_stored_file_across_projects(lib, tmp_path):
    item = _import(lib, make_wav(tmp_path / "ui_button_click.wav"))
    for game in ("panda-bamboo", "space-cats", "tiny-golf"):
        lib.record_usage(item.asset_id, game, usage_key="sfx/click")

    asset = lib.get_asset(item.asset_id)
    assert [u.project for u in asset.usage] == ["panda-bamboo", "space-cats", "tiny-golf"]
    assert lib.info()["counts"]["files"] == 1
    [(usage, summary)] = lib.project_usage("space-cats")
    assert (usage.variant_id, summary.id) == (item.variant_id, item.asset_id)


def test_variants_and_lineage_preserve_the_original(lib, tmp_path):
    item = _import(lib, make_png(tmp_path / "hero.png", (64, 64)))
    original_path = lib.get_asset(item.asset_id).path
    small = make_png(tmp_path / "hero_32.png", (32, 32))

    variant = lib.add_variant(item.asset_id, small, label="png 32", parent_variant_id=item.variant_id,
                              params={"resize": "32x32"}, make_primary=True)  # fmt: skip
    asset = lib.get_asset(item.asset_id)
    assert [v.label for v in asset.variants] == ["original", "png_32"]
    assert asset.primary_variant.id == variant.id
    assert variant.parent_variant_id == item.variant_id
    assert Path(original_path).is_file()
    with pytest.raises(ConflictError):
        lib.add_variant(item.asset_id, small, label="png_32")

    edited = lib.register_file(
        make_png(tmp_path / "hero_red.png", (64, 64), (255, 0, 0, 255)),
        NewAsset(name="hero red", type="character", origin=Origin.DERIVED),
    )
    lib.add_lineage(edited.asset_id, item.asset_id, LineageRelation.EDITED_FROM)
    assert lib.get_asset(edited.asset_id).lineage[0].asset_id == item.asset_id
    assert lib.get_asset(item.asset_id).lineage[0].direction == "incoming"


def test_register_file_dedupes_generated_content(lib, tmp_path):
    src = make_wav(tmp_path / "gen.wav")
    spec = NewAsset(name="coin", type="sfx", origin=Origin.PROCEDURAL, status="candidate")
    first = lib.register_file(src, spec, params={"seed": 1})
    second = lib.register_file(src, spec, params={"seed": 1})
    assert first.created and not second.created
    assert first.asset_id == second.asset_id


def test_collections_crud(lib, tmp_path):
    a = _import(lib, make_png(tmp_path / "a.png")).asset_id
    b = _import(lib, make_png(tmp_path / "b.png", color=(5, 5, 5, 255))).asset_id

    c = coll.create_collection(lib, "Panda Bamboo", kind="game", project="panda-bamboo")
    assert c.name == "panda-bamboo"
    with pytest.raises(ConflictError):
        coll.create_collection(lib, "panda-bamboo")
    _, added = coll.add_assets(lib, c.id, [a, b, a])
    assert added == 2
    _, removed = coll.remove_assets(lib, "panda-bamboo", [b])
    assert removed == 1
    c, assets = coll.collection_assets(lib, "panda-bamboo")
    assert c.asset_count == 1 and [x.id for x in assets] == [a]
    coll.delete_collection(lib, "panda-bamboo")
    assert coll.list_collections(lib) == []
    assert lib.get_asset(a).collections == []


def test_verify_detects_missing_and_corrupt_files(lib, tmp_path):
    a = lib.get_asset(_import(lib, make_png(tmp_path / "a.png")).asset_id)
    b = lib.get_asset(_import(lib, make_png(tmp_path / "b.png", color=(7, 7, 7, 255))).asset_id)
    assert lib.verify(rehash=True)["ok"]

    os.chmod(a.path, 0o644)
    Path(a.path).unlink()
    os.chmod(b.path, 0o644)
    data = bytearray(Path(b.path).read_bytes())
    data[-1] ^= 0xFF
    Path(b.path).write_bytes(bytes(data))

    report = lib.verify(rehash=True)
    assert not report["ok"]
    assert {i["problem"] for i in report["issues"]} == {"missing", "hash_mismatch"}
