from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile
from PIL import Image, ImageDraw

from assetforge.core import collections as coll
from assetforge.core.models import AssetPatch, ImportOptions, Status
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError
from assetforge.exporters import atlas as A
from assetforge.exporters.generic import ExportRequest, export

from conftest import make_wav, run_cli


def blob(size=(64, 64), box=(16, 8, 47, 55), colour=(200, 60, 60, 255)) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse(box, fill=colour)
    return image


# ---------------------------------------------------------------------- atlas packing


def test_frames_are_trimmed_with_offsets():
    frame = A.make_frame("hero", blob())
    assert frame.size == (32, 48) and frame.offset == (16, 8) and frame.source_size == (64, 64) and frame.trimmed
    untouched = A.make_frame("full", Image.new("RGBA", (10, 10), (1, 2, 3, 255)))
    assert not untouched.trimmed


def _no_overlap(sheet: A.Sheet, padding: int) -> None:
    boxes = [(x, y, x + f.size[0], y + f.size[1]) for f, x, y in sheet.placements]
    for i, a in enumerate(boxes):
        assert a[0] >= padding and a[1] >= padding and a[2] <= sheet.width and a[3] <= sheet.height
        for b in boxes[i + 1 :]:
            assert a[2] + padding <= b[0] or b[2] + padding <= a[0] or a[3] + padding <= b[1] or b[3] + padding <= a[1]


def test_pack_single_sheet_is_tight_and_non_overlapping():
    rng = np.random.default_rng(1)
    frames = [A.make_frame(f"f{i}", Image.new("RGBA", (int(rng.integers(8, 90)), int(rng.integers(8, 90))),
                                               (9, 9, 9, 255))) for i in range(40)]  # fmt: skip
    [sheet] = A.pack(frames, max_size=1024, padding=2)
    _no_overlap(sheet, 2)
    used = sum(f.size[0] * f.size[1] for f in frames)
    assert used / (sheet.width * sheet.height) > 0.5
    [pot] = A.pack(frames, max_size=1024, padding=2, power_of_two=True)
    assert pot.width & (pot.width - 1) == 0 and pot.height & (pot.height - 1) == 0


def test_pack_spills_into_multiple_pages_and_rejects_huge_frames():
    frames = [A.make_frame(f"big{i}", Image.new("RGBA", (100, 100), (9, 9, 9, 255))) for i in range(12)]
    sheets = A.pack(frames, max_size=256, padding=2)
    assert len(sheets) >= 3 and sum(len(s.placements) for s in sheets) == 12
    for sheet in sheets:
        _no_overlap(sheet, 2)
        assert sheet.width <= 256 and sheet.height <= 256
    with pytest.raises(InvalidInputError):
        A.pack([A.make_frame("huge", Image.new("RGBA", (300, 10), (1, 1, 1, 255)))], max_size=256)
    with pytest.raises(InvalidInputError):
        A.pack([A.make_frame("x", blob()), A.make_frame("x", blob())])


def test_build_atlas_json_and_animations():
    frames = [A.make_frame(n, blob()) for n in ("walk_02", "walk_01", "walk_03", "icon")]
    files, summary = A.build_atlas("hero", frames, formats=["webp", "png"])
    names = sorted(f.path.as_posix() for f in files)
    assert names == ["hero.png", "hero.png.json", "hero.webp", "hero.webp.json"]
    data = json.loads(next(f.data for f in files if f.path.name == "hero.png.json"))
    assert data["meta"]["image"] == "hero.png" and data["meta"]["scale"] == "1"
    assert data["animations"] == {"walk": ["walk_01", "walk_02", "walk_03"]}
    walk = data["frames"]["walk_01"]
    assert walk["trimmed"] and walk["sourceSize"] == {"w": 64, "h": 64} and walk["spriteSourceSize"]["x"] == 16
    assert summary["animations"] == {"walk": ["walk_01", "walk_02", "walk_03"]}

    multi, summary = A.build_atlas("big", [A.make_frame(f"b{i}", Image.new("RGBA", (100, 100), (5, 5, 5, 255)))
                                           for i in range(6)], formats=["png"], max_size=256)  # fmt: skip
    pages = [json.loads(f.data) for f in multi if f.path.suffix == ".json"]
    assert len(pages) == len(summary["pages"]) > 1
    assert pages[0]["meta"]["related_multi_packs"] == [f"big-{i}.png.json" for i in range(1, len(pages))]


# ---------------------------------------------------------------------- export


@pytest.fixture
def game(lib, tmp_path):
    """A small approved game collection: two sprites, a walk cycle, a background, a sound."""
    src = tmp_path / "src"
    src.mkdir()
    ids = {}
    for name, kind, colour in (("beetle", "character", (200, 60, 60, 255)), ("coin_icon", "icon", (240, 200, 30, 255))):
        blob(colour=colour).save(src / f"{name}.png")
        ids[name] = lib.import_paths([src / f"{name}.png"], ImportOptions(type=kind, collection="jungle")).items[0].asset_id
    for i in (1, 2, 3):
        blob(box=(10 + i * 4, 8, 40 + i * 4, 55)).save(src / f"panda_walk_{i:02d}.png")
        lib.import_paths([src / f"panda_walk_{i:02d}.png"], ImportOptions(type="character", collection="jungle"))
    bg = Image.new("RGB", (640, 360), (40, 120, 60))
    ImageDraw.Draw(bg).rectangle((0, 250, 640, 360), fill=(90, 60, 30))
    bg.save(src / "jungle.png")
    ids["bg"] = lib.import_paths([src / "jungle.png"], ImportOptions(type="background", key="levels/jungle",
                                                                    collection="jungle")).items[0].asset_id  # fmt: skip
    ids["sfx"] = lib.import_paths([make_wav(src / "coin.wav", 300)], ImportOptions(
        collection="jungle", license="CC-BY-4.0", attribution="Sound by Alex", commercial_use="allowed")).items[0].asset_id
    return ids


def test_export_collection_with_atlas_and_pixi_manifest(lib, game, tmp_path):
    out = tmp_path / "game" / "public" / "assets"
    result = export(lib, ExportRequest(out=str(out), collection="jungle", project="panda-bamboo", atlas="sprites",
                                       typescript=str(tmp_path / "game" / "src" / "assets.ts")))  # fmt: skip
    assert result.bundle == "jungle" and result.counts["atlas_frames"] == 5 and not result.skipped
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == [
        "ASSET_CREDITS.md", "assetforge-export.json", "levels/jungle.png", "levels/jungle.webp", "manifest.json",
        "sfx/coin.m4a", "sfx/coin.ogg", "sounds.json", "sprites.png", "sprites.png.json", "sprites.webp",
        "sprites.webp.json",
    ]  # fmt: skip
    assert Image.open(out / "levels/jungle.webp").size == (640, 360)
    assert soundfile.info(str(out / "sfx/coin.ogg")).format == "OGG"
    from assetforge.audio.codecs import read_audio

    assert read_audio(out / "sfx/coin.m4a")[0].shape[0] > 0  # AAC in MP4 for Safari/iOS

    manifest = json.loads((out / "manifest.json").read_text())
    [bundle] = manifest["bundles"]
    assert bundle["name"] == "jungle"
    assert {"alias": "levels/jungle", "src": "levels/jungle.{webp,png}"} in bundle["assets"]
    assert {"alias": "sprites", "src": "sprites.{webp,png}.json"} in bundle["assets"]
    assert not any(a["src"].endswith((".ogg", ".mp3", ".m4a")) for a in bundle["assets"])
    sheet = json.loads((out / "sprites.png.json").read_text())
    assert set(sheet["frames"]) == {"characters/beetle", "icons/coin_icon", "characters/panda_walk_01",
                                    "characters/panda_walk_02", "characters/panda_walk_03"}  # fmt: skip
    assert sheet["animations"] == {"characters/panda_walk": ["characters/panda_walk_01", "characters/panda_walk_02",
                                                             "characters/panda_walk_03"]}  # fmt: skip
    sounds = json.loads((out / "sounds.json").read_text())
    assert sounds == {"sounds": [{"alias": "sfx/coin", "src": ["sfx/coin.ogg", "sfx/coin.m4a"]}]}
    assert "Sound by Alex" in (out / "ASSET_CREDITS.md").read_text()
    ts = (tmp_path / "game" / "src" / "assets.ts").read_text()
    assert '"characters/panda_walk"' in ts and "export type SoundAlias" in ts

    usage = {u.usage_key for u, _ in lib.project_usage("panda-bamboo")}
    assert {"levels/jungle", "sfx/coin", "characters/beetle"} <= usage
    variants = [v.label for v in lib.get_asset(game["bg"]).variants]
    assert variants == ["original", "webp_of_original"]  # the web copy is a cataloged derivative


def test_reexport_is_incremental_and_clean_only_removes_owned_files(lib, game, tmp_path):
    out = tmp_path / "assets"
    export(lib, ExportRequest(out=str(out), collection="jungle"))
    variant_count = lib.info()["counts"]["variants"]
    again = export(lib, ExportRequest(out=str(out), collection="jungle"))
    assert again.counts["written"] == 0 and again.counts["unchanged"] > 0
    assert lib.info()["counts"]["variants"] == variant_count  # derivatives reused

    (out / "notes.txt").write_text("mine")
    coll.remove_assets(lib, "jungle", [game["bg"]])
    cleaned = export(lib, ExportRequest(out=str(out), collection="jungle", clean=True))
    assert set(cleaned.removed) == {"levels/jungle.png", "levels/jungle.webp"}
    assert not (out / "levels").exists() and (out / "notes.txt").read_text() == "mine"


def test_foreign_files_are_protected(lib, game, tmp_path):
    out = tmp_path / "assets"
    (out / "sfx").mkdir(parents=True)
    (out / "sfx" / "coin.ogg").write_bytes(b"hand-made sound")
    with pytest.raises(ConflictError):
        export(lib, ExportRequest(out=str(out), refs=[game["sfx"]]))
    result = export(lib, ExportRequest(out=str(out), refs=[game["sfx"]], overwrite=True))
    assert result.counts["written"] >= 2


def test_filters_layout_and_dry_run(lib, game, tmp_path):
    lib.set_status([game["beetle"]], Status.CANDIDATE)
    lib.update_asset(game["coin_icon"], AssetPatch(commercial_use="restricted"))
    out = tmp_path / "dry"
    result = export(lib, ExportRequest(out=str(out), collection="jungle", layout="flat", image_formats=["png"],
                                       dry_run=True))  # fmt: skip
    assert not out.exists() and {f.action for f in result.files} == {"planned"}
    reasons = {s["asset_id"]: s["reason"] for s in result.skipped}
    assert "candidate" in reasons[game["beetle"]] and "restricted" in reasons[game["coin_icon"]]
    paths = {f.path for f in result.files}
    assert "panda_walk_01.png" in paths and "levels/jungle.png" in paths  # flat, but keys still win

    included = export(lib, ExportRequest(out=str(out), collection="jungle", include_candidates=True,
                                         allow_restricted=True, image_formats=["png"], audio_formats=["wav"]))  # fmt: skip
    assert not included.skipped and (out / "characters/beetle.png").is_file() and (out / "sfx/coin.wav").is_file()


def test_max_image_size_and_name_collisions(lib, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    Image.new("RGB", (800, 400), (10, 20, 30)).save(a / "tree.png")
    big = Image.new("RGB", (800, 400), (30, 20, 10))
    ImageDraw.Draw(big).line((0, 0, 799, 399), fill=(255, 255, 255), width=5)
    big.save(b / "tree.png")
    ids = [lib.import_paths([p], ImportOptions(type="image")).items[0].asset_id for p in (a / "tree.png", b / "tree.png")]
    result = export(lib, ExportRequest(out=str(tmp_path / "out"), refs=ids, image_formats=["png"], max_image_size=256))
    assert sorted(f.path for f in result.files) == ["images/tree.png", "images/tree_2.png"]
    assert Image.open(tmp_path / "out" / "images/tree.png").size == (256, 128)


def test_nothing_to_export(lib, tmp_path):
    with pytest.raises(InvalidInputError):
        export(lib, ExportRequest(out=str(tmp_path / "o")))
    with pytest.raises(ValueError):
        ExportRequest(out="x", image_formats=["gif"])
    with pytest.raises(NotFoundError):
        export(lib, ExportRequest(out=str(tmp_path / "o"), project="nobody"))


def test_cli_export(capsys, home, tmp_path, monkeypatch):
    src = tmp_path / "beetle.png"
    blob().save(src)
    run_cli(capsys, "import", str(src), "--type", "sprite", "--collection", "bugs", "--json")
    out = tmp_path / "public" / "assets"
    code, result, err = run_cli(capsys, "export", "--collection", "bugs", "--out", str(out), "--atlas", "bugs-atlas",
                                "--image-format", "png", "--json")  # fmt: skip
    assert code == 0, err
    assert result["counts"]["atlas_frames"] == 1 and Path(result["out"], "bugs-atlas.png.json").is_file()
    code, result, _ = run_cli(capsys, "export", "--collection", "nope", "--out", str(out), "--json")
    assert code == 3
    code, result, _ = run_cli(capsys, "export", "--collection", "bugs", "--out", str(out), "--image-format", "tiff",
                              "--json")  # fmt: skip
    assert code == 4
    monkeypatch.setenv("COLUMNS", "200")
    code, text, _ = run_cli(capsys, "export", "--collection", "bugs", "--out", str(out), "--atlas", "bugs-atlas")
    assert code == 0 and "atlas bugs-atlas" in text
