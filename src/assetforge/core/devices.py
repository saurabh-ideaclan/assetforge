"""Per-device spend, shared directly between devices over SSH (never through GitHub).

Each device keeps its own catalog, so its local `generation_jobs` are exactly its own spend. Every sync
writes this device's numbers (daily spend by provider for the last ~2 months) to `sync/own.json`. A
device that can reach the others over SSH lists them in `sync.peers` (`host` or `host:/library/path`,
comma-separated) and, on each sync, copies each peer's `own.json` into its `sync/peers/` and copies its
own numbers (and every other peer's) into each peer's `sync/peers/`. A device that nobody can reach
needs no setting: its peer brings the files both ways.

    $ASSETFORGE_HOME/device.json             this device's stable id and name
    $ASSETFORGE_HOME/sync/own.json           this device's numbers
    $ASSETFORGE_HOME/sync/peers/<id>.json    the other devices' numbers, as last received

Budgets read `sync/peers/` locally, so a budget check never touches the network.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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

HISTORY_DAYS = 62
SCHEMA = 1
SSH_TIMEOUT = 30


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


def peers_dir(home: Path) -> Path:
    return home / "sync" / "peers"


def own_path(home: Path) -> Path:
    return home / "sync" / "own.json"


def others(home: Path) -> list[dict[str, Any]]:
    """The other devices' last received numbers (local files; no network)."""
    own = device(home)["id"]
    folder = peers_dir(home)
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


# ---------------------------------------------------------------- SSH transport


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def valid_snapshot(data: Any) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("days"), dict) and isinstance(data.get("device"), dict)
            and bool(re.fullmatch(r"[0-9a-f]{6,32}", str(data["device"].get("id", ""))))
            and isinstance(data.get("updated_at"), str))  # fmt: skip


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def parse_peer(spec: str) -> tuple[str, str]:
    """`host` or `host:/library` → (host, shell expression of the remote library folder)."""
    host, _, lib = spec.strip().partition(":")
    if not host or host.startswith("-"):
        raise StorageError(f"Bad peer {spec!r}", hint="Use host or host:/path/to/library")
    if not lib or lib in ("~", "~/.assetforge"):
        return host, '"$HOME/.assetforge"'
    if lib.startswith("~/"):
        return host, '"$HOME"/' + shlex.quote(lib[2:])
    return host, shlex.quote(lib)


def ssh(host: str, command: str, *, stdin: str | None = None) -> str:
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command], input=stdin,
                          capture_output=True, text=True, timeout=SSH_TIMEOUT)  # fmt: skip
    if proc.returncode != 0:
        raise StorageError(f"SSH to {host} failed", hint=proc.stderr.strip()[-300:])
    return proc.stdout


def pull_peer(spec: str) -> dict[str, Any] | None:
    """The peer's own numbers (None when it has never synced)."""
    host, lib = parse_peer(spec)
    out = ssh(host, f"cat {lib}/sync/own.json 2>/dev/null || true")
    if not out.strip():
        return None
    data = json.loads(out)
    if not valid_snapshot(data):
        raise StorageError(f"{host}'s spend file is not a spend snapshot")
    return data


def push_peer(spec: str, files: list[dict[str, Any]]) -> None:
    """Put these devices' numbers into the peer's `sync/peers/` (one SSH call, atomic per file)."""
    host, lib = parse_peer(spec)
    lines = [f"mkdir -p {lib}/sync/peers"]
    for data in files:
        dev = data["device"]["id"]  # validated hex, safe in a path
        body = _dump(data)
        marker = f"AF_{uuid.uuid4().hex}"
        lines.append(f"cat > {lib}/sync/peers/{dev}.json.tmp <<'{marker}'\n{body}{marker}")
        lines.append(f"mv -f {lib}/sync/peers/{dev}.json.tmp {lib}/sync/peers/{dev}.json")
    ssh(host, "sh -s", stdin="set -e\n" + "\n".join(lines) + "\n")


def sync(lib: Library) -> dict[str, Any]:
    """Write this device's numbers; with `sync.peers`, swap numbers with each peer over SSH."""
    from assetforge.core import settings_store

    home = lib.paths.home
    me = device(home)
    snap = snapshot(lib)
    _write_atomic(own_path(home), _dump(snap))
    specs = [x.strip() for x in str(settings_store.get(lib, "sync.peers") or "").split(",") if x.strip()]
    errors: dict[str, str] = {}
    received = []
    for spec in specs:  # first collect every peer's numbers, so each peer then gets everyone's
        try:
            data = pull_peer(spec)
        except (StorageError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            errors[spec] = f"{exc}: {getattr(exc, 'hint', '') or ''}".strip(": ")
            continue
        if data and data["device"]["id"] != me["id"]:
            _write_atomic(peers_dir(home) / f"{data['device']['id']}.json", _dump(data))
            received.append(data["device"].get("name", data["device"]["id"]))
    sent = []
    for spec in specs:
        if spec in errors:
            continue
        try:
            push_peer(spec, [snap, *others(home)])
            sent.append(spec)
        except (StorageError, OSError, subprocess.SubprocessError) as exc:
            errors[spec] = f"{exc}: {getattr(exc, 'hint', '') or ''}".strip(": ")
    out: dict[str, Any] = {"status": "ok" if not errors else "partial", "device": me, "peers": specs,
                           "received": received, "sent": sent, "others": others_summary(home)}  # fmt: skip
    if errors:
        out["errors"] = errors
    return out
