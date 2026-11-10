"""The Audio Analysis Engine on signals with a known truth: technical defects, spectral balance, voice-like
content (DSP cues only: no VAD model in a test home), musical structure, the diagnosis taxonomy, the
correction ops and the closed correction loop, reference profiles and placement, the CLI. No network."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from assetforge.audio.analysis import analyze_array, analyze_file, diagnose_file, to_json
from assetforge.audio.analysis import reference
from assetforge.audio.analysis.corrections import compare, correct_file, fill_donors, plan_corrections
from assetforge.audio.analysis.diagnose import _brief_allows_voices, diagnose, summarize
from assetforge.audio.analysis.interpret import text
from assetforge.audio.analysis.visuals import render
from assetforge.audio.process import apply_ops

SR = 44100


def _write(path: Path, x: np.ndarray) -> Path:
    sf.write(path, np.asarray(x, dtype=np.float32), SR, subtype="PCM_16")
    return path


def _tone(seconds: float, hz: float = 220.0, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return level * np.sin(2 * np.pi * hz * t)


def _murmur(seconds: float = 12.0) -> np.ndarray:
    from assetforge.audio.ambience.beds import BEDS

    n = int(seconds * SR)
    b = BEDS["crowd_murmur"]
    x = b.render(n, SR, np.random.default_rng(3), 0.5, {**b.params, "voices": 8}, n)
    return x / (np.max(np.abs(x)) + 1e-9) * 0.5


def _wind(seconds: float = 12.0) -> np.ndarray:
    from assetforge.audio.ambience.beds import BEDS

    n = int(seconds * SR)
    b = BEDS["wind"]
    x = b.render(n, SR, np.random.default_rng(4), 0.5, dict(b.params), n)
    return x / (np.max(np.abs(x)) + 1e-9) * 0.5


def test_technical_layer_measures_defects(tmp_path: Path) -> None:
    clipped = np.clip(10.0 * _tone(4), -1, 1)
    r = analyze_array(clipped, SR, kind="sfx")
    t = r["technical"]
    assert t["clipping"] and t["clipped_samples"] > 100 and t["true_peak_dbtp"] >= -0.1 and t["crest_db"] < 5
    f = diagnose(r, kind="sfx")
    assert f[0]["code"] == "clipping" and f[0]["severity"] == "hard" and f[0]["confidence"] == "measured"
    # a hard-cut loop: phase and level discontinuity at the seam
    cut = _tone(6, 330.7) * np.linspace(1.0, 0.3, 6 * SR)
    r = analyze_array(cut, SR, kind="music")
    assert r["technical"]["loop"]["seam_ok"] is False
    codes = {x["code"]: x for x in diagnose(r, kind="music", loop=True)}
    # music prefers a bar-aligned re-render (a crossfade would shorten the bar grid); a bed gets the crossfade
    assert codes["loop_seam"]["severity"] == "hard" and codes["loop_seam"]["corrections"][0]["action"] == "reseed"
    bed = {x["code"]: x for x in diagnose(r, kind="ambience", loop=True)}
    assert bed["loop_seam"]["corrections"][0]["action"] == "reloop"
    # the same file with loop unknown is only a note
    assert {x["code"]: x for x in diagnose(r, kind="music")}["loop_seam"]["severity"] == "note"
    # silence
    assert diagnose(analyze_array(np.zeros(2 * SR), SR, kind="sfx"), kind="sfx")[0]["code"] == "silent"
    # loudness timeline shapes
    assert len(t["loudness"]["short_term"]) > 5 and t["loudness"]["step_s"] == 0.1


def test_spectral_balance_names_rumble_and_hiss() -> None:
    from scipy.signal import butter, sosfilt

    rng = np.random.default_rng(1)
    n = 10 * SR
    brown = np.cumsum(rng.standard_normal(n))
    brown -= np.linspace(brown[0], brown[-1], n)
    rumble = sosfilt(butter(4, 60 / (SR / 2), "low", output="sos"), brown)
    rumble = rumble / np.max(np.abs(rumble)) * 0.5
    r = analyze_array(rumble, SR, kind="ambience")
    assert r["spectral"]["bands"]["shares"]["rumble"] > 0.9
    codes = [x["code"] for x in diagnose(r, kind="ambience", loop=False)]
    assert "sub_rumble" in codes
    hiss = sosfilt(butter(4, 9000 / (SR / 2), "high", output="sos"), rng.standard_normal(n)) * 0.2
    r = analyze_array(hiss, SR, kind="ambience")
    assert r["spectral"]["bands"]["shares"]["hiss"] > 0.5
    assert "hissy" in [x["code"] for x in diagnose(r, kind="ambience", loop=False)]


def test_voice_like_cues_fire_on_murmur_not_wind(tmp_path: Path, home: Path) -> None:
    murmur = analyze_array(_murmur(), SR, kind="ambience", with_vad=False)
    wind = analyze_array(_wind(), SR, kind="ambience", with_vad=False)
    assert murmur["speech"]["voice_like_share"] > 0.3 and murmur["speech"]["verdict"] == "suspected"
    assert wind["speech"]["voice_like_share"] == 0.0 and wind["speech"]["verdict"] == "unlikely"
    assert not (murmur["speech"]["vad"] or {}).get("available")  # no VAD asked for: the verdict says dsp only
    f = {x["code"]: x for x in diagnose(murmur, kind="ambience", brief="a deserted corridor at night, no people", loop=False)}
    assert f["voice_like"]["severity"] == "problem" and f["voice_like"]["cause"] == "source" and f["voice_like"]["where"]
    assert f["voice_like"]["corrections"][0]["action"] == "drop_layer"
    # the brief decides whether voices belong
    assert _brief_allows_voices("a packed market with crowd murmur", None)
    assert not _brief_allows_voices("a deserted corridor, no people", None)
    assert "voice_like" not in {x["code"] for x in diagnose(murmur, kind="ambience", brief="a tavern full of people", loop=False)}
    events = {e["label"] for e in murmur["events"]["summary"]}
    assert "speech_like" in events and "wind" in {e["label"] for e in wind["events"]["summary"]}


def test_music_structure_and_mechanical_timing() -> None:
    def arp(jitter: float) -> np.ndarray:
        n = 24 * SR
        out = np.zeros(n)
        step = int(0.25 * SR)
        notes = [262, 330, 392, 523]
        r = np.random.default_rng(3)
        pos = i = 0
        while pos + step < n:
            start = max(0, min(n - step, pos + int(r.uniform(-jitter, jitter) * SR)))
            env = np.exp(-np.linspace(0, 6, step))
            out[start:start + step] += 0.3 * env * np.sin(2 * np.pi * notes[i % 4] * np.arange(step) / SR)
            pos += step
            i += 1
        return out

    exact = analyze_array(arp(0.0), SR, kind="music")
    m = exact["music"]
    assert m["event_regularity_cv"] is not None and m["event_regularity_cv"] < 0.05
    assert m["tempo"]["bpm"] and abs(m["tempo"]["bpm"] - 120) < 3 and m["key"]["name"] == "C major"
    assert m["structure"]["repetition_index"] > 0.9
    assert "mechanical" in {x["code"] for x in diagnose(exact, kind="music", loop=False)}
    loose = analyze_array(arp(0.03), SR, kind="music")
    assert loose["music"]["event_regularity_cv"] > exact["music"]["event_regularity_cv"]
    # interpretation reads the numbers back with their units and uncertainty
    words = text(exact, diagnose(exact, kind="music", loop=False))
    assert "BPM" in words and "machine-exact" in words and "Findings" in words


def test_corrections_fix_what_they_target(tmp_path: Path) -> None:
    from scipy.signal import butter, sosfilt

    rng = np.random.default_rng(2)
    n = 10 * SR
    brown = np.cumsum(rng.standard_normal(n))
    brown -= np.linspace(brown[0], brown[-1], n)
    rumble = sosfilt(butter(4, 60 / (SR / 2), "low", output="sos"), brown)
    bed = 0.3 * rumble / np.max(np.abs(rumble)) + 0.05 * rng.standard_normal(n)
    src = _write(tmp_path / "bed.wav", bed)
    report = analyze_file(src, kind="ambience", with_vad=False)
    findings = diagnose(report, kind="ambience", loop=False)
    assert "sub_rumble" in {f["code"] for f in findings}
    plan = plan_corrections(findings, kind="ambience")
    assert any(op["op"] == "highpass" for op in plan["dsp"]) and plan["dsp"][-1]["op"] == "normalize"
    out = correct_file(src, tmp_path / "bed_fixed.wav", findings, report, kind="ambience")
    assert out["applied"] and "sub_rumble" in out["comparison"]["fixed"]
    # the op registry: each correction runs and keeps the shape
    x = np.stack([_tone(3), _tone(3, 220, 0.2)], axis=1)
    for ops in ([{"op": "peak_eq", "hz": 3000, "gain_db": -3}], [{"op": "dialogue_dip", "db": -4}], [{"op": "declick"}], [{"op": "reloop", "crossfade_ms": 80}],
                [{"op": "replace_segment", "start_s": 1.0, "end_s": 1.4, "donor_start_s": 2.0}], [{"op": "narrow", "width": 0.5}], [{"op": "compress"}], [{"op": "expand"}]):
        (y, _), _ = apply_ops((x.copy(), SR), ops)
        assert y.shape[1] == 2 and np.isfinite(y).all() and np.max(np.abs(y)) <= 1.0
    # a voice-like second gets a donor from the cleanest second two seconds away
    murmur = analyze_array(_murmur(), SR, kind="ambience", with_vad=False)
    f = diagnose(murmur, kind="ambience", brief="an empty corridor", loop=False)
    plan = fill_donors(plan_corrections(f, kind="ambience"), murmur)
    seg_ops = [op for op in plan["dsp"] if op["op"] == "replace_segment"]
    assert all(op["donor_start_s"] is not None and abs(op["donor_start_s"] - op["start_s"]) >= 2.0 for op in seg_ops)
    # compare() names fixed / remaining / regressions
    cmp_ = compare({"findings": [{"code": "a", "severity": "problem"}, {"code": "b", "severity": "problem"}]},
                   {"findings": [{"code": "b", "severity": "problem"}, {"code": "c", "severity": "problem"}]}, ["a", "b"])  # fmt: skip
    assert cmp_["fixed"] == ["a"] and cmp_["remaining"] == ["b"] and cmp_["regressions"] == ["c"] and cmp_["verdict"] == "mixed"


def test_reference_profile_and_placement(tmp_path: Path) -> None:
    reports = [analyze_array(_tone(8, hz) + 0.01 * np.random.default_rng(i).standard_normal(8 * SR), SR, kind="music", with_vad=False) for i, hz in enumerate((220, 247, 262, 294, 330))]
    prof = reference.profile(reports, name="demo/music")
    assert prof["n"] == 5 and "centroid_log" in prof["features"] and prof["features"]["centroid_log"]["sd"] > 0
    bright = analyze_array(_tone(8, 3000), SR, kind="music", with_vad=False)
    placed = reference.place(bright, prof)
    assert placed["distance"] and placed["z"]["centroid_log"] > 1.5 and any("brighter" in w for w in placed["notable"])
    assert reference.describe(prof)
    home = tmp_path / "home"
    reference.set_profile(home, "demo", "music", prof)
    row = reference.record_preference(home, "demo", asset_id="ast_1", decision="approved", kind="music", brief="calm", features=reference.features_of(reports[0]))
    assert row["decision"] == "approved" and reference.load_style(home, "demo")["history"][0]["asset_id"] == "ast_1"
    ranked = reference.rank(home, "demo", "music", [{"report": bright, "summary": {"usable": True}}, {"report": reports[1], "summary": {"usable": True}}])
    assert ranked[0]["report"] is reports[1]  # the one near the identity and the approved row comes first


def test_visuals_and_json(tmp_path: Path) -> None:
    src = _write(tmp_path / "t.wav", np.stack([_tone(4), _tone(4, 330)], axis=1))
    report = analyze_file(src, kind="music", with_vad=False)
    files = render(report, tmp_path / "vis", path=src)
    for name in ("waveform", "spectrogram", "mel", "chroma", "similarity", "timelines"):
        assert Path(files[name]).exists()
    tl = json.loads(Path(files["timelines"]).read_text())
    assert tl["loudness"]["short_term"] and tl["balance"]["octaves_db"] and "speech" in tl
    js = json.dumps(to_json(report))
    assert "arrays" not in js and "centroid_hz" in js


def test_diagnose_file_and_cli(tmp_path: Path, capsys, home: Path) -> None:
    from test_cli import run_cli

    src = _write(tmp_path / "m.wav", _murmur(8))
    d = diagnose_file(src, kind="ambience", brief="an empty corridor", with_vad=False)
    assert d["summary"]["problems"] or d["summary"]["hard"]
    code, payload, _ = run_cli(capsys, "audio", "diagnose", str(src), "--kind", "ambience", "--brief", "an empty corridor", "--json")
    assert code == 0 and payload["status"] == "ok" and any(f["code"] == "voice_like" for f in payload["findings"])
    code, payload, _ = run_cli(capsys, "audio", "inspect", str(src), "--kind", "ambience", "--visuals", str(tmp_path / "v"), "--json")
    assert code == 0 and (tmp_path / "v" / "spectrogram.png").exists()
    code, payload, _ = run_cli(capsys, "audio", "models", "status", "--json")
    assert code == 0 and payload["silero_vad"]["installed"] is False
    code, payload, _ = run_cli(capsys, "audio", "correct", str(src), "--kind", "ambience", "--brief", "an empty corridor", "--out", str(tmp_path / "fixed.wav"), "--json")
    assert code == 0 and payload["plan"]["reasons"]
