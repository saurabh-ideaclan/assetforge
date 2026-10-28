"""Agents get SFX, ambience and music through `request`; export ships loops, stems, takes and sprites."""

from __future__ import annotations

import json

from assetforge.audio import infer
from assetforge.core.models import Status
from assetforge.exporters.generic import ExportRequest, export
from assetforge.request import AssetRequest, handle, sound_plan

from conftest import run_cli


def test_inference_from_descriptions():
    amb = infer.ambience_from_words("rainy forest at night, 30s")
    assert amb["scene"] == "forest_night" and amb["beds"][0]["bed"] == "rain" and amb["length"] == 30
    assert amb["time_of_day"] == "night"
    music = infer.music_from_words("tense boss fight 140bpm loop in D minor, 16 bars")
    assert music == {"genre": "boss_fight", "mood": "tense", "tempo": 140.0, "key": "D minor", "bars": 16, "energy": 0.8}
    jingle = infer.music_from_words("victory jingle for the chiptune game")
    assert jingle["stinger"] == "victory" and jingle["form"] == "stinger" and jingle["genre"] == "chiptune"
    patch = infer.patch_from_words("deep rising metallic hum, 2s")
    assert patch["duration"] == 2 and patch["oscillators"][0]["wave"] == "fm" and patch["pitch"][-1][1] == 12


def test_sound_plan_routes_each_type(lib):
    plan = sound_plan(AssetRequest(type="sfx", description="heavy wooden door slam"), lib)
    assert plan == {"engine": "recipe", "recipe": "door", "preset": "heavy"}
    plan = sound_plan(AssetRequest(type="sfx", description="xyzzy plonk wobble"), lib)
    assert plan["engine"] == "design" and plan["inferred"] == "patch"
    plan = sound_plan(AssetRequest(type="ambience", description="busy tavern", sound={"length": 20}), lib)
    assert plan["settings"] == {"scene": "tavern", "intensity": 0.85, "length": 20}
    plan = sound_plan(AssetRequest(type="music", description="anything", sound={"score": {"tracks": []}}), lib)
    assert plan["settings"] == {"score": {"tracks": []}}


def test_request_generates_then_reuses_audio(lib):
    req = AssetRequest(type="ambience", description="quiet cave with dripping water", project="crypt",
                       sound={"length": 6})  # fmt: skip
    dry = handle(lib, req.model_copy(update={"dry_run": True}))
    assert dry.would == "generate" and dry.plan["engine"] == "ambience" and dry.plan["preview"]["scene"] == "cave"
    made = handle(lib, req)
    assert made.state == "ready" and made.generated["kind"] == "ambience" and made.generated["audio"]["loop"]
    again = handle(lib, req)
    assert again.reused and again.reuse_reason == "request"
    music = handle(lib, AssetRequest(type="music", description="calm fantasy village loop", sound={"bars": 4,
                                                                                                  "backend": "synth"}))  # fmt: skip
    assert music.generated["audio"]["bars"] == 4 and music.generated["audio"]["loop"]
    patch = handle(lib, AssetRequest(type="sfx", description="xyzzy plonk wobble"))
    assert patch.generated["inferred"] == "patch" and patch.variants[0]["format"] == "ogg"
    lib.set_status([made.asset_id], Status.APPROVED)
    reuse = handle(lib, AssetRequest(type="ambience", description="cave dripping water", project="crypt"))
    assert reuse.reused and reuse.asset_id == made.asset_id


def test_export_loops_stems_takes_and_sprite(lib, tmp_path):
    from assetforge.audio.ambience.engine import AmbienceRequest, generate_ambience
    from assetforge.audio.generate import AudioRequest, generate_audio

    amb = generate_ambience(lib, AmbienceRequest(scene="cave", length=5, seed=1, stems=True, status="approved"))
    sfx = generate_audio(lib, AudioRequest(recipe="footstep", variants=3, seed=2, format="ogg", status="approved"))
    coin = generate_audio(lib, AudioRequest(recipe="coin", seed=3, format="ogg", status="approved"))
    out = tmp_path / "game"
    result = export(lib, ExportRequest(out=str(out), refs=[amb["asset_id"], sfx.asset_id, coin.asset_id],
                                       audio_formats=["ogg"], audio_takes=True, audio_sprite="sfx_sprite"))  # fmt: skip
    sounds = {s["alias"]: s for s in json.loads((out / "sounds.json").read_text())["sounds"]}
    cave = sounds["ambience/cave"]
    assert cave["loop"] and cave["loopEnd"] == 5.0 and "stem_drip" in cave["extras"]
    assert (out / cave["extras"]["stem_drip"][0]).is_file()
    assert len(sounds["sfx/footstep"]["variations"]) == 2
    sprite = json.loads((out / "sfx_sprite.json").read_text())
    assert set(sprite["sprite"]) == {"coin", "footstep"} and (out / "sfx_sprite.ogg").is_file()
    assert result.outputs["audio_sprite"] == "sfx_sprite.json"


def test_cli_request_sound_options_and_guide(capsys, home):
    code, out, _ = run_cli(capsys, "request", "--type", "music", "-d", "victory jingle", "--sound", "backend=synth",
                           "--sound", "energy=0.9", "--json")  # fmt: skip
    assert code == 0 and out["generated"]["audio"]["kind"] == "stinger"
    code, out, _ = run_cli(capsys, "guide", "--json")
    names = [m["name"] for m in out["guide"]["methods"]]
    assert {"audio_ambience", "music_composer", "sound_design", "soundfont"} <= set(names)
    code, out, _ = run_cli(capsys, "methods", "--json")
    assert code == 0
