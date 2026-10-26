"""Background keying, image QC and prompt building."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from assetforge.core.models import AssetType, StyleProfile
from assetforge.image.prompts import build_prompt, pick_key_colour
from assetforge.image.transparency import remove_background
from assetforge.validation.image import analyze_image


def keyed_scene(bg=(255, 0, 255)) -> Image.Image:
    image = Image.new("RGB", (100, 100), bg)
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, 80, 80), fill=(30, 160, 60))
    draw.rectangle((45, 45, 55, 55), fill=bg)  # key-coloured detail enclosed by the subject
    return image


def test_remove_background_keeps_enclosed_key_colour_and_softens_edges():
    result = remove_background(keyed_scene(), "#FF00FF")
    alpha = np.asarray(result.image)[..., 3]
    assert alpha[0, 0] == 0 and alpha[99, 99] == 0
    assert alpha[50, 50] == 255  # inside the subject: kept although it is key-coloured
    assert alpha[50, 30] == 255
    assert 0.5 < result.removed_fraction < 0.8
    assert result.background_rgb == (255, 0, 255)
    rgb = np.asarray(result.image)[..., :3]
    assert rgb[0, 0].tolist() == [0, 0, 0]


def test_remove_background_trusts_an_off_key_but_flat_border():
    result = remove_background(keyed_scene(bg=(240, 20, 235)), "#FF00FF")
    assert result.background_rgb == (240, 20, 235)
    assert np.asarray(result.image)[0, 0, 3] == 0 and result.border_uniformity < 1


def test_image_qc():
    sprite = remove_background(keyed_scene(), "#FF00FF").image
    qc = analyze_image(sprite, asset_type=AssetType.CHARACTER, expect_transparency=True)
    assert qc["ok"] and qc["issues"] == ["excess_padding"] and qc["has_alpha"]
    assert qc["subject_bbox"] == [20, 20, 81, 81]

    opaque = keyed_scene()
    qc = analyze_image(opaque, asset_type=AssetType.CHARACTER, expect_transparency=True)
    assert "no_transparency" in qc["issues"]

    blank = analyze_image(Image.new("RGB", (50, 50), (9, 9, 9)))
    assert blank["issues"] == ["blank"] and not blank["ok"]
    empty = analyze_image(Image.new("RGBA", (50, 50), (0, 0, 0, 0)))
    assert empty["issues"] == ["empty"]

    cut = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    ImageDraw.Draw(cut).rectangle((0, 10, 30, 40), fill=(200, 10, 10, 255))
    ImageDraw.Draw(cut).rectangle((5, 15, 10, 20), fill=(10, 10, 200, 255))
    assert "touches_edge" in analyze_image(cut, asset_type=AssetType.SPRITE, expect_transparency=True)["issues"]


def test_prompt_templates():
    native = build_prompt("Cute cartoon beetle, front view", asset_type=AssetType.CHARACTER, transparent=True,
                          native_transparency=True, style_name="cute-jungle")  # fmt: skip
    assert native.final.startswith("Cute cartoon beetle, front view. A single game character")
    assert "Transparent background" in native.final and "Art direction: cute jungle." in native.final
    assert native.key_colour is None

    keyed = build_prompt("pink flamingo", asset_type=AssetType.SPRITE, transparent=True, native_transparency=False)
    assert keyed.key_colour == "#00FF00" and "solid pure green (#00FF00) background" in keyed.final

    profile = StyleProfile(id="sty_00001", name="cute-jungle", description="chunky cartoon with thick outlines",
                           palette=["#4CAF50", "#FFEB3B"], instructions="Soft shading only",
                           created_at="t", updated_at="t")  # fmt: skip
    styled = build_prompt("tree", asset_type=AssetType.BACKGROUND, transparent=False, native_transparency=True,
                          profile=profile, has_style_refs=True, negative="people, text")  # fmt: skip
    assert "Art direction: cute jungle. chunky cartoon with thick outlines." in styled.final
    assert "Soft shading only." in styled.final
    assert "#4CAF50, #FFEB3B" in styled.final and "Image 1: art-style reference only" in styled.final
    assert "Avoid: people, text." in styled.final and "Transparent" not in styled.final

    raw = build_prompt("exactly this", asset_type=AssetType.ICON, transparent=True, native_transparency=True, raw=True)
    assert raw.final == "exactly this" and raw.key_colour is None
    # raw prompts still get the key-colour sentence when the background has to be keyed out
    raw_keyed = build_prompt("exactly this", asset_type=AssetType.ICON, transparent=True, native_transparency=False,
                             raw=True)  # fmt: skip
    assert raw_keyed.final.startswith("exactly this. Render the subject") and raw_keyed.key_colour == "#FF00FF"

    assert pick_key_colour("a green frog", []) == ("magenta", "#FF00FF")
