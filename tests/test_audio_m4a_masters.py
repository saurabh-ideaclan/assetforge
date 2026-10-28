"""OGG + M4A delivery copies for every take, loop-safe AAC, and pruning/re-rendering WAV masters."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from assetforge.audio import codecs, masters, pipeline
from assetforge.audio.ambience.engine import AmbienceRequest, generate_ambience
from assetforge.audio.export import pcm_sha256
from assetforge.audio.generate import AudioRequest, generate_audio, reproduce_audio
from assetforge.core import qc
from assetforge.core.models import ImportOptions
from assetforge.exporters.generic import ExportRequest, export
from assetforge.validation.audio import loop_seam, seam_ok

from conftest import make_wav, run_cli

needs_ffmpeg = pytest.mark.skipif(codecs.ffmpeg() is None, reason="ffmpeg not installed")


def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(codecs, "ffmpeg", lambda: None)
    codecs._warned.clear()


@needs_ffmpeg
def test_every_take_and_stem_gets_an_m4a_twin(lib):
    sfx = generate_audio(lib, AudioRequest(recipe="coin", variants=2, seed=1, format="ogg"))
    labels = {v.label for v in lib.get_asset(sfx.asset_id).variants}
    assert {"v1_ogg", "v1_m4a", "v2_ogg", "v2_m4a"} <= labels and lib.get_asset(sfx.asset_id).primary_variant.label == "v1_ogg"
    assert all(Path(v.alternates["m4a"]).suffix == ".m4a" for v in sfx.variants)
    amb = generate_ambience(lib, AmbienceRequest(scene="cave", length=4, seed=1, stems=True))
    take = amb["variants"][0]
    assert take["alternates"]["m4a"].endswith(".m4a")
    assert all(x["alternates"]["m4a"].endswith(".m4a") for x in take["extras"].values())


@needs_ffmpeg
def test_m4a_loops_keep_length_alignment_and_seam(lib):
    res = generate_ambience(lib, AmbienceRequest(scene="rain_light", length=4, seed=2))
    asset = lib.get_asset(res["asset_id"])
    by_label = {v.label: v for v in asset.variants}
    wav, sr = codecs.read_audio(by_label["v1"].file.path)
    aac, _ = codecs.read_audio(by_label["v1_m4a"].file.path)
    assert len(aac) == len(wav) == 4 * sr  # no AAC priming or padding left in: loop points still line up
    mono_w, mono_a = wav.mean(axis=1), aac.mean(axis=1)
    lag = int(np.argmax(np.correlate(mono_a[:8000], mono_w[:8000], "full"))) - 7999
    assert lag == 0
    assert seam_ok(loop_seam(aac, sr))


def test_without_ffmpeg_m4a_is_skipped_with_a_warning(lib, monkeypatch, capsys):
    no_ffmpeg(monkeypatch)
    res = generate_audio(lib, AudioRequest(recipe="coin", seed=4, format="ogg"))
    assert {v.label for v in lib.get_asset(res.asset_id).variants} == {"v1", "v1_ogg"}
    assert "ffmpeg not found" in capsys.readouterr().err


@needs_ffmpeg
def test_reexport_adds_m4a_to_older_assets_without_rerendering(lib, tmp_path, monkeypatch):
    no_ffmpeg(monkeypatch)
    old = generate_audio(lib, AudioRequest(recipe="jump", seed=5, format="ogg", status="approved"))
    loop = generate_ambience(lib, AmbienceRequest(scene="cave", length=4, seed=5, status="approved"))
    monkeypatch.undo()
    jobs_before = lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0]
    result = export(lib, ExportRequest(out=str(tmp_path / "game"), refs=[old.asset_id, loop["asset_id"]]))
    assert not result.warnings
    sounds = {s["alias"]: s for s in json.loads((tmp_path / "game/sounds.json").read_text())["sounds"]}
    assert all(s["src"][0].endswith(".ogg") and s["src"][1].endswith(".m4a") for s in sounds.values())
    assert sounds["ambience/cave"]["loopEnd"] == 4.0
    assert lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0] == jobs_before  # nothing re-rendered
    derived = [v for v in lib.get_asset(old.asset_id).variants if v.file.ext == ".m4a"]
    assert derived and derived[0].parent_variant_id == next(v.id for v in lib.get_asset(old.asset_id).variants if v.label == "v1")


@needs_ffmpeg
def test_audio_sprite_has_an_m4a_twin(lib, tmp_path):
    a = generate_audio(lib, AudioRequest(recipe="coin", seed=6, format="ogg", status="approved"))
    b = generate_audio(lib, AudioRequest(recipe="ui_click", seed=6, format="ogg", status="approved"))
    export(lib, ExportRequest(out=str(tmp_path / "g"), refs=[a.asset_id, b.asset_id], audio_sprite="ui"))
    meta = json.loads((tmp_path / "g/ui.json").read_text())
    assert meta["src"] == ["ui.ogg", "ui.m4a"] and (tmp_path / "g/ui.m4a").is_file()


def test_prune_masters_then_rerender_on_demand(lib, tmp_path, monkeypatch):
    no_ffmpeg(monkeypatch)  # keep this test independent of ffmpeg
    sfx = generate_audio(lib, AudioRequest(recipe="laser", variants=2, seed=7, format="ogg"))
    amb = generate_ambience(lib, AmbienceRequest(scene="cave", length=4, seed=7, stems=True))
    imported = lib.import_paths([make_wav(tmp_path / "rec.wav", 400)], ImportOptions(type="sfx")).items[0].asset_id

    plan = masters.prune(lib, dry_run=True)
    pruned_assets = {i["asset_id"] for i in plan["items"]}
    assert pruned_assets == {sfx.asset_id, amb["asset_id"]} and plan["freed_bytes"] > 0
    assert all(Path(v.file.path).is_file() for v in lib.get_asset(sfx.asset_id).variants)  # dry run deletes nothing
    labels = {i["label"] for i in plan["items"] if i["asset_id"] == amb["asset_id"]}
    assert "v1" in labels and any(lab.startswith("v1_stem_") for lab in labels)

    done = masters.prune(lib)
    assert done["count"] == plan["count"]
    master = next(v for v in lib.get_asset(sfx.asset_id).variants if v.label == "v1")
    assert master.file.pruned and not Path(master.file.path).exists()
    assert Path(lib.get_asset(sfx.asset_id).primary_variant.file.path).is_file()  # OGG stays
    assert lib.verify()["ok"] and qc.check_asset(lib, sfx.asset_id)["ok"]
    assert reproduce_audio(lib, sfx.asset_id)["ok"] and pipeline.reproduce(lib, amb["asset_id"])["ok"]
    assert not any(v.file.pruned for v in lib.get_asset(imported).variants)

    path = masters.ensure_file(lib, master)  # anything that needs it re-renders it, byte-identical
    assert path.is_file() and not lib.get_asset(sfx.asset_id).variants[0].file.pruned
    assert pcm_sha256(codecs.read_audio(path, dtype="int16", always_2d=False)[0]) == master.params["pcm_sha256"]
    restored = masters.restore(lib, amb["asset_id"])
    assert restored["restored"] and lib.verify()["ok"]


def test_soundfont_music_is_kept_without_its_soundfont(lib, monkeypatch):
    no_ffmpeg(monkeypatch)
    from assetforge.audio.music.generate import MusicRequest, generate_music

    res = generate_music(lib, MusicRequest(genre="chiptune", bars=2, seed=1, backend="synth"))
    asset = lib.get_asset(res["asset_id"])
    meta = asset.metadata
    meta["generator"]["spec"] = meta["generator"]["spec"] | {"backend": "soundfont", "soundfont": {"sha256": "0" * 64}}
    lib.merge_metadata(asset.id, {"generator": meta["generator"]})
    report = masters.prune(lib, dry_run=True)
    assert asset.id not in {i["asset_id"] for i in report["items"]}
    assert any(s["asset_id"] == asset.id and "SoundFont" in s["reason"] for s in report["skipped"])


def test_cli_prune_and_restore(capsys, home, monkeypatch):
    no_ffmpeg(monkeypatch)
    code, out, _ = run_cli(capsys, "audio", "generate", "-r", "coin", "--seed", "1", "--format", "ogg", "--json")
    asset_id = out["asset_id"]
    code, out, _ = run_cli(capsys, "audio", "prune-masters", "--older-than", "1", "--json")
    assert code == 0 and out["prune"]["count"] == 0  # just made: younger than a day
    code, out, _ = run_cli(capsys, "audio", "prune-masters", "--type", "sfx", "--dry-run", "--json")
    assert code == 0 and out["prune"]["count"] == 1 and out["prune"]["dry_run"]
    code, out, _ = run_cli(capsys, "audio", "prune-masters", "--project", "nope", "--json")
    assert code == 0 and out["prune"]["count"] == 0
    code, out, _ = run_cli(capsys, "audio", "prune-masters", "--json")
    assert code == 0 and out["prune"]["freed_bytes"] > 0
    code, out, _ = run_cli(capsys, "audio", "restore-masters", asset_id, "--json")
    assert code == 0 and out["restored"][0]["restored"]


def test_masters_whose_recipe_changed_are_kept(lib, monkeypatch):
    no_ffmpeg(monkeypatch)
    from dataclasses import replace

    from assetforge.audio.recipes import RECIPES

    res = generate_audio(lib, AudioRequest(recipe="coin", seed=8, format="ogg"))
    monkeypatch.setitem(RECIPES, "coin", replace(RECIPES["coin"], render=lambda ctx, p: np.zeros(ctx.n) + 0.1))
    report = masters.prune(lib)
    assert report["count"] == 0 and any("re-render" in s["reason"] for s in report["skipped"])
    assert all(Path(v.file.path).is_file() for v in lib.get_asset(res.asset_id).variants)
