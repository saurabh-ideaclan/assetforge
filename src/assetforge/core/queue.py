"""A durable job queue on the catalog's `generation_jobs` table, safe for many agents and processes.

* A running job holds a *lease* (owner + expiry) that a heartbeat thread keeps extending. If the
  process dies, the lease lapses and `recover()` (run by every worker and `jobs recover`) puts the
  job back in the queue, or fails it after `max_attempts`.
* Claiming happens in one `BEGIN IMMEDIATE` transaction, so a queued job is handed to exactly one worker.
* Identical requests share one job: creating a request whose fingerprint matches a queued or
  running job returns that job instead, so callers wait on it rather than paying twice.
* Only the current lease owner can finish a job. A process that lost its lease can't overwrite the result.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from assetforge.core import db, jobs, settings_store
from assetforge.core.models import Job, JobStatus
from assetforge.core.util import dumps, utcnow
from assetforge.errors import ConflictError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

ACTIVE = ("queued", "running")
ABANDON_AFTER_SECONDS = 1800  # unleased generation jobs left "running" this long were orphaned by a crash
clock: Callable[[], float] = time.time
sleep: Callable[[float], None] = time.sleep


def lease_seconds(lib: Library) -> int:
    return int(settings_store.get(lib, "queue.lease_seconds"))


def create_request_job(
    lib: Library,
    record: dict[str, Any],
    fingerprint: str,
    *,
    owner: str,
    queued: bool,
    priority: int = 0,
    reuse_finished: bool = True,
) -> tuple[str | None, Job | None]:
    """Create a request job (queued, or running under `owner`), unless an identical one is active, or
    (with `reuse_finished`) already succeeded. Checked in the same transaction, so racing agents can't
    both create one. Returns (new_job_id, None) or (None, existing_job).
    """
    now = utcnow()
    statuses = "('queued', 'running', 'succeeded')" if reuse_finished else "('queued', 'running')"
    with db.transaction(lib.conn):
        row = lib.conn.execute(
            f"SELECT j.id FROM generation_jobs j LEFT JOIN assets a ON a.id = j.asset_id "
            f"WHERE j.kind = 'request' AND j.fingerprint = ? AND j.status IN {statuses} "
            "AND (j.status != 'succeeded' OR a.id IS NOT NULL) "
            "ORDER BY j.status = 'succeeded' DESC, j.created_at, j.id LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if row:
            active_id = row["id"]
        else:
            active_id = None
            job_id = db.next_id(lib.conn, "job")
            lib.conn.execute(
                """
                INSERT INTO generation_jobs (id, kind, status, fingerprint, request, provider, attempts, priority,
                                             max_attempts, lease_owner, lease_expires_at, created_at, updated_at)
                VALUES (?, 'request', ?, ?, ?, 'assetforge', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, "queued" if queued else "running", fingerprint, dumps(record), 0 if queued else 1,
                    priority, int(settings_store.get(lib, "queue.max_attempts")),
                    None if queued else owner, None if queued else clock() + lease_seconds(lib), now, now,
                ),
            )  # fmt: skip
    if active_id:
        return None, jobs.get_job(lib, active_id)
    return job_id, None


def _claim_where(lib: Library, where: str, params: list[Any], owner: str) -> Job | None:
    with db.transaction(lib.conn):
        row = lib.conn.execute(
            f"SELECT id FROM generation_jobs WHERE status = 'queued' AND {where} "
            "AND (run_after IS NULL OR run_after <= ?) ORDER BY priority DESC, created_at, id LIMIT 1",
            [*params, clock()],
        ).fetchone()
        if row is None:
            return None
        lib.conn.execute(
            "UPDATE generation_jobs SET status = 'running', lease_owner = ?, lease_expires_at = ?, "
            "attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (owner, clock() + lease_seconds(lib), utcnow(), row["id"]),
        )
    return jobs.get_job(lib, row["id"])


def claim_next(lib: Library, owner: str, kinds: tuple[str, ...] = ("request",)) -> Job | None:
    return _claim_where(lib, f"kind IN ({','.join('?' * len(kinds))})", list(kinds), owner)


def claim(lib: Library, job_id: str, owner: str) -> Job | None:
    """Claim one specific queued job (a synchronous caller taking over a queued twin request)."""
    return _claim_where(lib, "id = ?", [job_id], owner)


def heartbeat(conn: Any, job_id: str, owner: str, lease: float) -> bool:
    cursor = conn.execute(
        "UPDATE generation_jobs SET lease_expires_at = ? WHERE id = ? AND lease_owner = ? AND status = 'running'",
        (clock() + lease, job_id, owner),
    )
    return cursor.rowcount == 1


@contextmanager
def keep_alive(lib: Library, job_id: str, owner: str) -> Iterator[None]:
    """Extend the job's lease from a background thread (with its own DB connection) while work runs."""
    lease = lease_seconds(lib)
    stop = threading.Event()

    def beat() -> None:
        conn = db.connect(lib.paths.catalog)
        try:
            while not stop.wait(max(1.0, lease / 3)):
                heartbeat(conn, job_id, owner, lease)
        finally:
            conn.close()

    thread = threading.Thread(target=beat, name=f"lease-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def finish(lib: Library, job_id: str, owner: str, *, status: JobStatus, result: dict[str, Any] | None = None,
           error: dict[str, Any] | None = None, asset_id: str | None = None) -> bool:  # fmt: skip
    """Close a job we still own. Returns False if our lease was lost (the job was recovered elsewhere)."""
    with db.transaction(lib.conn):
        cursor = lib.conn.execute(
            "UPDATE generation_jobs SET status = ?, result = ?, error = ?, asset_id = ifnull(?, asset_id), "
            "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
            "WHERE id = ? AND lease_owner = ? AND status = 'running'",
            (status.value, dumps(result or {}), dumps(error) if error else None, asset_id, utcnow(), job_id, owner),
        )
    return cursor.rowcount == 1


def requeue(lib: Library, job_id: str, owner: str | None, *, delay: float, error: dict[str, Any]) -> bool:
    where, params = "id = ?", [job_id]
    if owner is not None:
        where += " AND lease_owner = ?"
        params.append(owner)
    with db.transaction(lib.conn):
        cursor = lib.conn.execute(
            f"UPDATE generation_jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
            f"run_after = ?, error = ?, updated_at = ? WHERE {where}",
            [clock() + delay, dumps(error), utcnow(), *params],
        )
    return cursor.rowcount == 1


def recover(lib: Library) -> dict[str, list[str]]:
    """Requeue (or fail) jobs whose lease expired; fail generation jobs orphaned by a crash."""
    now = clock()
    requeued, failed, abandoned = [], [], []
    with db.transaction(lib.conn):
        for row in lib.conn.execute(
            "SELECT id, attempts, max_attempts FROM generation_jobs WHERE status = 'running' "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (now,),
        ).fetchall():
            error = {"code": "LEASE_EXPIRED", "message": "The process running this job stopped responding"}
            if row["attempts"] < row["max_attempts"]:
                lib.conn.execute(
                    "UPDATE generation_jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, "
                    "run_after = NULL, error = ?, updated_at = ? WHERE id = ?",
                    (dumps(error), utcnow(), row["id"]),
                )
                requeued.append(row["id"])
            else:
                lib.conn.execute(
                    "UPDATE generation_jobs SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL, "
                    "error = ?, updated_at = ? WHERE id = ?",
                    (dumps({**error, "attempts": row["attempts"]}), utcnow(), row["id"]),
                )
                failed.append(row["id"])
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ABANDON_AFTER_SECONDS))
        for row in lib.conn.execute(
            "SELECT id FROM generation_jobs WHERE status = 'running' AND lease_owner IS NULL AND kind != 'request' "
            "AND updated_at < ?",
            (cutoff,),
        ).fetchall():
            lib.conn.execute(
                "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (dumps({"code": "ABANDONED", "message": "Interrupted; `assetforge jobs resume` can finish it"}),
                 utcnow(), row["id"]),
            )  # fmt: skip
            abandoned.append(row["id"])
    return {"requeued": requeued, "failed": failed, "abandoned": abandoned, "tmp_removed": _sweep_tmp(lib, now)}


TMP_MAX_AGE_SECONDS = 3600


def _sweep_tmp(lib: Library, now: float) -> int:
    """Delete scratch files/dirs left in <library>/tmp by killed processes (anything older than an hour)."""
    import shutil

    removed = 0
    if not lib.paths.tmp.is_dir():
        return 0
    for entry in lib.paths.tmp.iterdir():
        try:
            if now - entry.stat().st_mtime < TMP_MAX_AGE_SECONDS:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def wait(lib: Library, job_id: str, *, timeout: float, poll: float = 0.5) -> Job:
    """Wait until the job leaves queued/running (or the timeout passes); returns its latest state."""
    deadline = clock() + timeout
    while True:
        job = jobs.get_job(lib, job_id)
        if job.status.value not in ACTIVE or clock() >= deadline:
            return job
        recover(lib)  # a waiter can unstick a job whose owner died
        sleep(poll)


def cancel(lib: Library, job_id: str) -> Job:
    job = jobs.get_job(lib, job_id)
    if job.status != JobStatus.QUEUED:
        raise ConflictError(f"Only queued jobs can be cancelled; {job_id} is {job.status.value}", status=job.status.value)
    with db.transaction(lib.conn):
        lib.conn.execute("UPDATE generation_jobs SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'queued'",
                         (utcnow(), job_id))  # fmt: skip
    return jobs.get_job(lib, job_id)


def retry(lib: Library, job_id: str) -> Job:
    job = jobs.get_job(lib, job_id)
    if job.kind != "request":
        raise ConflictError("Only request jobs can be retried here; use `jobs resume` for image jobs")
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise ConflictError(f"{job_id} is {job.status.value}; only failed or cancelled jobs can be retried")
    with db.transaction(lib.conn):
        lib.conn.execute(
            "UPDATE generation_jobs SET status = 'queued', attempts = 0, run_after = NULL, updated_at = ? WHERE id = ?",
            (utcnow(), job_id),
        )
    return jobs.get_job(lib, job_id)


def pending(lib: Library, kinds: tuple[str, ...] = ("request",)) -> dict[str, int]:
    marks = ",".join("?" * len(kinds))
    rows = lib.conn.execute(
        f"SELECT status, count(*) FROM generation_jobs WHERE kind IN ({marks}) AND status IN ('queued', 'running') "
        "GROUP BY status",
        list(kinds),
    )
    counts = {"queued": 0, "running": 0}
    counts.update({r[0]: r[1] for r in rows})
    return counts
