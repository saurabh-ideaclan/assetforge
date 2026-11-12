"""The app's change feed: payloads, per-story area revisions when files change from another process, story art from
the library, the SSE handshake and the polling fallback, the build id, and the task routes (legacy shapes kept)."""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from assetforge.app import feed as feed_mod
from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.core import tasks
from assetforge.core.catalog import Library
from assetforge.story.project import StoryProject, register
from conftest import make_png
from test_story_contract import SAMPLE
from test_ui_server import call, jcall

AREAS = ("text", "cast", "art", "audio", "checks", "releases", "workflow", "settings", "progress")


@pytest.fixture
def kite(lib: Library, home: Path, tmp_path: Path) -> StoryProject:
    shutil.copytree(SAMPLE, tmp_path / "kite")
    project = StoryProject(tmp_path / "kite")
    register(home, project)
    return project


@pytest.fixture
def server(home: Path, kite: StoryProject, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")
    srv = AppServer(home, router, token="secret")
    srv.feed.interval = 0.05  # tests don't wait a second per change
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def changes(srv: AppServer, since: int | None = None) -> dict[str, Any]:
    status, data = jcall(srv, "/api/changes" + (f"?since={since}" if since is not None else ""))
    assert status == 200
    return data


def wait_changed(srv: AppServer, since: int, topic: str, timeout: float = 5) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = changes(srv, since)
        if topic in data["changed"]:
            return data
        time.sleep(0.05)
    raise AssertionError(f"{topic} did not change; last: {data['changed']}")


def write_from_another_process(path: Path, text: str) -> None:
    subprocess.run([sys.executable, "-c", f"import pathlib; pathlib.Path({str(path)!r}).write_text({text!r})"], check=True)


def test_payload_covers_library_jobs_tasks_and_every_story_area(server: AppServer) -> None:
    data = changes(server)
    assert data["full"] and data["seq"] >= 1 and data["build"] and data["started_at"] == server.feed.started_at
    rev = data["revisions"]
    assert {"library", "jobs", "tasks", "stories", "publishing", "logins", "build"} <= set(rev)
    assert {f"story:kite-season:{a}" for a in AREAS} <= set(rev)
    again = changes(server, data["seq"])  # nothing changed since
    assert again["changed"] == [] and not again["full"]
    unknown = changes(server, 10_000)  # a seq the server no longer remembers: the full state, flagged
    assert unknown["full"] and unknown["revisions"] == changes(server)["revisions"]


@pytest.mark.parametrize(("name", "area"), [
    ("scenes/s_roof.json", "text"), ("outline.json", "text"), ("cast.json", "cast"), ("acting.json", "cast"),
    ("audio/s_roof.json", "audio"), ("clarity/s_roof.json", "checks"), ("visual_check.json", "checks"),
    ("lint.json", "checks"), ("workflow.json", "workflow"), ("story.json", "settings"), ("visual_canon.json", "art"),
    ("releases/r0001/release.json", "releases"),
])  # fmt: skip
def test_story_area_changes_when_another_process_writes_a_file(server: AppServer, kite: StoryProject, name: str,
                                                                area: str) -> None:  # fmt: skip
    seq = changes(server)["seq"]
    target = kite.root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_text() if target.exists() else None
    payload = original + " " if original is not None and name.endswith("story.json") else json.dumps({"changed": time.time()})
    write_from_another_process(target, payload)
    data = wait_changed(server, seq, f"story:kite-season:{area}")
    others = {t for t in data["changed"] if t.startswith("story:kite-season:")} - {f"story:kite-season:{area}"}
    assert not others, f"{name} should only touch {area}, also changed {others}"
    if area == "releases":
        assert "publishing" in data["changed"]


def test_story_art_follows_its_library_keys(server: AppServer, home: Path, tmp_path: Path) -> None:
    from assetforge.core.models import AssetPatch, ImportOptions, Status

    seq = changes(server)["seq"]
    with Library(home) as other:  # another connection, as an agent's process would have
        aid = other.import_paths([make_png(tmp_path / "x.png", (32, 32))], ImportOptions(type="sprite")).items[0].asset_id
    data = wait_changed(server, seq, "library")
    assert "story:kite-season:art" not in data["changed"]  # unkeyed art is not the story's
    seq = data["seq"]
    with Library(home) as other:
        other.update_asset(aid, AssetPatch(key="vn/kite-season/char/ravi/neutral"))
    wait_changed(server, seq, "story:kite-season:art")
    seq = changes(server)["seq"]
    with Library(home) as other:
        other.set_status([aid], Status.REJECTED)
    wait_changed(server, seq, "story:kite-season:art")


def test_tasks_topic_changes_on_progress_and_on_a_dead_process(server: AppServer, home: Path) -> None:
    seq = changes(server)["seq"]
    t = tasks.create(home, "script.x", title="Work")
    seq = wait_changed(server, seq, "tasks")["seq"]
    t.step("half")
    seq = wait_changed(server, seq, "tasks")["seq"]
    t.close()  # stops heartbeating without ending: the feed must say it is no longer running
    with Library(home) as other:
        other.conn.execute("UPDATE tasks SET heartbeat_at = 1 WHERE id = ?", (t.id,))
    wait_changed(server, seq, "tasks")
    _, listing = jcall(server, "/api/tasks")
    assert next(x for x in listing["tasks"] if x["id"] == t.id)["state"] == "interrupted"


def read_events(srv: AppServer, count: int, path: str = "/api/events", token: bool = True,
                after: Any = None, timeout: float = 5) -> tuple[int, str, list[tuple[str, dict[str, Any]]]]:  # fmt: skip
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=timeout)
    headers = {"Host": f"127.0.0.1:{srv.port}"}
    if token:
        headers["X-AssetForge-Token"] = "secret"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    if resp.status != 200:
        return resp.status, resp.getheader("Content-Type") or "", []
    events: list[tuple[str, dict[str, Any]]] = []
    buf = b""
    deadline = time.time() + timeout
    triggered = False
    while len(events) < count and time.time() < deadline:
        buf += resp.read1(65536)
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            fields = dict(line.split(": ", 1) for line in block.decode().splitlines() if ": " in line)
            if "event" in fields:
                events.append((fields["event"], json.loads(fields["data"])))
                if after and not triggered:
                    after()
                    triggered = True
    conn.close()
    return 200, resp.getheader("Content-Type") or "", events


def test_sse_handshake_and_change_events(server: AppServer, kite: StoryProject) -> None:
    assert read_events(server, 1, token=False)[0] == 403  # same auth as every route
    status, ctype, events = read_events(server, 2, after=lambda: write_from_another_process(
        kite.root / "scenes" / "s_roof.json", (kite.root / "scenes" / "s_roof.json").read_text() + "\n"))
    assert status == 200 and ctype.startswith("text/event-stream")
    (first, hello), (second, change) = events[:2]
    assert first == "hello" and hello["full"] and "story:kite-season:text" in hello["revisions"]
    assert second == "change" and change["seq"] > hello["seq"] and "story:kite-season:text" in change["changed"]
    assert change["build"] == hello["build"]
    # resuming from a known seq starts with what changed since (not the full state)
    status, _, [(name, resumed)] = read_events(server, 1, path=f"/api/events?since={hello['seq']}")
    assert name == "hello" and not resumed["full"] and "story:kite-season:text" in resumed["changed"]


def test_static_files_keep_their_csp(server: AppServer) -> None:
    status, headers, _ = call(server, "/static/store.js")
    assert status == 200 and "script-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"].split("script-src")[1].split(";")[0]


def test_version_and_build_change_with_static_files(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("// v1")
    monkeypatch.setattr(feed_mod, "STATIC_DIR", static)
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        _, v1 = jcall(srv, "/api/version")
        assert v1["build"].startswith(feed_mod.PYTHON_HASH) and v1["started_at"] and v1["pid"]
        (static / "app.js").write_text("// version two")
        srv.feed._static_at = 0  # re-check now rather than in two seconds
        srv.feed.poke()
        deadline = time.time() + 5
        while time.time() < deadline and jcall(srv, "/api/version")[1]["build"] == v1["build"]:
            time.sleep(0.05)
        _, v2 = jcall(srv, "/api/version")
        assert v2["build"] != v1["build"] and v2["started_at"] == v1["started_at"]
    finally:
        srv.shutdown()
        srv.server_close()
        srv.close_pool()


def test_task_routes_cancel_dismiss_and_legacy_shapes(server: AppServer, home: Path) -> None:
    t = tasks.create(home, "script.loop", title="Loop", cancellable=True)
    _, listing = jcall(server, "/api/tasks?state=running")
    assert [x["id"] for x in listing["tasks"]] == [t.id] and listing["counts"]["running"] == 1
    status, data = jcall(server, f"/api/tasks/{t.id}/cancel", method="POST", body={})
    assert status == 200 and data["task"]["cancel_requested"]
    with pytest.raises(tasks.TaskCancelled):
        t.check_cancel()
    t.cancel_done()
    t.close()
    assert jcall(server, f"/api/tasks/{t.id}/cancel", method="POST", body={})[0] == 409
    w = tasks.create(home, "story.write", title="Write", story="kite-season", request={"action": "write"}, retryable=True)
    w.wait_for_you("Review and approve: Characters")
    w.close()
    _, legacy = jcall(server, f"/api/story-tasks/{w.id}")
    assert legacy["task"]["state"] == "done" and legacy["task"]["status"] == "waiting" and legacy["task"]["action"] == "write"
    assert jcall(server, f"/api/tasks/{w.id}/dismiss", method="POST", body={})[1]["task"]["state"] == "succeeded"
    assert jcall(server, "/api/tasks/ffffffffffff")[0] == 404


def wait_task(srv: AppServer, task_id: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, data = jcall(srv, f"/api/tasks/{task_id}")
        if data["task"]["state"] not in ("queued", "running"):
            return data["task"]
        time.sleep(0.05)
    raise AssertionError("task did not end")


def test_app_story_tasks_queue_on_conflict_and_retry(server: AppServer, home: Path) -> None:
    blocker = tasks.create(home, "story.write", title="Writing elsewhere", story="kite-season", locks=("text", "cast"))
    _, started = jcall(server, "/api/stories/kite-season/run", method="POST", body={"action": "check", "provider": "fake"})
    assert started["queued"] and started["waiting_for"] == "Writing elsewhere"
    _, second = jcall(server, "/api/stories/kite-season/run", method="POST", body={"action": "improve", "feedback": "x"})
    assert second["queued"]
    time.sleep(0.3)
    _, queued = jcall(server, f"/api/tasks/{started['task_id']}")
    assert queued["task"]["state"] == "queued" and queued["task"]["started_by"] == "app" and queued["task"]["can_cancel"]
    assert "Writing elsewhere" in queued["task"]["message"]
    _, legacy = jcall(server, f"/api/story-tasks/{started['task_id']}")
    assert legacy["task"]["state"] == "running"  # old pollers keep waiting while it is queued
    # a queued task can always be cancelled; it never starts
    jcall(server, f"/api/tasks/{second['task_id']}/cancel", method="POST", body={})
    cancelled = wait_task(server, second["task_id"])
    assert cancelled["state"] == "cancelled" and cancelled["retryable"]
    # an action that does not touch the text runs beside the writer
    _, plan = jcall(server, "/api/stories/kite-season/run", method="POST", body={"action": "dynamics_plan", "provider": "fake"})
    assert not plan["queued"] and wait_task(server, plan["task_id"])["state"] == "succeeded"
    blocker.succeed()
    blocker.close()
    done = wait_task(server, started["task_id"])
    assert done["state"] == "succeeded" and done["result_link"] == "#/story/kite-season/improve"
    assert done["steps"][0]["label"] == "Continuity check" and done["request"]["action"] == "check"
    assert jcall(server, f"/api/tasks/{started['task_id']}/retry", method="POST", body={})[0] == 409  # nothing to retry
    _, again = jcall(server, f"/api/tasks/{second['task_id']}/retry", method="POST", body={})
    assert again["task_id"] != second["task_id"]
    _, retried = jcall(server, f"/api/tasks/{again['task_id']}")
    assert retried["task"]["request"] == {"action": "improve", "feedback": "x"}
    jcall(server, f"/api/tasks/{again['task_id']}/cancel", method="POST", body={})  # don't rewrite the sample
    wait_task(server, again["task_id"])
    _, story = jcall(server, "/api/stories/kite-season")
    assert story["task"]["id"] in (again["task_id"], plan["task_id"], started["task_id"])
    _, activity = jcall(server, "/api/activity")
    assert activity["tasks"]["running"] == 0
