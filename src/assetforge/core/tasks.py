"""Durable tasks: one record for every long piece of work, wherever it was started (the app, the CLI, an agent's
script), so the app's Activity centre can show it, cancel it and say when it ended.

    with tasks.track(lib, "story.write", title="Write chapters", story="fog-lantern") as t:
        t.step("World")
        ...
        t.check_cancel()          # cooperative: raises TaskCancelled when someone pressed Cancel

A task is `queued | running | waiting | succeeded | failed | cancelled | interrupted`. While it runs, a daemon
thread writes a heartbeat every few seconds (its own SQLite connection, so any thread may call `step`/`log`). A task
whose heartbeat is older than `STALE_SECONDS`, or whose process is gone (same host, dead pid), is *interrupted*:
it is reported so by every read and written so by `recover()`; it is never shown as running.

`waiting` means the work stopped for a decision (a workflow gate, a paid step held back): it has no process and
stays in "Waiting for you" until it is dismissed or a newer task of the same kind on the same story starts.

Concurrency rule (stories): every task declares `locks`, the story areas it reads or writes (`text`, `cast`, `art`,
`audio`, `checks`, `releases`, `build`, ...). Two tasks on one story conflict when their lock sets intersect; the
later one waits `queued` until the earlier ends (`wait_turn`). Tasks without locks never wait.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from assetforge.core import db
from assetforge.errors import AssetForgeError, ConflictError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

STATES = ("queued", "running", "waiting", "succeeded", "failed", "cancelled", "interrupted")
LIVE = ("queued", "running")  # have a process behind them (heartbeat checked)
ACTIVE = ("queued", "running", "waiting")
HEARTBEAT_SECONDS = 4.0
STALE_SECONDS = 30.0
LOG_LINES = 200
STEPS_MAX = 120
RESULT_MAX_BYTES = 2_000_000
KEEP_ENDED = 500
HOST = socket.gethostname()
CURRENT: ContextVar[Task | None] = ContextVar("assetforge_task", default=None)  # the task this command runs as


class TaskCancelled(AssetForgeError):
    code = "CANCELLED"


def _now() -> float:
    return time.time()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def default_started_by() -> str:
    return os.environ.get("ASSETFORGE_STARTED_BY") or "script"


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str, ensure_ascii=False)


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def _home_of(target: Library | str | Path) -> Path:
    return Path(target.paths.home) if hasattr(target, "paths") else Path(target)


def _catalog(home: Path) -> Path:
    return home / "catalog.sqlite3"


# ---------------------------------------------------------------------- reading


def effective_state(row: sqlite3.Row | dict[str, Any], now: float | None = None) -> str:
    """The state to show: a live task whose process is gone or silent is interrupted, whatever the row says."""
    state = row["state"]
    if state not in LIVE:
        return state
    now = now or _now()
    beat = row["heartbeat_at"] or row["updated_at"] or 0
    if now - beat > STALE_SECONDS:
        return "interrupted"
    if row["host"] == HOST and not _pid_alive(row["pid"]):
        return "interrupted"
    return state


def _public(row: sqlite3.Row, *, full: bool = True) -> dict[str, Any]:
    now = _now()
    state = effective_state(row, now)
    data: dict[str, Any] = {
        "id": row["id"], "kind": row["kind"], "title": row["title"], "story": row["story"], "project": row["project"],
        "state": state, "steps": _loads(row["steps"], []), "progress": row["progress"], "message": row["message"],
        "started_by": row["started_by"], "pid": row["pid"], "host": row["host"], "command": row["command"],
        "locks": _loads(row["locks"], []), "cancellable": bool(row["cancellable"]) and state in LIVE,
        "can_cancel": state == "queued" or (bool(row["cancellable"]) and state == "running"),
        "retryable": bool(row["retryable"]) and row["request"] is not None and state in ("failed", "cancelled", "interrupted", "waiting"),
        "cancel_requested": bool(row["cancel_requested"]), "error": _loads(row["error"]),
        "result_link": row["result_link"], "created_at": row["created_at"], "started_at": row["started_at"],
        "ended_at": row["ended_at"], "heartbeat_at": row["heartbeat_at"], "updated_at": row["updated_at"],
        "elapsed": round(((row["ended_at"] or now) - (row["started_at"] or row["created_at"])), 1),
    }  # fmt: skip
    if state == "interrupted" and row["state"] != "interrupted" and not data["error"]:
        data["error"] = _interrupted_error(row)
    log = _loads(row["log"], [])
    data["log_tail"] = log[-8:]
    if full:
        data["log"] = log
        result = _loads(row["result"])
        if isinstance(result, dict) and "_calls" in result:  # model calls a story task made (see Task.succeed)
            data["calls"] = result.pop("_calls")
        data["result"] = result
        data["request"] = _loads(row["request"])
    return data


def _interrupted_error(row: Any) -> dict[str, Any]:
    where = f"pid {row['pid']} on {row['host']}" if row["pid"] else "its process"
    return {"code": "INTERRUPTED", "message": f"The process running this task stopped ({where}); it is not running any more",
            "retryable": bool(row["retryable"])}  # fmt: skip


def get(lib: Library, task_id: str, *, full: bool = True) -> dict[str, Any]:
    row = lib.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No task {task_id!r}")
    return _public(row, full=full)


def list_tasks(lib: Library, *, story: str | None = None, states: Iterable[str] | None = None, kind: str | None = None,
               limit: int = 60, full: bool = False) -> list[dict[str, Any]]:  # fmt: skip
    """Newest first; `states` filters on the effective state (so `running` never returns a dead task)."""
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    if story:
        sql += " AND story = ?"
        args.append(story)
    if kind:
        sql += " AND (kind = ? OR kind LIKE ?)"
        args += [kind, f"{kind}.%"]
    sql += " ORDER BY created_at DESC LIMIT ?"
    wanted = set(states or ())
    out = []
    for row in lib.conn.execute(sql, (*args, max(limit, 1) * (4 if wanted else 1))):
        item = _public(row, full=full)
        if wanted and item["state"] not in wanted:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def active_for_story(lib: Library, story: str) -> list[dict[str, Any]]:
    return [t for t in list_tasks(lib, story=story, states=LIVE, limit=20)]


def revision(lib: Library) -> str:
    """Changes whenever a task is created, steps, logs or ends, or a live one goes silent (interrupted)."""
    count, total = lib.conn.execute("SELECT count(*), total(rev) FROM tasks").fetchone()
    now = _now()
    silent = [r["id"] for r in lib.conn.execute(
        "SELECT id, state, heartbeat_at, updated_at, host, pid FROM tasks WHERE state IN ('queued', 'running')")
        if effective_state(r, now) == "interrupted"]  # fmt: skip
    return f"{count}:{int(total)}:{','.join(silent)}"


def counts(lib: Library) -> dict[str, int]:
    out = dict.fromkeys(("running", "queued", "waiting", "failed", "interrupted"), 0)
    for t in list_tasks(lib, states=("running", "queued", "waiting"), limit=200):
        out[t["state"]] += 1
    return out


# ---------------------------------------------------------------------- writing (outside a running task)


def recover(lib: Library) -> list[str]:
    """Write `interrupted` on live tasks whose process is gone or silent (server start and the change feed call
    this). Returns their ids."""
    now = _now()
    found = []
    for row in lib.conn.execute("SELECT * FROM tasks WHERE state IN ('queued', 'running')").fetchall():
        if effective_state(row, now) != "interrupted":
            continue
        with db.transaction(lib.conn):
            changed = lib.conn.execute(
                "UPDATE tasks SET state = 'interrupted', ended_at = ?, error = coalesce(error, ?), updated_at = ?, "
                "rev = rev + 1 WHERE id = ? AND state IN ('queued', 'running')",
                (now, _dumps(_interrupted_error(row)), now, row["id"]),
            ).rowcount  # fmt: skip
        if changed:
            found.append(row["id"])
            _release_jobs(lib, row, now)
    return found


def _release_jobs(lib: Library, row: sqlite3.Row, now: float) -> int:
    """A dead task's text jobs (same project, started after it, still `running`) died with it: mark them failed so no
    view shows them as being written. Skipped while another live task works on the same story."""
    if not row["project"]:
        return 0
    if row["story"] and any(effective_state(r, now) in LIVE for r in lib.conn.execute(
            "SELECT * FROM tasks WHERE story = ? AND id != ? AND state IN ('queued', 'running')", (row["story"], row["id"]))):  # fmt: skip
        return 0
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((row["started_at"] or row["created_at"]) - 1))
    error = _dumps({"code": "INTERRUPTED", "message": "The process making this call stopped (its task was interrupted)"})
    with db.transaction(lib.conn):
        return lib.conn.execute(
            "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE kind = 'text' AND status = 'running' "
            "AND json_extract(request, '$.project') = ? AND created_at >= ?",
            (error, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), row["project"], since),
        ).rowcount  # fmt: skip


def request_cancel(lib: Library, task_id: str) -> dict[str, Any]:
    """Ask a task to stop. Queued tasks always can; running ones only when their code checks the flag."""
    task = get(lib, task_id, full=False)
    if task["state"] not in LIVE:
        raise ConflictError(f"The task already ended ({task['state']})")
    if task["state"] == "running" and not task["cancellable"]:
        raise ConflictError("This task can't be stopped part-way; it will finish its current work",
                            hint="Its steps are kept: anything it already made stays")  # fmt: skip
    now = _now()
    with db.transaction(lib.conn):
        lib.conn.execute("UPDATE tasks SET cancel_requested = 1, message = ?, updated_at = ?, rev = rev + 1 WHERE id = ?",
                         ("Cancelling…", now, task_id))  # fmt: skip
    return get(lib, task_id, full=False)


def dismiss(lib: Library, task_id: str) -> dict[str, Any]:
    """Take a `waiting` task off "Waiting for you" (the decision was made elsewhere)."""
    task = get(lib, task_id, full=False)
    if task["state"] != "waiting":
        raise ConflictError(f"Only waiting tasks can be dismissed (this one is {task['state']})")
    now = _now()
    with db.transaction(lib.conn):
        lib.conn.execute("UPDATE tasks SET state = 'succeeded', message = 'Dismissed', updated_at = ?, rev = rev + 1 "
                         "WHERE id = ?", (now, task_id))  # fmt: skip
    return get(lib, task_id, full=False)


def prune(lib: Library, keep: int = KEEP_ENDED) -> int:
    with db.transaction(lib.conn):
        return lib.conn.execute(
            "DELETE FROM tasks WHERE state NOT IN ('queued', 'running', 'waiting') AND id NOT IN "
            "(SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?)", (keep,)).rowcount  # fmt: skip


def conflicting(lib: Library, task: dict[str, Any] | sqlite3.Row) -> list[dict[str, Any]]:
    """Live tasks on the same story created before this one whose locks intersect its locks."""
    locks = set(_loads(task["locks"], []) if isinstance(task["locks"], str) else task["locks"] or [])
    if not task["story"] or not locks:
        return []
    out = []
    for row in lib.conn.execute("SELECT * FROM tasks WHERE story = ? AND id != ? AND state IN ('queued', 'running') "
                                "AND created_at <= ?", (task["story"], task["id"], task["created_at"])):  # fmt: skip
        if effective_state(row) not in LIVE:
            continue
        if locks & set(_loads(row["locks"], [])):
            out.append(_public(row, full=False))
    return out


# ---------------------------------------------------------------------- a running task


class Task:
    """Handle on one task. Thread-safe: it owns a connection and a lock; the heartbeat runs in a daemon thread."""

    def __init__(self, home: Path, task_id: str) -> None:
        self.home = home
        self.id = task_id
        self._conn = db.connect(_catalog(home), check_same_thread=False)
        self._lock = threading.Lock()
        self._cancel = False
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None
        self.ended = False
        row = self._conn.execute("SELECT steps, log, story, kind FROM tasks WHERE id = ?", (task_id,)).fetchone()
        self._steps: list[dict[str, Any]] = _loads(row["steps"], [])
        self._log: list[str] = _loads(row["log"], [])
        self.story, self.kind = row["story"], row["kind"]
        self._declared = any(s.get("state") == "pending" for s in self._steps)

    # -------------------------------------------------------------- plumbing

    def _write(self, sql_set: str, args: tuple[Any, ...] = (), *, bump: bool = True) -> None:
        now = _now()
        with self._lock:
            for attempt in range(5):
                try:
                    with db.transaction(self._conn):
                        self._conn.execute(f"UPDATE tasks SET {sql_set}, updated_at = ?{', rev = rev + 1' if bump else ''} "
                                           "WHERE id = ?", (*args, now, self.id))  # fmt: skip
                    return
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))

    def start_heartbeat(self) -> None:
        if self._beat is not None:
            return

        def loop() -> None:
            while not self._stop.wait(HEARTBEAT_SECONDS):
                try:
                    with self._lock:
                        self._conn.execute("UPDATE tasks SET heartbeat_at = ? WHERE id = ?", (_now(), self.id))
                        row = self._conn.execute("SELECT cancel_requested FROM tasks WHERE id = ?", (self.id,)).fetchone()
                    if row and row[0]:
                        self._cancel = True
                except sqlite3.Error:
                    continue

        self._conn.execute("UPDATE tasks SET heartbeat_at = ? WHERE id = ?", (_now(), self.id))
        self._beat = threading.Thread(target=loop, name=f"task-heartbeat-{self.id}", daemon=True)
        self._beat.start()

    def close(self) -> None:
        self._stop.set()
        if self._beat is not None and self._beat is not threading.current_thread():
            self._beat.join(timeout=2)
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # -------------------------------------------------------------- cancel

    @property
    def cancelled(self) -> bool:
        if not self._cancel:
            with self._lock:
                row = self._conn.execute("SELECT cancel_requested FROM tasks WHERE id = ?", (self.id,)).fetchone()
            self._cancel = bool(row and row[0])
        return self._cancel

    def check_cancel(self) -> None:
        """Raise TaskCancelled when someone asked this task to stop (call it between units of work)."""
        if self._cancel or self.cancelled:
            raise TaskCancelled("Cancelled before it finished; work already done is kept")

    def set_cancellable(self, value: bool = True) -> None:
        self._write("cancellable = ?", (int(value),))

    # -------------------------------------------------------------- progress

    def set_steps(self, labels: Iterable[str]) -> None:
        self._steps = [{"label": str(x), "state": "pending"} for x in labels][:STEPS_MAX]
        self._declared = bool(self._steps)
        self._write("steps = ?", (_dumps(self._steps),))

    def step(self, label: str | int, *, progress: float | None = None, message: str | None = None) -> None:
        """Start a step (by label, or the index of a declared step); the previous running step is marked done."""
        now = _now()
        for s in self._steps:
            if s.get("state") == "running":
                s.update(state="done", ended=now)
        if isinstance(label, int):
            for i, s in enumerate(self._steps):
                if i < label and s["state"] != "done":
                    s.update(state="done", ended=now)
            if 0 <= label < len(self._steps):
                self._steps[label].update(state="running", at=now)
            text = self._steps[label]["label"] if 0 <= label < len(self._steps) else str(label)
        else:
            text = label
            existing = next((s for s in self._steps if s["label"] == label and s["state"] == "pending"), None)
            if existing:
                existing.update(state="running", at=now)
            else:
                self._steps.append({"label": label, "state": "running", "at": now})
                if len(self._steps) > STEPS_MAX:
                    self._steps = self._steps[:10] + [{"label": "…", "state": "done"}] + self._steps[-(STEPS_MAX - 11):]
        if progress is None and self._declared:
            progress = sum(1 for s in self._steps if s["state"] == "done") / max(len(self._steps), 1)
        self._write("steps = ?, progress = coalesce(?, progress), message = ?", (_dumps(self._steps), progress, message or text))
        self.log(f"→ {text}")

    def done_step(self, label: str) -> None:
        """Record a finished unit of work (e.g. one model call) without leaving a step running."""
        now = _now()
        for s in self._steps:
            if s.get("state") == "running":
                s.update(state="done", ended=now)
        self._steps.append({"label": label, "state": "done", "at": now, "ended": now})
        if len(self._steps) > STEPS_MAX:
            self._steps = self._steps[:10] + [{"label": "…", "state": "done"}] + self._steps[-(STEPS_MAX - 11):]
        self._write("steps = ?, message = ?", (_dumps(self._steps), label))

    def progress(self, value: float | None, message: str | None = None) -> None:
        self._write("progress = ?, message = coalesce(?, message)", (value, message))

    def log(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log.append(f"{stamp} {str(line)[:500]}")
        self._log = self._log[-LOG_LINES:]
        self._write("log = ?", (_dumps(self._log),))

    # -------------------------------------------------------------- ending

    def _end(self, state: str, *, result: Any = None, error: dict[str, Any] | None = None, message: str | None = None,
             link: str | None = None) -> None:  # fmt: skip
        if self.ended:
            return
        now = _now()
        final = "failed" if state == "failed" else "done"
        for s in self._steps:
            if s.get("state") == "running":
                s.update(state=final, ended=now)
        payload = _dumps(result)
        if payload is not None and len(payload) > RESULT_MAX_BYTES:
            payload = _dumps({"truncated": True, "bytes": len(payload)})
        self._write("state = ?, ended_at = ?, result = coalesce(?, result), error = ?, message = coalesce(?, message), "
                    "result_link = coalesce(?, result_link), steps = ?, progress = CASE WHEN ? = 'succeeded' THEN 1 ELSE progress END",
                    (state, now, payload, _dumps(error), message, link, _dumps(self._steps), state))  # fmt: skip
        self.ended = True

    def succeed(self, result: Any = None, *, message: str | None = None, link: str | None = None,
                calls: list[Any] | None = None) -> None:  # fmt: skip
        """`calls` (a story context's model calls) ride along with a dict result and come back as `calls`."""
        if calls is not None and isinstance(result, dict):
            result = {**result, "_calls": list(calls)}
        self._end("succeeded", result=result, message=message or "Done", link=link)

    def wait_for_you(self, message: str, *, result: Any = None, link: str | None = None,
                     error: dict[str, Any] | None = None, calls: list[Any] | None = None) -> None:  # fmt: skip
        """End the work but keep it in "Waiting for you" until a decision is made."""
        if calls is not None and isinstance(result, dict):
            result = {**result, "_calls": list(calls)}
        self._end("waiting", result=result, message=message, link=link, error=error)

    def fail(self, error: dict[str, Any], *, result: Any = None) -> None:
        self._end("failed", result=result, error=error, message=error.get("message"))

    def cancel_done(self) -> None:
        self._end("cancelled", error={"code": "CANCELLED", "message": "Cancelled; work already done is kept"},
                  message="Cancelled")  # fmt: skip

    def interrupt(self, message: str = "Stopped (interrupted)") -> None:
        self._end("interrupted", error={"code": "INTERRUPTED", "message": message})

    def wait_turn(self, poll: float = 0.5) -> None:
        """While queued: wait until no earlier conflicting task is live, then switch to running."""
        while True:
            with self._lock:
                row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (self.id,)).fetchone()
            if row["state"] != "queued":
                return
            blockers = conflicting(_ConnLib(self._conn, self._lock), row)
            if not blockers:
                now = _now()
                self._write("state = 'running', started_at = ?, message = ?", (now, "Started"))
                return
            self.check_cancel()
            self._write("message = ?", (f"Queued behind “{blockers[0]['title']}”",), bump=False)
            time.sleep(poll)


class _ConnLib:
    """Just enough of a Library for `conflicting` (a connection used under its owner's lock)."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self.conn = _LockedConn(conn, lock)


class _LockedConn:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn, self._lock = conn, lock

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()


def create(target: Library | str | Path, kind: str, *, title: str = "", story: str | None = None,
           project: str | None = None, started_by: str | None = None, request: Any = None, locks: Iterable[str] = (),
           cancellable: bool = False, retryable: bool = False, steps: Iterable[str] = (), result_link: str | None = None,
           command: str | None = None, queued: bool = False, supersede: bool = True) -> Task:  # fmt: skip
    """Insert a task row (running, or queued when `queued`) and return its handle with the heartbeat started."""
    home = _home_of(target)
    task_id = uuid.uuid4().hex[:12]
    now = _now()
    step_list = [{"label": str(x), "state": "pending"} for x in steps]
    conn = db.connect(_catalog(home))
    try:
        with db.transaction(conn):
            if supersede and story:  # a newer run of the same kind settles what an older one was waiting for
                conn.execute("UPDATE tasks SET state = 'succeeded', message = 'Superseded by a newer run', updated_at = ?, "
                             "rev = rev + 1 WHERE story = ? AND kind = ? AND state = 'waiting'", (now, story, kind))  # fmt: skip
            conn.execute(
                "INSERT INTO tasks (id, kind, title, story, project, state, steps, started_by, pid, host, command, request, "
                "locks, cancellable, retryable, result_link, created_at, started_at, heartbeat_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, kind, title or kind, story, project, "queued" if queued else "running", _dumps(step_list),
                 started_by or default_started_by(), os.getpid(), HOST, command, _dumps(request), _dumps(sorted(set(locks))),
                 int(cancellable), int(retryable), (result_link or "").replace("{id}", task_id) or None, now, None if queued else now, now, now),
            )  # fmt: skip
    finally:
        conn.close()
    task = Task(home, task_id)
    task.start_heartbeat()
    return task


@contextmanager
def track(target: Library | str | Path, kind: str, **options: Any) -> Iterator[Task]:
    """Record the enclosed work as a task. Ends it succeeded (unless the body ended it), failed on an error,
    cancelled on TaskCancelled, interrupted on Ctrl-C. Queued tasks wait for their turn first."""
    task = create(target, kind, **options)
    try:
        if options.get("queued"):
            task.wait_turn()
        yield task
    except TaskCancelled:
        task.cancel_done()
        raise
    except AssetForgeError as exc:
        task.fail(exc.to_dict())
        raise
    except KeyboardInterrupt:
        task.interrupt("Stopped with Ctrl-C")
        raise
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        if code:
            task.fail({"code": "EXIT", "message": f"Exited with code {code}"})
        raise
    except Exception as exc:
        task.fail({"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})
        raise
    else:
        task.succeed()
    finally:
        if not task.ended:
            task.succeed()
        task.close()


def command_line() -> str:
    return " ".join(["assetforge", *sys.argv[1:]])
