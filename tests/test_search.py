from __future__ import annotations

from assetforge.core import collections as coll
from assetforge.core.models import AssetPatch, AssetType, FileKind, ImportOptions, Scope, Status
from assetforge.core.search import SearchQuery, search

from conftest import make_png, make_wav


def _ids(result):
    return [hit.id for hit in result.results]


def _import(lib, path, **options):
    return lib.import_paths([path], ImportOptions(**options)).items[0].asset_id


def test_search_ranks_by_coverage_and_returns_paths(lib, pack):
    lib.import_paths([pack])

    result = search(lib, SearchQuery(text="soft cartoon coin pickup sound", limit=5))

    top = result.results[0]
    assert top.name.startswith("coin_pickup_")
    assert top.type == AssetType.SFX
    assert set(top.matched_terms) == {"coin", "pickup", "sound"}
    assert top.coverage == 0.6
    assert top.path and top.primary_variant.file.path == top.path
    assert result.terms == ["soft", "cartoon", "coin", "pickup", "sound"]


def test_search_filters(lib, pack):
    lib.import_paths([pack])

    images = search(lib, SearchQuery(text="panda", kind=FileKind.IMAGE, transparent=True, limit=100))
    assert images.count == 20
    assert all(hit.primary_variant.file.has_alpha for hit in images.results)

    buttons = search(lib, SearchQuery(tags=["buttons"], limit=500))
    assert buttons.count == 40

    long_audio = search(lib, SearchQuery(min_duration_ms=900, kind=FileKind.AUDIO))
    assert {hit.name for hit in long_audio.results} == {"jungle_theme", "long_ambient_bed"}

    music = search(lib, SearchQuery(text="jungle", types=[AssetType.MUSIC]))
    assert _ids(music) and all(hit.type == AssetType.MUSIC for hit in music.results)

    strict = search(lib, SearchQuery(text="coin jump", all_terms=True))
    assert strict.count == 0


def test_restricted_rejected_and_project_scoped_assets_are_hidden_by_default(lib, tmp_path):
    shared = _import(lib, make_wav(tmp_path / "laser_shot.wav", freq=900))
    restricted = _import(lib, make_wav(tmp_path / "laser_blast.wav", freq=950), commercial_use="restricted")
    rejected = _import(lib, make_wav(tmp_path / "laser_zap.wav", freq=990))
    lib.set_status([rejected], Status.REJECTED)
    other_game = _import(lib, make_wav(tmp_path / "laser_beam.wav", freq=1000), scope=Scope.PROJECT, project="space-cats")
    mine = _import(lib, make_wav(tmp_path / "laser_pew.wav", freq=1100), scope=Scope.PROJECT, project="panda-bamboo")

    assert set(_ids(search(lib, SearchQuery(text="laser")))) == {shared, other_game, mine}
    assert set(_ids(search(lib, SearchQuery(text="laser", project="panda-bamboo")))) == {shared, mine}
    assert restricted in _ids(search(lib, SearchQuery(text="laser", include_restricted=True)))
    assert _ids(search(lib, SearchQuery(text="laser", statuses=[Status.REJECTED]))) == [rejected]


def test_style_scope_and_style_preference(lib, tmp_path):
    plain = _import(lib, make_png(tmp_path / "tree_a.png", color=(1, 1, 1, 255)))
    jungle = _import(lib, make_png(tmp_path / "tree_b.png", color=(2, 2, 2, 255)), style="cute-jungle")
    styled_only = _import(
        lib, make_png(tmp_path / "tree_c.png", color=(3, 3, 3, 255)), scope=Scope.STYLE, style="cute-jungle"
    )

    ranked = search(lib, SearchQuery(text="tree", style="cute-jungle"))
    assert set(_ids(ranked)[:2]) == {jungle, styled_only}
    assert _ids(ranked)[2] == plain

    as_project = search(lib, SearchQuery(text="tree", project="any-game"))
    assert styled_only not in _ids(as_project)
    as_project_with_style = search(lib, SearchQuery(text="tree", project="any-game", style="cute-jungle"))
    assert styled_only in _ids(as_project_with_style)
    assert set(_ids(search(lib, SearchQuery(text="tree", style="cute-jungle", strict_style=True)))) == {
        jungle,
        styled_only,
    }


def test_search_sees_metadata_updates_and_collections(lib, tmp_path):
    asset_id = _import(lib, make_png(tmp_path / "thing.png"))
    assert search(lib, SearchQuery(text="golden trophy")).count == 0

    lib.update_asset(asset_id, AssetPatch(description="A shiny golden trophy", add_tags=["reward"]))
    assert _ids(search(lib, SearchQuery(text="golden trophy"))) == [asset_id]
    assert _ids(search(lib, SearchQuery(text="rewards"))) == [asset_id]  # porter stemming

    coll.create_collection(lib, "victory-screen")
    coll.add_assets(lib, "victory-screen", [asset_id])
    assert _ids(search(lib, SearchQuery(collection="victory-screen"))) == [asset_id]


def test_synonyms(lib, tmp_path):
    asset_id = _import(lib, make_png(tmp_path / "forest_bg.png"))
    assert _ids(search(lib, SearchQuery(text="forest background"))) == [asset_id]
