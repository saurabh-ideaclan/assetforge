"""Layered creature voices from installed recordings (a small generated pack stands in for the CC0 packs)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from assetforge.audio import pipeline
from assetforge.audio.creatures import engine, pack
from assetforge.audio.creatures.engine import CreatureRequest, creature_from_words, generate_creature
from assetforge.audio.masters import prune
from assetforge.errors import InvalidInputError
from assetforge.request import AssetRequest, sound_plan

from conftest import run_cli

SR = 44100


def voice(seconds: float, f0: float, *, bright: float = 2000, rng: np.random.Generator) -> np.ndarray:
    """A crude rough voice: harmonics with a wandering pitch, breath noise, an envelope."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    pitch = f0 * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(2, 5) * t))
    phase = 2 * np.pi * np.cumsum(pitch) / SR
    x = sum(np.sin(k * phase) / k for k in range(1, 12) if k * f0 < bright)
    x = x + 0.3 * rng.standard_normal(n) * (bright / 8000)
    return 0.5 * x / np.abs(x).max() * np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 0.5


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    rng = np.random.default_rng(0)
    root = tmp_path / "recordings"
    root.mkdir()
    files = {"growl_01": (1.2, 140, 1500), "growl_02": (1.0, 180, 1500), "snarl_03": (0.9, 200, 1800),
             "snarl_04": (1.1, 160, 1600), "roar_01": (1.6, 120, 2500), "roar_02": (1.4, 150, 2200),
             "grunt_01": (0.6, 60, 240), "grunt_02": (0.7, 55, 220), "attack_01": (0.5, 260, 3000),
             "attack_02": (0.6, 300, 3200), "breath_01": (0.6, 0, 0), "hurt_01": (0.5, 300, 2500),
             "die_01": (1.0, 220, 2000), "slime_01": (0.5, 400, 3000), "slime_02": (0.4, 350, 2800),
             "human_01": (0.6, 200, 2000)}  # fmt: skip
    for name, (seconds, f0, bright) in files.items():
        x = (rng.standard_normal(int(seconds * SR)) * 0.2 if f0 == 0 else voice(seconds, f0, bright=bright, rng=rng))
        soundfile.write(root / f"{name}.wav", x, SR)
    return root


@pytest.fixture
def creature_lib(lib, pack_dir):
    pack.add_folder(lib.paths.home, "test-pack", pack_dir, license_="CC0")
    return lib


def test_recordings_are_cataloged_by_role_and_family(lib, pack_dir):
    info = pack.add_folder(lib.paths.home, "test-pack", pack_dir, license_="CC0")
    clips = {Path(c["path"]).stem: c for c in pack.load_index(lib.paths.home)["clips"]}
    assert info["clips"] == len(clips) and "human_01" not in clips  # people are not creature voices
    assert clips["growl_01"]["roles"] == ["growl"] and "body" in clips["grunt_01"]["roles"]
    assert clips["roar_01"]["roles"][0] == "roar" and clips["breath_01"]["roles"] == ["breath"]
    assert clips["die_01"]["roles"] == ["death"] and clips["slime_01"]["family"] == "slime"
    assert pack.status(lib.paths.home)["ready"]


def test_takes_are_layered_different_blends_that_reproduce(creature_lib):
    res = generate_creature(creature_lib, CreatureRequest(creature="troll", sound="roar", variants=3, seed=4))
    blends = [tuple(layer["clip"] for layer in v["info"]["layers"]) for v in res["variants"]]
    roles = {layer["role"] for v in res["variants"] for layer in v["info"]["layers"]}
    assert {"identity", "body", "intake"} <= roles  # a roar: main voice, weight under it, an inhale before
    assert len(set(blends)) > 1  # takes never all sound alike
    assert all(v["duration_ms"] < 5000 for v in res["variants"])
    assert pipeline.reproduce(creature_lib, res["asset_id"])["ok"]


def centroid(path: str) -> float:
    x, sr = soundfile.read(path, always_2d=True)
    spec = np.abs(np.fft.rfft(x.mean(axis=1))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return float((spec * f).sum() / spec.sum())


def test_bigger_creatures_sound_bigger(creature_lib):
    small = generate_creature(creature_lib, CreatureRequest(creature="goblin", sound="growl", seed=2, format="wav"))
    big = generate_creature(creature_lib, CreatureRequest(creature="goblin", sound="growl", size=1.0, seed=2, format="wav"))
    assert centroid(big["variants"][0]["path"]) < 0.8 * centroid(small["variants"][0]["path"])


def test_small_odd_creatures_stay_themselves(creature_lib):
    res = generate_creature(creature_lib, CreatureRequest(creature="slime", sound="attack", seed=1))
    layers = res["variants"][0]["info"]["layers"]
    assert [layer["role"] for layer in layers] == ["identity"] and "slime" in layers[0]["clip"]


def test_without_recordings_it_says_how_to_install(lib):
    with pytest.raises(InvalidInputError, match="creature recordings") as exc:
        generate_creature(lib, CreatureRequest(creature="wolf"))
    assert "creature install" in exc.value.hint


def test_masters_from_missing_recordings_are_kept(creature_lib):
    res = generate_creature(creature_lib, CreatureRequest(creature="wolf", seed=3))
    assert res["asset_id"] in {i["asset_id"] for i in prune(creature_lib, dry_run=True)["items"]}
    (pack.folder(creature_lib.paths.home) / pack.INDEX).unlink()
    report = prune(creature_lib, dry_run=True)
    assert any(s["asset_id"] == res["asset_id"] and "creature recordings" in s["reason"] for s in report["skipped"])


@pytest.mark.parametrize(("text", "expected"), [
    ("angry cave troll roar", {"creature": "troll", "sound": "roar", "aggression": 0.85}),
    ("small goblin hurt", {"creature": "goblin", "sound": "hurt", "size": 0.25}),
    ("wolf howl", {"creature": "wolf", "sound": "roar"}),
    ("monster growl", {"creature": "beast", "sound": "growl"}),
    ("dragon death", {"creature": "dragon", "sound": "death"}),
    ("player hit", None), ("laser blast", None), ("orc footsteps", None),
])  # fmt: skip
def test_descriptions_to_creature_voices(text, expected):
    assert creature_from_words(text) == expected


def test_requests_use_creature_voices_only_when_recordings_exist(lib, pack_dir):
    req = AssetRequest(type="sfx", description="angry troll roar")
    assert sound_plan(req, lib)["engine"] == "recipe"  # synth roar as the fallback
    pack.add_folder(lib.paths.home, "test-pack", pack_dir, license_="CC0")
    assert sound_plan(req, lib) == {"engine": "creature", "settings": {"creature": "troll", "sound": "roar", "aggression": 0.85}}


def test_install_verifies_pinned_hashes(lib, tmp_path):
    fake = tmp_path / "downloads"
    fake.mkdir()
    (fake / "monster_sfx_pack.zip").write_bytes(b"not the real pack")
    monkey_url = pack.PACKS["ogrebane"]["downloads"][0]
    assert monkey_url["file"] == "monster_sfx_pack.zip"
    res = pack.install(lib.paths.home, ["ogrebane"], source_dir=fake)
    assert "SHA-256" in res["skipped"]["ogrebane"] and not res["installed"]


def test_cli(capsys, home, pack_dir):
    code, out, _ = run_cli(capsys, "creature", "install", "--json")
    assert code != 0 and "--yes" in out["error"]["message"]
    code, out, _ = run_cli(capsys, "creature", "add-folder", str(pack_dir), "--name", "mine", "--license", "CC0", "--json")
    assert code == 0 and out["info"]["clips"] > 10
    code, out, _ = run_cli(capsys, "creature", "list", "--json")
    assert "dragon" in out["creatures"] and "roar" in out["sounds"]
    code, out, _ = run_cli(capsys, "creature", "generate", "wolf", "-s", "growl", "-n", "2", "--seed", "1", "--json")
    assert code == 0 and len(out["variants"]) == 2 and out["asset"]["type"] == "sfx"
    code, out, _ = run_cli(capsys, "creature", "status", "--json")
    assert out["ready"] and out["packs"]["mine"]["license"] == "CC0"


def test_presets_are_complete():
    for name, preset in engine.CREATURES.items():
        assert preset["family"] in pack.FAMILIES and 0 <= preset["size"] <= 1, name
