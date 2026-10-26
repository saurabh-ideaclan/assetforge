from __future__ import annotations

from pathlib import Path

import pytest

from assetforge.core.models import AssetType, ImportOptions, Scope
from assetforge.errors import ConflictError, InvalidInputError, NotFoundError

from conftest import PACK_DUPLICATES, PACK_UNIQUE, make_png, make_wav


def _by_name(report, filename):
    return next(i for i in report.items if Path(i.path).name == filename)


def test_import_pack_catalogs_unique_files_and_detects_duplicates(lib, pack):
    report = lib.import_paths([pack])

    assert report.status == "ok"
    assert report.summary["created"] == PACK_UNIQUE
    assert report.summary["duplicate"] == PACK_DUPLICATES
    assert report.summary["ignored"] == 1  # README.txt; hidden files are skipped silently
    assert report.summary["error"] == 0
    info = lib.info()
    assert info["counts"]["assets"] == PACK_UNIQUE
    assert info["counts"]["files"] == PACK_UNIQUE

    dup = _by_name(report, "copy_of_blue_button_00.png")
    original = _by_name(report, "blue_button_00.png")
    assert dup.action == "duplicate"
    assert dup.duplicate_of == original.asset_id
    assert dup.sha256 == original.sha256


def test_import_infers_types_tags_and_file_metadata(lib, pack):
    report = lib.import_paths([pack])

    expected = {
        "blue_button_00.png": AssetType.UI,
        "panda_idle_00.png": AssetType.CHARACTER,
        "jungle_bg_00.png": AssetType.BACKGROUND,
        "bamboo_00.png": AssetType.OBJECT,  # it sits in an Objects folder
        "coin_pickup_00.wav": AssetType.SFX,
        "jungle_theme.wav": AssetType.MUSIC,
        "long_ambient_bed.wav": AssetType.AMBIENCE,
    }
    for filename, asset_type in expected.items():
        assert _by_name(report, filename).type == asset_type, filename

    button = lib.get_asset(_by_name(report, "blue_button_00.png").asset_id)
    assert button.name == "blue_button_00"
    assert button.tags == ["buttons", "ui"]
    assert button.primary_variant.file.width == 32
    assert button.primary_variant.file.height == 12
    assert button.primary_variant.file.has_alpha is False
    assert Path(button.path).is_file()
    assert Path(button.path).read_bytes() == (pack / "UI" / "Buttons" / "blue_button_00.png").read_bytes()

    transparent = lib.get_asset(_by_name(report, "panda_idle_01.png").asset_id)
    assert transparent.primary_variant.file.has_alpha is True

    coin = lib.get_asset(_by_name(report, "coin_pickup_00.wav").asset_id)
    assert coin.primary_variant.file.duration_ms == 150
    assert coin.primary_variant.file.sample_rate == 8000
    assert coin.primary_variant.file.channels == 1
    assert coin.primary_variant.file.mime_type.startswith("audio/")
    assert coin.history[0].event == "created"


def test_reimport_creates_nothing(lib, pack):
    lib.import_paths([pack])
    again = lib.import_paths([pack])

    assert again.summary["created"] == 0
    assert again.summary["duplicate"] == PACK_UNIQUE + PACK_DUPLICATES
    assert lib.info()["counts"]["assets"] == PACK_UNIQUE


def test_dry_run_writes_nothing_and_reports_in_batch_duplicates(lib, pack):
    report = lib.import_paths([pack], ImportOptions(dry_run=True))

    assert report.dry_run
    assert report.summary["would_create"] == PACK_UNIQUE
    assert report.summary["duplicate"] == PACK_DUPLICATES
    assert lib.info()["counts"]["assets"] == 0
    assert lib.store.iter_objects() == []


def test_corrupt_file_is_reported_and_others_still_import(lib, tmp_path):
    good = make_png(tmp_path / "in" / "good.png")
    bad = tmp_path / "in" / "broken.png"
    bad.write_bytes(b"not really a png")

    report = lib.import_paths([good.parent])

    assert report.status == "partial"
    assert _by_name(report, "good.png").action == "created"
    broken = _by_name(report, "broken.png")
    assert broken.action == "error"
    assert broken.error["code"] == "INVALID_INPUT"


def test_explicit_options_apply(lib, tmp_path):
    src = make_wav(tmp_path / "x.wav")
    report = lib.import_paths(
        [src],
        ImportOptions(
            name="Beetle Hit",
            key="enemy/beetle/hit",
            description="Soft crunchy cartoon insect hit",
            tags=["Insect", "cartoon"],
            scope=Scope.PROJECT,
            project="Panda Bamboo",
            style="cute-jungle",
            license="CC0-1.0",
            commercial_use="allowed",
            collection="panda-bamboo",
        ),
    )
    asset = lib.get_asset(report.items[0].asset_id)

    assert (asset.name, asset.key, asset.project, asset.style) == (
        "beetle_hit", "enemy/beetle/hit", "panda-bamboo", "cute-jungle",
    )  # fmt: skip
    assert asset.tags == ["cartoon", "insect"]
    assert asset.collections == ["panda-bamboo"]
    assert asset.license == "CC0-1.0"
    assert lib.get_asset("panda-bamboo:enemy/beetle/hit").id == asset.id


def test_import_validation_errors(lib, tmp_path):
    a = make_png(tmp_path / "a.png")
    b = make_png(tmp_path / "b.png", color=(1, 2, 3, 255))

    with pytest.raises(InvalidInputError):
        lib.import_paths([a, b], ImportOptions(name="both"))
    with pytest.raises(InvalidInputError):
        lib.import_paths([a], ImportOptions(scope=Scope.PROJECT))
    with pytest.raises(NotFoundError):
        lib.import_paths([tmp_path / "missing"])

    lib.import_paths([a], ImportOptions(key="ui/a"))
    report = lib.import_paths([b], ImportOptions(key="ui/a"))
    assert report.items[0].error["code"] == ConflictError.code
