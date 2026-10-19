"""Spending limits for paid generation, enforced atomically across agents.

Spend = actual cost of finished paid jobs + estimated cost of calls still in flight. Before each
provider call, the engine *reserves* its estimate on the job inside one write transaction, after
checking the daily, monthly and per-call limits. Concurrent agents can't overshoot a budget
together. Once the call returns, the actual cost replaces the reservation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from assetforge.core import db, settings_store
from assetforge.core.util import utcnow
from assetforge.errors import BudgetExceededError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PAID_KINDS = ("image", "image_edit")


def _period_starts(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    fmt = lambda d: d.isoformat(timespec="seconds").replace("+00:00", "Z")  # noqa: E731
    return {"daily": fmt(day), "monthly": fmt(month)}


def spent(lib: Library, since: str) -> float:
    marks = ",".join("?" * len(PAID_KINDS))
    row = lib.conn.execute(
        f"SELECT ifnull(sum(ifnull(cost_usd, CASE WHEN status = 'running' THEN estimated_cost_usd END)), 0) "
        f"FROM generation_jobs WHERE kind IN ({marks}) AND created_at >= ?",
        [*PAID_KINDS, since],
    ).fetchone()
    return round(float(row[0]), 4)


def summary(lib: Library) -> dict[str, Any]:
    starts = _period_starts()
    out: dict[str, Any] = {}
    for period, since in starts.items():
        limit = settings_store.get(lib, f"budget.{period}_usd")
        used = spent(lib, since)
        out[period] = {"since": since, "spent_usd": used, "limit_usd": limit,
                       "remaining_usd": None if limit is None else round(max(0.0, limit - used), 4)}  # fmt: skip
    out["per_request_usd"] = settings_store.get(lib, "budget.per_request_usd")
    marks = ",".join("?" * len(PAID_KINDS))
    out["by_provider_monthly"] = {
        r["provider"]: round(r["total"], 4)
        for r in lib.conn.execute(
            f"SELECT provider, sum(cost_usd) AS total FROM generation_jobs WHERE kind IN ({marks}) "
            "AND created_at >= ? AND cost_usd IS NOT NULL GROUP BY provider ORDER BY provider",
            [*PAID_KINDS, starts["monthly"]],
        )
    }
    return out


def reserve(lib: Library, job_id: str, estimate: float, *, max_cost: float | None = None) -> None:
    """Check limits and record `estimate` on the running job, atomically. Raises BudgetExceededError."""
    per_call = max_cost if max_cost is not None else settings_store.get(lib, "budget.per_request_usd")
    if per_call is not None and estimate > per_call:
        raise BudgetExceededError(
            f"Estimated cost ${estimate:.3f} exceeds the per-request limit ${per_call:.3f}",
            hint="Lower quality/size/n, or raise --max-cost / budget.per_request_usd",
            estimate_usd=estimate, limit_usd=per_call,
        )  # fmt: skip
    starts = _period_starts()
    with db.transaction(lib.conn):
        for period, since in starts.items():
            limit = settings_store.get(lib, f"budget.{period}_usd")
            if limit is None:
                continue
            used = spent(lib, since)
            if used + estimate > limit + 1e-9:
                raise BudgetExceededError(
                    f"{period.capitalize()} budget ${limit:.2f} would be exceeded "
                    f"(spent/reserved ${used:.3f} + this call ~${estimate:.3f})",
                    hint="See `assetforge budget`; raise the limit with `assetforge config set` or wait",
                    period=period, spent_usd=used, estimate_usd=estimate, limit_usd=limit,
                )  # fmt: skip
        lib.conn.execute(
            "UPDATE generation_jobs SET estimated_cost_usd = ?, updated_at = ? WHERE id = ?",
            (round(estimate, 4), utcnow(), job_id),
        )
