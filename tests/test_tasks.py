"""Durable tasks (core/tasks.py): lifecycle, heartbeat staleness → interrupted, cooperative cancel, the story
concurrency rule (queue on intersecting locks), recovery at server start, and CLI commands registering themselves."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from assetforge.core import tasks
from assetforge.core.catalog import Library
from assetforge.errors import ConflictError, InvalidInputError
from assetforge.story.project import StoryProject, register
from assetforge.story.tracked import TrackedCtx
from conftest import run_cli
from test_story_contract import SAMPLE


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


@pytest.fixture
def kite(lib: Library, home: Path, tmp_path: Path) -> StoryProject:
    shutil.copytree(SAMPLE, tmp_path / "kite")
    project = StoryProject(tmp_path / "kite")
    register(home, project)
    return project


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_lifecycle_steps_log_and_result(lib: Library) -> None:
    with tasks.track(lib, "script.render", title="Render", story="s1", steps=["One", "Two"]) as t:
        assert tasks.get(lib, t.id)["state"] == "running"
        t.step(0)
        t.log("half way")
        t.step(1)
        assert tasks.get(lib, t.id)["progress"] == pytest.approx(0.5)
        t.done_step("an extra unit of work")
    task = tasks.get(lib, t.id)
    assert task["state"] == "succeeded" and task["started_by"] == "script" and task["progress"] == 1
    assert [s["state"] for s in task["steps"]] == ["done", "done", "done"]
    assert any("half way" in line for line in task["log"]) and task["ended_at"] >= task["started_at"]
    assert task["pid"] and task["host"] == tasks.HOST and not task["can_cancel"]


def test_failure_and_explicit_result(lib: Library) -> None:
    with pytest.raises(InvalidInputError):
        with tasks.track(lib, "script.x", title="Breaks") as t:
            raise InvalidInputError("bad input", hint="fix it")
    failed = tasks.get(lib, t.id)
    assert failed["state"] == "failed" and failed["error"]["code"] == "INVALID_INPUT" and failed["error"]["hint"] == "fix it"
    with tasks.track(lib, "script.y", title="Has a result", started_by="app") as t2:
        t2.succeed({"made": 3}, link="#/assets")
    done = tasks.get(lib, t2.id)
    assert done["result"] == {"made": 3} and done["result_link"] == "#/assets" and done["started_by"] == "app"


def test_heartbeat_staleness_and_dead_process_mean_interrupted(lib: Library) -> None:
    silent = tasks.create(lib, "script.silent", title="Silent")
    silent.close()  # the heartbeat stops but the row still says running
    lib.conn.execute("UPDATE tasks SET heartbeat_at = ? WHERE id = ?", (time.time() - tasks.STALE_SECONDS - 5, silent.id))
    assert tasks.get(lib, silent.id)["state"] == "interrupted"  # every read says so at once
    assert tasks.get(lib, silent.id)["error"]["code"] == "INTERRUPTED"
    gone = tasks.create(lib, "script.gone", title="Process gone")
    gone.close()
    lib.conn.execute("UPDATE tasks SET pid = ? WHERE id = ?", (dead_pid(), gone.id))
    assert tasks.get(lib, gone.id)["state"] == "interrupted"  # same host, dead pid: no need to wait for staleness
    alive = tasks.create(lib, "script.alive", title="Alive")
    assert tasks.list_tasks(lib, states=["running"])[0]["id"] == alive.id  # `running` never returns a dead task
    before = tasks.revision(lib)
    assert set(tasks.recover(lib)) == {silent.id, gone.id}
    assert tasks.revision(lib) != before
    row = lib.conn.execute("SELECT state, ended_at FROM tasks WHERE id = ?", (gone.id,)).fetchone()
    assert row["state"] == "interrupted" and row["ended_at"]
    assert tasks.recover(lib) == []
    alive.succeed()
    alive.close()


def test_interrupted_task_releases_its_running_text_jobs(lib: Library) -> None:
    from assetforge.core.util import dumps

    t = tasks.create(lib, "story.write", title="Write", story="s1", project="vn-s1")
    t.close()
    lib.conn.execute("UPDATE tasks SET pid = ? WHERE id = ?", (dead_pid(), t.id))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for jid, project in (("job_00001", "vn-s1"), ("job_00002", "vn-other")):
        lib.conn.execute("INSERT INTO generation_jobs (id, kind, status, request, fingerprint, created_at, updated_at) "
                         "VALUES (?, 'text', 'running', ?, ?, ?, ?)", (jid, dumps({"project": project}), jid, now, now))  # fmt: skip
    tasks.recover(lib)
    status = dict(lib.conn.execute("SELECT id, status FROM generation_jobs").fetchall())
    assert status == {"job_00001": "failed", "job_00002": "running"}


def test_cooperative_cancel(lib: Library) -> None:
    started = threading.Event()
    seen: dict[str, str] = {}

    def work() -> None:
        try:
            with tasks.track(lib.paths.home, "script.loop", title="Loop", cancellable=True) as t:
                seen["id"] = t.id
                started.set()
                for _ in range(200):
                    time.sleep(0.02)
                    t.check_cancel()
        except tasks.TaskCancelled:
            seen["cancelled"] = "yes"

    thread = threading.Thread(target=work)
    thread.start()
    started.wait(5)
    assert tasks.get(lib, seen["id"])["can_cancel"]
    tasks.request_cancel(lib, seen["id"])
    thread.join(10)
    task = tasks.get(lib, seen["id"])
    assert seen.get("cancelled") and task["state"] == "cancelled" and task["error"]["code"] == "CANCELLED"
    with pytest.raises(ConflictError, match="already ended"):
        tasks.request_cancel(lib, seen["id"])
    stubborn = tasks.create(lib, "script.stubborn", title="Can't stop")
    with pytest.raises(ConflictError, match="can't be stopped"):
        tasks.request_cancel(lib, stubborn.id)
    stubborn.succeed()
    stubborn.close()


def test_tracked_story_context_reports_calls_and_stops_when_cancelled(lib: Library, kite: StoryProject) -> None:
    t = tasks.create(lib, "story.check", title="Check", story="kite-season", cancellable=True)
    ctx = TrackedCtx(lib=lib, project=kite, task=t)
    assert ctx.config.slug == "kite-season"
    ctx.calls.append({"task": "check", "provider": "fake", "model": "fake-text-1", "reused": False})
    assert [s["label"] for s in tasks.get(lib, t.id)["steps"]] == ["Continuity check · fake fake-text-1"]
    tasks.request_cancel(lib, t.id)
    with pytest.raises(tasks.TaskCancelled):
        ctx.config  # noqa: B018 - read before every model call
    with pytest.raises(tasks.TaskCancelled):
        ctx.calls.append({"task": "scene.a.p1"})
    t.cancel_done()
    t.close()


def test_intersecting_locks_queue_and_others_run_side_by_side(lib: Library) -> None:
    first = tasks.create(lib, "story.write", title="Write", story="s1", locks=("text", "cast"))
    art = tasks.create(lib, "story.assets", title="Art", story="s1", locks=("art",), queued=True)
    art.wait_turn(poll=0.01)  # no intersection: runs at once
    assert tasks.get(lib, art.id)["state"] == "running"
    other_story = tasks.create(lib, "story.improve", title="Elsewhere", story="s2", locks=("text",), queued=True)
    other_story.wait_turn(poll=0.01)
    assert tasks.get(lib, other_story.id)["state"] == "running"
    second = tasks.create(lib, "story.improve", title="Improve", story="s1", locks=("text",), queued=True)
    assert [b["id"] for b in tasks.conflicting(lib, tasks.get(lib, second.id))] == [first.id]
    done = threading.Event()
    threading.Thread(target=lambda: (second.wait_turn(poll=0.02), done.set()), daemon=True).start()
    time.sleep(0.3)
    waiting = tasks.get(lib, second.id)
    assert not done.is_set() and waiting["state"] == "queued" and "Queued behind" in waiting["message"]
    first.succeed()
    assert done.wait(5) and tasks.get(lib, second.id)["state"] == "running"
    for t in (first, art, other_story, second):
        t.succeed()
        t.close()


def test_waiting_tasks_are_dismissed_or_superseded(lib: Library) -> None:
    t = tasks.create(lib, "story.write", title="Write", story="s1")
    t.wait_for_you("Review and approve: Characters", link="#/story/s1/workflow")
    t.close()
    assert tasks.get(lib, t.id)["state"] == "waiting" and tasks.counts(lib)["waiting"] == 1
    newer = tasks.create(lib, "story.write", title="Write again", story="s1")
    assert tasks.get(lib, t.id)["state"] == "succeeded"
    newer.wait_for_you("Again")
    newer.close()
    assert tasks.dismiss(lib, newer.id)["state"] == "succeeded"
    with pytest.raises(ConflictError):
        tasks.dismiss(lib, newer.id)


def test_server_start_marks_orphans_interrupted(home: Path, lib: Library) -> None:
    from assetforge.app.api import router
    from assetforge.app.server import AppServer

    orphan = tasks.create(lib, "story.write", title="Left running by a dead app", story="s1", started_by="app")
    orphan.close()
    lib.conn.execute("UPDATE tasks SET pid = ? WHERE id = ?", (dead_pid(), orphan.id))
    srv = AppServer(home, router, token="secret")
    try:
        assert orphan.id in srv.recovered["tasks"] and "jobs" in srv.recovered
        assert lib.conn.execute("SELECT state FROM tasks WHERE id = ?", (orphan.id,)).fetchone()[0] == "interrupted"
    finally:
        srv.server_close()
        srv.close_pool()


def test_cli_story_commands_register_themselves(home: Path, lib: Library, kite: StoryProject, fake: None,
                                               capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    code, out, _ = run_cli(capsys, "story", "check", "--dir", str(kite.root), "--provider", "fake", "--json")
    assert code == 0 and out["status"] == "ok"
    [task] = tasks.list_tasks(lib, kind="story.check", full=True)
    assert task["state"] == "succeeded" and task["started_by"] == "cli" and task["story"] == "kite-season"
    assert task["project"] == "vn-kite-season" and "story check" in task["command"] and task["locks"] == ["checks", "text"]
    assert task["steps"] and task["steps"][0]["label"].startswith("Continuity check")
    assert task["result_link"] == "#/story/kite-season" and task["cancellable"] is False  # ended: no longer cancellable
    # a failing command is recorded as failed, with the error the CLI printed
    code, out, _ = run_cli(capsys, "story", "refine", "--dir", str(kite.root), "--scene", "nope", "--note", "x",
                           "--provider", "fake", "--json")  # fmt: skip
    assert code != 0
    [refine] = tasks.list_tasks(lib, kind="story.refine", full=True)
    assert refine["state"] == "failed" and refine["error"]["code"] == out["error"]["code"]
    # read-only commands and dry runs are not tasks
    run_cli(capsys, "story", "status", "--dir", str(kite.root), "--json")
    run_cli(capsys, "story", "assets", "--dir", str(kite.root), "--dry-run", "--json")
    assert {t["kind"] for t in tasks.list_tasks(lib)} == {"story.check", "story.refine"}


def test_started_by_comes_from_the_environment(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_STARTED_BY", "agent")
    with tasks.track(lib, "script.z") as t:
        pass
    assert tasks.get(lib, t.id)["started_by"] == "agent"
