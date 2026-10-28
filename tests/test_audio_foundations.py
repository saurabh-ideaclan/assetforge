"""Audio foundations: features for discovery, loops, mastering, music theory, sound profiles, audio search filters."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from assetforge import sound
from assetforge.audio import dsp, features, looping, master
from assetforge.audio.loudness import integrated_loudness
from assetforge.audio.music import theory
from assetforge.core.models import AssetType, ImportOptions
from assetforge.core.search import SearchQuery, search
from assetforge.errors import InvalidInputError
from assetforge.validation.audio import loop_seam

from conftest import run_cli

SR = 22050


def pulse_track(bpm: float, seconds: float, freqs=(220.0, 261.63, 329.63)) -> np.ndarray:
    """Plucks on every beat over a sustained A-minor triad."""
    t = np.arange(int(seconds * SR)) / SR
    x = 0.15 * sum(np.sin(2 * np.pi * f * t) for f in freqs)
    hit = np.sin(2 * np.pi * 440 * t[:1500]) * np.exp(-t[:1500] * 40)
    for beat in np.arange(0, seconds, 60 / bpm):
        i = int(beat * SR)
        x[i : i + len(hit)] += hit[: len(x) - i]
    return x


def write(path: Path, x: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(path), x, sr)
    return path


def test_features_estimate_tempo_key_and_texture():
    f = features.analyze(pulse_track(120, 10), SR)
    assert abs(f["bpm"] - 120) < 3 and f["key"] == "A minor"
    noise = features.analyze(np.random.default_rng(0).uniform(-0.3, 0.3, SR * 4), SR)
    assert noise["flatness"] > 0.35 and "key" not in noise and "noisy" in features.tech_terms(
        '{"flatness": 0.5, "duration_s": 4, "channels": 1}')  # fmt: skip
    assert features.tech_terms('{"duration_s": 30, "channels": 2, "key": "D minor"}', loop=True) == [
        "stereo", "long", "loop", "minor"]  # fmt: skip


def test_loops_are_seamless_and_master_hits_targets():
    rng = np.random.default_rng(3)
    length, fade = SR * 4, SR // 2
    bed = dsp.filt(rng.uniform(-1, 1, length + fade), "lowpass", 800, SR)
    loop = looping.crossfade_loop(dsp.as_stereo(bed), length, fade)
    seam = loop_seam(loop, SR)
    assert len(loop) == length and not seam["loop_click"] and seam["loop_level_jump_db"] < 1.5
    ringing = np.zeros(length + SR)
    ringing[length - 100 : length + SR] = np.exp(-np.arange(SR + 100) / 3000)
    folded = looping.fold_loop(ringing, length)
    assert folded[0] > 0.9 and len(folded) == length
    y, report = master.master(dsp.as_stereo(bed[:length]) * 0.01, SR, target_lufs=-24, loop=True)
    assert abs(integrated_loudness(y, SR) + 24) < 0.5 and report["gain_db"] > 0 and len(y) == length
    loud, report = master.master(pulse_track(120, 4) * 3, SR, target_lufs=-10)
    assert np.max(np.abs(loud)) <= 10 ** (-1 / 20) + 1e-9


def test_theory_keys_chords_and_meter():
    a_minor = theory.parse_key("Am")
    assert a_minor.name == "A minor" and a_minor.mode == "minor"
    assert [c.root for c in theory.progression("i VI bII V7", a_minor)] == [9, 5, 10, 4]
    assert theory.chord("V7", theory.parse_key("C")).intervals == (0, 4, 7, 10)
    assert theory.parse_key("F# dorian").name == "F# dorian" and theory.note_number("Bb3") == 58
    assert theory.parse_meter("6/8").beats_per_bar == 3 and theory.parse_meter("6/8").compound
    with pytest.raises(InvalidInputError):
        theory.parse_key("H minor")


def test_sound_profiles_merge_and_cli(capsys, home, lib, tmp_path):
    profile = sound.set_profile(lib, "crypt-run", sound.SoundDirection(genre="horror", space="cave", key="dm",
                                                                       tempo=[90, 120]))  # fmt: skip
    assert profile.direction.key == "D minor" and profile.direction.space_effects()[0]["name"] == "reverb"
    merged, layers = sound.resolve(lib, "crypt-run", sound.SoundDirection(mood="tense", avoid=["chiptune"]))
    assert merged.genre == "horror" and merged.mood == "tense" and layers == ["profile:crypt-run", "request"]
    assert sound.loudness_target(merged, "ambience") == -24.0
    art_yaml = tmp_path / "art.yaml"
    art_yaml.write_text("game: sky-hop\nstyle: cartoon\nsound:\n  genre: chiptune\n  tempo: 140\n  space: dry\n")
    code, out, _ = run_cli(capsys, "art", "profile", "set", "--file", str(art_yaml), "--json")
    assert code == 0 and out["sound_profile"]["genre"] == "chiptune"
    code, out, _ = run_cli(capsys, "sound", "profile", "set", "sky-hop", "--mood", "playful", "--json")
    assert code == 0 and out["profile"]["sound"] == {"genre": "chiptune", "mood": "playful", "space": "dry",
                                                       "tempo": [140.0]}  # fmt: skip
    code, out, _ = run_cli(capsys, "sound", "profile", "set", "x", "--space", "attic", "--json")
    assert code == 4


def test_audio_search_filters_and_similarity(capsys, home, lib, tmp_path):
    def add(name: str, x: np.ndarray, **opts) -> str:
        return lib.import_paths([write(tmp_path / f"{name}.wav", x)], ImportOptions(**opts)).items[0].asset_id

    rng = np.random.default_rng(1)
    groove = add("groove", pulse_track(120, 7.93), type=AssetType.MUSIC)  # ends mid-cycle: not loopable
    fast = add("fast_track", pulse_track(150, 8, (293.66, 349.23, 440.0)), type=AssetType.MUSIC)
    rain = add("rain_bed", np.tile(dsp.filt(rng.uniform(-0.4, 0.4, SR), "lowpass", 2000, SR), 6),
               type=AssetType.AMBIENCE)  # fmt: skip
    lib.merge_metadata(fast, {"audio": {"bpm": 150, "key": "D minor", "loop": True}})

    def found(**kwargs) -> list[str]:
        return [h.id for h in search(lib, SearchQuery(**kwargs)).results]

    assert found(bpm=120) == [groove]
    assert found(bpm=150, key="D minor") == [fast]
    assert set(found(key="minor")) == {groove, fast}
    assert fast in found(loopable=True) and groove not in found(loopable=True)
    assert found(text="rain noisy") and found(text="rain noisy")[0] == rain
    similar = [h.id for h in search(lib, SearchQuery(similar=groove)).results]
    assert similar[0] == fast  # another tonal pulse track, not the rain
    code, out, _ = run_cli(capsys, "search", "--bpm", "150", "--key", "D minor", "--json")
    assert code == 0 and [r["id"] for r in out["results"]] == [fast]
    code, out, _ = run_cli(capsys, "reindex", "--json")
    assert code == 0 and out["reindex"]["audio_features_computed"] == 0
