"""Keep every device on the latest `main`, and restart the Mac app at a quiet moment.

Devices only ever follow `origin/main` (fast-forward only). A checkout with uncommitted changes or
commits GitHub doesn't have is the one you develop on: it is never touched, and `publish` pushes it.
New code is pulled only when the library is idle (no running paid jobs, request jobs or app tasks) and,
if AssetForge.app is open, nobody has touched the keyboard or mouse for `update.idle_minutes`.
Then dependencies are synced when the lock file changed, the app is rebuilt when its shell changed,
and the app is relaunched in the background so the new server code is in use.
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
    """Seconds since the last keyboard/mouse input on this Mac (None when unknown)."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def app_running() -> bool:
    try:
        proc = subprocess.run(["osascript", "-e", f'application id "{BUNDLE_ID}" is running'],
                              capture_output=True, text=True, timeout=15)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip() == "true"


def restart_app() -> bool:
    """Quit AssetForge.app gracefully and reopen it in the background (no focus steal)."""
    subprocess.run(["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'], capture_output=True, timeout=30)
    for _ in range(40):
        if not app_running():
            break
        time.sleep(0.5)
    else:
        return False
    return subprocess.run(["open", "-g", "-b", BUNDLE_ID], capture_output=True, timeout=30).returncode == 0


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
    if shell_changed and Path("~/Applications/AssetForge.app").expanduser().exists():
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
            reasons.append(f"the app is open and this Mac was used {int(idle // 60)} min ago")
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


def agent_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{AGENT_LABEL}.plist"


def install_agent(home: Path, *, load: bool = True) -> dict[str, Any]:
    import plistlib

    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    (home / "sync").mkdir(parents=True, exist_ok=True)
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
    return {"plist": str(path), "loaded": loaded, "every_seconds": AGENT_INTERVAL_SECONDS,
            "log": plist["StandardOutPath"], "python": plist["ProgramArguments"][0]}  # fmt: skip


def uninstall_agent() -> dict[str, Any]:
    path = agent_path()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return {"plist": str(path), "removed": existed}


def trim_log(home: Path, max_bytes: int = 1_000_000) -> None:
    log = home / "sync" / "agent.log"
    try:
        if log.stat().st_size > max_bytes:
            data = log.read_bytes()[-max_bytes // 2 :]
            log.write_bytes(data[data.find(b"\n") + 1 :])
    except OSError:
        pass
