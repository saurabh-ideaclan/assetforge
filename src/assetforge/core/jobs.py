"""Generation job records: what was requested, by which engine/provider, and what it produced.

Jobs make generation auditable and let a repeated request with the same fingerprint
return the earlier result instead of paying (in time or money) to generate it again.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from assetforge.core import db
from assetforge.core.models import Job, JobStatus
from assetforge.core.util import dumps, loads, utcnow
from assetforge.errors import NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library


def fingerprint(data: Any) -> str:
    return hashlib.sha256(dumps(data).encode()).hexdigest()


def _row_to_job(row: Any) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        fingerprint=row["fingerprint"],
        request=loads(row["request"], {}),
        provider=row["provider"],
        model=row["model"],
        asset_id=row["asset_id"],
        result=loads(row["result"], {}),
        error=loads(row["error"], None),
        cost_usd=row["cost_usd"],
        attempts=row["attempts"],
        **{k: row[k] for k in ("estimated_cost_usd", "max_attempts", "priority", "lease_owner", "lease_expires_at",
                                "run_after") if k in row.keys()},  # fmt: skip
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_job(
    lib: Library,
    kind: str,
    request: dict[str, Any],
    *,
    fingerprint: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    now = utcnow()
    with db.transaction(lib.conn):
        job_id = db.next_id(lib.conn, "job")
        lib.conn.execute(
            """
            INSERT INTO generation_jobs (id, kind, status, fingerprint, request, provider, model, attempts,
                                         created_at, updated_at)
            VALUES (?, ?, 'running', ?, ?, ?, ?, 1, ?, ?)
            """,
            (job_id, kind, fingerprint, dumps(request), provider, model, now, now),
        )
    return job_id


def complete_job(
    lib: Library, job_id: str, *, asset_id: str, result: dict[str, Any], cost_usd: float | None = 0.0
) -> None:
    with db.transaction(lib.conn):
        lib.conn.execute(
            "UPDATE generation_jobs SET status = 'succeeded', asset_id = ?, result = ?, cost_usd = ?, "
            "updated_at = ? WHERE id = ?",
            (asset_id, dumps(result), cost_usd, utcnow(), job_id),
        )


def fail_job(lib: Library, job_id: str, error: dict[str, Any]) -> None:
    with db.transaction(lib.conn):
        lib.conn.execute(
            "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (dumps(error), utcnow(), job_id),
        )


def find_completed(lib: Library, kind: str, fingerprint_: str) -> Job | None:
    """The latest successful job for this fingerprint whose asset still exists."""
    row = lib.conn.execute(
        """
        SELECT j.* FROM generation_jobs j JOIN assets a ON a.id = j.asset_id
        WHERE j.kind = ? AND j.fingerprint = ? AND j.status = 'succeeded'
        ORDER BY j.created_at DESC, j.id DESC LIMIT 1
        """,
        (kind, fingerprint_),
    ).fetchone()
    return _row_to_job(row) if row else None


def get_job(lib: Library, job_id: str) -> Job:
    row = lib.conn.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"No job {job_id!r}", job_id=job_id)
    return _row_to_job(row)


def list_jobs(
    lib: Library, *, status: JobStatus | None = None, kind: str | None = None, limit: int = 50
) -> list[Job]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status.value)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    sql = "SELECT * FROM generation_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    return [_row_to_job(row) for row in lib.conn.execute(sql, [*params, limit])]


def update_job(lib: Library, job_id: str, **fields: Any) -> None:
    """Update attempts/provider/model/error/status on a job; dict values are stored as JSON."""
    allowed = {"status", "provider", "model", "attempts", "error", "result", "cost_usd", "asset_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"cannot update {unknown}")
    values = {k: dumps(v) if isinstance(v, dict | list) else v for k, v in fields.items()}
    values["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in values)
    with db.transaction(lib.conn):
        lib.conn.execute(f"UPDATE generation_jobs SET {assignments} WHERE id = ?", [*values.values(), job_id])
