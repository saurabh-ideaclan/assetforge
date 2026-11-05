"""Stories in the desktop app: writer catalog (sign-in + models), create → write → improve → export → play, all
through the HTTP API with the offline providers. Also the improve step and model discovery on their own."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.config import Settings
from assetforge.core.catalog import Library
from assetforge.errors import ProviderError
from assetforge.llm import runner
from assetforge.llm.base import TextCall
from assetforge.llm.claude_cli import ClaudeCLIProvider
from assetforge.llm.codex_cli import CodexCLIProvider
from assetforge.story import steps

from test_ui_server import call, jcall


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


@pytest.fixture
def server(home: Path, fake: None):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def wait_task(srv: AppServer, task_id: str, timeout: float = 120) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, data = jcall(srv, f"/api/story-tasks/{task_id}")
        if data["task"]["state"] != "running":
            return data["task"]
        time.sleep(0.1)
    raise AssertionError("task did not finish")


def test_app_story_flow(server: AppServer, home: Path) -> None:
    status, info = jcall(server, "/api/llm/providers")
    assert status == 200 and "fake" in {p["name"] for p in info["providers"]}
    status, made = jcall(server, "/api/stories", method="POST", body={
        "title": "Fog Lantern", "premise": "An apprentice uncovers smugglers.", "scenes": 4, "endings": 2, "provider": "fake"})
    assert status == 200 and made["id"] == "fog-lantern" and made["dir"].startswith(str(home))
    sid = made["id"]

    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    # one task per story at a time
    assert jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "check"})[0] in (409, 200)
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done", task
    assert task["result"]["assembled"] and task["result"]["export"]["missing"]

    _, detail = jcall(server, f"/api/stories/{sid}")
    assert detail["status"]["scenes_written"] == 4 and detail["playable"] and detail["assets"]["have"] == 0
    assert {"world", "cast", "outline", "scenes", "issues"} <= set(detail)

    _, settings = jcall(server, f"/api/stories/{sid}/settings", method="POST", body={"provider": "fake", "model": "fake-text-1"})
    assert settings["config"]["model"] == "fake-text-1"

    assert jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "improve", "feedback": ""})[0] == 400
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "improve", "feedback": "More humour"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["revised"], task
    _, detail = jcall(server, f"/api/stories/{sid}")
    assert detail["feedback"][-1]["feedback"] == "More humour"

    status, headers, body = call(server, f"/play/{sid}/")
    assert status == 200 and b"assetforge-vn-player" in body
    status, headers, body = call(server, f"/play/{sid}/story.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and body.startswith(b"window.STORY")
    assert call(server, f"/play/{sid}/../story.json")[0] == 404  # nothing outside build/

    _, listing = jcall(server, "/api/stories")
    assert [s["id"] for s in listing["stories"]] == [sid] and listing["stories"][0]["playable"]


def test_app_reports_task_failures(server: AppServer, monkeypatch: pytest.MonkeyPatch) -> None:
    from assetforge.story import offline

    _, made = jcall(server, "/api/stories", method="POST", body={"title": "Broken", "premise": "x y z", "provider": "fake"})

    def boom(config):  # noqa: ANN001, ANN202
        def fail(_):  # noqa: ANN001, ANN202
            raise ProviderError("expired", provider="fake", kind="auth", hint="sign in again")
        return fail

    monkeypatch.setattr(offline, "world", boom)
    _, run = jcall(server, f"/api/stories/{made['id']}/run", method="POST", body={"action": "write"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "failed" and task["error"]["code"] == "PROVIDER_AUTH" and task["error"]["hint"]


# ---------------------------------------------------------------------- improve + model discovery


def test_improve_rewrites_only_targeted_scenes(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=5, endings=2,
                         provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    untouched = project.scene_path("e01").read_text()
    result = steps.improve(steps.Ctx(lib, project), "Make Tom funnier", scenes=["s02"])
    assert result["revised"] == ["s02"] and result["errors"] == 0
    assert "Make Tom funnier" in project.scene_path("s02").read_text()
    assert project.scene_path("e01").read_text() == untouched
    history = json.loads((project.root / "feedback.json").read_text())["history"]
    assert history[-1]["edits"] == [{"scene": "s02", "note": "Make Tom funnier"}]


class Spawn:
    def __init__(self, outputs: dict[str, tuple[int, str]]):
        self.outputs, self.calls = outputs, []

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        key = " ".join(argv[1:3])
        code, out = self.outputs.get(key, (0, ""))
        return subprocess.CompletedProcess(argv, code, out, "")


def test_signed_in_checks_and_codex_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(runner, "spawn", Spawn({
        "auth status": (0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})),
        "login status": (0, "Logged in using ChatGPT\n"),
        "--version ": (0, "codex-cli 0.145.0"),
    }))
    assert ClaudeCLIProvider(Settings({})).signed_in() == (True, "max")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(json.dumps({"client_version": "0.166.0", "models": [
        {"slug": "hidden", "visibility": "hide", "priority": 0},
        {"slug": "gpt-b", "display_name": "B", "visibility": "list", "priority": 2},
        {"slug": "gpt-a", "display_name": "A", "visibility": "list", "priority": 1}]}))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    codex = CodexCLIProvider(Settings({}))
    assert codex.signed_in() == (True, "ChatGPT")
    assert [m["id"] for m in codex.models()] == ["", "gpt-a", "gpt-b"]


def test_codex_without_a_model_finds_one_the_cli_accepts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from assetforge.llm import codex_cli

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-new", "visibility": "list", "priority": 1}, {"slug": "gpt-old", "visibility": "list", "priority": 2}]}))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(codex_cli, "_WORKING", None)
    monkeypatch.setattr(runner, "which", lambda name: f"/bin/{name}")
    tried: list[str] = []

    def fake_run(self, call: TextCall):  # noqa: ANN001, ANN202
        tried.append(call.model)
        if call.model == "gpt-new":
            raise ProviderError("codex-cli: The 'gpt-new' model requires a newer version of Codex.", provider="codex-cli",
                                kind="rejected")  # fmt: skip
        return "ok"

    monkeypatch.setattr(codex_cli.CodexCLIProvider, "_run", fake_run)
    provider = codex_cli.CodexCLIProvider(Settings({}))
    assert provider.run(TextCall(prompt="x")) == "ok" and tried == ["gpt-new", "gpt-old"]
    assert provider.default_model() == "gpt-old"
    tried.clear()
    provider.run(TextCall(prompt="y"))
    assert tried == ["gpt-old"]  # remembered


def test_runner_finds_clis_outside_a_bare_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # what a Finder-launched app gets
    monkeypatch.setattr(runner, "EXTRA_DIRS", (str(tmp_path),))
    assert str(tmp_path) in runner.search_path().split(":")
    assert runner.clean_env()["PATH"].startswith("/usr/bin:/bin:")


# ---------------------------------------------------------------------- sign-in (link out, code back)


@pytest.fixture
def fake_login_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in `claude` that prints a sign-in link, waits for a code on its terminal, and 'stores a login'."""
    marker = tmp_path / "signed-in"
    script = tmp_path / "claude"
    script.write_text(f"""#!/usr/bin/env python3
import sys
print("Opening browser to sign in...")
print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=" + "x" * 300)
code = input("Paste code here if prompted > ").strip()
if code == "good-code":
    open({str(marker)!r}, "w").write("ok")
    print("Login successful.")
    sys.exit(0)
print("Invalid code: invalid_grant")
sys.exit(1)
""")
    script.chmod(0o755)
    monkeypatch.setattr(runner, "which", lambda name: str(script) if name == "claude" else None)
    monkeypatch.setattr(runner, "popen", subprocess.Popen)
    monkeypatch.setattr(ClaudeCLIProvider, "signed_in", lambda self: (True, "pro") if marker.exists() else (False, "no"))
    return marker


def _wait(session, states, timeout=20.0):  # noqa: ANN001, ANN202
    deadline = time.time() + timeout
    while session.state not in states and time.time() < deadline:
        time.sleep(0.1)
    return session.state


def test_login_shows_link_takes_code_and_confirms(fake_login_cli: Path) -> None:
    from assetforge.llm import login

    session = login.start("claude-cli", Settings({}))
    assert _wait(session, ("waiting",)) == "waiting"
    assert session.url.startswith("https://claude.com/cai/oauth/authorize") and len(session.url) > 300  # unwrapped
    login.submit(session.id, "good-code")
    assert _wait(session, ("done", "failed")) == "done" and session.account == "pro"


def test_login_reports_a_rejected_code(fake_login_cli: Path) -> None:
    from assetforge.llm import login

    session = login.start("claude-cli", Settings({}))
    _wait(session, ("waiting",))
    with pytest.raises(Exception):
        login.submit(session.id, "two words")  # codes are one piece
    login.submit(session.id, "stale-code")
    assert _wait(session, ("done", "failed")) == "failed" and "rejected" in session.error


def test_login_api_flow(server: AppServer, fake_login_cli: Path) -> None:
    status, data = jcall(server, "/api/llm/login", method="POST", body={"provider": "claude-cli"})
    assert status == 200
    lid = data["login"]["id"]
    deadline = time.time() + 20
    while data["login"]["state"] == "starting" and time.time() < deadline:
        time.sleep(0.1)
        data = jcall(server, f"/api/llm/login/{lid}")[1]
    assert data["login"]["url"] and data["login"]["needs_code"]
    jcall(server, f"/api/llm/login/{lid}/code", method="POST", body={"code": "good-code"})
    while data["login"]["state"] not in ("done", "failed") and time.time() < deadline:
        time.sleep(0.1)
        data = jcall(server, f"/api/llm/login/{lid}")[1]
    assert data["login"]["state"] == "done"
    assert jcall(server, "/api/llm/login", method="POST", body={"provider": "nope"})[0] == 400


def test_gemini_retired_free_tier_is_a_clear_unsupported_error() -> None:
    from assetforge.llm.gemini_cli import GeminiCLIProvider

    out = json.dumps({"error": {"message": "onMessage: 'This client is no longer supported for Gemini Code Assist for "
                                           "individuals. To continue using Gemini, please migrate to Antigravity'"}})  # fmt: skip
    with pytest.raises(ProviderError) as info:
        GeminiCLIProvider(Settings({})).parse(1, out, "", TextCall(prompt="x"))
    assert info.value.kind == "unsupported" and info.value.allows_fallback and not info.value.retryable
    assert "--skip-trust" in GeminiCLIProvider(Settings({})).argv(TextCall(prompt="x"))
