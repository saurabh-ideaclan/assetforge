"""`library snapshot`: a shareable copy of the library without WAVs, jobs or caches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.audio import codecs, masters
from assetforge.audio.export import pcm_sha256
from assetforge.audio.generate import AudioRequest, generate_audio
from assetforge.core import qc
from assetforge.core.catalog import Library
from assetforge.core.models import ImportOptions
from assetforge.core.snapshot import snapshot
from assetforge.errors import InvalidInputError, PolicyError, StorageError
from assetforge.exporters.generic import ExportRequest, export

from conftest import make_png, make_wav, run_cli

needs_ffmpeg = pytest.mark.skipif(codecs.ffmpeg() is None, reason="ffmpeg not installed")


@pytest.fixture
def stocked(lib, tmp_path, monkeypatch):
    """A synthesized sound (WAV master + OGG), an imported WAV with an OGG copy as its child, and an image."""
    monkeypatch.setattr(codecs, "ffmpeg", lambda: None)  # no M4A twins: keeps the test independent of ffmpeg
    codecs._warned.clear()
    sfx = generate_audio(lib, AudioRequest(recipe="coin", seed=3, format="ogg", status="approved")).asset_id
    song = lib.import_paths([make_wav(tmp_path / "in/song.wav", 300)], ImportOptions(type="music")).items[0].asset_id
    from assetforge.audio.process import process_audio_asset

    process_audio_asset(lib, song, [{"op": "format", "format": "ogg", "quality": 0.6}], label="ogg_of_original")
    image = lib.import_paths([make_png(tmp_path / "in/hero.png")], ImportOptions(type="character")).items[0].asset_id
    monkeypatch.undo()
    return {"sfx": sfx, "song": song, "image": image}


def wavs(folder: Path) -> list[Path]:
    return sorted((folder / "objects").rglob("*.wav"))


def test_snapshot_is_a_working_library_without_wavs(lib, stocked, tmp_path):
    dest = tmp_path / "share"
    report = snapshot(lib, dest)
    assert report["assets"] == 3 and report["files"] == 3 and report["wav"]["pruned"] == 1
    assert [w["asset_id"] for w in report["wav"]["withheld"]] == [stocked["song"]]
    assert not wavs(dest)
    assert {p.name for p in dest.iterdir()} >= {"catalog.sqlite3", "objects", ".gitignore", ".gitattributes", "README.md"}
    assert ".env" in (dest / ".gitignore").read_text()

    with Library(dest) as copy:
        assert copy.verify()["ok"]
        assert all(qc.check_asset(copy, a)["ok"] for a in stocked.values())
        song = copy.get_asset(stocked["song"])
        assert song.primary_variant.label == "ogg_of_original" and Path(song.primary_variant.file.path).is_file()
        original = next(v for v in song.variants if v.label == "original")
        assert original.file.withheld
        with pytest.raises(StorageError, match="not in this copy"):
            masters.ensure_file(copy, original)
        master = next(v for v in copy.get_asset(stocked["sfx"]).variants if v.label == "v1")
        path = masters.ensure_file(copy, master)  # pruned in the snapshot: re-renders identically
        assert pcm_sha256(codecs.read_audio(path, dtype="int16", always_2d=False)[0]) == master.params["pcm_sha256"]

    # the source library is untouched
    assert lib.verify()["ok"] and len(wavs(lib.paths.home)) == 2
    assert lib.get_asset(stocked["song"]).primary_variant.label == "original"


def test_rerun_updates_in_place(lib, stocked, tmp_path):
    dest = tmp_path / "share"
    first = snapshot(lib, dest)
    again = snapshot(lib, dest)
    assert again["copied"] == 0 and again["removed"] == 0 and again["files"] == first["files"]
    assert again["wav"]["carried"] == 2 and again["wav"]["pruned"] == 0 and not wavs(dest)

    image = lib.get_asset(stocked["image"])
    lib.conn.execute("DELETE FROM asset_variants WHERE asset_id = ?", (image.id,))
    lib.conn.execute("DELETE FROM assets WHERE id = ?", (image.id,))
    lib.conn.execute("DELETE FROM asset_files WHERE sha256 = ?", (image.primary_variant.file.sha256,))
    extra = generate_audio(lib, AudioRequest(recipe="jump", seed=4, format="ogg")).asset_id
    third = snapshot(lib, dest)
    assert third["removed"] == 1 and third["copied"] >= 1 and third["wav"]["pruned"] == 1
    with Library(dest) as copy:
        assert copy.verify()["ok"] and copy.get_asset(extra)
        assert not copy.conn.execute("SELECT 1 FROM assets WHERE id = ?", (image.id,)).fetchone()


@needs_ffmpeg
def test_export_from_a_snapshot_uses_existing_copies(lib, tmp_path):
    song = lib.import_paths([make_wav(tmp_path / "in/theme.wav", 300)], ImportOptions(type="music", status="approved"))
    song_id = song.items[0].asset_id
    from assetforge.audio.process import process_audio_asset

    for fmt in ("ogg", "m4a"):
        process_audio_asset(lib, song_id, [{"op": "format", "format": fmt}], label=f"{fmt}_of_original")
    snapshot(lib, tmp_path / "share")
    with Library(tmp_path / "share") as copy:
        before = len(copy.get_asset(song_id).variants)
        result = export(copy, ExportRequest(out=str(tmp_path / "game"), refs=[song_id]))
        assert not result.warnings
        sound = json.loads((tmp_path / "game/sounds.json").read_text())["sounds"][0]
        assert [Path(s).suffix for s in sound["src"]] == [".ogg", ".m4a"]
        assert len(copy.get_asset(song_id).variants) == before  # the stored M4A, not a re-encode of the OGG


def test_refuses_a_catalog_with_a_key(lib, stocked, tmp_path):
    lib.merge_metadata(stocked["image"], {"note": "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"})
    with pytest.raises(PolicyError):
        snapshot(lib, tmp_path / "share")
    assert not (tmp_path / "share/catalog.sqlite3").exists()


def test_refuses_a_folder_inside_the_library(lib):
    with pytest.raises(InvalidInputError):
        snapshot(lib, lib.paths.home / "share")
    with pytest.raises(InvalidInputError):
        snapshot(lib, lib.paths.home)


def test_cli_snapshot(capsys, home, tmp_path):
    code, out, _ = run_cli(capsys, "import", str(make_png(tmp_path / "in/a.png")), "--json")
    assert code == 0
    code, out, _ = run_cli(capsys, "library", "snapshot", str(tmp_path / "share"), "--json")
    assert code == 0 and out["status"] == "ok" and out["snapshot"]["assets"] == 1
    code, out, _ = run_cli(capsys, "--home", str(tmp_path / "share"), "verify", "--json")
    assert code == 0 and out["status"] == "ok"
