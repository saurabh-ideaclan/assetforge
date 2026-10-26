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
    assert any(v.label == "aligned_4x2_64x64_bottom" and v.file.width == 256 for v in sheet_asset.variants)
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
