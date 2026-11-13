"""Device spend sharing (spend-ledger branch) and code updates from origin/main."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from assetforge import updater
from assetforge.core import budget, devices, settings_store
from assetforge.core.catalog import Library
from assetforge.core.util import utcnow
from assetforge.errors import BudgetExceededError, ConflictError


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("ASSETFORGE_DEVICE_NAME", raising=False)


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def bare(tmp_path: Path, name: str = "remote.git") -> Path:
    path = tmp_path / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


def add_job(lib: Library, cost: float | None, *, status: str = "succeeded", estimate: float | None = None,
            provider: str = "openai") -> None:  # fmt: skip
    now = utcnow()
    lib.conn.execute(
        "INSERT INTO generation_jobs (id, kind, status, provider, cost_usd, estimated_cost_usd, created_at, updated_at) "
        "VALUES (?, 'image', ?, ?, ?, ?, ?, ?)",
        (f"job_{uuid.uuid4().hex[:8]}", status, provider, cost, estimate, now, now),
    )
    lib.conn.commit()


@pytest.fixture
def two(tmp_path: Path):
    remote = bare(tmp_path)
    with Library(tmp_path / "a") as a, Library(tmp_path / "b") as b:
        for lib in (a, b):
            settings_store.set_value(lib, "sync.remote", str(remote))
        yield a, b, remote


# ---------------------------------------------------------------- spend


def test_device_identity_is_stable(tmp_path: Path) -> None:
    first = devices.device(tmp_path / "x")
    assert devices.device(tmp_path / "x") == first
    assert devices.device(tmp_path / "y")["id"] != first["id"]


def test_each_device_sees_the_others_spend(two) -> None:
    a, b, remote = two
    add_job(a, 1.25)
    add_job(b, 0.5, provider="gemini")
    add_job(b, None, status="running", estimate=0.25)  # in-flight calls count their estimate

    ra = devices.sync(a)
    assert ra["pushed"] and ra["others"] == []
    rb = devices.sync(b)
    assert [o["monthly_usd"] for o in rb["others"]] == [1.25]
    devices.sync(a)  # pulls b's file
    others = devices.others_summary(a.paths.home)
    assert others[0]["daily_usd"] == 0.75 and others[0]["by_provider_monthly"] == {"gemini": 0.5, "openai": 0.25}

    s = budget.summary(a)
    assert s["daily"]["this_device_usd"] == 1.25 and s["daily"]["other_devices_usd"] == 0.75
    assert s["daily"]["spent_usd"] == 2.0 and s["combined"] is True

    # the ledger is its own orphan branch with one file per device
    files = run(remote, "ls-tree", "--name-only", "-r", devices.LEDGER_BRANCH).split()
    assert sorted(files) == sorted(f"devices/{devices.device(lib.paths.home)['id']}.json" for lib in (a, b))
    assert run(remote, "branch", "--list", "main") == ""


def test_budget_limits_count_other_devices(two) -> None:
    a, b, _ = two
    add_job(b, 4.0)
    devices.sync(b)
    devices.sync(a)
    settings_store.set_value(a, "budget.daily_usd", 5.0)
    add_job(a, None, status="running")
    job = a.conn.execute("SELECT id FROM generation_jobs WHERE status = 'running'").fetchone()[0]
    with pytest.raises(BudgetExceededError):
        budget.reserve(a, job, 1.5)  # 4.0 elsewhere + 1.5 > 5.0
    settings_store.set_value(a, "budget.include_other_devices", False)
    budget.reserve(a, job, 1.5)  # own spend only
    assert budget.summary(a)["daily"]["spent_usd"] == 1.5


def test_unchanged_numbers_are_not_pushed_again(two) -> None:
    a, _, remote = two
    add_job(a, 1.0)
    assert devices.sync(a)["pushed"]
    before = run(remote, "rev-parse", devices.LEDGER_BRANCH)
    assert not devices.sync(a)["pushed"]
    assert run(remote, "rev-parse", devices.LEDGER_BRANCH) == before
    add_job(a, 1.0)
    assert devices.sync(a)["pushed"]


def test_push_race_retries_without_losing_the_other_device(two, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, _ = two
    add_job(a, 1.0)
    add_job(b, 2.0)
    real_fetch = devices._fetch
    calls = {"n": 0}

    def racing_fetch(folder: Path) -> bool:
        ok = real_fetch(folder)
        calls["n"] += 1
        if calls["n"] == 1:  # b pushes right after a fetched, so a's first push is rejected
            monkeypatch.setattr(devices, "_fetch", real_fetch)
            devices.sync(b)
            monkeypatch.setattr(devices, "_fetch", racing_fetch)
        return ok

    monkeypatch.setattr(devices, "_fetch", racing_fetch)
    result = devices.sync(a)
    assert result["pushed"] and [o["monthly_usd"] for o in result["others"]] == [2.0]
    assert [o["monthly_usd"] for o in devices.sync(b)["others"]] == [1.0]


def test_tick_syncs_spend_hourly(two, monkeypatch: pytest.MonkeyPatch) -> None:
    a, _, _ = two
    settings_store.set_value(a, "update.auto", False)
    assert updater.tick(a)["spend"]["status"] == "ok"
    assert updater.tick(a)["spend"]["status"] == "not_due"
    assert updater.tick(a, force_spend=True)["spend"]["status"] == "ok"


# ---------------------------------------------------------------- code updates


@pytest.fixture
def code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    origin = bare(tmp_path, "code.git")
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True, capture_output=True)
    (dev / "pyproject.toml").write_text("[project]\nname='x'\n")
    run(dev, "add", ".")
    run(dev, "commit", "-q", "-m", "first")
    run(dev, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    monkeypatch.setattr(updater, "app_running", lambda: False)
    monkeypatch.setattr(updater, "user_idle_seconds", lambda: 3600.0)
    return dev, other


def commit(repo: Path, name: str, text: str = "x") -> None:
    (repo / name).write_text(text)
    run(repo, "add", name)
    run(repo, "commit", "-q", "-m", name)


def test_other_device_pulls_main_when_idle(code, lib: Library) -> None:
    dev, other = code
    assert updater.apply(lib, root=other)["status"] == "up_to_date"
    commit(dev, "feature.py")
    assert updater.publish(dev)["status"] == "pushed"

    add_job(lib, None, status="running", estimate=0.1)
    pending = updater.apply(lib, root=other)
    assert pending["status"] == "pending" and "generation job" in pending["reasons"][0]
    lib.conn.execute("UPDATE generation_jobs SET status = 'succeeded'")
    lib.conn.commit()

    done = updater.apply(lib, root=other)
    assert done["status"] == "updated" and done["commits"] == 1 and (other / "feature.py").exists()
    assert updater.apply(lib, root=other)["status"] == "up_to_date"


def test_open_app_waits_for_the_user_to_step_away(code, lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    dev, other = code
    commit(dev, "a.py")
    updater.publish(dev)
    monkeypatch.setattr(updater, "app_running", lambda: True)
    monkeypatch.setattr(updater, "user_idle_seconds", lambda: 30.0)
    assert updater.apply(lib, root=other)["status"] == "pending"
    restarted = []
    monkeypatch.setattr(updater, "restart_app", lambda: restarted.append(1) or True)
    monkeypatch.setattr(updater, "user_idle_seconds", lambda: 3600.0)
    result = updater.apply(lib, root=other)
    assert result["status"] == "updated" and "app restarted" in result["steps"] and restarted


def test_development_checkout_is_never_touched(code, lib: Library) -> None:
    dev, other = code
    commit(other, "local.py")  # a commit GitHub doesn't have
    commit(dev, "b.py")
    updater.publish(dev)
    assert updater.apply(lib, root=other)["status"] == "blocked"
    run(other, "reset", "-q", "--hard", "origin/main~1")
    (other / "pyproject.toml").write_text("changed")  # uncommitted edit
    assert "uncommitted" in updater.apply(lib, root=other)["reason"]


def test_device_on_a_clean_side_branch_goes_back_to_main(code, lib: Library) -> None:
    dev, other = code
    run(other, "checkout", "-q", "-b", "old-experiment")
    commit(dev, "c.py")
    updater.publish(dev)
    result = updater.apply(lib, root=other)
    assert result["status"] == "updated" and run(other, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_publish_refuses_to_leave_work_on_another_branch(code) -> None:
    dev, _ = code
    run(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "d.py")
    with pytest.raises(ConflictError, match="devices only follow main"):
        updater.publish(dev)
    run(dev, "checkout", "-q", "main")
    commit(dev, "e.py")
    with pytest.raises(ConflictError) as err:
        updater.publish(dev)
    assert err.value.details["branches"] == {"feature": 1}
    run(dev, "merge", "-q", "--no-edit", "feature")
    assert updater.publish(dev)["status"] == "pushed"


def test_agent_plist_runs_tick(tmp_path: Path) -> None:
    plist = updater.agent_plist(tmp_path, tmp_path)
    assert plist["ProgramArguments"][-4:] == ["assetforge", "sync", "tick", "--json"]
    assert plist["EnvironmentVariables"]["ASSETFORGE_HOME"] == str(tmp_path)


# ---------------------------------------------------------------- devices without GitHub write access


def test_read_only_device_is_relayed_by_another(two, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b, remote = two
    settings_store.set_value(b, "sync.push", False)
    settings_store.set_value(a, "sync.relay", "legion")
    add_job(a, 1.0)
    add_job(b, 3.0)

    rb = devices.sync(b)  # read only: writes own.json, pushes nothing
    assert rb["mode"] == "read-only" and devices.own_path(b.paths.home).is_file()

    def fake_ssh(spec: str) -> dict:
        assert spec == "legion"
        import json as _json

        return _json.loads(devices.own_path(b.paths.home).read_text())

    monkeypatch.setattr(devices, "fetch_relayed", fake_ssh)
    ra = devices.sync(a)
    assert ra["pushed"] and ra["relayed"] and [o["monthly_usd"] for o in ra["others"]] == [3.0]
    assert [o["monthly_usd"] for o in devices.sync(b)["others"]] == [1.0]  # b reads a's file from the ledger
    assert budget.summary(b)["daily"]["spent_usd"] == 4.0
    assert not devices.sync(a)["pushed"]  # nothing new to carry


def test_relay_failure_does_not_stop_own_push(two, monkeypatch: pytest.MonkeyPatch) -> None:
    a, _, _ = two
    settings_store.set_value(a, "sync.relay", "legion")

    def down(spec: str) -> dict:
        raise devices.StorageError("Couldn't read legion's spend over SSH")

    monkeypatch.setattr(devices, "fetch_relayed", down)
    add_job(a, 1.0)
    result = devices.sync(a)
    assert result["pushed"] and "legion" in result["relay_errors"]


def test_relayed_file_must_look_like_a_snapshot() -> None:
    assert not devices.valid_snapshot({"days": {}, "device": {"id": "../../x"}, "updated_at": "z"})
    assert devices.valid_snapshot({"days": {}, "device": {"id": "abc123def0"}, "updated_at": "2026-11-13T00:00:00Z"})


# ---------------------------------------------------------------- Linux


def test_linux_idle_hint() -> None:
    now = 1_800_000_000.0
    assert updater.parse_idle_hint({"IdleHint": "no", "IdleSinceHint": "1"}, now) == 0.0
    assert updater.parse_idle_hint({"IdleHint": "yes", "IdleSinceHint": str(int((now - 900) * 1e6))}, now) == 900.0


def test_restart_keeps_the_session_token() -> None:
    argv = ["/r/.venv/bin/python", "/r/.venv/bin/assetforge", "--home", "/h", "ui", "--port", "8772", "--json"]
    line = '{"url": "http://127.0.0.1:8772/?token=abc", "port": 8772, "token": "abc", "home": "/h"}\n'
    assert updater.relaunch_argv(argv, line) == [*argv[:5], "--token", "abc", *argv[5:]]
    assert updater.relaunch_argv(argv, "") == argv


def test_systemd_units(tmp_path: Path) -> None:
    units = updater.systemd_units(tmp_path, tmp_path)
    service = units["assetforge-sync.service"]
    assert "sync tick --json" in service and "KillMode=process" in service and f"ASSETFORGE_HOME={tmp_path}" in service
    assert "OnUnitInactiveSec=600s" in units["assetforge-sync.timer"]


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="needs /proc (Linux)")
def test_ui_server_restarts_with_same_command(tmp_path: Path) -> None:
    import sys
    import time

    root = tmp_path / "checkout"
    (root / ".venv" / "bin").mkdir(parents=True)
    py = root / ".venv" / "bin" / "python"
    py.symlink_to(sys.executable)
    script = root / ".venv" / "bin" / "assetforge"
    script.write_text("import json, os, sys, time\n"
                      "tok = sys.argv[sys.argv.index('--token') + 1] if '--token' in sys.argv else 'first'\n"
                      "print(json.dumps({'token': tok, 'pid': os.getpid()}), flush=True)\n"
                      "time.sleep(60)\n")  # fmt: skip
    out = tmp_path / "state.json"
    proc = subprocess.Popen([str(py), str(script), "ui", "--json"], stdout=open(out, "w"), cwd=tmp_path)  # noqa: SIM115
    for _ in range(40):
        if out.stat().st_size:
            break
        time.sleep(0.1)
    assert updater.ui_processes(root) == [proc.pid]
    assert updater.restart_ui_servers(root)
    proc.wait(5)
    new = updater.ui_processes(root)
    assert len(new) == 1 and new[0] != proc.pid
    import json

    assert json.loads(out.read_text())["token"] == "first"  # same token passed back in
    for pid in new:
        os.kill(pid, 9)
