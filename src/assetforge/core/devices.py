"""Per-device spend, shared between devices that use the same provider keys.

Each device keeps its own catalog, so its local `generation_jobs` are exactly its own spend. To see
what the other devices spent, every device publishes a small JSON file (daily spend by provider for
the last ~2 months) to the `spend-ledger` branch of a git remote (by default this checkout's
`origin`) and reads the others' files from its local clone of that branch:

    $ASSETFORGE_HOME/device.json                  this device's stable id and name
    $ASSETFORGE_HOME/sync/ledger/                 clone of the ledger branch
    $ASSETFORGE_HOME/sync/ledger/devices/<id>.json one file per device; each device only writes its own

The ledger branch holds no code (an orphan branch), so it never mixes with `main`. Budgets read the
others' last synced numbers from the local clone, so a check never touches the network.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.core.util import utcnow
from assetforge.errors import StorageError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

LEDGER_BRANCH = "spend-ledger"
HISTORY_DAYS = 62
HEARTBEAT_HOURS = 24  # republish unchanged numbers this often so others can see the device is alive
SCHEMA = 1


# ---------------------------------------------------------------- identity


def device(home: Path) -> dict[str, str]:
    """This device's id (created once, kept in the library) and display name."""
    path = home / "device.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
    if not data.get("id"):
        data = {"id": uuid.uuid4().hex[:10], "created_at": utcnow()}
        home.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    name = os.environ.get("ASSETFORGE_DEVICE_NAME") or data.get("name") or socket.gethostname().split(".")[0]
    return {"id": str(data["id"]), "name": str(name)}


# ---------------------------------------------------------------- numbers


def local_days(lib: Library, days: int = HISTORY_DAYS) -> dict[str, dict[str, float]]:
    """{"YYYY-MM-DD": {provider: usd}} for this device's paid calls (running calls count their estimate)."""
    from assetforge.core.budget import PAID_KINDS

    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    marks = ",".join("?" * len(PAID_KINDS))
    out: dict[str, dict[str, float]] = {}
    for row in lib.conn.execute(
        f"SELECT substr(created_at, 1, 10) AS day, ifnull(provider, 'unknown') AS provider, "
        f"sum(ifnull(cost_usd, CASE WHEN status = 'running' THEN estimated_cost_usd END)) AS total "
        f"FROM generation_jobs WHERE kind IN ({marks}) AND created_at >= ? GROUP BY day, provider",
        [*PAID_KINDS, cutoff],
    ):
        if row["total"]:
            out.setdefault(row["day"], {})[row["provider"]] = round(float(row["total"]), 4)
    return dict(sorted(out.items()))


def _sum_since(days: dict[str, dict[str, float]], since: str) -> float:
    start = since[:10]
    return round(sum(v for day, providers in days.items() if day >= start for v in providers.values()), 4)


def snapshot(lib: Library) -> dict[str, Any]:
    from assetforge.core.budget import _period_starts

    days = local_days(lib)
    starts = _period_starts()
    return {"schema": SCHEMA, "device": device(lib.paths.home), "updated_at": utcnow(), "days": days,
            "totals": {"daily_usd": _sum_since(days, starts["daily"]),
                       "monthly_usd": _sum_since(days, starts["monthly"])}}  # fmt: skip


def ledger_dir(home: Path) -> Path:
    return home / "sync" / "ledger"


def others(home: Path) -> list[dict[str, Any]]:
    """The other devices' last synced files (from the local ledger clone; no network)."""
    own = device(home)["id"]
    folder = ledger_dir(home) / "devices"
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        dev = data.get("device") or {}
        if not isinstance(data.get("days"), dict) or not dev.get("id") or dev["id"] == own:
            continue
        out.append(data)
    return out


def others_spent(home: Path, since: str) -> float:
    return round(sum(_sum_since(d["days"], since) for d in others(home)), 4)


def others_summary(home: Path) -> list[dict[str, Any]]:
    from assetforge.core.budget import _period_starts

    starts = _period_starts()
    now = datetime.now(UTC)
    rows = []
    for d in others(home):
        try:
            age = (now - datetime.fromisoformat(d["updated_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        except (KeyError, ValueError, AttributeError):
            age = None
        monthly_by_provider: dict[str, float] = {}
        for day, providers in d["days"].items():
            if day >= starts["monthly"][:10]:
                for p, v in providers.items():
                    monthly_by_provider[p] = round(monthly_by_provider.get(p, 0.0) + v, 4)
        rows.append({"id": d["device"]["id"], "name": d["device"].get("name", d["device"]["id"]),
                     "updated_at": d.get("updated_at"), "age_hours": None if age is None else round(age, 2),
                     "daily_usd": _sum_since(d["days"], starts["daily"]),
                     "monthly_usd": _sum_since(d["days"], starts["monthly"]),
                     "by_provider_monthly": monthly_by_provider})  # fmt: skip
    return rows


# ---------------------------------------------------------------- git transport


def git(cwd: Path, *args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
    env["GIT_SSH_COMMAND"] = env.get("GIT_SSH_COMMAND") or "ssh -o BatchMode=yes -o ConnectTimeout=20"
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    if check and proc.returncode != 0:
        raise StorageError(f"git {args[0]} failed", hint=(proc.stderr or proc.stdout).strip()[-800:])
    return proc


def default_remote() -> str | None:
    """The `origin` URL of the AssetForge checkout this code runs from."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            proc = git(parent, "remote", "get-url", "origin", check=False)
            return proc.stdout.strip() or None
    return None


def remote_url(lib: Library) -> str | None:
    from assetforge.core import settings_store

    return settings_store.get(lib, "sync.remote") or default_remote()


def _ensure_clone(folder: Path, remote: str) -> None:
    if not (folder / ".git").is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        git(folder, "init", "-q", "-b", LEDGER_BRANCH)
        git(folder, "remote", "add", "origin", remote)
    elif git(folder, "remote", "get-url", "origin", check=False).stdout.strip() != remote:
        git(folder, "remote", "set-url", "origin", remote)


def _fetch(folder: Path) -> bool:
    """Fetch the ledger branch and move the local copy onto it. False when the branch doesn't exist yet."""
    proc = git(folder, "fetch", "-q", "origin", f"+refs/heads/{LEDGER_BRANCH}:refs/remotes/origin/{LEDGER_BRANCH}",
               check=False)  # fmt: skip
    if proc.returncode != 0:
        if "couldn't find remote ref" in proc.stderr.lower():
            return False
        raise StorageError("Couldn't fetch the spend ledger", hint=proc.stderr.strip()[-800:])
    git(folder, "reset", "-q", "--hard", f"origin/{LEDGER_BRANCH}")
    return True


def _same_numbers(path: Path, snap: dict[str, Any]) -> bool:
    try:
        old = json.loads(path.read_text())
        age = datetime.now(UTC) - datetime.fromisoformat(old["updated_at"].replace("Z", "+00:00"))
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return False
    return old.get("days") == snap["days"] and old.get("device") == snap["device"] and age < timedelta(hours=HEARTBEAT_HOURS)


def sync(lib: Library, *, remote: str | None = None, force: bool = False, attempts: int = 4) -> dict[str, Any]:
    """Pull the other devices' spend and publish this device's. Only this device's file is ever written."""
    remote = remote or remote_url(lib)
    if not remote:
        return {"status": "skipped", "reason": "no git remote (set `assetforge config set sync.remote <url>`)"}
    folder = ledger_dir(lib.paths.home)
    _ensure_clone(folder, remote)
    me = device(lib.paths.home)
    pushed = False
    for attempt in range(attempts):
        _fetch(folder)
        snap = snapshot(lib)
        path = folder / "devices" / f"{me['id']}.json"
        if not force and _same_numbers(path, snap):
            break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
        git(folder, "add", str(path.relative_to(folder)))
        git(folder, "-c", f"user.name=AssetForge {me['name']}", "-c", "user.email=assetforge-ledger@localhost",
            "commit", "-q", "-m", f"spend: {me['name']} ${snap['totals']['monthly_usd']:.2f} this month")  # fmt: skip
        proc = git(folder, "push", "-q", "origin", f"HEAD:refs/heads/{LEDGER_BRANCH}", check=False)
        if proc.returncode == 0:
            pushed = True
            break
        if attempt == attempts - 1:  # someone else pushed in between: refetch and rewrite our file
            raise StorageError("Couldn't push this device's spend", hint=proc.stderr.strip()[-800:])
    return {"status": "ok", "device": me, "pushed": pushed, "remote": remote, "branch": LEDGER_BRANCH,
            "others": others_summary(lib.paths.home)}  # fmt: skip
