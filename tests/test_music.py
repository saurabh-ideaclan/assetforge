"""Music: instruments, scores, MIDI, the composer, rendering (loops, stems, layers), edit/vary and SoundFonts."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import numpy as np
import pytest

from assetforge.audio import pipeline
from assetforge.audio.music import composer, drums, instruments, render, soundfont
from assetforge.audio.music.generate import MusicRequest, generate_music, take_score
from assetforge.audio.music.midi import read_midi, write_midi
from assetforge.audio.music.score import Score
from assetforge.audio.music.theory import parse_key
from assetforge.errors import InvalidInputError
from assetforge.validation.audio import loop_seam, seam_ok

from conftest import run_cli

SR = 22050

SCORE = {
    "tempo": 120, "key": "A minor", "bars": 2, "loop": True,
    "tracks": [
        {"name": "lead", "instrument": "flute", "role": "melody",
         "notes": [{"pitch": p, "start": i, "beats": 1} for i, p in enumerate(["A4", "C5", "E5", "D5", "C5", "B4", "A4", "E4"])]},
        {"name": "bass", "instrument": "synth_bass", "role": "bass",
         "notes": [{"pitch": "A2", "start": 0, "beats": 4}, {"pitch": "E2", "start": 4, "beats": 4}]},
        {"name": "kit", "instrument": "drums", "role": "drums",
         "notes": [{"pitch": "kick", "start": b, "beats": 0.5} for b in (0, 2, 4, 6)] +
                  [{"pitch": 38, "start": b, "beats": 0.5} for b in (1, 3, 5, 7)]},
    ],
}  # fmt: skip


def test_every_instrument_and_drum_piece_renders():
    rng = np.random.default_rng(1)
    for inst in instruments.INSTRUMENTS.values():
        x = inst.render(60, 0.4, 0.8, SR, rng)
        assert np.isfinite(x).all() and 0.01 < np.max(np.abs(x)) < 3, inst.name
    for kit in drums.KITS:
        for piece in drums.GM_DRUMS:
            assert np.max(np.abs(drums.render_hit(piece, kit, 0.8, rng, SR))) > 0.01
    assert instruments.resolve_instrument("Rhodes") == "electric_piano" and len(instruments.INSTRUMENTS) >= 25
    with pytest.raises(InvalidInputError):
        instruments.resolve_instrument("theremin_9000")


def test_score_validation_and_midi_round_trip(tmp_path):
    score = Score(**SCORE)
    assert score.total_beats == 8 and abs(score.seconds - 4.0) < 1e-9
    with pytest.raises(ValueError):
        Score(tempo=120, tracks=[{"name": "x", "instrument": "drums", "notes": [{"pitch": "gong", "start": 0, "beats": 1}]}])
    path = write_midi(score, tmp_path / "s.mid")
    back = read_midi(path, loop=True)
    assert back.tempo == 120 and back.key == "A minor" and back.meter == "4/4"
    assert [len(t.notes) for t in back.tracks] == [8, 2, 8] and back.tracks[2].instrument == "drums"
    assert back.tracks[2].notes[1].pitch in ("snare", "kick")


def test_render_loop_length_seam_and_stems_sum():
    take = render.render({"score": SCORE, "stems": True}, 1, 3, SR)
    assert len(take.mix) == int(4.0 * SR)  # exactly two bars at 120 bpm
    assert seam_ok(loop_seam(take.mix / np.max(np.abs(take.mix)), SR))
    stems = sum(v for k, v in take.extras.items() if k.startswith("stem_"))
    assert set(take.extras) == {"stem_melody", "stem_bass", "stem_drums"} and np.allclose(stems, take.mix, atol=1e-6)
    assert take.files["midi"].is_file()
    again = render.render({"score": SCORE, "stems": True}, 1, 3, SR)
    assert np.array_equal(again.mix, take.mix)


@pytest.mark.parametrize("genre", sorted(composer.GENRES))
def test_composer_every_genre_is_in_key_and_seeded(genre):
    brief = composer.Brief(genre=genre, seconds=16)
    a, b, c = composer.compose(brief, 1), composer.compose(brief, 1), composer.compose(brief, 2)
    assert a == b and a != c and a.loop
    key = parse_key(a.key)
    pitches = a.melodic_pitches()
    in_key = sum(key.in_key(p) for p in pitches) / len(pitches)
    borrowed = any(sym[0] in "b#" for p in composer.GENRES[genre].progressions for sym in p.split())
    allowed = 0.8 if borrowed or any(s in key.scale for s in ("harmonic", "phrygian", "lydian")) else 0.9
    assert in_key >= allowed, (genre, in_key)
    assert a.bars % 4 == 0 and genre in composer.GENRES


def test_composer_forms_stingers_energy_and_pins():
    linear = composer.compose(composer.Brief(genre="credits", form="linear", bars=24), 3)
    assert [s.name for s in linear.sections] == ["intro", "A", "B", "outro"] and not linear.loop
    for kind in composer.STINGERS:
        st = composer.compose(composer.Brief(genre="chiptune", form="stinger", stinger=kind), 4)
        assert st.bars <= 3 and st.seconds < 9 and not st.loop
    calm = composer.compose(composer.Brief(genre="epic_battle", energy=0.1, seconds=16), 5)
    full = composer.compose(composer.Brief(genre="epic_battle", energy=0.9, seconds=16), 5)
    assert len(calm.tracks) < len(full.tracks)
    assert calm.tracks[0].notes == next(t for t in full.tracks if t.name == "melody").notes  # same tune
    seeds = composer.seeds_for(composer.Brief(genre="lofi"), 7)
    pinned = composer.compose(composer.Brief(genre="lofi", fixed={"structure": seeds["structure"], "melody": seeds["melody"]}), 99)
    original = composer.compose(composer.Brief(genre="lofi"), 7)
    assert pinned.key == original.key and pinned.tracks[0].notes == original.tracks[0].notes
    with pytest.raises(InvalidInputError):
        composer.Brief(genre="dubstep polka")
    assert composer.resolve_genre("8bit") == "chiptune" and composer.resolve_genre("boss") == "boss_fight"


def test_generate_music_with_profile_layers_and_reuse(lib):
    from assetforge import sound

    sound.set_profile(lib, "crypt-run", sound.SoundDirection(genre="horror", key="D minor", tempo=[60, 70],
                                                             instruments=["choir", "taiko"]))  # fmt: skip
    res = generate_music(lib, MusicRequest(project="crypt-run", seconds=12, seed=2, stems=True, intensity_layers=[0.1, 0.9],
                                           backend="synth"))  # fmt: skip
    asset = lib.get_asset(res["asset_id"])
    audio = asset.metadata["audio"]
    assert audio["key"] == "D minor" and 60 <= audio["bpm"] <= 70 and audio["loop"] and audio["loop_end"] > 0
    assert {"calm", "intense"} <= set(res["variants"][0]["extras"]) and "midi" in res["variants"][0]["files"]
    spec = asset.metadata["generator"]["spec"]
    assert spec["compose"]["genre"] == "horror" and spec["compose"]["instruments"]["drums"] == "drums_orchestral"
    score, _, _ = take_score(lib, res["asset_id"])
    assert score.key == "D minor"
    assert generate_music(lib, MusicRequest(project="crypt-run", seconds=12, seed=2, stems=True,
                                            intensity_layers=[0.1, 0.9], backend="synth"))["reused"]  # fmt: skip
    assert pipeline.reproduce(lib, res["asset_id"])["ok"]


def test_cli_music(capsys, home, tmp_path):
    code, out, _ = run_cli(capsys, "music", "genres", "--json")
    assert code == 0 and out["count"] >= 16 and "victory" in out["stingers"]
    code, out, _ = run_cli(capsys, "music", "instruments", "--json")
    assert code == 0 and any(i["name"] == "drums_orchestral" for i in out["instruments"])
    code, out, _ = run_cli(capsys, "music", "compose", "--genre", "chiptune", "--bars", "4", "--seed", "1",
                           "-i", "melody=chip_triangle", "--backend", "synth", "--json")  # fmt: skip
    assert code == 0 and out["audio"]["bars"] == 4 and "chip_triangle" in out["audio"]["instruments"]
    piece = out["asset_id"]
    code, out, _ = run_cli(capsys, "music", "compose", "--stinger", "level_up", "--seed", "2", "--backend", "synth", "--json")
    assert code == 0 and out["asset"]["name"] == "level_up_stinger" and not out["audio"]["loop"]
    code, out, _ = run_cli(capsys, "music", "edit", piece, "--tempo", "100", "--transpose", "2", "--mute", "drums", "--json")
    assert code == 0 and out["audio"]["bpm"] == 100 and "drums_chip" not in out["audio"]["instruments"]
    code, out, _ = run_cli(capsys, "music", "vary", piece, "--keep", "chords", "--seed", "3", "--json")
    assert code == 0 and out["asset"]["name"].endswith("_reharm")
    code, out, _ = run_cli(capsys, "music", "vary", piece, "-n", "1", "--seed", "4", "--json")
    assert code == 0 and out["variants"][0]["label"] == "v2_ogg"
    score_file = tmp_path / "score.json"
    score_file.write_text(json.dumps(SCORE))
    code, out, _ = run_cli(capsys, "music", "render", "--score", str(score_file), "--seed", "1", "--json")
    assert code == 0 and out["audio"]["bpm"] == 120 and out["audio"]["loop"]
    midi_out = tmp_path / "piece.mid"
    code, out, _ = run_cli(capsys, "music", "score", piece, "--midi", str(midi_out), "--json")
    assert code == 0 and midi_out.is_file()
    code, out, _ = run_cli(capsys, "music", "render", "--midi", str(midi_out), "--no-loop", "--seed", "1", "--json")
    assert code == 0 and not out["audio"]["loop"]
    code, out, _ = run_cli(capsys, "search", "--key", "major", "--loopable", "--json")
    assert code == 0 and out["count"] >= 1


# ---------------------------------------------------------------------- SoundFont backend (fake fluidsynth)

FAKE_FLUIDSYNTH = """#!{python}
import sys, wave, struct, math
args = sys.argv[1:]
out = args[args.index("-F") + 1]
rate = int(args[args.index("-r") + 1])
with wave.open(out, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
    frames = b"".join(struct.pack("<hh", int(8000 * math.sin(i / 7)), int(8000 * math.sin(i / 7))) for i in range(rate))
    w.writeframes(frames)
"""


@pytest.fixture
def fake_fluidsynth(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "fluidsynth"
    exe.write_text(FAKE_FLUIDSYNTH.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return exe


def test_soundfont_status_fallback_and_rendering(lib, tmp_path, fake_fluidsynth, monkeypatch):
    home = lib.paths.home
    assert not soundfont.status(home)["ready"]
    res = generate_music(lib, MusicRequest(score=SCORE, seed=1))  # auto, not ready → synth
    assert lib.get_asset(res["asset_id"]).metadata["generator"]["spec"]["backend"] == "synth"
    with pytest.raises(InvalidInputError):
        generate_music(lib, MusicRequest(score=SCORE, seed=1, backend="soundfont"))
    font_a = tmp_path / "a.sf2"
    font_a.write_bytes(b"RIFF" + b"\0" * 2000)
    soundfont.register(home, "fake", font_a, license_="test")
    assert soundfont.status(home)["ready"]
    res = generate_music(lib, MusicRequest(score=SCORE, seed=1, name="sf_piece"))
    spec = lib.get_asset(res["asset_id"]).metadata["generator"]["spec"]
    assert spec["backend"] == "soundfont" and spec["soundfont"]["sha256"] == soundfont.default(home)["sha256"]
    assert set(res["variants"][0]["info"]["backends"].values()) == {"soundfont:fake"}
    assert pipeline.reproduce(lib, res["asset_id"])["ok"]
    font_b = tmp_path / "b.sf2"
    font_b.write_bytes(b"RIFF" + b"\1" * 2000)
    soundfont.register(home, "other", font_b, license_="test")
    again = generate_music(lib, MusicRequest(score=SCORE, seed=1, name="sf_piece"))
    assert again["reuse_reason"] != "fingerprint"  # a different SoundFont is a different request
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(InvalidInputError):
        pipeline.reproduce(lib, res["asset_id"])  # recorded as SoundFont; can't silently fall back


def test_cli_soundfont(capsys, home, tmp_path):
    code, out, _ = run_cli(capsys, "audio", "soundfont", "status", "--json")
    assert code == 0 and out["soundfont"]["ready"] is False
    code, out, _ = run_cli(capsys, "audio", "soundfont", "install", "--json")
    assert code == 4 and "--yes" in out["error"]["message"]
    font = tmp_path / "mine.sf2"
    font.write_bytes(b"RIFF" + b"\0" * 2000)
    code, out, _ = run_cli(capsys, "audio", "soundfont", "install", "--name", "mine", "--file", str(font), "--json")
    assert code == 0 and out["installed"]["name"] == "mine" and Path(out["installed"]["path"]).is_file()
    code, out, _ = run_cli(capsys, "audio", "soundfont", "use", "mine", "--json")
    assert code == 0
