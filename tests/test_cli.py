"""The CLI is AssetForge's API for agents: these tests pin its JSON contract and exit codes."""

from __future__ import annotations

from pathlib import Path

from assetforge.errors import ExitCode

from conftest import PACK_DUPLICATES, PACK_UNIQUE, make_png, run_cli


def test_init_and_info(capsys, home):
    code, out, _ = run_cli(capsys, "init", "--json")
    assert code == 0
    assert out["status"] == "ok"
    assert out["library"]["home"] == str(home.resolve())

    code, out, _ = run_cli(capsys, "info", "--json")
    assert out["library"]["counts"]["assets"] == 0


def test_phase1_completion_flow(capsys, home, pack):
    """Import a folder of hundreds of assets, search, retrieve paths, detect duplicates: no API key."""
    code, out, _ = run_cli(capsys, "import", str(pack), "--collection", "starter-pack", "--json")
    assert code == 0, out
    assert out["summary"]["created"] == PACK_UNIQUE
    assert out["summary"]["duplicate"] == PACK_DUPLICATES
    duplicates = [i for i in out["items"] if i["action"] == "duplicate"]
    assert all(i["duplicate_of"].startswith("ast_") for i in duplicates)

    code, out, _ = run_cli(capsys, "search", "jump sound effect", "--limit", "3", "--json")
    assert code == 0
    assert out["count"] == 3
    hit = out["results"][0]
    assert hit["name"].startswith("jump_") and hit["type"] == "sfx"
    assert Path(hit["path"]).is_file()

    code, out, _ = run_cli(capsys, "get", hit["id"], "--json")
    asset = out["asset"]
    assert asset["variants"][0]["file"]["path"] == hit["path"]
    assert asset["collections"] == ["starter-pack"]

    code, out, _ = run_cli(capsys, "get", hit["id"], "--path")
    assert out.strip() == hit["path"]

    code, out, _ = run_cli(capsys, "collection", "show", "starter-pack", "--json")
    assert out["collection"]["asset_count"] == PACK_UNIQUE

    code, out, _ = run_cli(capsys, "verify", "--json")
    assert code == 0 and out["verify"]["checked"] == PACK_UNIQUE


def test_errors_are_json_on_stdout_and_text_on_stderr(capsys, home):
    code, out, err = run_cli(capsys, "get", "ast_00042", "--json")
    assert code == ExitCode.NOT_FOUND
    assert out == {"status": "error", "error": {"code": "NOT_FOUND", "message": "No asset matches 'ast_00042'",
                                                "retryable": False, "details": {"ref": "ast_00042"}}}  # fmt: skip
    assert "NOT_FOUND" in err

    code, out, _ = run_cli(capsys, "import", "/definitely/missing", "--json")
    assert code == ExitCode.NOT_FOUND and out["error"]["code"] == "NOT_FOUND"


def test_usage_errors_are_json_when_requested(capsys, home):
    code, out, err = run_cli(capsys, "search", "--limit", "many", "--json")
    assert code == ExitCode.USAGE
    assert out["status"] == "error" and out["error"]["code"] == "USAGE"
    assert err

    code, out, _ = run_cli(capsys, "search", "x", "--limit", "0", "--json")
    assert code == ExitCode.INVALID_INPUT and out["error"]["code"] == "INVALID_INPUT"


def test_partial_import_exit_code(capsys, home, tmp_path):
    make_png(tmp_path / "in" / "ok.png")
    (tmp_path / "in" / "bad.png").write_bytes(b"garbage")

    code, out, _ = run_cli(capsys, "import", str(tmp_path / "in"), "--json")
    assert code == ExitCode.PARTIAL
    assert out["status"] == "partial"
    assert out["summary"]["created"] == 1 and out["summary"]["error"] == 1


def test_update_review_use_and_collections(capsys, home, tmp_path):
    src = make_png(tmp_path / "beetle.png")
    _, out, _ = run_cli(capsys, "import", str(src), "--status", "candidate", "--json")
    asset_id = out["items"][0]["asset_id"]

    code, out, _ = run_cli(
        capsys, "update", asset_id, "--description", "cute beetle enemy", "--add-tag", "enemy",
        "--meta", "frames=4", "--meta", "note=hello", "--key", "enemy/beetle", "--json",
    )  # fmt: skip
    assert code == 0
    assert out["asset"]["metadata"] == {"frames": 4, "note": "hello"}
    assert out["asset"]["key"] == "enemy/beetle"

    code, out, _ = run_cli(capsys, "review", "approve", "enemy/beetle", "--note", "ok", "--json")
    assert out["review"]["status"] == "approved" and out["review"]["asset_ids"] == [asset_id]
    assert out["review"]["qc"][0]["ok"]

    code, out, _ = run_cli(capsys, "use", asset_id, "--project", "panda-bamboo", "--as", "enemies/beetle", "--json")
    assert out["usage"]["usage_key"] == "enemies/beetle"
    code, out, _ = run_cli(capsys, "usage", "--project", "panda-bamboo", "--json")
    assert out["count"] == 1 and out["usage"][0]["asset"]["id"] == asset_id

    run_cli(capsys, "collection", "create", "bugs", "--kind", "character", "--json")
    code, out, _ = run_cli(capsys, "collection", "add", "bugs", asset_id, "--json")
    assert out["added"] == 1
    code, out, _ = run_cli(capsys, "collection", "list", "--json")
    assert out["collections"][0]["asset_count"] == 1
    code, out, _ = run_cli(capsys, "collection", "create", "bugs", "--json")
    assert code == ExitCode.CONFLICT and out["error"]["code"] == "CONFLICT"


def test_human_output_does_not_crash(capsys, home, pack, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    assert run_cli(capsys, "import", str(pack / "UI"))[0] == 0
    code, out, _ = run_cli(capsys, "search", "button")
    assert code == 0 and "blue_button" in out
    assert run_cli(capsys, "info")[0] == 0
    assert run_cli(capsys, "collection", "list")[0] == 0
