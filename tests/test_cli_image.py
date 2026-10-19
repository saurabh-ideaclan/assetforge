"""CLI contract for `assetforge image …`, `assetforge style …` and `assetforge jobs resume`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.errors import ExitCode
from assetforge.image import engine

from conftest import make_png, run_cli


@pytest.fixture(autouse=True)
def fake_enabled(monkeypatch):
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")
    monkeypatch.setattr(engine, "sleep", lambda s: None)


def test_providers_listing(capsys, home):
    code, out, _ = run_cli(capsys, "image", "providers", "--json")
    assert code == 0
    by_name = {p["name"]: p for p in out["providers"]}
    assert by_name["openai"]["configured"] is False and by_name["openai"]["key_env"] == "OPENAI_API_KEY"
    assert by_name["openai"]["native_transparency"] and not by_name["gemini"]["native_transparency"]
    assert by_name["fake"]["configured"]


def test_generate_matches_the_spec_example(capsys, home):
    code, out, err = run_cli(
        capsys, "image", "generate", "--type", "character", "--prompt", "Cute cartoon beetle, front view",
        "--style", "cute-jungle", "--provider", "fake", "--transparent", "--json",
    )  # fmt: skip
    assert code == 0, err
    assert out["status"] == "ok" and out["reused"] is False and out["job_id"].startswith("job_")
    [asset] = out["assets"]
    assert asset["asset_id"].startswith("ast_") and Path(asset["path"]).is_file() and asset["has_alpha"]

    code, got, _ = run_cli(capsys, "get", asset["asset_id"], "--json")
    assert got["asset"]["metadata"]["generator"]["prompt"] == "Cute cartoon beetle, front view"
    assert got["asset"]["origin"] == "ai" and got["asset"]["status"] == "candidate"

    code, again, _ = run_cli(
        capsys, "image", "generate", "--type", "character", "--prompt", "Cute cartoon beetle, front view",
        "--style", "cute-jungle", "--provider", "fake", "--transparent", "--json",
    )  # fmt: skip
    assert again["reused"] is True and again["assets"][0]["asset_id"] == asset["asset_id"]


def test_generate_from_request_file_and_dry_run(capsys, home, tmp_path):
    request = tmp_path / "bg.json"
    request.write_text(json.dumps({"prompt": "jungle clearing", "type": "background", "provider": "fake",
                                   "project": "panda-bamboo", "tags": ["level-1"]}))  # fmt: skip
    code, out, _ = run_cli(capsys, "image", "generate", "--file", str(request), "--dry-run", "--json")
    assert code == 0 and out["dry_run"] and "game background scene" in out["prompt"] and out["job_id"] is None

    code, out, _ = run_cli(capsys, "image", "generate", "--file", str(request), "-t", "misty", "--json")
    assert code == 0
    _, got, _ = run_cli(capsys, "get", out["assets"][0]["asset_id"], "--json")
    assert {"level-1", "misty", "background"} <= set(got["asset"]["tags"])


def test_edit_and_as_variant(capsys, home):
    _, gen, _ = run_cli(capsys, "image", "generate", "-p", "slime monster", "--type", "character",
                        "--provider", "fake", "--json")  # fmt: skip
    source = gen["assets"][0]["asset_id"]
    code, out, err = run_cli(capsys, "image", "edit", source, "-p", "make it red", "--provider", "fake", "--json")
    assert code == 0, err
    assert out["assets"][0]["asset_id"] != source
    _, got, _ = run_cli(capsys, "get", out["assets"][0]["asset_id"], "--json")
    assert got["asset"]["lineage"][0] == {**got["asset"]["lineage"][0], "relation": "edited_from", "asset_id": source}

    code, out, _ = run_cli(capsys, "image", "edit", source, "-p", "add a hat", "--as-variant", "--provider", "fake",
                           "--json")  # fmt: skip
    assert code == 0 and out["assets"][0]["asset_id"] == source


def test_errors(capsys, home, tmp_path):
    code, out, _ = run_cli(capsys, "image", "generate", "--json")
    assert code == ExitCode.INVALID_INPUT and "prompt" in out["error"]["message"]

    code, out, _ = run_cli(capsys, "image", "generate", "-p", "a cat", "--provider", "openai", "--no-fallback",
                           "--json")  # fmt: skip
    assert code == ExitCode.PROVIDER and out["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert "OPENAI_API_KEY" in out["error"]["message"]

    code, out, _ = run_cli(capsys, "image", "generate", "-p", "a cat", "--provider", "dalle", "--json")
    assert code == ExitCode.INVALID_INPUT and "openai" in out["error"]["details"]["available"]

    code, out, _ = run_cli(capsys, "image", "generate", "-p", "a cat", "--aspect", "5:1", "--json")
    assert code == ExitCode.INVALID_INPUT

    code, out, _ = run_cli(capsys, "image", "edit", "ast_00404", "-p", "x y z", "--json")
    assert code == ExitCode.NOT_FOUND


def test_style_commands_feed_generation(capsys, home, tmp_path):
    ref = make_png(tmp_path / "look.png", (32, 32))
    _, imported, _ = run_cli(capsys, "import", str(ref), "--json")
    ref_id = imported["items"][0]["asset_id"]
    code, out, _ = run_cli(capsys, "style", "create", "Cute Jungle", "--description", "chunky cartoon",
                           "--palette", "#4CAF50,#ffeb3b", "--reference", ref_id, "--json")  # fmt: skip
    assert code == 0 and out["style"]["name"] == "cute-jungle" and out["style"]["palette"] == ["#4CAF50", "#FFEB3B"]

    code, out, _ = run_cli(capsys, "style", "update", "cute-jungle", "--instructions", "thick outlines", "--json")
    assert out["style"]["instructions"] == "thick outlines"
    code, out, _ = run_cli(capsys, "style", "list", "--json")
    assert out["count"] == 1

    code, out, _ = run_cli(capsys, "image", "generate", "-p", "beetle", "--style", "cute-jungle", "--provider",
                           "fake", "--dry-run", "--json")  # fmt: skip
    assert "chunky cartoon" in out["prompt"] and "thick outlines" in out["prompt"] and "#4CAF50" in out["prompt"]

    code, out, _ = run_cli(capsys, "style", "create", "cute-jungle", "--json")
    assert code == ExitCode.CONFLICT
    code, out, _ = run_cli(capsys, "style", "create", "bad", "--palette", "green", "--json")
    assert code == ExitCode.INVALID_INPUT
    code, out, _ = run_cli(capsys, "style", "delete", "cute-jungle", "--json")
    assert out["deleted"]


def test_jobs_resume_command(capsys, home, monkeypatch):
    real = engine.finalize
    monkeypatch.setattr(engine, "finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    code, out, err = run_cli(capsys, "image", "generate", "-p", "gem", "--provider", "fake", "--json")
    assert code == 1 and out["error"]["code"] == "INTERNAL_ERROR" and "RuntimeError: crash" in out["error"]["message"]
    assert "Traceback" in err and (home / "logs" / "errors.log").is_file()
    monkeypatch.setattr(engine, "finalize", real)
    _, listing, _ = run_cli(capsys, "jobs", "list", "--status", "failed", "--json")
    job_id = listing["jobs"][0]["id"]
    code, out, _ = run_cli(capsys, "jobs", "resume", job_id, "--json")
    assert code == 0 and out["job_id"] == job_id and len(out["assets"]) == 1

    _, audio, _ = run_cli(capsys, "audio", "generate", "-r", "coin", "--json")
    code, out, _ = run_cli(capsys, "jobs", "resume", audio["job_id"], "--json")
    assert code == ExitCode.INVALID_INPUT


def test_human_output(capsys, home, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    assert run_cli(capsys, "image", "providers")[0] == 0
    code, out, _ = run_cli(capsys, "image", "generate", "-p", "shield icon", "--type", "icon", "--provider", "fake")
    assert code == 0 and "Generated with fake" in out
    assert run_cli(capsys, "style", "list")[0] == 0
