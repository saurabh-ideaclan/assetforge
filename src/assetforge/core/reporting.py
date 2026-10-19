"""Usage & cost overview reports over a date range (presets or custom), from `usage_records`.

Ranges are evaluated in a chosen timezone (default UTC) and are half-open: [start, end).
Custom dates are inclusive, so `--from 2026-10-01 --to 2026-10-19` covers both whole days.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from assetforge.core import budget, metering, pricing
from assetforge.core.metering import TOKEN_FIELDS
from assetforge.errors import InvalidInputError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PRESETS = ("today", "yesterday", "this-week", "last-7-days", "this-month", "last-month", "last-30-days",
           "this-year", "all")  # fmt: skip
INTERVALS = ("day", "week", "month")
MAX_BUCKETS = 400


@dataclass(frozen=True)
class Range:
    start: datetime | None  # aware, inclusive
    end: datetime  # aware, exclusive
    tz: tzinfo
    label: str

    def utc_bounds(self) -> tuple[str | None, str]:
        fmt = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")  # noqa: E731
        return (fmt(self.start) if self.start else None), fmt(self.end)


def parse_tz(name: str | None) -> tzinfo:
    if not name or name.upper() == "UTC":
        return UTC
    if name == "local":
        local = datetime.now().astimezone().tzinfo
        assert local is not None
        return local
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidInputError(f"Unknown timezone {name!r}; use UTC, local or an IANA name like Asia/Kolkata") from exc


def _parse_point(text: str, tz: tzinfo, *, end: bool) -> datetime:
    text = text.strip()
    try:
        if len(text) == 10:  # a date: the whole day is included
            day = date.fromisoformat(text)
            moment = datetime(day.year, day.month, day.day, tzinfo=tz)
            return moment + timedelta(days=1) if end else moment
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidInputError(f"Dates must look like 2026-10-19 or 2026-10-19T14:30, got {text!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=tz)


def resolve_range(preset: str | None = None, start: str | None = None, end: str | None = None, *,
                  tz_name: str | None = None, now: datetime | None = None) -> Range:  # fmt: skip
    tz = parse_tz(tz_name)
    now = (now or datetime.now(UTC)).astimezone(tz)
    if start or end:
        if preset:
            raise InvalidInputError("Use either a preset range or --from/--to, not both")
        lo = _parse_point(start, tz, end=False) if start else None
        hi = _parse_point(end, tz, end=True) if end else now
        if lo and lo >= hi:
            raise InvalidInputError("--from must be before --to")
        label = f"{start or 'beginning'} to {end or 'now'}"
        return Range(lo, hi, tz, label)
    preset = preset or "last-30-days"
    if preset not in PRESETS:
        raise InvalidInputError(f"Unknown range {preset!r}", allowed=list(PRESETS))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    if preset == "today":
        lo, hi = today, tomorrow
    elif preset == "yesterday":
        lo, hi = today - timedelta(days=1), today
    elif preset == "this-week":
        lo, hi = today - timedelta(days=today.weekday()), tomorrow
    elif preset == "last-7-days":
        lo, hi = today - timedelta(days=6), tomorrow
    elif preset == "last-30-days":
        lo, hi = today - timedelta(days=29), tomorrow
    elif preset == "this-month":
        lo, hi = today.replace(day=1), tomorrow
    elif preset == "last-month":
        first = today.replace(day=1)
        lo, hi = (first - timedelta(days=1)).replace(day=1), first
    elif preset == "this-year":
        lo, hi = today.replace(month=1, day=1), tomorrow
    else:
        lo, hi = None, tomorrow
    return Range(lo, hi, tz, preset)


def _bucket(moment: datetime, interval: str) -> str:
    if interval == "day":
        return moment.date().isoformat()
    if interval == "week":
        monday = moment.date() - timedelta(days=moment.weekday())
        return f"week of {monday.isoformat()}"
    return moment.strftime("%Y-%m")


def _empty() -> dict[str, Any]:
    return {"calls": 0, "images": 0, **dict.fromkeys(TOKEN_FIELDS, 0), "total_tokens": 0, "cost_usd": 0.0,
            "estimated_cost_usd": 0.0, "_estimated_calls": 0, "_actual_for_estimated": 0.0}  # fmt: skip


def _add(acc: dict[str, Any], row: dict[str, Any]) -> None:
    acc["calls"] += 1
    acc["images"] += row["images"]
    for field in (*TOKEN_FIELDS, "total_tokens"):
        acc[field] += row[field]
    acc["cost_usd"] += row["cost_usd"] or 0.0
    if row["estimated_cost_usd"] is not None:
        acc["estimated_cost_usd"] += row["estimated_cost_usd"]
        acc["_estimated_calls"] += 1
        acc["_actual_for_estimated"] += row["cost_usd"] or 0.0


def _finish(acc: dict[str, Any], grand_cost: float | None = None) -> dict[str, Any]:
    out = {k: v for k, v in acc.items() if not k.startswith("_")}
    out["cost_usd"] = round(out["cost_usd"], 4)
    out["estimated_cost_usd"] = round(out["estimated_cost_usd"], 4)
    out["avg_cost_per_image"] = round(out["cost_usd"] / out["images"], 4) if out["images"] else None
    actual = acc["_actual_for_estimated"]
    out["estimate_error_pct"] = (round((acc["estimated_cost_usd"] - actual) / actual * 100, 1)
                                 if acc["_estimated_calls"] and actual else None)  # fmt: skip
    if grand_cost is not None:
        out["share"] = round(out["cost_usd"] / grand_cost, 4) if grand_cost else 0.0
    return out


def _series_keys(rng: Range, interval: str, seen: list[str]) -> list[str]:
    if rng.start is None:
        return sorted(set(seen))
    keys: list[str] = []
    cursor = rng.start
    while cursor < rng.end and len(keys) < MAX_BUCKETS:
        key = _bucket(cursor, interval)
        if not keys or keys[-1] != key:
            keys.append(key)
        cursor += timedelta(days=1)
    return keys


def records(lib: Library, rng: Range, *, provider: str | None = None, model: str | None = None,
            project: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
    lo, hi = rng.utc_bounds()
    where, params = ["created_at < ?"], [hi]
    if lo:
        where.append("created_at >= ?")
        params.append(lo)
    for column, value in (("provider", provider), ("model", model), ("project", project)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    rows = lib.conn.execute(f"SELECT * FROM usage_records WHERE {' AND '.join(where)} ORDER BY created_at, id", params)
    return [dict(r) for r in rows]


def build(lib: Library, rng: Range, *, interval: str = "day", provider: str | None = None, model: str | None = None,
          project: str | None = None, top: int = 10) -> dict[str, Any]:  # fmt: skip
    if interval not in INTERVALS:
        raise InvalidInputError(f"Unknown interval {interval!r}", allowed=list(INTERVALS))
    backfilled = metering.backfill(lib)
    rows = records(lib, rng, provider=provider, model=model, project=project)

    totals = _empty()
    groups: dict[str, dict[str, dict[str, Any]]] = {g: defaultdict(_empty) for g in ("provider", "model", "project", "operation")}
    series: dict[str, dict[str, Any]] = defaultdict(_empty)
    seen_buckets = []
    for row in rows:
        moment = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(rng.tz)
        _add(totals, row)
        _add(groups["provider"][row["provider"]], row)
        _add(groups["model"][f"{row['provider']}/{row['model']}"], row)
        _add(groups["project"][row["project"] or "(none)"], row)
        _add(groups["operation"][row["operation"]], row)
        key = _bucket(moment, interval)
        seen_buckets.append(key)
        _add(series[key], row)
    grand = totals["cost_usd"]

    def table(name: str) -> list[dict[str, Any]]:
        return sorted(({"key": k, **_finish(v, grand)} for k, v in groups[name].items()),
                      key=lambda r: (-r["cost_usd"], r["key"]))  # fmt: skip

    job_ids = [r["job_id"] for r in sorted(rows, key=lambda r: -(r["cost_usd"] or 0))[:top] if r["job_id"]]
    prompts = {}
    for job_id in job_ids:
        job = lib.conn.execute("SELECT request FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        request = json.loads(job["request"] or "{}").get("request", {}) if job else {}
        prompts[job_id] = request.get("prompt")
    top_jobs = [{"job_id": r["job_id"], "created_at": r["created_at"], "provider": r["provider"], "model": r["model"],
                 "images": r["images"], "total_tokens": r["total_tokens"], "cost_usd": round(r["cost_usd"] or 0, 4),
                 "estimated_cost_usd": r["estimated_cost_usd"], "project": r["project"], "prompt": prompts.get(r["job_id"])}
                for r in sorted(rows, key=lambda r: -(r["cost_usd"] or 0))[:top]]  # fmt: skip

    lo, hi = rng.utc_bounds()
    activity = defaultdict(lambda: defaultdict(int))
    params = [hi] + ([lo] if lo else [])
    for row in lib.conn.execute(
        "SELECT kind, status, count(*) AS n FROM generation_jobs WHERE created_at < ? "
        + ("AND created_at >= ? " if lo else "") + "GROUP BY kind, status", params,
    ):  # fmt: skip
        activity[row["kind"]][row["status"]] = row["n"]

    price_table = pricing.table(lib)
    used_models = sorted({f"{r['provider']}/{r['model']}" for r in rows})
    return {
        "range": {"label": rng.label, "from": lo, "to": hi, "tz": str(rng.tz), "interval": interval,
                  "filters": {"provider": provider, "model": model, "project": project}},  # fmt: skip
        "totals": _finish(totals),
        "by_provider": table("provider"),
        "by_model": table("model"),
        "by_project": table("project"),
        "by_operation": table("operation"),
        "series": [{"period": k, **_finish(series[k] if k in series else _empty())}
                   for k in _series_keys(rng, interval, seen_buckets)],  # fmt: skip
        "top_jobs": top_jobs,
        "activity": {k: dict(v) for k, v in sorted(activity.items())},
        "pricing": {"source_url": price_table["source_url"], "fetched_at": price_table["fetched_at"],
                    "age_days": price_table["age_days"],
                    "rates": {m: price_table["models"].get(m) for m in used_models}},  # fmt: skip
        "budget": budget.summary(lib),
        "backfilled_records": backfilled,
    }


def to_csv(rows: list[dict[str, Any]]) -> str:
    columns = ["created_at", "provider", "model", "operation", "job_id", "project", "quality", "size", "images",
               *TOKEN_FIELDS, "total_tokens", "cost_usd", "estimated_cost_usd", "price_source"]  # fmt: skip
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
