"""CLI contract for processing, previews, QC, review and lineage, plus the Phase 04 completion flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from assetforge.errors import ExitCode
from assetforge.image import engine

from conftest import make_png, make_wav, run_cli


@pytest.fixture(autouse=True)
def fake_enabled(monkeypatch):
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")
    monkeypatch.setattr(engine, "sleep", lambda s: None)


def test_phase4_completion_flow(capsys, home):
    """Generate candidates, inspect a contact sheet, pick one, produce validated game-ready derivatives."""
    code, gen, err = run_cli(capsys, "image", "generate", "-p", "cute beetle enemy", "--type", "character",
                             "-n", "4", "--provider", "fake", "--json")  # fmt: skip
    assert code == 0, err
    ids = [a["asset_id"] for a in gen["assets"]]

    code, sheet, _ = run_cli(capsys, "preview", "--job", gen["job_id"], "--html", "--json")
    assert code == 0 and sheet["count"] == 4 and [a["asset_id"] for a in sheet["assets"]] == ids
    assert Image.open(sheet["sheet"]).size[0] > 1000 and Path(sheet["html"]).read_text().count("class=\"card") == 4

    code, queue, _ = run_cli(capsys, "review", "queue", "--json")
    assert [a["id"] for a in queue["queue"]] == ids

    code, picked, _ = run_cli(capsys, "review", "pick", ids[2], "--note", "cutest", "--json")
    assert code == 0 and picked["review"]["rejected"] == [ids[0], ids[1], ids[3]]

    code, processed, _ = run_cli(capsys, "image", "process", ids[2], "--preset", "web-sprite", "--primary", "--json")
    assert code == 0 and processed["results"][0]["label"] == "trim_webp"

    code, report, _ = run_cli(capsys, "qc", ids[2], "--strict", "--json")
    assert code == 0 and report["ok"] and report["reports"][0]["label"] == "trim_webp"
    assert "excess_padding" not in report["reports"][0]["warnings"]

    _, got, _ = run_cli(capsys, "get", ids[2], "--json")
    assert got["asset"]["status"] == "approved" and got["asset"]["path"].endswith(".webp")
    _, graph, _ = run_cli(capsys, "lineage", ids[2], "--json")
    original = graph["lineage"]["variants"][0]
    assert original["children"][0]["label"] == "trim_webp" and original["children"][0]["primary"]


def test_image_process_flags_and_batch(capsys, home, tmp_path):
    a = make_png(tmp_path / "a.png", (200, 100))
    b = make_png(tmp_path / "b.png", (100, 200), (1, 2, 3, 255))
    _, imported, _ = run_cli(capsys, "import", str(a), str(b), "--type", "sprite", "--json")
    ids = [i["asset_id"] for i in imported["items"]]
    code, out, _ = run_cli(capsys, "image", "process", *ids, "--resize", "64x64", "--mode", "pad", "--hue", "30",
                           "--format", "webp", "--json")  # fmt: skip
    assert code == 0 and out["count"] == 2
    for result in out["results"]:
        assert result["label"] == "recolor_64x64_webp" and Image.open(result["path"]).size == (64, 64)

    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "crop", "x": 0, "y": 0, "width": 50, "height": 50}, {"op": "flip"}]))
    code, out, _ = run_cli(capsys, "image", "process", ids[0], "--ops-file", str(ops), "--label", "corner", "--json")
    assert code == 0 and Image.open(out["results"][0]["path"]).size == (50, 50)

    code, out, _ = run_cli(capsys, "image", "process", ids[0], ids[0] + "x", "--trim", "--json")
    assert code == ExitCode.PARTIAL and out["status"] == "partial"
    code, out, _ = run_cli(capsys, "image", "process", ids[0], "--preset", "shiny", "--json")
    assert code == ExitCode.INVALID_INPUT


def test_audio_process_and_generic_process(capsys, home, tmp_path):
    _, imported, _ = run_cli(capsys, "import", str(make_wav(tmp_path / "zap.wav", 300)), "--json")
    asset_id = imported["items"][0]["asset_id"]
    code, out, err = run_cli(capsys, "audio", "process", asset_id, "--trim-silence", "--normalize", "lufs",
                             "--target", "-18", "--fade-out", "10", "--format", "ogg", "--primary", "--json")  # fmt: skip
    assert code == 0, err
    assert out["results"][0]["label"] == "trim_m18lufs_fade_ogg" and out["results"][0]["path"].endswith(".ogg")

    code, out, _ = run_cli(capsys, "process", asset_id, "--preset", "game-wav", "--json")
    assert code == 0 and out["results"][0]["path"].endswith(".wav")
    _, img, _ = run_cli(capsys, "import", str(make_png(tmp_path / "i.png", (40, 40))), "--json")
    code, out, _ = run_cli(capsys, "process", img["items"][0]["asset_id"], "--preset", "pixel-art-2x", "--json")
    assert code == 0 and Image.open(out["results"][0]["path"]).size == (80, 80)

    code, qc_out, _ = run_cli(capsys, "qc", asset_id, "--json")
    assert qc_out["reports"][0]["metrics"]["loudness_lufs"] == pytest.approx(-18, abs=0.6)


def test_review_gate_and_simple_decisions(capsys, home, tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (32, 32), (0, 0, 0)).save(blank)
    _, imported, _ = run_cli(capsys, "import", str(blank), "--status", "candidate", "--json")
    asset_id = imported["items"][0]["asset_id"]

    code, out, err = run_cli(capsys, "review", "approve", asset_id, "--json")
    assert code == ExitCode.POLICY and out["error"]["code"] == "POLICY_VIOLATION" and "blank" in err
    code, out, _ = run_cli(capsys, "review", "approve", asset_id, "--force", "--json")
    assert code == 0 and out["review"]["qc"][0]["errors"] == ["blank"]
    code, out, _ = run_cli(capsys, "review", "reject", asset_id, "--note", "placeholder", "--json")
    assert out["review"] == {"status": "rejected", "asset_ids": [asset_id]}
    code, out, _ = run_cli(capsys, "review", "archive", asset_id, "--json")
    assert out["review"]["status"] == "archived"

    code, out, _ = run_cli(capsys, "qc", "--status", "archived", "--strict", "--json")
    assert code == ExitCode.ERROR and out["failing"] == 1
    code, out, _ = run_cli(capsys, "qc", "--json")
    assert code == ExitCode.INVALID_INPUT


def test_preview_selection_and_link(capsys, home, tmp_path):
    for i in range(3):
        run_cli(capsys, "import", str(make_png(tmp_path / f"tree_{i}.png", (64, 64), (i * 40, 90, 20, 255))),
                "--collection", "trees", "--json")  # fmt: skip
    code, out, _ = run_cli(capsys, "preview", "--collection", "trees", "--columns", "3", "--json")
    assert code == 0 and out["count"] == 3 and Path(out["sheet"]).is_file()
    code, out, _ = run_cli(capsys, "preview", "-q", "tree", "--limit", "2", "--json")
    assert out["count"] == 2
    code, out, _ = run_cli(capsys, "preview", "-q", "nothing-matches-this", "--json")
    assert code == ExitCode.NOT_FOUND

    code, out, _ = run_cli(capsys, "link", "ast_00002", "--to", "ast_00001", "--relation", "edited_from", "--json")
    assert out["link"] == {"asset_id": "ast_00002", "relation": "edited_from", "related_asset_id": "ast_00001"}
    _, graph, _ = run_cli(capsys, "lineage", "ast_00001", "--json")
    assert graph["lineage"]["descendants"][0]["asset_id"] == "ast_00002"


def test_human_output(capsys, home, tmp_path, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    _, imported, _ = run_cli(capsys, "import", str(make_png(tmp_path / "a.png", (64, 64))), "--type", "sprite",
                             "--status", "candidate", "--json")  # fmt: skip
    asset_id = imported["items"][0]["asset_id"]
    assert run_cli(capsys, "image", "process", asset_id, "--trim", "--primary")[0] == 0
    code, out, _ = run_cli(capsys, "lineage", asset_id)
    assert code == 0 and "variants" in out and "trim" in out
    code, out, _ = run_cli(capsys, "qc", asset_id)
    assert code == 0 and asset_id in out
    assert run_cli(capsys, "review", "queue")[0] == 0
    assert run_cli(capsys, "preview", asset_id)[0] == 0
