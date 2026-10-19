from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from assetforge.core.models import ImportOptions, Status
from assetforge.errors import ConflictError, InvalidInputError
from assetforge.image import process as P

from conftest import make_png


def sprite(size=(100, 80), box=(30, 20, 60, 50), colour=(220, 40, 40, 255)) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=colour)
    return image


def test_trim_with_margin_and_opaque_borders():
    trimmed = P.op_trim(sprite())
    assert trimmed.size == (31, 31)
    assert P.op_trim(sprite(), margin=5).size == (41, 41)
    edge = P.op_trim(sprite(box=(0, 0, 9, 9)), margin=3)  # margin past the canvas is padded transparently
    assert edge.size == (16, 16) and np.asarray(edge)[0, 0, 3] == 0
    flat = Image.new("RGB", (50, 50), (10, 120, 200))
    ImageDraw.Draw(flat).rectangle((10, 15, 19, 24), fill=(250, 250, 0))
    assert P.op_trim(flat).size == (10, 10)
    with pytest.raises(InvalidInputError):
        P.op_trim(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("contain", (256, 128)), ("cover", (256, 128)), ("pad", (256, 256))],
)
def test_resize_modes_never_stretch(mode, expected):
    image = Image.new("RGBA", (400, 200), (0, 128, 255, 255))
    out = P.op_resize(image, 256, 128 if mode == "cover" else 256, mode=mode)
    assert out.size == expected
    if mode == "pad":
        alpha = np.asarray(out)[..., 3]
        assert alpha[0, 0] == 0 and alpha[128, 128] == 255  # letterboxed, content centred


def test_one_sided_resize_scale_and_upscale_guard():
    image = Image.new("RGBA", (400, 200), (0, 128, 255, 255))
    assert P.op_resize(image, width=100).size == (100, 50)
    assert P.op_resize(image, height=100).size == (200, 100)
    assert P.op_resize(image, 800, 800, upscale=False).size == (400, 200)
    pixel = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    pixel.putpixel((1, 0), (255, 255, 255, 255))
    doubled = np.asarray(P.op_scale(pixel, 4, resample="nearest"))
    assert set(np.unique(doubled[..., 0])) == {0, 255}  # pixel art stays crisp


def test_resize_premultiplies_alpha_so_edges_do_not_darken():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((0, 0, 31, 63), fill=(255, 255, 255, 255))
    small = np.asarray(P.op_resize(image, 16, 16))
    edge = small[8, 8]
    assert 0 < edge[3] < 255 or edge[3] == 255
    partial = small[small[..., 3] > 10]
    assert partial[..., :3].min() > 240  # no grey fringe from transparent black


def test_recolor_flip_rotate_crop_pad():
    image = sprite(colour=(220, 40, 40, 255))
    shifted = np.asarray(P.op_recolor(image, hue=120))
    r, g, b, a = shifted[30, 40]
    assert g > r and a == 255 and shifted[0, 0, 3] == 0
    grey = np.asarray(P.op_recolor(image, grayscale=True))[30, 40]
    assert grey[0] == grey[1] == grey[2]
    tinted = np.asarray(P.op_recolor(image, tint="#0000FF", tint_strength=1.0))[30, 40]
    assert tinted[2] > tinted[0]
    assert P.op_flip(image, "vertical").size == (100, 80)
    assert P.op_rotate(image, 90).size == (80, 100)
    assert P.op_crop(image, 10, 10, 20, 30).size == (20, 30)
    assert P.op_pad(image, margin=10).size == (120, 100)
    with pytest.raises(InvalidInputError):
        P.op_crop(image, 90, 0, 20, 20)
    with pytest.raises(InvalidInputError):
        P.op_rotate(image, 45)


def test_validate_ops():
    assert P.validate_ops([{"op": "remove-background"}])[0]["op"] == "remove_background"
    for bad in ([], [{"op": "explode"}], [{"op": "trim", "size": 3}], [{"op": "format"}, {"op": "trim"}],
                [{"op": "format", "format": "gif"}], ["trim"]):  # fmt: skip
        with pytest.raises(InvalidInputError):
            P.validate_ops(bad)


def test_encoding(tmp_path):
    image = sprite()
    png = P.encode(image, tmp_path / "a", {"format": "png"})
    webp = P.encode(image, tmp_path / "b", {"format": "webp"})
    jpg = P.encode(image, tmp_path / "c", {"format": "jpg", "background": "#000000"})
    assert png.suffix == ".png" and webp.suffix == ".webp" and jpg.suffix == ".jpg"
    assert Image.open(webp).mode == "RGBA"
    assert np.asarray(Image.open(webp))[30, 40].tolist() == [220, 40, 40, 255]  # lossless by default
    assert Image.open(jpg).mode == "RGB" and Image.open(jpg).getpixel((0, 0))[0] < 10


def _sprite_asset(lib, tmp_path, **options):
    path = tmp_path / "hero.png"
    sprite((200, 160), (60, 40, 120, 100)).save(path)
    return lib.import_paths([path], ImportOptions(type="sprite", **options)).items[0].asset_id


def test_process_asset_creates_traceable_idempotent_variants(lib, tmp_path):
    asset_id = _sprite_asset(lib, tmp_path)
    ops = P.expand_preset("web-sprite")
    first = P.process_image_asset(lib, asset_id, ops, make_primary=True)
    asset = lib.get_asset(asset_id)
    variant = next(v for v in asset.variants if v.id == first.variant_id)
    assert first.label == "trim_webp" and not first.reused
    assert variant.parent_variant_id == asset.variants[0].id and variant.params["ops"][0]["op"] == "trim"
    assert asset.primary_variant.id == first.variant_id and Path(asset.path).suffix == ".webp"
    assert Image.open(first.path).size == (65, 65)

    again = P.process_image_asset(lib, asset_id, ops)
    assert again.reused and again.variant_id == first.variant_id
    with pytest.raises(ConflictError):
        P.process_image_asset(lib, asset_id, [{"op": "trim"}, {"op": "format", "format": "webp"}],
                              label="trim_webp", variant="original")  # fmt: skip
    from_original = P.process_image_asset(lib, asset_id, [{"op": "scale", "factor": 0.5}], variant="original")
    assert Image.open(from_original.path).size == (100, 80)
    assert lib.get_asset(asset_id).primary_variant.id == first.variant_id  # unchanged without make_primary


def test_new_asset_derivatives_inherit_restrictions_and_link(lib, tmp_path):
    asset_id = _sprite_asset(lib, tmp_path, commercial_use="restricted", license="CC-BY-NC", status=Status.APPROVED)
    derived = P.process_image_asset(lib, asset_id, [{"op": "recolor", "hue": 90}], new_asset=True, name="hero_teal")
    asset = lib.get_asset(derived.asset_id)
    assert derived.new_asset and asset.name == "hero_teal" and asset.origin == "derived"
    assert asset.commercial_use == "restricted" and asset.license == "CC-BY-NC"
    assert (asset.lineage[0].relation, asset.lineage[0].asset_id) == ("derived_from", asset_id)
    assert "derived" in asset.tags


def test_processing_rejects_non_images(lib, tmp_path):
    from conftest import make_wav

    audio_id = lib.import_paths([make_wav(tmp_path / "x.wav")], ImportOptions()).items[0].asset_id
    with pytest.raises(InvalidInputError):
        P.process_image_asset(lib, audio_id, [{"op": "trim"}])
    with pytest.raises(InvalidInputError):
        P.process_image_asset(lib, _sprite_asset(lib, tmp_path), [{"op": "trim"}], variant="nope")
    make_png  # noqa: B018  (keep the shared helper import used)
