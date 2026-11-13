"""Keep every device on the latest `main`, and restart the Mac app at a quiet moment.

Devices only ever follow `origin/main` (fast-forward only). A checkout with uncommitted changes or
commits GitHub doesn't have is the one you develop on: it is never touched, and `publish` pushes it.
New code is pulled only when the library is idle (no running paid jobs, request jobs or app tasks) and,
if AssetForge.app is open, nobody has touched the keyboard or mouse for `update.idle_minutes`.
Then dependencies are synced when the lock file changed, the app is rebuilt when its shell changed,
and the app is relaunched in the background so the new server code is in use. On Linux the "app" is any
`assetforge … ui` server run from this checkout: it is restarted with its own command line, environment,
output files and session token, so an open browser tab keeps working; idle time comes from logind.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.core import devices, settings_store
from assetforge.core.devices import git
from assetforge.core.util import utcnow
from assetforge.errors import ConflictError, StorageError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

BRANCH = "main"
BUNDLE_ID = "com.ideaclan.assetforge"
AGENT_LABEL = "com.ideaclan.assetforge.sync"
AGENT_INTERVAL_SECONDS = 600
STALE_JOB_MINUTES = 30
TASK_HEARTBEAT_SECONDS = 60


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return parent
    raise StorageError("This AssetForge isn't running from a git checkout", hint="Clone the repo and run it from there")


# ---------------------------------------------------------------- state


def state_path(home: Path) -> Path:
    return home / "sync" / "state.json"


def load_state(home: Path) -> dict[str, Any]:
    try:
        return json.loads(state_path(home).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(home: Path, **changes: Any) -> dict[str, Any]:
    state = {**load_state(home), **changes}
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)
    return state


# ---------------------------------------------------------------- git status


def status(root: Path | None = None, *, fetch: bool = True) -> dict[str, Any]:
    root = root or repo_root()
    if fetch:
        git(root, "fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    remote = git(root, "rev-parse", f"origin/{BRANCH}").stdout.strip()
    behind = int(git(root, "rev-list", "--count", f"HEAD..origin/{BRANCH}").stdout)
    ahead = int(git(root, "rev-list", "--count", f"origin/{BRANCH}..HEAD").stdout)
    dirty = [line[3:] for line in git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()]
    return {"root": str(root), "branch": branch, "head": head[:12], "origin_main": remote[:12], "behind": behind,
            "ahead": ahead, "dirty": dirty}  # fmt: skip


def unmerged_branches(root: Path) -> dict[str, int]:
    """Local branches holding commits that `main` doesn't have."""
    out = {}
    for name in git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split():
        if name != BRANCH and (n := int(git(root, "rev-list", "--count", f"{BRANCH}..{name}").stdout)):
            out[name] = n
    return out


# ---------------------------------------------------------------- is it a good moment?


def busy(lib: Library) -> list[str]:
    """Reasons not to swap code under running work right now."""
    reasons = []
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - STALE_JOB_MINUTES * 60))
    row = lib.conn.execute("SELECT count(*) FROM generation_jobs WHERE status = 'running' AND updated_at >= ?",
                           (cutoff,)).fetchone()  # fmt: skip
    if row[0]:
        reasons.append(f"{row[0]} generation job(s) running")
    try:
        row = lib.conn.execute("SELECT count(*) FROM tasks WHERE (state = 'running' AND heartbeat_at >= ?) "
                               "OR (state = 'queued' AND created_at >= ?)",
                               (time.time() - TASK_HEARTBEAT_SECONDS, cutoff)).fetchone()  # fmt: skip
        if row[0]:
            reasons.append(f"{row[0]} app task(s) running")
    except Exception:  # noqa: BLE001  (older library without the tasks table)
        pass
    return reasons


def user_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input (None when unknown)."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                                 timeout=10).stdout  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else None
    return _linux_idle_seconds()


def _linux_idle_seconds() -> float | None:
    """From logind: the desktop session's IdleHint (set by GNOME/KDE after their idle delay)."""
    try:
        sessions = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True,
                                  timeout=10).stdout.split("\n")  # fmt: skip
        user = os.environ.get("USER") or ""
        best: float | None = None
        for line in sessions:
            parts = line.split()
            if len(parts) < 3 or parts[2] != user:
                continue
            props = dict(kv.split("=", 1) for kv in subprocess.run(
                ["loginctl", "show-session", parts[0], "-p", "Type", "-p", "IdleHint", "-p", "IdleSinceHint"],
                capture_output=True, text=True, timeout=10).stdout.split() if "=" in kv)  # fmt: skip
            if props.get("Type") not in ("x11", "wayland", "mir"):
                continue
            idle = parse_idle_hint(props, time.time())
            best = idle if best is None else min(best, idle)
        return best
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def parse_idle_hint(props: dict[str, str], now: float) -> float:
    if props.get("IdleHint") != "yes":
        return 0.0
    since = int(props.get("IdleSinceHint") or 0) / 1e6
    return max(0.0, now - since) if since else 0.0


def ui_processes(root: Path | None = None) -> list[int]:
    """Linux: pids of `assetforge … ui` servers running from this checkout's virtualenv."""
    root = (root or repo_root()).resolve()
    pids = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            argv = [a.decode() for a in argv if a]
        except OSError:
            continue
        if int(proc.name) == os.getpid() or len(argv) < 3 or "ui" not in argv:
            continue
        exe = argv[0]
        if not exe.startswith(str(root)) or not any(a.endswith("assetforge") or a == "assetforge" for a in argv[1:]):
            continue
        pids.append(int(proc.name))
    return pids


def app_running() -> bool:
    if sys.platform != "darwin":
        return bool(ui_processes())
    try:
        proc = subprocess.run(["osascript", "-e", f'application id "{BUNDLE_ID}" is running'],
                              capture_output=True, text=True, timeout=15)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip() == "true"


def restart_app() -> bool:
    """Mac: quit AssetForge.app and reopen it in the background. Linux: restart the UI servers in place."""
    if sys.platform != "darwin":
        return restart_ui_servers()
    subprocess.run(["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'], capture_output=True, timeout=30)
    for _ in range(40):
        if not app_running():
            break
        time.sleep(0.5)
    else:
        return False
    return subprocess.run(["open", "-g", "-b", BUNDLE_ID], capture_output=True, timeout=30).returncode == 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return Path(f"/proc/{pid}").exists()


def relaunch_argv(argv: list[str], stdout_text: str) -> list[str]:
    """Same command, same session token (read from the server's first stdout line), so open pages keep working."""
    if "--token" in argv:
        return argv
    try:
        token = json.loads(stdout_text.strip().splitlines()[0]).get("token")
    except (IndexError, json.JSONDecodeError, AttributeError):
        token = None
    if not token:
        return argv
    i = argv.index("ui") + 1
    return [*argv[:i], "--token", str(token), *argv[i:]]


def restart_ui_servers(root: Path | None = None) -> bool:
    """Stop each UI server and start it again with its own command line, folder, environment and output files."""
    ok = True
    for pid in ui_processes(root):
        base = Path(f"/proc/{pid}")
        try:
            argv = [a.decode() for a in (base / "cmdline").read_bytes().split(b"\0") if a]
            cwd = os.readlink(base / "cwd")
            env = dict(kv.split("=", 1) for kv in (base / "environ").read_bytes().decode(errors="replace").split("\0")
                       if "=" in kv)  # fmt: skip
            out_path = Path(os.readlink(base / "fd" / "1"))
            err_path = Path(os.readlink(base / "fd" / "2"))
        except OSError:
            ok = False
            continue
        out_text = out_path.read_text(errors="replace") if out_path.is_file() else ""
        argv = relaunch_argv(argv, out_text)
        os.kill(pid, 15)
        for _ in range(40):
            if not _alive(pid):
                break
            time.sleep(0.5)
        else:
            os.kill(pid, 9)
            time.sleep(1)
        stdout = open(out_path, "w") if out_path.is_file() else subprocess.DEVNULL  # noqa: SIM115
        stderr = open(err_path, "a") if err_path.is_file() else subprocess.DEVNULL  # noqa: SIM115
        subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                         start_new_session=True)  # fmt: skip
        if out_path.is_file():
            for _ in range(120):
                if out_path.stat().st_size:
                    break
                time.sleep(0.25)
            else:
                ok = False
    return ok


# ---------------------------------------------------------------- update


def _venv_python(root: Path) -> str:
    candidate = root / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _uv() -> str | None:
    for path in (shutil.which("uv"), "~/.local/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if path and Path(path).expanduser().exists():
            return str(Path(path).expanduser())
    return None


def _after_pull(root: Path, changed: list[str]) -> list[str]:
    steps = []
    if any(f in ("uv.lock", "pyproject.toml") for f in changed):
        uv = _uv()
        if not uv:
            raise StorageError("Dependencies changed but `uv` was not found", hint="Install uv, then run `uv sync`")
        proc = subprocess.run([uv, "sync"], cwd=root, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise StorageError("`uv sync` failed after the update", hint=proc.stderr[-800:])
        steps.append("uv sync")
    shell_changed = any(f.startswith("macos/") or f == "src/assetforge/app/installer.py" for f in changed)
    if shell_changed and sys.platform == "darwin" and Path("~/Applications/AssetForge.app").expanduser().exists():
        proc = subprocess.run([_venv_python(root), "-m", "assetforge", "app", "install", "--json"], cwd=root,
                              capture_output=True, text=True, timeout=900)  # fmt: skip
        steps.append("app rebuilt" if proc.returncode == 0 else "app rebuild failed")
    return steps


def apply(lib: Library, *, now: bool = False, root: Path | None = None, restart: bool = True) -> dict[str, Any]:
    """Fast-forward to origin/main when it's safe; returns {"status": up_to_date|updated|pending|blocked, ...}."""
    root = root or repo_root()
    st = status(root)
    if st["branch"] != BRANCH:
        own = int(git(root, "rev-list", "--count", f"origin/{BRANCH}..HEAD").stdout)
        if st["dirty"] or own:
            return {"status": "blocked", "reason": f"on branch {st['branch']} with work that isn't on main", **st}
        git(root, "checkout", "-q", BRANCH)
        st = status(root, fetch=False)
    if st["dirty"]:
        return {"status": "blocked", "reason": "uncommitted changes (this looks like the device you develop on)", **st}
    if st["ahead"]:
        return {"status": "blocked", "reason": f"{st['ahead']} local commit(s) not on GitHub: run `assetforge sync publish`",
                **st}  # fmt: skip
    if not st["behind"]:
        return {"status": "up_to_date", **st}
    was_running = app_running() if restart else False
    if not now:
        reasons = busy(lib)
        idle = user_idle_seconds()
        need = settings_store.get(lib, "update.idle_minutes") * 60
        if was_running and idle is not None and idle < need:
            reasons.append(f"the app is open and this computer was used {int(idle // 60)} min ago")
        if reasons:
            return {"status": "pending", "reasons": reasons, **st}
    old = git(root, "rev-parse", "HEAD").stdout.strip()
    proc = git(root, "merge", "--ff-only", "-q", f"origin/{BRANCH}", check=False)
    if proc.returncode != 0:
        return {"status": "blocked", "reason": "fast-forward failed", "detail": proc.stderr.strip()[-500:], **st}
    changed = git(root, "diff", "--name-only", old, "HEAD").stdout.split()
    steps = _after_pull(root, changed)
    if was_running:
        steps.append("app restarted" if restart_app() else "app restart failed")
    return {"status": "updated", "from": old[:12], "to": st["origin_main"], "commits": st["behind"],
            "files_changed": len(changed), "steps": steps, **{**st, "behind": 0}}  # fmt: skip


def publish(root: Path | None = None, *, allow_unmerged: bool = False) -> dict[str, Any]:
    """Push `main` so every device gets it. Refuses when work would be left behind on another branch."""
    root = root or repo_root()
    st = status(root)
    if st["branch"] != BRANCH:
        raise ConflictError(f"You're on {st['branch']}; devices only follow {BRANCH}",
                            hint=f"Merge it into {BRANCH} (git checkout {BRANCH} && git merge {st['branch']}), then publish")  # fmt: skip
    if st["dirty"]:
        raise ConflictError("Uncommitted changes would not be published", hint="Commit them first", files=st["dirty"][:20])
    if st["behind"]:
        raise ConflictError(f"GitHub's {BRANCH} has {st['behind']} commit(s) this checkout doesn't",
                            hint=f"git pull --rebase origin {BRANCH}, then publish again")  # fmt: skip
    left = unmerged_branches(root)
    if left and not allow_unmerged:
        raise ConflictError("Some branches have commits that are not on main, so devices would miss them",
                            hint="Merge them into main (or pass --allow-unmerged)", branches=left)  # fmt: skip
    if not st["ahead"]:
        return {"status": "up_to_date", **st, "unmerged": left}
    git(root, "push", "-q", "origin", f"{BRANCH}:{BRANCH}", timeout=300)
    return {"status": "pushed", "commits": st["ahead"], **{**st, "ahead": 0}, "unmerged": left}


# ---------------------------------------------------------------- the periodic job


def tick(lib: Library, *, force_spend: bool = False) -> dict[str, Any]:
    """What the launch agent runs every few minutes: hourly spend sync, then the update check."""
    home = lib.paths.home
    state = load_state(home)
    out: dict[str, Any] = {"at": utcnow(), "device": devices.device(home)}
    every = settings_store.get(lib, "sync.interval_minutes") * 60
    last = state.get("spend_synced_epoch", 0)
    if force_spend or time.time() - last >= every:
        try:
            out["spend"] = devices.sync(lib)
            state = save_state(home, spend_synced_epoch=time.time(), spend_synced_at=utcnow(), spend_error=None)
        except Exception as exc:  # noqa: BLE001  (keep going: the update must not depend on the ledger)
            out["spend"] = {"status": "error", "error": str(exc), "hint": getattr(exc, "hint", None)}
            save_state(home, spend_error=str(exc), spend_error_at=utcnow())
    else:
        out["spend"] = {"status": "not_due", "next_in_minutes": round((every - (time.time() - last)) / 60, 1)}
    if settings_store.get(lib, "update.auto"):
        try:
            out["update"] = apply(lib)
        except Exception as exc:  # noqa: BLE001
            out["update"] = {"status": "error", "error": str(exc), "hint": getattr(exc, "hint", None)}
        save_state(home, update_checked_at=utcnow(), update=out["update"])
    else:
        out["update"] = {"status": "disabled"}
    return out


# ---------------------------------------------------------------- launch agent


def agent_plist(home: Path, root: Path) -> dict[str, Any]:
    path_dirs = ["/opt/homebrew/bin", "/usr/local/bin", str(Path("~/.local/bin").expanduser()),
                 "/usr/bin", "/bin", "/usr/sbin", "/sbin"]  # fmt: skip
    log = str(home / "sync" / "agent.log")
    env = {"PATH": ":".join(path_dirs), "ASSETFORGE_HOME": str(home)}
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return {"Label": AGENT_LABEL, "ProgramArguments": [_venv_python(root), "-m", "assetforge", "sync", "tick", "--json"],
            "StartInterval": AGENT_INTERVAL_SECONDS, "RunAtLoad": True, "WorkingDirectory": str(home),
            "EnvironmentVariables": env, "StandardOutPath": log, "StandardErrorPath": log,
            "ProcessType": "Background", "LowPriorityIO": True}  # fmt: skip


SYSTEMD_UNIT = "assetforge-sync"


def systemd_units(home: Path, root: Path) -> dict[str, str]:
    """Linux: a user service (one tick) and a timer that runs it every 10 minutes and after boot."""
    path = ":".join([str(Path("~/.local/bin").expanduser()), "/usr/local/bin", "/usr/bin", "/bin"])
    log = home / "sync" / "agent.log"
    service = f"""[Unit]
Description=AssetForge: share device spend, update from GitHub main

[Service]
Type=oneshot
WorkingDirectory={home}
Environment=ASSETFORGE_HOME={home}
Environment=PATH={path}
ExecStart={_venv_python(root)} -m assetforge sync tick --json
StandardOutput=append:{log}
StandardError=append:{log}
# the restarted UI server must outlive this run
KillMode=process
Nice=10
"""
    timer = f"""[Unit]
Description=Run AssetForge sync every {AGENT_INTERVAL_SECONDS // 60} minutes

[Timer]
OnBootSec=2min
OnUnitInactiveSec={AGENT_INTERVAL_SECONDS}s
Persistent=true

[Install]
WantedBy=timers.target
"""
    return {f"{SYSTEMD_UNIT}.service": service, f"{SYSTEMD_UNIT}.timer": timer}


def agent_path() -> Path:
    if sys.platform == "darwin":
        return Path("~/Library/LaunchAgents").expanduser() / f"{AGENT_LABEL}.plist"
    return Path("~/.config/systemd/user").expanduser() / f"{SYSTEMD_UNIT}.timer"


def install_agent(home: Path, *, load: bool = True) -> dict[str, Any]:
    (home / "sync").mkdir(parents=True, exist_ok=True)
    if sys.platform != "darwin":
        folder = agent_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in systemd_units(home, repo_root()).items():
            (folder / name).write_text(text)
        if load:
            for cmd in (["daemon-reload"], ["enable", "--now", f"{SYSTEMD_UNIT}.timer"], ["start", f"{SYSTEMD_UNIT}.service", "--no-block"]):
                proc = subprocess.run(["systemctl", "--user", *cmd], capture_output=True, text=True)
                if proc.returncode != 0:
                    raise StorageError(f"systemctl --user {' '.join(cmd)} failed", hint=proc.stderr.strip())
        return {"unit": str(agent_path()), "loaded": load, "every_seconds": AGENT_INTERVAL_SECONDS,
                "log": str(home / "sync" / "agent.log"), "python": _venv_python(repo_root())}  # fmt: skip
    import plistlib

    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = agent_plist(home, repo_root())
    path.write_bytes(plistlib.dumps(plist))
    loaded = False
    if load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        proc = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise StorageError("launchctl couldn't load the sync agent", hint=proc.stderr.strip())
        loaded = True
    return {"plist": str(path), "unit": str(path), "loaded": loaded, "every_seconds": AGENT_INTERVAL_SECONDS,
            "log": plist["StandardOutPath"], "python": plist["ProgramArguments"][0]}  # fmt: skip


def uninstall_agent() -> dict[str, Any]:
    path = agent_path()
    existed = path.exists()
    if sys.platform != "darwin":
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT}.timer"], capture_output=True)
        for name in (f"{SYSTEMD_UNIT}.service", f"{SYSTEMD_UNIT}.timer"):
            (path.parent / name).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return {"unit": str(path), "removed": existed}
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink(missing_ok=True)
    return {"plist": str(path), "unit": str(path), "removed": existed}


def trim_log(home: Path, max_bytes: int = 1_000_000) -> None:
    log = home / "sync" / "agent.log"
    try:
        if log.stat().st_size > max_bytes:
            data = log.read_bytes()[-max_bytes // 2 :]
            log.write_bytes(data[data.find(b"\n") + 1 :])
    except OSError:
        pass
