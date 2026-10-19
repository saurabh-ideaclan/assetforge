"""QC checks, QC-gated approval, picking candidates, previews and lineage graphs."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile
from PIL import Image

from assetforge.config import Settings
from assetforge.core import lineage, previews, qc, review
from assetforge.core.models import ImportOptions, Status
from assetforge.errors import PolicyError
from assetforge.image import engine, providers
from assetforge.image import process as image_proc
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.providers.fake import FakeProvider

from conftest import make_png, make_wav


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider(Settings({}), name="openai")
    providers.OVERRIDES["openai"] = provider
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    return provider


def imported(lib, path, **options):
    return lib.import_paths([path], ImportOptions(**options)).items[0].asset_id


def test_qc_flags_blank_images_and_blocks_approval(lib, tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (64, 64), (12, 12, 12)).save(blank)
    asset_id = imported(lib, blank, status="candidate")
    report = qc.check_asset(lib, asset_id)
    assert not report["ok"] and report["errors"] == ["blank"]
    assert lib.get_asset(asset_id).metadata["qc"]["errors"] == ["blank"]
    assert lib.get_asset(asset_id).history[0].event == "qc"

    with pytest.raises(PolicyError) as exc:
        review.approve(lib, [asset_id])
    assert exc.value.code == "POLICY_VIOLATION" and lib.get_asset(asset_id).status == Status.CANDIDATE
    review.approve(lib, [asset_id], force=True, note="placeholder art")
    asset = lib.get_asset(asset_id)
    assert asset.status == Status.APPROVED and "forced" in asset.history[0].data["note"]


def test_qc_audio_errors_and_loop_warnings(lib, tmp_path):
    silent = tmp_path / "silent.wav"
    soundfile.write(str(silent), np.zeros(8000), 8000)
    assert qc.check_asset(lib, imported(lib, silent))["errors"] == ["silent"]

    clipped = tmp_path / "clipped.wav"
    soundfile.write(str(clipped), np.clip(3 * np.sin(np.linspace(0, 300, 8000)), -1, 1), 8000)
    assert "clipping" in qc.check_asset(lib, imported(lib, clipped))["errors"]

    loop = tmp_path / "loop.wav"
    t = np.arange(48000) / 48000
    soundfile.write(str(loop), 0.5 * np.sin(2 * np.pi * 110.25 * t), 48000)
    report = qc.check_asset(lib, imported(lib, loop, type="music"))
    assert report["ok"] and "loop_click" in report["warnings"]
    assert report["metrics"]["loudness_lufs"] < 0

    good = qc.check_asset(lib, imported(lib, make_wav(tmp_path / "ok.wav")))
    assert good["ok"] and good["errors"] == []


def test_qc_missing_files_and_variant_choice(lib, tmp_path):
    asset_id = imported(lib, make_png(tmp_path / "a.png", (64, 64)), type="sprite")
    image_proc.process_image_asset(lib, asset_id, [{"op": "scale", "factor": 0.5}], label="half")
    assert qc.check_asset(lib, asset_id, variant="half")["label"] == "half"
    path = lib.get_asset(asset_id).path
    import os

    os.chmod(path, 0o644)
    os.remove(path)
    assert qc.check_asset(lib, asset_id)["errors"] == ["missing_file"]


def test_pick_approves_one_candidate_and_rejects_its_siblings(lib, fake):
    result = generate_image(lib, ImageRequest(prompt="treasure chest", type="sprite", n=3, provider="openai"),
                            Settings({}))  # fmt: skip
    ids = [a.asset_id for a in result.assets]
    other = generate_image(lib, ImageRequest(prompt="other thing", type="sprite", provider="openai"), Settings({}))
    assert review.siblings(lib, ids[1]) == [ids[0], ids[2]]

    queue = review.queue(lib)
    assert [a.id for a, _ in queue] == [*ids, other.assets[0].asset_id]

    picked = review.pick(lib, ids[1], note="best lid")
    assert picked["asset_ids"] == [ids[1]] and picked["rejected"] == [ids[0], ids[2]]
    statuses = [lib.get_asset(i).status for i in ids]
    assert statuses == [Status.REJECTED, Status.APPROVED, Status.REJECTED]
    assert lib.get_asset(other.assets[0].asset_id).status == Status.CANDIDATE
    assert lib.get_asset(ids[0]).history[0].data["note"] == f"not picked; {ids[1]} was chosen"


def test_approve_can_switch_the_primary_variant(lib, tmp_path):
    asset_id = imported(lib, make_png(tmp_path / "a.png", (64, 64)), type="sprite", status="candidate")
    half = image_proc.process_image_asset(lib, asset_id, [{"op": "scale", "factor": 0.5}])
    review.approve(lib, [asset_id], primary=half.variant_id)
    assert lib.get_asset(asset_id).primary_variant.id == half.variant_id


def test_lineage_graph(lib, tmp_path, fake):
    base = generate_image(lib, ImageRequest(prompt="hero", type="character", provider="openai"), Settings({}))
    base_id = base.assets[0].asset_id
    trimmed = image_proc.process_image_asset(lib, base_id, [{"op": "trim"}], make_primary=True)
    teal = image_proc.process_image_asset(lib, base_id, [{"op": "recolor", "hue": 90}], new_asset=True)
    smaller = image_proc.process_image_asset(lib, teal.asset_id, [{"op": "scale", "factor": 0.5}], new_asset=True)

    graph = lineage.lineage(lib, base_id)
    assert graph["ancestors"] == []
    [child] = graph["descendants"]
    assert child["asset_id"] == teal.asset_id and child["relation"] == "derived_from"
    assert child["children"][0]["asset_id"] == smaller.asset_id
    [root_variant] = graph["variants"]
    assert root_variant["label"] == "original" and root_variant["children"][0]["variant_id"] == trimmed.variant_id
    assert root_variant["children"][0]["primary"] and root_variant["children"][0]["ops"] == ["trim"]

    up = lineage.lineage(lib, smaller.asset_id)
    assert up["ancestors"][0]["asset_id"] == teal.asset_id
    assert up["ancestors"][0]["parents"][0]["asset_id"] == base_id


def test_previews(lib, tmp_path):
    image_id = imported(lib, make_png(tmp_path / "a.png", (300, 150)), type="sprite")
    audio_id = imported(lib, make_wav(tmp_path / "b.wav", duration_ms=400))
    assets = [lib.get_asset(image_id), lib.get_asset(audio_id)]
    qc.check_asset(lib, image_id)

    thumb = previews.thumbnail(lib, assets[0].primary_variant)
    assert thumb.is_file() and max(Image.open(thumb).size) == 256
    assert previews.thumbnail(lib, assets[0].primary_variant) == thumb  # cached
    wave = Image.open(previews.thumbnail(lib, assets[1].primary_variant))
    assert wave.size == (256, 128)

    sheet = previews.contact_sheet(lib, [lib.get_asset(i) for i in (image_id, audio_id)], tmp_path / "s.png",
                                   columns=2, title="Review")  # fmt: skip
    assert Image.open(sheet).size == (2 * (256 + 10) + 10, 36 + 256 + 64 + 20)
    page = previews.gallery_html(lib, assets, tmp_path / "g.html").read_text()
    assert "<audio controls" in page and f"assetforge review approve {image_id}" in page
    assert assets[0].primary_variant.file.path in page.replace("file://", "")
