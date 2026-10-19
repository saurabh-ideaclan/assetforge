"""CLI contract for `assetforge audio …` and `assetforge jobs …`."""

from __future__ import annotations

import json
from pathlib import Path

from assetforge.errors import ExitCode

from conftest import make_wav, run_cli


def test_recipes_discovery(capsys, home):
    code, out, _ = run_cli(capsys, "audio", "recipes", "--json")
    assert code == 0 and out["count"] >= 20
    assert {"soft", "retro", "punchy"} <= {p["name"] for p in out["presets"]}
    assert {"reverb", "delay", "bitcrush"} <= {e["name"] for e in out["effects"]}

    code, out, _ = run_cli(capsys, "audio", "recipes", "coin", "--json")
    names = [p["name"] for p in out["recipe"]["params"]]
    assert names[:3] == ["duration", "pitch", "brightness"] and "note" in names

    code, out, _ = run_cli(capsys, "audio", "recipes", "nope", "--json")
    assert code == ExitCode.INVALID_INPUT and "coin" in out["error"]["details"]["available"]


def test_generate_matches_the_spec_example(capsys, home):
    code, out, err = run_cli(
        capsys, "audio", "generate", "--recipe", "cartoon_hit", "--preset", "soft", "--duration", "0.35",
        "--variants", "5", "--seed", "42", "--format", "ogg", "--json",
    )  # fmt: skip
    assert code == 0, err
    assert out["status"] == "ok" and out["reused"] is False
    assert out["asset_id"].startswith("ast_") and out["job_id"].startswith("job_")
    assert len(out["variants"]) == 5
    for v in out["variants"]:
        assert v["variant_id"].startswith("var_") and Path(v["path"]).is_file() and v["format"] == "ogg"

    code, again, _ = run_cli(
        capsys, "audio", "generate", "--recipe", "cartoon_hit", "--preset", "soft", "--duration", "0.35",
        "--variants", "5", "--seed", "42", "--format", "ogg", "--json",
    )  # fmt: skip
    assert again["reused"] is True and again["asset_id"] == out["asset_id"]

    code, rep, _ = run_cli(capsys, "audio", "reproduce", out["asset_id"], "--json")
    assert code == 0 and rep["reproduce"]["ok"] and len(rep["reproduce"]["variants"]) == 5


def test_generate_from_request_file_with_flag_overrides(capsys, home, tmp_path):
    request = tmp_path / "beetle-hit.json"
    request.write_text(json.dumps({
        "recipe": "cartoon_hit", "duration_ms": 350, "variants": 2, "seed": 7, "format": "ogg",
        "project": "panda-bamboo", "key": "enemy/beetle/hit", "description": "Short soft crunchy insect hit",
        "params": {"crunch": 0.7}, "tags": ["insect"],
    }))  # fmt: skip
    code, out, err = run_cli(capsys, "audio", "generate", "--file", str(request), "-p", "wobble=0.1",
                             "--variants", "3", "--json")  # fmt: skip
    assert code == 0, err
    assert len(out["variants"]) == 3
    assert out["variants"][0]["params"]["crunch"] == 0.7 and out["variants"][0]["params"]["wobble"] == 0.1
    assert out["asset"]["key"] == "enemy/beetle/hit" and "insect" in out["asset"]["tags"]

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"recipe": "coin", "sample_rate": 1}))
    code, out, _ = run_cli(capsys, "audio", "generate", "--file", str(bad), "--json")
    assert code == ExitCode.INVALID_INPUT and "sample_rate" in out["error"]["message"]


def test_vary_effects_and_from(capsys, home):
    _, base, _ = run_cli(capsys, "audio", "generate", "-r", "laser", "--seed", "1", "-e", "delay:feedback=0.4",
                         "--json")  # fmt: skip
    code, more, _ = run_cli(capsys, "audio", "vary", base["asset_id"], "-n", "2", "--json")
    assert code == 0 and [v["label"] for v in more["variants"]] == ["v2", "v3"]

    code, derived, _ = run_cli(capsys, "audio", "generate", "--from", base["asset_id"], "-p", "fm=0.9", "--json")
    assert code == 0 and derived["asset_id"] != base["asset_id"]
    _, got, _ = run_cli(capsys, "get", derived["asset_id"], "--json")
    assert got["asset"]["metadata"]["generator"]["effects"][0]["name"] == "delay"

    code, out, _ = run_cli(capsys, "audio", "generate", "-r", "coin", "-e", "flanger", "--json")
    assert code == ExitCode.INVALID_INPUT


def test_analyze_files_and_assets(capsys, home, tmp_path):
    path = make_wav(tmp_path / "beep.wav", duration_ms=300)
    code, out, _ = run_cli(capsys, "audio", "analyze", str(path), "--json")
    assert code == 0 and out["analysis"]["duration_ms"] == 300 and out["analysis"]["ok"]

    _, gen, _ = run_cli(capsys, "audio", "generate", "-r", "ui_click", "--json")
    code, out, _ = run_cli(capsys, "audio", "analyze", gen["asset_id"], "--json")
    assert code == 0 and out["analysis"]["peak_dbfs"] == -1.0


def test_jobs_commands(capsys, home):
    _, gen, _ = run_cli(capsys, "audio", "generate", "-r", "coin", "--json")
    code, out, _ = run_cli(capsys, "jobs", "list", "--json")
    assert code == 0 and out["jobs"][0]["id"] == gen["job_id"] and out["jobs"][0]["status"] == "succeeded"
    code, out, _ = run_cli(capsys, "jobs", "show", gen["job_id"], "--json")
    assert out["job"]["request"]["recipe"] == "coin" and out["job"]["model"].startswith("assetforge-synth")
    code, out, _ = run_cli(capsys, "jobs", "show", "job_99999", "--json")
    assert code == ExitCode.NOT_FOUND


def test_human_output(capsys, home, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    assert run_cli(capsys, "audio", "recipes")[0] == 0
    assert run_cli(capsys, "audio", "recipes", "jump")[0] == 0
    code, out, _ = run_cli(capsys, "audio", "generate", "-r", "jump", "-n", "2")
    assert code == 0 and "Generated" in out and "v2" in out
    assert run_cli(capsys, "jobs", "list")[0] == 0
