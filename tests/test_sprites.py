"""Sprite sheets → aligned frames → animations, and their export (atlas anchors, animations.json)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from assetforge.core import collections as coll
from assetforge.core.models import AssetType, ImportOptions
from assetforge.errors import InvalidInputError
from assetforge.exporters.generic import ExportRequest, export
from assetforge.image.sprites import AnimationSpec, frame_names, parse_animation, slice_asset, slice_image

from conftest import run_cli

CELL = (100, 120)


def sheet(path: Path, columns=4, rows=2, *, skip: set[int] = frozenset(), opaque=False, caption: int | None = None):
    """A grid of subjects of uneven height and position (like a model's output), on transparency or magenta."""
    bg = (255, 0, 255, 255) if opaque else (0, 0, 0, 0)
    image = Image.new("RGBA", (columns * CELL[0], rows * CELL[1]), bg)
    draw = ImageDraw.Draw(image)
    for i in range(columns * rows):
        if i in skip:
            continue
        x0, y0 = (i % columns) * CELL[0], (i // columns) * CELL[1]
        h = 50 + (i % 3) * 12  # heights 50/62/74
        top = y0 + 15 + (i % 2) * 9  # and not on a common baseline
        draw.rectangle((x0 + 30, top, x0 + 66, top + h), fill=(40 + 20 * i, 90, 160, 255))
        draw.ellipse((x0 + 38, top + 6, x0 + 48, top + 16), fill=(250, 250, 250, 255))
        if i == caption:
            draw.rectangle((x0 + 35, top + h + 8, x0 + 60, top + h + 12), fill=(10, 10, 10, 255))  # text below
    image.save(path)
    return path


def bottom_row(image: Image.Image) -> int:
    alpha = np.asarray(image.getchannel("A"))
    return int(np.nonzero(alpha.any(axis=1))[0].max())


def test_slice_aligns_frames_on_one_baseline_with_a_shared_scale(tmp_path):
    with Image.open(sheet(tmp_path / "s.png")) as image:
        frames, report = slice_image(image.copy(), 4, 2, cell=96, anchor="bottom", margin=4)
    # the test subjects differ in height by 32%, which the report flags (fine for crouch/jump poses)
    assert report.found == 8 and report.issues == ["inconsistent_frame_size"] and report.cell == (96, 96)
    assert {f.size for f in frames} == {(96, 96)}
    assert {bottom_row(f) for f in frames} == {96 - 4 - 1}  # feet on one baseline
    heights = [np.ptp(np.nonzero(np.asarray(f.getchannel("A")).any(axis=1))[0]) + 1 for f in frames]
    # one shared scale: the tallest frame fills the cell, the others keep their relative height
    assert max(heights) == 96 - 8 and heights[0] / heights[2] == pytest.approx(51 / 75, abs=0.03)


def test_slice_reports_missing_frames_and_keys_opaque_sheets(tmp_path):
    with Image.open(sheet(tmp_path / "s.png", skip={5}, opaque=True, caption=2)) as image:
        frames, report = slice_image(image.copy(), 4, 2, anchor="center")
    assert frames[5] is None and report.empty_cells == [5] and "missing_frames" in report.issues
    assert report.found == 7 and "dropped_pieces" in report.issues  # the caption under frame 2 is dropped
    corner = frames[0].getpixel((0, 0))
    assert corner[3] == 0  # the magenta backdrop was removed


def test_names_and_animation_specs():
    assert parse_animation("walk=0-3@8") == ("walk", AnimationSpec(frames=[0, 1, 2, 3], fps=8, loop=True))
    assert parse_animation("Die=6-7:once")[1].loop is False
    with pytest.raises(InvalidInputError):
        parse_animation("walk 0..3")
    names = frame_names("hero", 8, animations={"walk": AnimationSpec(frames=[0, 1, 2, 3]),
                                               "jump": AnimationSpec(frames=[4, 5])})  # fmt: skip
    assert names[:5] == ["hero_walk_01", "hero_walk_02", "hero_walk_03", "hero_walk_04", "hero_jump_01"]
    assert names[7] == "hero_08"
    assert frame_names("hero", 2, names=["Idle pose, arms down", "wave"]) == ["hero_idle_pose_arms_down", "hero_wave"]
    with pytest.raises(InvalidInputError):
        frame_names("hero", 4, animations={"walk": AnimationSpec(frames=[3, 4])})


@pytest.fixture
def knight_sheet(lib, tmp_path):
    return lib.import_paths([sheet(tmp_path / "knight_sheet.png")], ImportOptions(
        type=AssetType.SPRITESHEET, project="castle", style="pixel-art", genre="platformer")).items[0].asset_id  # fmt: skip


def test_slice_asset_creates_frames_metadata_and_aligned_sheet(lib, knight_sheet):
    outcome = slice_asset(lib, knight_sheet, columns=4, rows=2, cell=64,
                          animations={"walk": AnimationSpec(frames=[0, 1, 2, 3], fps=8),
                                      "attack": AnimationSpec(frames=[4, 5, 6], fps=12, loop=False)})  # fmt: skip
    assert [f["name"] for f in outcome.frames][:5] == ["knight_walk_01", "knight_walk_02", "knight_walk_03",
                                                        "knight_walk_04", "knight_attack_01"]  # fmt: skip
    frame = lib.get_asset(outcome.frames[0]["asset_id"])
    assert frame.type == AssetType.ANIMATION and frame.project == "castle" and frame.genre == "platformer"
    assert frame.metadata["frame"]["pivot"] == [0.5, 0.9375] and frame.metadata["frame"]["fps"] == 8
    assert any(l.asset_id == knight_sheet and l.relation == "derived_from" for l in frame.lineage)
    assert (frame.primary_variant.file.width, frame.primary_variant.file.height) == (64, 64)
    sheet_asset = lib.get_asset(knight_sheet)
    meta = sheet_asset.metadata["animation"]
    assert meta["animations"]["attack"]["loop"] is False and meta["cell"] == [64, 64]
    assert any(v.label == "aligned_4x2_64x64_bottom_shared" and v.file.width == 256 for v in sheet_asset.variants)
    _, members = coll.collection_assets(lib, outcome.collection)
    assert len(members) == 8
    # slicing again with the same settings reuses everything (content dedupe)
    again = slice_asset(lib, knight_sheet, columns=4, rows=2, cell=64,
                        animations={"walk": AnimationSpec(frames=[0, 1, 2, 3], fps=8),
                                    "attack": AnimationSpec(frames=[4, 5, 6], fps=12, loop=False)})  # fmt: skip
    assert not any(f["created"] for f in again.frames)


def test_export_frames_to_atlas_with_anchors_and_animations(lib, knight_sheet, tmp_path):
    outcome = slice_asset(lib, knight_sheet, columns=4, rows=2, cell=64,
                          animations={"walk": AnimationSpec(frames=[0, 1, 2, 3], fps=8)})  # fmt: skip
    out = tmp_path / "game" / "assets"
    result = export(lib, ExportRequest(out=str(out), collection=outcome.collection, include_candidates=True,
                                       atlas="knight", image_formats=["png"], layout="flat"))  # fmt: skip
    atlas = json.loads((out / "knight.png.json").read_text())
    assert atlas["animations"]["knight_walk"] == [f"knight_walk_0{i}" for i in range(1, 5)]
    assert atlas["frames"]["knight_walk_01"]["anchor"] == {"x": 0.5, "y": 0.9375}
    assert atlas["frames"]["knight_walk_01"]["sourceSize"] == {"w": 64, "h": 64}
    anims = json.loads((out / "animations.json").read_text())
    assert anims["knight_walk"] == {"frames": [f"knight_walk_0{i}" for i in range(1, 5)], "fps": 8.0, "loop": True,
                                    "anchor": [0.5, 0.9375], "cell": [64, 64]}  # fmt: skip
    assert result.outputs["animations"] == "animations.json"


def test_cli_slice_and_assemble(capsys, home, lib, knight_sheet, tmp_path):
    code, out, _ = run_cli(capsys, "sprite", "slice", knight_sheet, "--grid", "4x2", "-a", "run=0-3@12",
                           "--cell", "48", "--json")  # fmt: skip
    assert code == 0 and out["slice"]["animations"]["run"]["fps"] == 12 and out["slice"]["report"]["found"] == 8
    frames = [f["asset_id"] for f in out["slice"]["frames"]][4:8]
    code, out, _ = run_cli(capsys, "sprite", "assemble", *frames, "--name", "knight idle", "--fps", "6",
                           "--anchor", "center", "--json")  # fmt: skip
    assert code == 0 and out["assemble"]["animations"]["knight_idle"]["fps"] == 6
    sheet_asset = lib.get_asset(out["assemble"]["sheet_asset_id"])
    cell_w = out["assemble"]["report"]["cell"][0]
    assert sheet_asset.type == AssetType.SPRITESHEET and sheet_asset.primary_variant.file.width == 4 * cell_w
    assert run_cli(capsys, "sprite", "slice", knight_sheet, "--grid", "four", "--json")[0] == 4


def icon_sheet(path: Path, columns=6, rows=3, cell=(90, 90)):
    """A sheet of separate, differently sized items (like an AI icon set)."""
    image = Image.new("RGBA", (columns * cell[0], rows * cell[1]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for i in range(columns * rows):
        x0, y0 = (i % columns) * cell[0], (i // columns) * cell[1]
        size = 30 + (i % 4) * 10
        draw.ellipse((x0 + 45 - size // 2, y0 + 45 - size // 2, x0 + 45 + size // 2, y0 + 45 + size // 2),
                     fill=(30 + 12 * i, 200 - 8 * i, 90, 255))  # fmt: skip
    image.save(path)
    return path


def test_prompt_grid_and_items():
    from assetforge.image.sprites import prompt_grid, prompt_items

    text = ("A sheet of 18 icons in a neat grid of 6 columns and 3 rows. Row 1, left to right: 1. a gold token coin "
            "with a star; 2. a red joystick on a black base; 3. a grey cartridge")  # fmt: skip
    assert prompt_grid(text) == (6, 3) and prompt_grid("a 4x2 grid of poses") == (4, 2)
    assert prompt_grid("3 rows by 5 columns") == (5, 3) and prompt_grid("a jungle scene") is None
    assert prompt_items(text) == ["a gold token coin with a star", "a red joystick on a black base", "a grey cartridge"]


def test_grid_is_detected_and_wrong_grids_are_refused(lib, tmp_path):
    from assetforge.image.sprites import detect_grid, plan_slice

    path = icon_sheet(tmp_path / "icons_sheet.png")
    with Image.open(path) as image:
        guess = detect_grid(image.copy())
    assert (guess.columns, guess.rows, guess.subjects) == (6, 3, 18)
    asset_id = lib.import_paths([path], ImportOptions(type=AssetType.SPRITESHEET)).items[0].asset_id
    with pytest.raises(InvalidInputError) as err:
        slice_asset(lib, asset_id, columns=4, rows=2)  # would glue icons together
    assert err.value.details["suggested_grid"] == [6, 3]
    plan = plan_slice(lib, asset_id)
    assert (plan.columns, plan.rows, plan.source, plan.fit) == (6, 3, "detected", "each") and len(plan.boxes) == 18
    outcome = slice_asset(lib, asset_id)
    assert outcome.grid == [6, 3] and outcome.report["found"] == 18 and not outcome.report["issues"]
    frame = lib.get_asset(outcome.frames[0]["asset_id"])
    assert frame.type == AssetType.ICON and frame.metadata["frame"]["fit"] == "each"


def test_reslicing_archives_the_old_frames_and_restore_brings_them_back(lib, tmp_path):
    from assetforge.core import review

    asset_id = lib.import_paths([icon_sheet(tmp_path / "gems_sheet.png", columns=3, rows=2)],
                                ImportOptions(type=AssetType.SPRITESHEET)).items[0].asset_id  # fmt: skip
    first = slice_asset(lib, asset_id, columns=3, rows=2, cell=64)
    second = slice_asset(lib, asset_id, columns=3, rows=2, cell=96)  # different frames
    assert sorted(second.archived) == sorted(f["asset_id"] for f in first.frames)
    assert {lib.get_asset(a).status for a in second.archived} == {"archived"}
    restored = review.restore(lib, second.archived[:1])["restored"]
    assert list(restored.values()) == ["approved"]  # the status it had before
    third = slice_asset(lib, asset_id, columns=3, rows=2, cell=96, replace_previous=False)
    assert third.archived == []


def test_frames_live_under_their_sheet(lib, tmp_path):
    from assetforge.core import review
    from assetforge.core.browse import BrowseFilters, browse
    from assetforge.core.search import SearchQuery, search

    sheet_id = lib.import_paths([icon_sheet(tmp_path / "loot_sheet.png", columns=3, rows=2)],
                                ImportOptions(type=AssetType.SPRITESHEET)).items[0].asset_id  # fmt: skip
    other = lib.import_paths([icon_sheet(tmp_path / "solo.png", columns=1, rows=1)]).items[0].asset_id
    outcome = slice_asset(lib, sheet_id, names=["sword", "shield", "potion", "key", "map", "bow"])
    frame_ids = [f["asset_id"] for f in outcome.frames]
    assert all(lib.get_asset(f).parent_asset_id == sheet_id for f in frame_ids)
    assert lib.get_asset(sheet_id).children == 6 and lib.summaries([sheet_id])[0].children == 6

    total, items = browse(lib, BrowseFilters(group=True))
    assert {a.id for a in items} == {sheet_id, other} and total == 2  # frames tucked inside the sheet
    assert browse(lib, BrowseFilters())[0] == 8  # ungrouped: everything
    assert [a.id for a in browse(lib, BrowseFilters(group=True, q="potion"))[1]] == [sheet_id]  # found via its frame
    assert len(browse(lib, BrowseFilters(parent=sheet_id))[1]) == 6
    # agents still find frames on their own, with the link to the sheet
    hits = search(lib, SearchQuery(text="potion")).results
    assert hits[0].id == frame_ids[2] and hits[0].parent_asset_id == sheet_id

    # archiving the sheet takes its frames along; restoring brings back only those
    review.archive(lib, [frame_ids[5]], note="not needed")
    result = review.archive(lib, [sheet_id])
    assert sorted(result["parts"]) == sorted(frame_ids[:5])
    assert browse(lib, BrowseFilters(group=True))[0] == 1
    restored = review.restore(lib, [sheet_id])["restored"]
    assert set(restored) == {sheet_id, *frame_ids[:5]} and lib.get_asset(frame_ids[5]).status == "archived"
