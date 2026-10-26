"""Discovery: visual features, colour/tone/orientation filters, natural-language search, similarity, duplicates."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from assetforge.config import LibraryPaths
from assetforge.core import db, visual
from assetforge.core.catalog import Library
from assetforge.core.models import AssetType, ImportOptions, NewAsset, Origin
from assetforge.core.search import SearchQuery, search
from assetforge.core.storage import ObjectStore
from assetforge.errors import InvalidInputError

from conftest import run_cli


def blob(path: Path, size=(64, 64), fg=(30, 140, 40, 255), bg=(0, 0, 0, 0), shape="ellipse") -> Path:
    """A transparent-background subject (or a full-frame scene when bg is opaque)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, bg)
    draw = ImageDraw.Draw(image)
    w, h = size
    box = (w * 0.2, h * 0.15, w * 0.8, h * 0.9)
    (draw.ellipse if shape == "ellipse" else draw.rectangle)(box, fill=fg)
    draw.rectangle((w * 0.4, h * 0.3, w * 0.5, h * 0.4), fill=(250, 250, 250, 255))
    image.save(path)
    return path


def add(lib: Library, path: Path, **opts) -> str:
    return lib.import_paths([path], ImportOptions(**opts)).items[0].asset_id


@pytest.fixture
def stocked(lib, tmp_path):
    ids = {
        "forest_bg": add(lib, blob(tmp_path / "forest.png", (160, 90), (20, 90, 30, 255), (10, 50, 20, 255), "rect"),
                         type=AssetType.BACKGROUND, category="environment", subcategory="forest", genre="platformer",
                         description="Misty pine forest at dusk"),
        "desert_bg": add(lib, blob(tmp_path / "desert.png", (160, 90), (230, 190, 90, 255), (240, 210, 140, 255), "rect"),
                         type=AssetType.BACKGROUND, category="environment", description="Bright sand dunes"),
        "ghoul": add(lib, blob(tmp_path / "ghoul.png", (64, 96), (40, 30, 50, 255)), type=AssetType.ENEMY,
                     style="dark-fantasy", theme="undead", description="Hunched ghoul"),
        "slime": add(lib, blob(tmp_path / "slime.png", (64, 64), (90, 220, 90, 255)), type=AssetType.ENEMY,
                     style="cartoon", description="Bouncy slime"),
        "coin_icon": add(lib, blob(tmp_path / "coin.png", (32, 32), (240, 200, 30, 255)), type=AssetType.ICON),
    }
    return ids


def ids(result) -> list[str]:
    return [h.id for h in result.results]


def test_features_colors_tone_aspect_and_hash(tmp_path):
    f = visual.features(blob(tmp_path / "g.png", (120, 60), (20, 160, 40, 255)))
    assert f["aspect"] == 2.0 and 0.2 < f["alpha_fraction"] < 0.8
    assert f["colors"][0]["name"] == "green" and len(f["dhash"]) == 16
    assert visual.color_name((10, 10, 10)) == "black" and visual.color_name((128, 128, 128)) == "gray"
    assert visual.color_name((120, 70, 30)) == "brown" and visual.color_name((40, 80, 230)) == "blue"
    assert visual.parse_color("#ff0000") == "red" and visual.parse_color("Grey") == "gray"
    with pytest.raises(InvalidInputError):
        visual.parse_color("mauve-ish")
    assert visual.orientation(2.0) == "landscape" and visual.tone(0.1) == "dark"


def test_file_features_are_stored_and_exposed(lib, stocked):
    f = lib.get_asset(stocked["forest_bg"]).primary_variant.file
    assert f.aspect == pytest.approx(160 / 90, abs=1e-3) and f.dhash and f.colors[0]["name"] == "green"
    assert f.mean_luma < visual.DARK_LUMA  # a dark forest


def test_natural_language_queries_use_tech_colour_and_discovery_terms(lib, stocked):
    assert ids(search(lib, SearchQuery(text="forest backgrounds")))[0] == stocked["forest_bg"]
    hits = ids(search(lib, SearchQuery(text="transparent dark enemy", all_terms=True)))
    assert hits == [stocked["ghoul"]]
    assert ids(search(lib, SearchQuery(text="dark fantasy monster")))[0] == stocked["ghoul"]  # monster ~ enemy
    assert stocked["desert_bg"] in ids(search(lib, SearchQuery(text="yellow landscape", all_terms=True)))
    assert ids(search(lib, SearchQuery(text="platformer environment", all_terms=True))) == [stocked["forest_bg"]]


def test_structured_filters(lib, stocked):
    assert ids(search(lib, SearchQuery(category="environment", subcategory="forest"))) == [stocked["forest_bg"]]
    assert set(ids(search(lib, SearchQuery(orientation="landscape")))) == {stocked["forest_bg"], stocked["desert_bg"]}
    assert ids(search(lib, SearchQuery(types=[AssetType.ENEMY], colors=["green"]))) == [stocked["slime"]]
    assert ids(search(lib, SearchQuery(colors=["#f0c81e"], max_width=40))) == [stocked["coin_icon"]]
    assert ids(search(lib, SearchQuery(tone="light", types=[AssetType.BACKGROUND]))) == [stocked["desert_bg"]]
    assert ids(search(lib, SearchQuery(aspect="16:9"))) and not ids(search(lib, SearchQuery(aspect="1:3")))
    assert ids(search(lib, SearchQuery(theme="undead"))) == [stocked["ghoul"]]
    assert len(ids(search(lib, SearchQuery(formats=["png"], origins=[Origin.IMPORT])))) == 5
    with pytest.raises(InvalidInputError):
        search(lib, SearchQuery(aspect="wide"))


def test_similar_and_duplicates(lib, stocked, tmp_path):
    # the same picture at another size is a near-duplicate; a different subject is not
    twin = add(lib, blob(tmp_path / "slime_big.png", (128, 128), (90, 220, 90, 255)), type=AssetType.ENEMY)
    ranked = visual.similar(lib, stocked["slime"], limit=3)
    assert ranked[0][0] == twin and ranked[0][2] <= visual.NEAR_DUPLICATE_BITS
    assert [d["asset_id"] for d in visual.near_duplicates(lib, stocked["slime"])] == [twin]
    groups = visual.duplicate_groups(lib)
    assert [sorted(g["asset_id"] for g in group) for group in groups] == [sorted([stocked["slime"], twin])]
    hits = search(lib, SearchQuery(similar=stocked["slime"], types=[AssetType.ENEMY]))
    assert ids(hits)[0] == twin and stocked["forest_bg"] not in ids(hits)


def test_cli_search_similar_duplicates_reindex(capsys, home, lib, stocked, tmp_path):
    code, out, _ = run_cli(capsys, "search", "forest", "--genre", "platformer", "--orientation", "landscape", "--json")
    assert code == 0 and [r["id"] for r in out["results"]] == [stocked["forest_bg"]]
    code, out, _ = run_cli(capsys, "search", "--orientation", "diagonal", "--json")
    assert code == 4
    code, out, _ = run_cli(capsys, "similar", stocked["ghoul"], "--limit", "2", "--json")
    assert code == 0 and out["count"] == 2 and "distance" in out["results"][0]
    code, out, _ = run_cli(capsys, "duplicates", "--json")
    assert code == 0 and out["count"] == 0
    lib.conn.execute("UPDATE asset_files SET dhash = NULL, colors = NULL")
    code, out, _ = run_cli(capsys, "reindex", "--json")
    assert code == 0 and out["reindex"] == {"visual_features_computed": 5, "assets_indexed": 5}
    code, out, _ = run_cli(capsys, "update", stocked["slime"], "--category", "Creature", "--genre", "puzzle", "--json")
    assert out["asset"]["category"] == "creature" and out["asset"]["genre"] == "puzzle"


def test_v3_library_migrates_and_reindexes(home, tmp_path):
    """An existing v3 catalog keeps its assets; the rebuilt search index is refilled automatically."""
    paths = LibraryPaths(home)
    paths.ensure()
    stored = ObjectStore(paths).put_file(blob(tmp_path / "old.png", (40, 40), (200, 30, 30, 255)))
    conn = sqlite3.connect(paths.catalog, isolation_level=None)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, statements in db.MIGRATIONS[:3]:
        for statement in statements:
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations VALUES (?, 'then')", (version,))
    rel = str(stored.path.relative_to(paths.objects.parent))
    conn.execute("INSERT INTO asset_files (sha256, rel_path, ext, mime_type, kind, size_bytes, width, height, has_alpha, "
                 "created_at) VALUES (?, ?, '.png', 'image/png', 'image', 1, 40, 40, 1, 'then')", (stored.sha256, rel))  # fmt: skip
    conn.execute("INSERT INTO assets (id, name, type, primary_variant_id, created_at, updated_at) "
                 "VALUES ('ast_00001', 'red_beetle', 'character', 'var_00001', 'then', 'then')")  # fmt: skip
    conn.execute("INSERT INTO asset_variants (id, asset_id, file_sha256, label, created_at) "
                 "VALUES ('var_00001', 'ast_00001', ?, 'original', 'then')", (stored.sha256,))  # fmt: skip
    conn.close()

    with Library(home) as lib:
        assert lib.schema_version == 4
        assert ids(search(lib, SearchQuery(text="beetle"))) == ["ast_00001"]
        assert lib.get_asset("ast_00001").primary_variant.file.dhash is None
        visual.ensure_features(lib, stored.sha256)
        lib.rebuild_fts()
        assert ids(search(lib, SearchQuery(text="red transparent", all_terms=True))) == ["ast_00001"]


def test_register_file_records_discovery_fields(lib, tmp_path):
    reg = lib.register_file(blob(tmp_path / "x.png"), NewAsset(name="crate", type=AssetType.OBJECT, category="Prop",
                                                               theme="sci-fi", genre="shooter"))  # fmt: skip
    asset = lib.get_asset(reg.asset_id)
    assert (asset.category, asset.theme, asset.genre) == ("prop", "sci-fi", "shooter")
    assert ids(search(lib, SearchQuery(text="scifi shooter prop", all_terms=True))) == [reg.asset_id]
