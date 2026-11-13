"""Device spend shared over SSH, and code updates from origin/main."""

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
def two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Device a (can SSH to b, lists it in sync.peers) and device b (no settings). SSH runs the shell locally."""
    calls: list[str] = []

    def local_ssh(host: str, command: str, *, stdin: str | None = None) -> str:
        calls.append(host)
        if host == "down":
            raise devices.StorageError("SSH to down failed", hint="Connection timed out")
        proc = subprocess.run(["sh", "-c", command], input=stdin, capture_output=True, text=True, check=True)
        return proc.stdout

    monkeypatch.setattr(devices, "ssh", local_ssh)
    with Library(tmp_path / "a") as a, Library(tmp_path / "b") as b:
        settings_store.set_value(a, "sync.peers", f"legion:{b.paths.home}")
        yield a, b, calls


# ---------------------------------------------------------------- spend


def test_device_identity_is_stable(tmp_path: Path) -> None:
    first = devices.device(tmp_path / "x")
    assert devices.device(tmp_path / "x") == first
    assert devices.device(tmp_path / "y")["id"] != first["id"]


def test_devices_swap_spend_over_ssh(two) -> None:
    a, b, calls = two
    add_job(a, 1.25)
    add_job(b, 0.5, provider="gemini")
    add_job(b, None, status="running", estimate=0.25)  # in-flight calls count their estimate

    rb = devices.sync(b)  # b has no peers: it only writes its own numbers, no network
    assert rb["peers"] == [] and devices.own_path(b.paths.home).is_file() and calls == []

    ra = devices.sync(a)  # a pulls b's numbers and pushes its own into b
    assert ra["status"] == "ok" and ra["received"] and calls == ["legion", "legion"]
    others = devices.others_summary(a.paths.home)
    assert others[0]["daily_usd"] == 0.75 and others[0]["by_provider_monthly"] == {"gemini": 0.5, "openai": 0.25}
    assert [o["monthly_usd"] for o in devices.others_summary(b.paths.home)] == [1.25]

    s = budget.summary(a)
    assert s["daily"]["this_device_usd"] == 1.25 and s["daily"]["other_devices_usd"] == 0.75
    assert s["daily"]["spent_usd"] == 2.0 and s["combined"] is True
    assert budget.summary(b)["daily"]["spent_usd"] == 2.0


def test_hub_forwards_third_device(two, tmp_path: Path) -> None:
    a, b, _ = two
    with Library(tmp_path / "c") as c:
        settings_store.set_value(a, "sync.peers", f"legion:{b.paths.home}, other:{c.paths.home}")
        add_job(c, 2.0)
        devices.sync(c)
        devices.sync(b)
        devices.sync(a)
        assert sorted(o["monthly_usd"] for o in devices.others_summary(b.paths.home)) == [0.0, 2.0]


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


def test_unreachable_peer_is_reported_not_fatal(two) -> None:
    a, b, _ = two
    settings_store.set_value(a, "sync.peers", f"down, legion:{b.paths.home}")
    add_job(b, 1.0)
    devices.sync(b)
    result = devices.sync(a)
    assert result["status"] == "partial" and "down" in result["errors"] and result["sent"] == [f"legion:{b.paths.home}"]
    assert [o["monthly_usd"] for o in result["others"]] == [1.0]


def test_peer_files_are_validated_and_paths_quoted(two) -> None:
    assert not devices.valid_snapshot({"days": {}, "device": {"id": "../../x"}, "updated_at": "z"})
    assert devices.valid_snapshot({"days": {}, "device": {"id": "abc123def0"}, "updated_at": "2026-11-13T00:00:00Z"})
    assert devices.parse_peer("legion") == ("legion", '"$HOME/.assetforge"')
    assert devices.parse_peer("legion:/a b/lib;rm") == ("legion", "'/a b/lib;rm'")
    with pytest.raises(devices.StorageError):
        devices.parse_peer("-oProxyCommand=x")


def test_tick_syncs_spend_hourly(two) -> None:
    a, _, _ = two
    settings_store.set_value(a, "update.auto", False)
    assert updater.tick(a)["spend"]["status"] == "ok"
    assert updater.tick(a)["spend"]["status"] == "not_due"
    assert updater.tick(a, force_spend=True)["spend"]["status"] == "ok"


def test_tick_refreshes_own_numbers_between_swaps(two) -> None:
    """A device with no peers is pulled on the other's schedule, so its own.json must be current every tick."""
    a, b, _ = two
    settings_store.set_value(b, "update.auto", False)
    assert updater.tick(b)["spend"]["status"] == "ok"
    add_job(b, 3.0)  # spent after b's hourly sync
    assert updater.tick(b)["spend"]["status"] == "not_due"
    devices.sync(a)
    assert [o["daily_usd"] for o in devices.others_summary(a.paths.home)] == [3.0]


def test_peer_is_not_sent_its_own_numbers(two) -> None:
    a, b, _ = two
    devices.sync(b)
    devices.sync(a)
    devices.sync(a)
    ids = sorted(p.stem for p in devices.peers_dir(b.paths.home).glob("*.json"))
    assert ids == [devices.device(a.paths.home)["id"]]


def test_old_settings_do_not_break_config_list(lib: Library) -> None:
    lib.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('sync.relay', 'legion', 'x')")
    lib.conn.commit()
    assert "sync.relay" not in settings_store.all_settings(lib)


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
